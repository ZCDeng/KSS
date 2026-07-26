"""Content-addressed artifact storage for Deep Research."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from kss.storage.db import connect, ensure_schema

from .repository import dumps, new_id, utc_now


class ArtifactSafetyError(ValueError):
    pass


class ArtifactStore:
    """Write-once object store rooted under ``storage/agent/research``."""

    def __init__(self, *, root: Path, db_path: Path) -> None:
        self.root = Path(root).resolve()
        self.db_path = Path(db_path)
        self.objects_root = self.root / "objects" / "sha256"
        self.staging_root = self.root / "staging"
        self.orphans_root = self.root / "orphans"
        self.objects_root.mkdir(parents=True, exist_ok=True)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self._assert_inside_root(self.objects_root)
        self._assert_inside_root(self.staging_root)

    def _assert_inside_root(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactSafetyError("artifact path escapes research root") from exc
        return resolved

    def _object_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ArtifactSafetyError("invalid sha256 digest")
        path = self.objects_root / digest[:2] / digest
        self._assert_inside_root(path)
        return path

    def put_bytes(
        self,
        *,
        goal_id: str,
        kind: str,
        name: str,
        data: bytes,
        task_id: str | None = None,
        attempt_id: str | None = None,
        media_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fd, tmp = tempfile.mkstemp(prefix="artifact-", dir=str(self.staging_root))
        tmp_path = Path(tmp)
        digest = hashlib.sha256(data).hexdigest()
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            object_path = self._object_path(digest)
            object_path.parent.mkdir(parents=True, exist_ok=True)
            if not object_path.exists():
                os.replace(tmp_path, object_path)
                directory_fd = os.open(object_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            else:
                tmp_path.unlink(missing_ok=True)
            artifact_id = new_id("art")
            now = utc_now()
            with connect(self.db_path) as conn:
                ensure_schema(conn)
                conn.execute(
                    """
                    INSERT INTO research_artifacts (
                        artifact_id, goal_id, task_id, attempt_id, kind, name, object_hash,
                        size_bytes, media_type, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        goal_id,
                        task_id,
                        attempt_id,
                        kind,
                        name,
                        digest,
                        len(data),
                        media_type,
                        dumps(metadata or {}),
                        now,
                    ),
                )
                row = conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS seq FROM research_events WHERE goal_id=?", (goal_id,)).fetchone()
                sequence = int(row["seq"])
                event_id = new_id("evt")
                frame = {
                    "protocol_version": 1,
                    "event_id": event_id,
                    "goal_id": goal_id,
                    "goal": goal_id,
                    "sequence": sequence,
                    "timestamp": now,
                    "event": "artifact_ready",
                    "type": "artifact_ready",
                    "task_id": task_id,
                    "attempt_id": attempt_id,
                    "artifact": {
                        "artifact_id": artifact_id,
                        "id": artifact_id,
                        "kind": kind,
                        "name": name,
                        "object_hash": digest,
                        "size_bytes": len(data),
                        "media_type": media_type,
                    },
                }
                conn.execute(
                    """
                    INSERT INTO research_events (
                        event_id, goal_id, sequence, event_type, task_id, attempt_id,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, 'artifact_ready', ?, ?, ?, ?)
                    """,
                    (event_id, goal_id, sequence, task_id, attempt_id, dumps(frame), now),
                )
            return {
                "artifact_id": artifact_id,
                "id": artifact_id,
                "goal_id": goal_id,
                "kind": kind,
                "name": name,
                "object_hash": digest,
                "size_bytes": len(data),
                "media_type": media_type,
                "metadata": metadata or {},
                "path": str(object_path),
            }
        finally:
            tmp_path.unlink(missing_ok=True)

    def read_bytes(self, object_hash: str) -> bytes:
        path = self._object_path(object_hash)
        resolved = path.resolve()
        resolved.relative_to(self.root)
        return resolved.read_bytes()

    def list_goal(self, goal_id: str) -> list[dict[str, Any]]:
        from .repository import loads

        with connect(self.db_path) as conn:
            ensure_schema(conn)
            rows = conn.execute("SELECT * FROM research_artifacts WHERE goal_id=? ORDER BY created_at", (goal_id,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["id"] = item["artifact_id"]
            item["metadata"] = loads(item.pop("metadata_json"), {})
            out.append(item)
        return out

    def export_object(self, *, object_hash: str, destination: Path, allow_overwrite: bool = False) -> dict[str, Any]:
        destination = Path(destination).expanduser()
        parent = destination.parent.resolve()
        parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not allow_overwrite:
            raise FileExistsError(str(destination))
        data = self.read_bytes(object_hash)
        fd, tmp = tempfile.mkstemp(prefix=".kss-export-", dir=str(parent))
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, destination)
        finally:
            tmp_path.unlink(missing_ok=True)
        return {"destination": str(destination), "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}

    def clean_staging_and_isolate_orphans(self) -> dict[str, int]:
        removed_staging = 0
        for path in self.staging_root.glob("*"):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
                removed_staging += 1
        with connect(self.db_path) as conn:
            ensure_schema(conn)
            referenced = {str(r["object_hash"]) for r in conn.execute("SELECT object_hash FROM research_artifacts")}
        isolated = 0
        for obj in self.objects_root.glob("*/*"):
            if not obj.is_file() or obj.name in referenced:
                continue
            target = self.orphans_root / obj.parent.name / obj.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(obj), str(target))
            isolated += 1
        return {"removed_staging": removed_staging, "isolated_orphans": isolated}

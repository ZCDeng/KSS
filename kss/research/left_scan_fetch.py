"""Locate a dated 左侧机会扫描 file on disk or Google Drive."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from kss.research.left_scan import (
    GOOGLE_DOC_MIME,
    PDF_MIME,
    LeftScanError,
    ScanCandidate,
)

DEFAULT_FOLDER_ID = "19Pk-kZG2YBextK6MUBkdh2-led2xLqRf"
DEFAULT_FOLDER_NAME = "左侧机会扫描"


def token_path(state_root: Path) -> Path:
    override = os.environ.get("KSS_GDRIVE_TOKEN_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(state_root) / "secrets" / "gdrive_oauth.json"


def folder_id() -> str:
    return os.environ.get("KSS_GDRIVE_FOLDER_ID", "").strip() or DEFAULT_FOLDER_ID


def folder_name() -> str:
    return os.environ.get("KSS_LEFT_SCAN_FOLDER_NAME", "").strip() or DEFAULT_FOLDER_NAME


def _mtime_iso(path: Path) -> str:
    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return PDF_MIME
    if suffix in {".html", ".htm"}:
        return "text/html"
    return "application/octet-stream"


def local_search_roots(state_root: Path) -> list[Path]:
    roots: list[Path] = []
    explicit = os.environ.get("KSS_LEFT_SCAN_DIR", "").strip()
    if explicit:
        roots.append(Path(explicit).expanduser())
    roots.append(Path(state_root) / "inbox" / "left_scan")
    home = Path.home()
    cloud = home / "Library" / "CloudStorage"
    if cloud.is_dir():
        for drive_root in sorted(cloud.glob("GoogleDrive-*")):
            for parent_name in ("My Drive", "我的云端硬盘"):
                roots.append(drive_root / parent_name / folder_name())
    return roots


def list_local_candidates(state_root: Path) -> list[ScanCandidate]:
    found: list[ScanCandidate] = []
    seen: set[str] = set()
    for root in local_search_roots(state_root):
        try:
            if not root.is_dir():
                continue
            children = list(root.iterdir())
        except OSError:
            continue
        for path in children:
            if not path.is_file():
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                stat = path.stat()
            except OSError:
                continue
            found.append(
                ScanCandidate(
                    name=path.name,
                    mime=_guess_mime(path),
                    modified=_mtime_iso(path),
                    size=int(stat.st_size),
                    path=str(path),
                )
            )
    return found


def _load_oauth(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "installed" in payload or "web" in payload:
        raise LeftScanError("gdrive_token_is_client_secrets")
    return {str(key): str(value) for key, value in payload.items() if value}


def _access_token(path: Path) -> str:
    data = _load_oauth(path)
    cached = data.get("access_token", "").strip()
    expiry = data.get("expiry") or data.get("token_expiry") or ""
    if cached and expiry:
        try:
            end = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            if end > datetime.now(timezone.utc):
                return cached
        except ValueError:
            pass
    refresh = data.get("refresh_token", "").strip()
    client_id = data.get("client_id", "").strip()
    client_secret = data.get("client_secret", "").strip()
    if not (refresh and client_id and client_secret):
        if cached:
            return cached
        raise LeftScanError("gdrive_token_incomplete")
    response = requests.post(
        data.get("token_uri") or "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise LeftScanError(f"gdrive_token_refresh_failed:{response.status_code}")
    body = response.json()
    access = str(body.get("access_token") or "")
    if not access:
        raise LeftScanError("gdrive_token_refresh_empty")
    return access


def list_drive_candidates(state_root: Path) -> list[ScanCandidate]:
    path = token_path(state_root)
    if not path.is_file():
        return []
    token = _access_token(path)
    query = f"'{folder_id()}' in parents and trashed = false"
    response = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        params={
            "q": query,
            "fields": "files(id,name,mimeType,modifiedTime,size)",
            "pageSize": 100,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise LeftScanError(f"gdrive_list_failed:{response.status_code}")
    files = response.json().get("files") or []
    found: list[ScanCandidate] = []
    for item in files:
        found.append(
            ScanCandidate(
                name=str(item.get("name") or ""),
                mime=str(item.get("mimeType") or ""),
                modified=str(item.get("modifiedTime") or ""),
                size=int(item.get("size") or 0),
                file_id=str(item.get("id") or "") or None,
            )
        )
    return found


def download_candidate(candidate: ScanCandidate, *, state_root: Path) -> bytes:
    if candidate.path:
        return Path(candidate.path).read_bytes()
    if not candidate.file_id:
        raise LeftScanError("scan_file_has_no_source")
    token = _access_token(token_path(state_root))
    params: dict[str, str] = {"supportsAllDrives": "true"}
    url = f"https://www.googleapis.com/drive/v3/files/{quote(candidate.file_id)}"
    if candidate.mime == GOOGLE_DOC_MIME:
        url += "/export"
        params["mimeType"] = "text/html"
    else:
        params["alt"] = "media"
    response = requests.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    if response.status_code >= 400:
        raise LeftScanError(f"gdrive_download_failed:{response.status_code}")
    return response.content


def collect_candidates(state_root: Path) -> list[ScanCandidate]:
    found = list_local_candidates(state_root)
    try:
        found.extend(list_drive_candidates(state_root))
    except LeftScanError:
        if found:
            return found
        raise
    return found

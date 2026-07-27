"""Cross-process ownership of the single production research execution slot.

ResearchService instances normally live inside a long-running sidecar, while a
scheduled report uses a short-lived runner. A process-local thread map is not
enough to keep those two paths from collecting evidence concurrently.
"""

from __future__ import annotations

import os
from pathlib import Path

try:  # macOS production path; tests may run without fcntl.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class ResearchExecutionSlot:
    """An exclusive, non-blocking lock shared by all research processes."""

    def __init__(self, state_root: Path) -> None:
        self.path = Path(state_root) / "storage" / "agent" / "research" / ".execution.lock"
        self._fd: int | None = None

    def acquire(self) -> bool:
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.write(fd, f"pid={os.getpid()}\n".encode("ascii"))
            os.fsync(fd)
            self._fd = fd
            return True
        except OSError:
            os.close(fd)
            return False

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

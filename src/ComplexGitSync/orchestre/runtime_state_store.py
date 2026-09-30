"""runtime_state_store — Where a runtime snapshot is stored when no workspace has one.

Ring: 3
Contract: Where a runtime snapshot is stored when no workspace has one.
Imports: none
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class RuntimeStateStore:
    """Persistent registry that maps ``.cgs`` files to their latest ``.gts`` snapshots."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is None:
            base_dir = RuntimeStateStore._resolve_state_base_dir()
        self.base_dir = Path(base_dir)

    def latest_snapshot_for(self, cgs_path: Path | str) -> Path | None:
        """Return the path to the latest snapshot for *cgs_path*, or ``None``."""
        record_path = self._record_path(Path(cgs_path).resolve())
        if not record_path.is_file():
            return None
        snapshot_path = Path(record_path.read_text(encoding="utf-8").strip())
        if snapshot_path.is_file():
            return snapshot_path
        return None

    def record_snapshot(self, cgs_path: Path | str, snapshot_path: Path | str) -> None:
        """Record *snapshot_path* as the latest snapshot for *cgs_path*."""
        record_path = self._record_path(Path(cgs_path).resolve())
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(str(Path(snapshot_path).resolve()), encoding="utf-8")

    def _record_path(self, resolved_cgs_path: Path) -> Path:
        key = hashlib.sha256(str(resolved_cgs_path).encode()).hexdigest()[:24]
        return self.base_dir / f"{key}.ptr"

    @staticmethod
    def _resolve_state_base_dir() -> Path:
        xdg_state = os.environ.get("XDG_STATE_HOME")
        if xdg_state:
            return Path(xdg_state) / "ComplexGitSync" / "snapshots"
        return Path.home() / ".local" / "state" / "ComplexGitSync" / "snapshots"


__all__ = [
    "RuntimeStateStore",
]

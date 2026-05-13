"""Runtime state store for ComplexGitSync snapshot tracking."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


class RuntimeStateStore:
    """Persistent registry that maps ``.cgs`` files to their latest ``.gts`` snapshots.

    The store keeps a small index file per ``.cgs`` path (keyed by a SHA-256
    digest of the resolved path) inside *base_dir*.  When no explicit
    *base_dir* is given the location is resolved at construction time from:

    1. ``$XDG_STATE_HOME/ComplexGitSync/snapshots/`` — when ``XDG_STATE_HOME``
       is set.
    2. ``~/.local/state/ComplexGitSync/snapshots/`` — as the final fallback.

    Usage::

        store = RuntimeStateStore()
        store.record_snapshot(cgs_path, gts_path)
        latest = store.latest_snapshot_for(cgs_path)
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is None:
            base_dir = _resolve_default_base_dir()
        self.base_dir = Path(base_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def latest_snapshot_for(self, cgs_path: Path | str) -> Path | None:
        """Return the path to the latest snapshot for *cgs_path*, or ``None``.

        Returns ``None`` when no snapshot has been recorded or the recorded
        path no longer exists on disk.
        """
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

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _record_path(self, resolved_cgs_path: Path) -> Path:
        key = hashlib.sha256(str(resolved_cgs_path).encode()).hexdigest()[:24]
        return self.base_dir / f"{key}.ptr"


def _resolve_default_base_dir() -> Path:
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "ComplexGitSync" / "snapshots"
    return Path.home() / ".local" / "state" / "ComplexGitSync" / "snapshots"

"""Backward-compatible re-export shim — SyncState now lives in git_repo.py."""
from .git_repo import SyncState
__all__ = ["SyncState"]

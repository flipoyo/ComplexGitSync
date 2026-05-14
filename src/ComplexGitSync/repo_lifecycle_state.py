"""Backward-compatible re-export shim — RepoLifecycleState now lives in git_repo.py."""
from .git_repo import RepoLifecycleState
__all__ = ["RepoLifecycleState"]

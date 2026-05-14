"""Backward-compatible re-export shim — RepoNode now lives in git_repo.py."""
from .git_repo import RepoNode
__all__ = ["RepoNode"]

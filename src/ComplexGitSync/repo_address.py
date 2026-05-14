"""Backward-compatible re-export shim — RepoAddress now lives in git_repo.py."""
from .git_repo import RepoAddress
__all__ = ["RepoAddress"]

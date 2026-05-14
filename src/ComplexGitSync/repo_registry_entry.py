"""Backward-compatible re-export shim — RepoRegistryEntry now lives in git_repo.py."""
from .git_repo import RepoRegistryEntry
__all__ = ["RepoRegistryEntry"]

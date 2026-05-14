"""Backward-compatible re-export shim — GitProvider now lives in git_repo.py."""
from .git_repo import GitProvider
__all__ = ["GitProvider"]

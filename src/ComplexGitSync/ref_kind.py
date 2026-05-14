"""Backward-compatible re-export shim — RefKind now lives in git_repo.py."""
from .git_repo import RefKind
__all__ = ["RefKind"]

"""Backward-compatible re-export shim — NodeType now lives in git_repo.py."""
from .git_repo import NodeType
__all__ = ["NodeType"]

"""Backward-compatible re-export shim — ProjectTreeState now lives in git_tree.py."""
from .git_tree import ProjectTreeState
__all__ = ["ProjectTreeState"]

"""Backward-compatible re-export shim — TreeLifecycleState now lives in git_tree.py."""
from .git_tree import TreeLifecycleState
__all__ = ["TreeLifecycleState"]

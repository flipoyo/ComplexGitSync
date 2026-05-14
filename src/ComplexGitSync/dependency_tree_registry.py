"""Backward-compatible re-export shim — DependencyTreeRegistry now lives in git_tree.py."""
from .git_tree import DependencyTreeRegistry
__all__ = ["DependencyTreeRegistry"]

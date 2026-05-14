"""Backward-compatible re-export shim — render functions now live in git_tree.py."""
from .git_tree import format_project_tree, format_registry_json
__all__ = ["format_project_tree", "format_registry_json"]

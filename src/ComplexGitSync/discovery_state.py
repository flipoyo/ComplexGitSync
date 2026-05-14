"""Backward-compatible re-export shim — DiscoveryState now lives in git_repo.py."""
from .git_repo import DiscoveryState
__all__ = ["DiscoveryState"]

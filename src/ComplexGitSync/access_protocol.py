"""Backward-compatible re-export shim — AccessProtocol now lives in git_repo.py."""
from .git_repo import AccessProtocol
__all__ = ["AccessProtocol"]

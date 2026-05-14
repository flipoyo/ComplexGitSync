"""Backward-compatible re-export shim — ComplexGitSyncClient now lives in orchestre.py."""
from .orchestre import ComplexGitSyncClient
__all__ = ["ComplexGitSyncClient"]

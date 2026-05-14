"""Backward-compatible re-export shim — document classes now live in orchestre.py."""
from .orchestre import CgsDocument, ConfigDocument, GocDocument, GtsDocument
__all__ = ["CgsDocument", "ConfigDocument", "GocDocument", "GtsDocument"]

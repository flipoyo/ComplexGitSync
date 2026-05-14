"""Backward-compatible re-export shim — discover_nested_configs now lives in orchestre.py."""
from .orchestre import discover_nested_configs
__all__ = ["discover_nested_configs"]

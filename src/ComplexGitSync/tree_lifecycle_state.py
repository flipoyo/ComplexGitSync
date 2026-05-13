"""Tree-level lifecycle state enumeration."""

from __future__ import annotations

from enum import StrEnum


class TreeLifecycleState(StrEnum):
    """Lifecycle state of the full dependency tree."""

    UNLOADED = "UNLOADED"
    DECLARED = "DECLARED"
    DISCOVERING = "DISCOVERING"
    PENDING = "PENDING"
    READY = "READY"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"

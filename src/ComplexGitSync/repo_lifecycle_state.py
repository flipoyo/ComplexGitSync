"""Per-repository lifecycle state enumeration."""

from __future__ import annotations

from enum import StrEnum


class RepoLifecycleState(StrEnum):
    """Lifecycle state of a single repository entry."""

    DECLARED = "DECLARED"
    PENDING = "PENDING"
    READY = "READY"
    FALLBACK_READY = "FALLBACK_READY"
    MISSING = "MISSING"
    ERROR = "ERROR"

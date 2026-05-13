"""Repository synchronization state enumeration."""

from __future__ import annotations

from enum import StrEnum


class SyncState(StrEnum):
    """Synchronization state of a single repository relative to its remote."""

    ALIGNED = "ALIGNED"
    FALLBACK_APPLIED = "FALLBACK_APPLIED"
    DETACHED_EXACT = "DETACHED_EXACT"
    DIRTY = "DIRTY"
    AHEAD = "AHEAD"
    BEHIND = "BEHIND"
    DIVERGED = "DIVERGED"
    ERROR = "ERROR"
    PENDING = "PENDING"

"""Nested-config discovery state enumeration."""

from __future__ import annotations

from enum import StrEnum


class DiscoveryState(StrEnum):
    """State of nested ``.cgs`` discovery for a repository entry."""

    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    DISABLED = "DISABLED"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"

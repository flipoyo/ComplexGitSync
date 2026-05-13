"""Git reference kind enumeration."""

from __future__ import annotations

from enum import StrEnum


class RefKind(StrEnum):
    """Kind of a Git reference (branch, tag, detached HEAD, etc.)."""

    AUTO = "auto"
    BRANCH = "branch"
    TAG = "tag"
    DETACHED = "detached"
    UNKNOWN = "unknown"

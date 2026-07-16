"""TIME-L0 state anchoring for ComplexGitSync.

The private TIME-L0 anchor is local execution context only.  Public state
identity is always the SHA-256 hash of ``.@`` and never the anchor itself.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class TimeL0State:
    """Public identity derived from a private TIME-L0 anchor."""

    state_hash: str

    @property
    def state_id(self) -> str:
        return f"state({self.state_hash})"


def new_time_l0_anchor() -> TimeL0State:
    """Create a private TIME-L0 anchor for one generated State."""

    instant = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    private_anchor = (
        f"TIME-L0:{instant}:{time.time_ns()}:{os.getpid()}:{secrets.token_hex(16)}"
    )
    return TimeL0State(state_hash=hash_time_l0_anchor(private_anchor))


def hash_time_l0_anchor(anchor: str) -> str:
    """Return ``HASH(.@)`` for a private TIME-L0 anchor."""

    return hashlib.sha256(f".{anchor}".encode("utf-8")).hexdigest()

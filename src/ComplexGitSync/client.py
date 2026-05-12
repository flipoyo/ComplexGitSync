from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ComplexGitSyncClient:
    """Bootstrap client shell for future implementation."""

    session: Any | None = None

    def is_loaded(self) -> bool:
        return self.session is not None

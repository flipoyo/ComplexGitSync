from __future__ import annotations

from dataclasses import dataclass, field

from .orchestre import Orchestre

@dataclass
class ComplexGitSyncClient:
    """Bootstrap client shell for future implementation."""

    orchestre: Orchestre = field(default_factory=Orchestre)

    def is_loaded(self) -> bool:
        return bool(self.orchestre.git_tree.repos)

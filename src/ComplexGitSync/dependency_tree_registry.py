"""In-memory dependency-tree registry holding all repository entries."""

from __future__ import annotations

from dataclasses import dataclass, field

from .repo_lifecycle_state import RepoLifecycleState
from .repo_registry_entry import RepoRegistryEntry
from .tree_lifecycle_state import TreeLifecycleState


@dataclass
class DependencyTreeRegistry:
    """Authoritative in-memory graph of all repository entries.

    The registry is the single source of truth for the current state of the
    dependency tree.  It is populated from a ``.cgs`` or ``.gts`` document and
    updated in place as operations (clone, checkout, …) progress.

    The tree lifecycle state is recomputed on demand via
    :meth:`recompute_tree_state`; it is also updated automatically at the end
    of discovery and builder operations.
    """

    entries: dict[str, RepoRegistryEntry] = field(default_factory=dict)
    lifecycle_state: TreeLifecycleState = TreeLifecycleState.UNLOADED

    def add(self, entry: RepoRegistryEntry) -> RepoRegistryEntry:
        """Register *entry* and return it."""
        self.entries[entry.repo_id] = entry
        return entry

    def get(self, repo_id: str) -> RepoRegistryEntry:
        """Return the entry for *repo_id*."""
        return self.entries[repo_id]

    def values(self) -> list[RepoRegistryEntry]:
        """Return all entries as a list."""
        return list(self.entries.values())

    def __iter__(self):
        return iter(self.entries.values())

    def children_of(self, parent_id: str | None) -> list[RepoRegistryEntry]:
        """Return direct children of *parent_id*, sorted by path then name."""
        return sorted(
            [entry for entry in self.entries.values() if entry.parent_id == parent_id],
            key=lambda entry: (str(entry.relative_path or ""), entry.name),
        )

    def is_complete(self) -> bool:
        """Return ``True`` when every reachable entry has all required paths set."""
        if not self.entries:
            return False
        for entry in self.entries.values():
            if not entry.is_reachable:
                return False
            if entry.absolute_path is None:
                return False
            if entry.parent_id is not None and entry.relative_path is None:
                return False
        return True

    def is_ready(self) -> bool:
        """Return ``True`` when every entry is ``READY`` or ``FALLBACK_READY``."""
        if not self.is_complete():
            return False
        for entry in self.entries.values():
            if entry.repo_lifecycle_state not in {
                RepoLifecycleState.READY,
                RepoLifecycleState.FALLBACK_READY,
            }:
                return False
            if not entry.commit_sha:
                return False
            if entry.resolved_ref_kind is None or not entry.resolved_ref_name:
                return False
        return True

    def recompute_tree_state(self) -> TreeLifecycleState:
        """Recompute and store the tree lifecycle state; return the new value."""
        if not self.entries:
            self.lifecycle_state = TreeLifecycleState.UNLOADED
        elif any(entry.repo_lifecycle_state == RepoLifecycleState.ERROR for entry in self.entries.values()):
            self.lifecycle_state = TreeLifecycleState.ERROR
        elif self.is_ready():
            self.lifecycle_state = TreeLifecycleState.READY
        elif any(
            entry.repo_lifecycle_state == RepoLifecycleState.PENDING for entry in self.entries.values()
        ):
            self.lifecycle_state = TreeLifecycleState.PENDING
        elif all(
            entry.repo_lifecycle_state == RepoLifecycleState.DECLARED for entry in self.entries.values()
        ):
            self.lifecycle_state = TreeLifecycleState.DECLARED
        else:
            self.lifecycle_state = TreeLifecycleState.PARTIAL
        return self.lifecycle_state

    @property
    def registry_complete(self) -> bool:
        """Convenience property; delegates to :meth:`is_complete`."""
        return self.is_complete()

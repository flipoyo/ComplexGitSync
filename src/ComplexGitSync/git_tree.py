"""Core dependency-tree model for ComplexGitSync.

This module is the **GitTree anchor** — the authoritative source for the
in-memory tree structure, lifecycle, registry, and tree-level utilities.

Classes defined here (Tier 1 — Core State):
    TreeLifecycleState      Tree-level lifecycle progression enum
    GitTree                 In-memory dict of GitRepo nodes (MAIN class)
    DependencyTreeRegistry  Authoritative runtime graph of all repo entries
    ProjectTreeState        Frozen snapshot of tree readiness (read-only)

Functions defined here (Tier 2 — Actions / tree utilities):
    make_repo_id                Build a colon-separated repo ID from path
    promote_to_parent           Upgrade a LEAF entry to PARENT
    register_relative_path      Guard against duplicate relative paths
    build_tree_state            Derive a ProjectTreeState from the registry
    find_strongly_connected_components  Tarjan's SCC algorithm on a path-based graph
    fix_circularities           Cycle-breaking engine: SCC detection + hash deduplication
    topological_sort            Return registry entries in safe clone/sync order
    format_project_tree         Render the tree as indented text
    format_registry_json        Render the registry as JSON
    iter_tree                   Iterate the registry parent-first (root → leaves)
    iter_tree_leaf_first        Iterate the registry leaf-first (leaves → root)
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path, PurePath, PurePosixPath
from typing import TYPE_CHECKING, Any, Iterator, TypeVar

from .errors import ConfigValidationError, NestedConfigDiscoveryError
from .git_repo import (
    AccessProtocol,
    DiscoveryState,
    GitProvider,
    GitRepo,
    NodeType,
    RefKind,
    RepoLifecycleState,
    RepoRegistryEntry,
    SyncState,
)

_E = TypeVar("_E", bound=Enum)

ROOT_REPO_ID = "root"

if TYPE_CHECKING:
    from .orchestre import GitRunner


@dataclass(slots=True)
class GitTreeGitCommands:
    """Git command facade bound to :class:`GitTree` operations.

    ``registry`` stores the currently bound
    :class:`DependencyTreeRegistry` used when callers omit an explicit
    registry argument.
    """

    registry: "DependencyTreeRegistry | None" = field(default=None)

    def bind_registry(self, registry: "DependencyTreeRegistry") -> "DependencyTreeRegistry":
        self.registry = registry
        return registry

    def _resolve_registry(
        self,
        registry: "DependencyTreeRegistry | None" = None,
    ) -> "DependencyTreeRegistry":
        if isinstance(registry, DependencyTreeRegistry):
            self.registry = registry
            return registry
        if isinstance(self.registry, DependencyTreeRegistry):
            return self.registry
        raise RuntimeError("No ComplexGitSync registry is bound to GitTree.git.")

    def checkout(
        self,
        git_runner: "GitRunner",
        branch_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
        registry: "DependencyTreeRegistry | None" = None,
    ) -> None:
        from .operations import checkout_tree

        checkout_tree(self._resolve_registry(registry), git_runner, branch_name, ref_kind=ref_kind)

    def branch(
        self,
        git_runner: "GitRunner",
        branch_name: str,
        *,
        registry: "DependencyTreeRegistry | None" = None,
    ) -> None:
        from .operations import branch_tree

        branch_tree(self._resolve_registry(registry), git_runner, branch_name)

    def pull(
        self,
        git_runner: "GitRunner",
        *,
        registry: "DependencyTreeRegistry | None" = None,
    ) -> None:
        from .operations import restart_tree

        restart_tree(self._resolve_registry(registry), git_runner)

    def add(
        self,
        git_runner: "GitRunner",
        *,
        registry: "DependencyTreeRegistry | None" = None,
    ) -> None:
        from .operations import add_tree

        add_tree(self._resolve_registry(registry), git_runner)

    def commit(
        self,
        git_runner: "GitRunner",
        message: str,
        *,
        stage_all: bool = True,
        registry: "DependencyTreeRegistry | None" = None,
    ) -> None:
        from .operations import commit_tree

        commit_tree(self._resolve_registry(registry), git_runner, message, stage_all=stage_all)

    def push(
        self,
        git_runner: "GitRunner",
        *,
        registry: "DependencyTreeRegistry | None" = None,
    ) -> None:
        from .operations import push_tree

        push_tree(self._resolve_registry(registry), git_runner)

    def tag(
        self,
        git_runner: "GitRunner",
        tag_name: str,
        *,
        registry: "DependencyTreeRegistry | None" = None,
    ) -> None:
        from .operations import tag_tree

        tag_tree(self._resolve_registry(registry), git_runner, tag_name)

    def freeze(
        self,
        git_runner: "GitRunner",
        tag_name: str,
        *,
        message: str | None = None,
        stage_all: bool = True,
        registry: "DependencyTreeRegistry | None" = None,
    ) -> None:
        from .operations import freeze_release_tree

        freeze_release_tree(
            self._resolve_registry(registry),
            git_runner,
            tag_name,
            message=message,
            stage_all=stage_all,
        )

    def clone(
        self,
        git_runner: "GitRunner",
        remote_url: str,
        destination: Path | str,
        *,
        branch: str,
    ) -> None:
        git_runner.clone(remote_url, destination, branch=branch)


# ---------------------------------------------------------------------------
# TreeLifecycleState
# ---------------------------------------------------------------------------


class TreeLifecycleState(StrEnum):
    """Lifecycle state of the full dependency tree."""

    UNLOADED = "UNLOADED"
    DECLARED = "DECLARED"
    DISCOVERING = "DISCOVERING"
    PENDING = "PENDING"
    READY = "READY"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# GitTree — core in-memory tree
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GitTree:
    """In-memory dictionary of :class:`~.git_repo.GitRepo` nodes.

    Owns the lifecycle of in-memory repository identity and supports
    correction operations (:meth:`force_repo_sha`, :meth:`force_repo_keys`).
    The runtime mutable state (sync flags, lifecycle states, …) lives in the
    :class:`DependencyTreeRegistry`.
    """

    repos: dict[str, GitRepo] = field(default_factory=dict)
    git: GitTreeGitCommands = field(default_factory=GitTreeGitCommands)

    def add_repo(self, repo: GitRepo) -> None:
        self.repos[repo.project_name] = repo

    def force_repo_sha(self, project_name: str, commit_sha: str) -> None:
        self._get_repo(project_name).commit_sha = commit_sha

    def force_repo_keys(
        self,
        project_name: str,
        *,
        gitprovider: GitProvider | None = None,
        project_owner_name: str | None = None,
        project_name_override: str | None = None,
        group_name: str | None = None,
        gitprovider_url: str | None = None,
        access_protocol: AccessProtocol | None = None,
    ) -> None:
        repo = self._get_repo(project_name)
        if gitprovider is not None:
            repo.gitprovider = gitprovider
        if project_owner_name is not None:
            repo.project_owner_name = project_owner_name
        if project_name_override is not None:
            if project_name_override != project_name:
                if project_name_override in self.repos:
                    raise ValueError(
                        f"Cannot rename repository '{project_name}' to '{project_name_override}': "
                        "target key already exists in GitTree."
                    )
                del self.repos[project_name]
                self.repos[project_name_override] = repo
                repo.project_name = project_name_override
        if group_name is not None:
            repo.group_name = group_name
        if gitprovider_url is not None:
            repo.gitprovider_url = gitprovider_url
        if access_protocol is not None:
            repo.access_protocol = access_protocol

    def _get_repo(self, project_name: str) -> GitRepo:
        try:
            return self.repos[project_name]
        except KeyError as exc:
            raise KeyError(f"Unknown repository '{project_name}' in GitTree.") from exc

    def propagate_tag(self, registry: DependencyTreeRegistry, tag_name: str) -> None:
        """Propagate *tag_name* across *registry* from parent to leaves."""
        for entry in iter_tree(registry):
            entry.target_ref_kind = RefKind.TAG
            entry.target_ref_name = tag_name


# ---------------------------------------------------------------------------
# DependencyTreeRegistry
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# ProjectTreeState
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectTreeState:
    """Read-only snapshot of the tree's lifecycle, readiness, and completeness.

    Returned by :meth:`~ComplexGitSync.orchestre.ComplexGitSyncClient.get_tree_state`
    and stored inside ``.gts`` snapshots.
    """

    lifecycle_state: TreeLifecycleState
    is_ready: bool
    registry_complete: bool


# ---------------------------------------------------------------------------
# Tree utility functions
# ---------------------------------------------------------------------------


def make_repo_id(parent_id: str | None, relative_path: PurePath | str | None, name: str) -> str:
    """Build a colon-separated repo ID from the parent ID and relative path."""
    normalized = _normalize_repo_id_segment(relative_path)
    if normalized == ".":
        return ROOT_REPO_ID if parent_id is None else parent_id
    if normalized == "":
        normalized = _normalize_repo_id_segment(name)
    return f"{parent_id or ROOT_REPO_ID}:{normalized}"


def promote_to_parent(
    registry: DependencyTreeRegistry,
    repo_id: str,
    source_cgs_path: Path | None = None,
) -> RepoRegistryEntry:
    """Upgrade a LEAF registry entry to PARENT node type."""
    entry = registry.get(repo_id)
    entry.node_type = NodeType.PARENT
    if source_cgs_path is not None:
        entry.source_cgs_path = source_cgs_path
    return entry


def register_relative_path(
    seen_relative_paths: set[Path],
    relative_path: Path,
    *,
    error_type: type[Exception],
    context: str,
) -> None:
    """Guard against duplicate relative paths; raise *error_type* on collision."""
    if relative_path in seen_relative_paths:
        raise error_type(f"duplicate relative_path '{relative_path}' under {context}")
    seen_relative_paths.add(relative_path)


def build_tree_state(registry: DependencyTreeRegistry) -> ProjectTreeState:
    """Derive a :class:`ProjectTreeState` snapshot from the live *registry*."""
    return ProjectTreeState(
        lifecycle_state=registry.recompute_tree_state(),
        is_ready=registry.is_ready(),
        registry_complete=registry.registry_complete,
    )


# ---------------------------------------------------------------------------
# Cycle-breaking engine — helpers
# ---------------------------------------------------------------------------


def _build_path_graph(registry: DependencyTreeRegistry) -> dict[Path, set[Path]]:
    """Build a directed dependency graph keyed by absolute path.

    Each edge ``parent_abs_path -> child_abs_path`` represents a declared
    dependency: the parent repository declares the child as a direct dependency
    in its ``.cgs`` file.

    Every node that has an ``absolute_path`` set is present in the returned
    dict (with at least an empty set as its value) so that
    :func:`find_strongly_connected_components` can iterate all nodes.
    """
    graph: dict[Path, set[Path]] = {}
    for entry in registry.values():
        if entry.absolute_path is None:
            continue
        path = entry.absolute_path
        graph.setdefault(path, set())
        if entry.parent_id and entry.parent_id in registry.entries:
            parent_entry = registry.entries[entry.parent_id]
            if parent_entry.absolute_path is not None:
                parent_path = parent_entry.absolute_path
                graph.setdefault(parent_path, set())
                graph[parent_path].add(path)
    return graph


def find_strongly_connected_components(
    graph: dict[Path, set[Path]],
) -> list[list[Path]]:
    """Find strongly connected components (SCCs) using Tarjan's algorithm.

    Parameters
    ----------
    graph:
        Directed dependency graph as returned by :func:`_build_path_graph`:
        ``{node: set_of_successors}``.  Nodes without outgoing edges must
        still be present (with an empty successor set) so that every node
        participates in the traversal.

    Returns
    -------
    list[list[Path]]
        One inner list per SCC.  Trivial SCCs (a single node with no
        self-loop) have length 1.  Non-trivial SCCs (length > 1) represent
        genuine cycles in the dependency graph.
    """
    index_counter = [0]
    stack: list[Path] = []
    lowlink: dict[Path, int] = {}
    index: dict[Path, int] = {}
    on_stack: dict[Path, bool] = {}
    sccs: list[list[Path]] = []

    def _strong_connect(node: Path) -> None:
        index[node] = index_counter[0]
        lowlink[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack[node] = True

        # Iterate successors in sorted order for deterministic output.
        for successor in sorted(graph.get(node, set()), key=str):
            if successor not in index:
                _strong_connect(successor)
                lowlink[node] = min(lowlink[node], lowlink[successor])
            elif on_stack.get(successor, False):
                lowlink[node] = min(lowlink[node], index[successor])

        if lowlink[node] == index[node]:
            scc: list[Path] = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                scc.append(w)
                if w == node:
                    break
            sccs.append(scc)

    for node in sorted(graph, key=str):  # deterministic traversal order
        if node not in index:
            _strong_connect(node)

    return sccs


def _select_scc_anchor(
    scc: list[Path],
    graph: dict[Path, set[Path]],
    path_to_entries: dict[Path, list[RepoRegistryEntry]],
) -> Path:
    """Select the anchor (canonical) path for an SCC using three heuristics.

    The anchor is the node that will be *kept* in the registry when the cycle
    is broken; all other nodes in the SCC that provide back-edges to the
    anchor will be marked ``is_external_reference = True`` and removed.

    Heuristics applied in order (first heuristic that produces a unique winner
    decides):

    1. **Most external incoming edges** — the node with the most edges
       arriving from outside the SCC is the most externally referenced.
    2. **Closest to project root** — fewest ``:`` segments in ``repo_id``.
    3. **Smallest SHA-256 hash** — deterministic tie-breaker on path string.
    """
    scc_set = set(scc)

    # Heuristic 1: count incoming edges from nodes OUTSIDE the SCC.
    external_in: dict[Path, int] = {p: 0 for p in scc}
    for source, targets in graph.items():
        if source not in scc_set:
            for target in targets:
                if target in scc_set:
                    external_in[target] = external_in.get(target, 0) + 1

    max_ext = max(external_in.values(), default=0)
    candidates = [p for p in scc if external_in[p] == max_ext]

    if len(candidates) > 1:
        # Heuristic 2: fewest colon-separated segments in repo_id.
        def _min_depth(path: Path) -> int:
            entries = path_to_entries.get(path, [])
            return min((e.repo_id.count(":") for e in entries), default=9999)

        min_d = min(_min_depth(p) for p in candidates)
        candidates = [p for p in candidates if _min_depth(p) == min_d]

    if len(candidates) > 1:
        # Heuristic 3: deterministic SHA-256 hash of the path string.
        candidates = [
            min(candidates, key=lambda p: hashlib.sha256(str(p).encode()).hexdigest())
        ]

    return candidates[0]


# ---------------------------------------------------------------------------
# Cycle-breaking engine — main entry point
# ---------------------------------------------------------------------------


def fix_circularities(registry: DependencyTreeRegistry) -> tuple[str, ...]:
    """Cycle-breaking engine: resolve circularities and produce a valid DAG.

    This function operates in two phases:

    **Phase 1 — SCC-based cycle detection and breaking**

    A directed dependency graph is built from the registry (edges go from
    parent to child, keyed by resolved absolute path).
    :func:`find_strongly_connected_components` (Tarjan's algorithm) identifies
    groups of paths that form a dependency cycle — for example when repository
    *A* declares *B* as a dependency and *B* declares *A* as a dependency.

    For each non-trivial SCC (two or more nodes), an *Anchor* path is selected
    using three heuristics applied in order:

    1. **Most external incoming edges** — most externally referenced node.
    2. **Closest to project root** — fewest ``:`` segments in ``repo_id``.
    3. **Smallest SHA-256 hash** — deterministic tie-breaker.

    For every registry entry whose ``absolute_path`` matches the anchor *and*
    whose parent's ``absolute_path`` belongs to a non-anchor SCC member, the
    entry is a *back-edge*: it is flagged ``is_external_reference = True`` and
    scheduled for removal.  The Anchor's canonical entry (the one sitting
    highest in the tree) is preserved; the back-edge duplicate is discarded so
    the graph becomes a DAG.

    **Phase 2 — Hash-compatibility deduplication (original behaviour)**

    After cycle breaking, the remaining entries are grouped by resolved
    absolute path.  Residual duplicates (e.g., entries loaded from an older
    ``.gts`` snapshot that were not covered by the SCC phase) are removed when
    their synchronisation state is *compatible* with the canonical entry: same
    lifecycle/sync state, no conflicting commit SHA, no conflicting worktree
    marker.

    Returns a tuple of strings, one per removed entry, each in the form::

        "fixed_circularity:<removed_id>\u2192<canonical_id>"

    The registry tree state is recomputed only when at least one entry is
    removed.

    See Also
    --------
    find_strongly_connected_components : Tarjan's SCC algorithm used in Phase 1.
    topological_sort : Returns entries in safe clone/sync order after this call.
    """
    # -----------------------------------------------------------------------
    # Build auxiliary mappings (shared by both phases).
    # -----------------------------------------------------------------------
    path_to_entries: dict[Path, list[RepoRegistryEntry]] = {}
    for entry in registry.values():
        if entry.absolute_path is None:
            continue
        path_to_entries.setdefault(entry.absolute_path, []).append(entry)

    # -----------------------------------------------------------------------
    # Phase 1 — SCC-based cycle breaking
    # -----------------------------------------------------------------------
    graph = _build_path_graph(registry)
    sccs = find_strongly_connected_components(graph)

    changes: list[str] = []
    ids_to_remove: set[str] = set()

    for scc in sccs:
        if len(scc) <= 1:
            continue

        anchor_path = _select_scc_anchor(scc, graph, path_to_entries)

        # Canonical entry for the anchor: the one closest to the project root.
        anchor_entries = sorted(
            path_to_entries.get(anchor_path, []),
            key=lambda e: e.repo_id.count(":"),
        )
        if not anchor_entries:
            continue
        canonical = anchor_entries[0]

        scc_non_anchor: set[Path] = set(scc) - {anchor_path}

        # Find back-edge entries: entries whose absolute_path equals the
        # anchor_path but whose parent's absolute_path is a non-anchor SCC
        # member.  These entries represent the cycle-creating back-reference.
        for entry in list(registry.values()):
            if entry.absolute_path != anchor_path:
                continue
            if entry.repo_id == canonical.repo_id:
                continue
            if entry.parent_id is None or entry.parent_id not in registry.entries:
                continue
            parent_entry = registry.entries[entry.parent_id]
            if parent_entry.absolute_path not in scc_non_anchor:
                continue
            # Back-edge: mark as external reference and schedule for removal.
            entry.is_external_reference = True
            ids_to_remove.add(entry.repo_id)
            changes.append(f"fixed_circularity:{entry.repo_id}\u2192{canonical.repo_id}")

    # -----------------------------------------------------------------------
    # Phase 2 — Hash-compatibility deduplication (original behaviour)
    # -----------------------------------------------------------------------
    for _abs_path, entries in path_to_entries.items():
        if len(entries) <= 1:
            continue
        # Only consider entries not already scheduled for removal.
        remaining = [e for e in entries if e.repo_id not in ids_to_remove]
        if len(remaining) <= 1:
            continue
        # Canonical entry: fewest colon-separated segments in repo_id.
        remaining.sort(key=lambda e: e.repo_id.count(":"))
        canonical = remaining[0]
        for duplicate in remaining[1:]:
            if not _is_compatible_duplicate(canonical, duplicate):
                continue
            ids_to_remove.add(duplicate.repo_id)
            changes.append(f"fixed_circularity:{duplicate.repo_id}\u2192{canonical.repo_id}")

    if ids_to_remove:
        for repo_id in ids_to_remove:
            if repo_id in registry.entries:
                del registry.entries[repo_id]
        registry.recompute_tree_state()

    return tuple(changes)


def _is_compatible_duplicate(canonical: RepoRegistryEntry, duplicate: RepoRegistryEntry) -> bool:
    if canonical.repo_lifecycle_state != duplicate.repo_lifecycle_state:
        return False
    if canonical.sync_state != duplicate.sync_state:
        return False
    if canonical.commit_sha and duplicate.commit_sha and canonical.commit_sha != duplicate.commit_sha:
        return False
    if canonical.worktree_state and duplicate.worktree_state and canonical.worktree_state != duplicate.worktree_state:
        return False
    return True


def topological_sort(registry: DependencyTreeRegistry) -> list[RepoRegistryEntry]:
    """Return registry entries in topological order (parents before children).

    Uses Kahn's algorithm (iterative BFS) to produce a valid ordering of the
    dependency tree.  Entries with no parent (i.e., the project root) come
    first; their children follow in sorted ``repo_id`` order.

    This ordering is safe for sequential clone/pull operations: a parent
    repository is always processed before any of its children.

    Entries flagged as ``is_external_reference = True`` are included in the
    output so that callers have a complete picture of the graph, but they
    should be skipped by any cloning or synchronisation logic.

    Parameters
    ----------
    registry:
        The registry to sort.

    Returns
    -------
    list[RepoRegistryEntry]
        Entries in parent-first topological order.
    """
    in_degree: dict[str, int] = {rid: 0 for rid in registry.entries}
    children_map: dict[str, list[str]] = {rid: [] for rid in registry.entries}

    for entry in registry.values():
        if entry.parent_id is not None and entry.parent_id in registry.entries:
            in_degree[entry.repo_id] += 1
            children_map[entry.parent_id].append(entry.repo_id)

    queue: deque[str] = deque(
        sorted(rid for rid, deg in in_degree.items() if deg == 0)
    )
    result: list[RepoRegistryEntry] = []

    while queue:
        node = queue.popleft()
        result.append(registry.entries[node])
        for child in sorted(children_map.get(node, [])):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def format_project_tree(
    registry: DependencyTreeRegistry,
    *,
    include_current_ref: bool = True,
    include_target_ref: bool = True,
    include_node_type: bool = True,
    verbose: bool = True,
) -> str:
    """Render *registry* as an indented text tree."""
    lines: list[str] = []
    for entry in iter_tree(registry):
        bits = [entry.name]
        if include_node_type:
            bits.append(f"[{entry.node_type.value}]")
        bits.append(f"path={entry.absolute_path}")
        if include_current_ref and entry.current_ref_name and entry.current_ref_kind:
            bits.append(f"current={entry.current_ref_kind.value}:{entry.current_ref_name}")
        if include_target_ref and entry.target_ref_name and entry.target_ref_kind:
            bits.append(f"target={entry.target_ref_kind.value}:{entry.target_ref_name}")
        bits.append(f"sync={entry.sync_state.value}")
        bits.append(f"state={entry.repo_lifecycle_state.value}")
        if verbose and entry.commit_sha:
            bits.append(f"sha={entry.commit_sha}")
        lines.append(f"{'  ' * _depth(entry.repo_id)}- " + " ".join(bits))
    return "\n".join(lines)


def format_repo_tree_outline(registry: DependencyTreeRegistry) -> str:
    """Render *registry* as a minimal repo-only tree outline."""
    children_by_parent: dict[str | None, list[RepoRegistryEntry]] = {}
    for entry in registry.values():
        children_by_parent.setdefault(entry.parent_id, []).append(entry)
    for children in children_by_parent.values():
        children.sort(key=lambda child: child.name)

    labels = {
        NodeType.ROOT: "project",
        NodeType.PARENT: "parent",
        NodeType.LEAF: "leaf",
    }
    lines: list[str] = []

    def walk(entry: RepoRegistryEntry, *, prefix: str, is_last: bool, is_root: bool = False) -> None:
        label = labels.get(entry.node_type, entry.node_type.value)
        if is_root:
            lines.append(f"{entry.name} ({label})")
        else:
            branch = "└── " if is_last else "├── "
            lines.append(f"{prefix}{branch}{entry.name} ({label})")

        children = children_by_parent.get(entry.repo_id, [])
        if is_root:
            child_prefix = ""
        else:
            child_prefix = prefix + ("    " if is_last else "│   ")
        for index, child in enumerate(children):
            walk(child, prefix=child_prefix, is_last=index == len(children) - 1)

    root_entry = registry.entries.get(ROOT_REPO_ID)
    if root_entry is None:
        return ""
    walk(root_entry, prefix="", is_last=True, is_root=True)
    return "\n".join(lines)


def format_registry_json(registry: DependencyTreeRegistry) -> str:
    """Render *registry* as a JSON array."""
    data: list[dict[str, object]] = []
    for entry in sorted(registry.values(), key=lambda item: item.repo_id):
        data.append(
            {
                "repo_id": entry.repo_id,
                "name": entry.name,
                "node_type": entry.node_type.value,
                "parent_id": entry.parent_id,
                "absolute_path": str(entry.absolute_path),
                "relative_path": str(entry.relative_path) if entry.relative_path else None,
                "current_ref_kind": entry.current_ref_kind.value if entry.current_ref_kind else None,
                "current_ref_name": entry.current_ref_name,
                "target_ref_kind": entry.target_ref_kind.value if entry.target_ref_kind else None,
                "target_ref_name": entry.target_ref_name,
                "resolved_ref_kind": entry.resolved_ref_kind.value if entry.resolved_ref_kind else None,
                "resolved_ref_name": entry.resolved_ref_name,
                "commit_sha": entry.commit_sha,
                "repo_lifecycle_state": entry.repo_lifecycle_state.value,
                "sync_state": entry.sync_state.value,
                "discovery_state": entry.discovery_state.value,
                "fallback_branch": entry.fallback_branch,
                "fallback_applied": entry.fallback_applied,
                "fallback_reason": entry.fallback_reason,
                "worktree_state": entry.worktree_state,
                "is_reachable": entry.is_reachable,
                "project_owner_name": entry.project_owner_name,
                "project_name": entry.project_name,
            }
        )
    return json.dumps(data, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Tree traversal utilities (public)
# ---------------------------------------------------------------------------


def iter_tree(registry: DependencyTreeRegistry) -> Iterator[RepoRegistryEntry]:
    """Yield every entry in *registry* in parent-first (root → leaves) order."""
    yield from _iter_tree(registry)


def iter_tree_leaf_first(registry: DependencyTreeRegistry) -> Iterator[RepoRegistryEntry]:
    """Yield every entry in *registry* in leaf-first (leaves → root) order."""
    yield from reversed(list(_iter_tree(registry)))


# ---------------------------------------------------------------------------
# Private helpers (also used by orchestre.py builders)
# ---------------------------------------------------------------------------


def _iter_tree(registry: DependencyTreeRegistry):
    stack = list(reversed(registry.children_of(None)))
    while stack:
        entry = stack.pop()
        yield entry
        children = registry.children_of(entry.repo_id)
        stack.extend(reversed(children))


def _depth(repo_id: str) -> int:
    return 0 if repo_id == "root" else repo_id.count(":")


def _validate_repo_shape(repo: Any) -> None:
    if not isinstance(repo, dict):
        raise ConfigValidationError("Invalid .cgs document:\n  • each [[repos]] entry must be a table")


def _is_root_repo_spec(
    repo: dict[str, Any],
    project_name: str | None,
    root_identity_assigned: bool,
) -> bool:
    relative_path = repo.get("relative_path")
    if isinstance(relative_path, str) and relative_path.strip() in {".", ""}:
        return True
    return not root_identity_assigned and project_name is not None and repo.get("project_name") == project_name


def _normalise_relative_path(repo: dict[str, Any]) -> Path:
    raw = repo.get("relative_path") or repo.get("project_name")
    return Path(str(raw))


def _normalize_repo_id_segment(relative_path: PurePath | str | None) -> str:
    if relative_path is None:
        return ""
    if isinstance(relative_path, PurePath):
        raw = relative_path.as_posix()
    else:
        raw = str(relative_path).replace("\\", "/")
    if raw == "":
        return ""
    return PurePosixPath(raw).as_posix()


def _apply_repo_identity(
    entry: RepoRegistryEntry,
    repo: dict[str, Any],
    default_branch: str | None,
) -> None:
    entry.gitprovider = _parse_enum(GitProvider, repo.get("gitprovider"), GitProvider.GITHUB)
    entry.project_owner_name = _as_optional_str(repo.get("project_owner_name"))
    entry.project_name = _as_optional_str(repo.get("project_name"))
    entry.group_name = _as_optional_str(repo.get("group_name"))
    entry.gitprovider_url = _as_optional_str(repo.get("gitprovider_url"))
    entry.access_protocol = _parse_enum(AccessProtocol, repo.get("access_protocol"), AccessProtocol.SSH)
    entry.default_branch = str(repo.get("default_branch") or default_branch)
    entry.fallback_branch = _as_optional_str(repo.get("fallback_branch"))
    entry.nested_config = _as_optional_str(repo.get("nested_config"))
    entry.discovery_state = _initial_discovery_state(entry.nested_config)


def _initial_discovery_state(nested_config: Any) -> DiscoveryState:
    if nested_config == "disabled":
        return DiscoveryState.DISABLED
    if nested_config:
        return DiscoveryState.PENDING
    return DiscoveryState.RESOLVED


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _parse_optional_enum(enum_type: type[_E], value: Any) -> _E | None:
    if value is None:
        return None
    return enum_type(str(value))


def _parse_gts_node_type(raw_value: str) -> NodeType:
    normalized = raw_value.lower()
    if normalized in {"root", "rootrepo"}:
        return NodeType.ROOT
    if normalized in {"parent", "parentrepo"}:
        return NodeType.PARENT
    return NodeType.LEAF


def _parse_enum(enum_type: type[_E], value: Any, default: _E) -> _E:
    if value is None:
        return default
    return enum_type(str(value))

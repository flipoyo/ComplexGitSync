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
    format_project_tree         Render the tree as indented text
    format_registry_json        Render the registry as JSON
    iter_tree                   Iterate the registry parent-first (root → leaves)
    iter_tree_leaf_first        Iterate the registry leaf-first (leaves → root)
"""

from __future__ import annotations

import json
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
    """Git command facade bound to :class:`GitTree` operations."""

    def checkout(
        self,
        registry: "DependencyTreeRegistry",
        git_runner: "GitRunner",
        branch_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
    ) -> None:
        from .operations import checkout_tree

        checkout_tree(registry, git_runner, branch_name, ref_kind=ref_kind)


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

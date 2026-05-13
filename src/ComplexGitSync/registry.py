from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePath, PurePosixPath
from typing import Any

from .access_protocol import AccessProtocol
from .documents import CgsDocument
from .errors import ConfigValidationError
from .git_provider import GitProvider

ROOT_REPO_ID = "root"


class TreeLifecycleState(StrEnum):
    UNLOADED = "UNLOADED"
    DECLARED = "DECLARED"
    DISCOVERING = "DISCOVERING"
    PENDING = "PENDING"
    READY = "READY"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"


class RepoLifecycleState(StrEnum):
    DECLARED = "DECLARED"
    PENDING = "PENDING"
    READY = "READY"
    FALLBACK_READY = "FALLBACK_READY"
    MISSING = "MISSING"
    ERROR = "ERROR"


class DiscoveryState(StrEnum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    DISABLED = "DISABLED"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"


class SyncState(StrEnum):
    ALIGNED = "ALIGNED"
    FALLBACK_APPLIED = "FALLBACK_APPLIED"
    DETACHED_EXACT = "DETACHED_EXACT"
    DIRTY = "DIRTY"
    AHEAD = "AHEAD"
    BEHIND = "BEHIND"
    DIVERGED = "DIVERGED"
    ERROR = "ERROR"
    PENDING = "PENDING"


class RefKind(StrEnum):
    AUTO = "auto"
    BRANCH = "branch"
    TAG = "tag"
    DETACHED = "detached"
    UNKNOWN = "unknown"


class NodeType(StrEnum):
    ROOT = "root"
    PARENT = "parent"
    LEAF = "leaf"


@dataclass(frozen=True)
class RepoNode:
    repo_id: str
    name: str
    absolute_path: Path
    parent_id: str | None = None
    relative_path: Path | None = None
    source_cgs_path: Path | None = None
    node_type: NodeType = NodeType.LEAF


@dataclass
class RepoRegistryEntry:
    repo_id: str
    name: str
    node_type: NodeType
    parent_id: str | None
    absolute_path: Path
    relative_path: Path | None = None
    source_cgs_path: Path | None = None
    current_ref_kind: RefKind | None = None
    current_ref_name: str | None = None
    target_ref_kind: RefKind | None = None
    target_ref_name: str | None = None
    resolved_ref_kind: RefKind | None = None
    resolved_ref_name: str | None = None
    commit_sha: str | None = None
    repo_lifecycle_state: RepoLifecycleState = RepoLifecycleState.DECLARED
    sync_state: SyncState = SyncState.PENDING
    discovery_state: DiscoveryState = DiscoveryState.PENDING
    fallback_branch: str | None = None
    fallback_applied: bool = False
    fallback_reason: str | None = None
    worktree_state: str | None = None
    is_reachable: bool = True
    gitprovider: GitProvider = GitProvider.GITHUB
    project_owner_name: str | None = None
    project_name: str | None = None
    group_name: str | None = None
    gitprovider_url: str | None = None
    access_protocol: AccessProtocol = AccessProtocol.SSH
    default_branch: str | None = None
    nested_config: str | None = None
    remote_name: str | None = None


@dataclass
class DependencyTreeRegistry:
    entries: dict[str, RepoRegistryEntry] = field(default_factory=dict)
    lifecycle_state: TreeLifecycleState = TreeLifecycleState.UNLOADED

    def add(self, entry: RepoRegistryEntry) -> RepoRegistryEntry:
        self.entries[entry.repo_id] = entry
        return entry

    def get(self, repo_id: str) -> RepoRegistryEntry:
        return self.entries[repo_id]

    def values(self) -> list[RepoRegistryEntry]:
        return list(self.entries.values())

    def __iter__(self):
        return iter(self.entries.values())

    def children_of(self, parent_id: str | None) -> list[RepoRegistryEntry]:
        return sorted(
            [entry for entry in self.entries.values() if entry.parent_id == parent_id],
            key=lambda entry: (str(entry.relative_path or ""), entry.name),
        )

    def is_complete(self) -> bool:
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
        return self.is_complete()


@dataclass(frozen=True)
class ProjectTreeState:
    lifecycle_state: TreeLifecycleState
    is_ready: bool
    registry_complete: bool


def make_repo_id(parent_id: str | None, relative_path: PurePath | str | None, name: str) -> str:
    normalized = _normalize_repo_id_segment(relative_path)
    if normalized == ".":
        return ROOT_REPO_ID if parent_id is None else parent_id
    if normalized == "":
        normalized = _normalize_repo_id_segment(name)
    return f"{parent_id or ROOT_REPO_ID}:{normalized}"


def build_registry_from_cgs_document(
    document: CgsDocument,
    config_path: Path | str,
) -> DependencyTreeRegistry:
    source_path = Path(config_path).resolve()
    root_path = source_path.parent.resolve()
    root_entry = RepoRegistryEntry(
        repo_id=ROOT_REPO_ID,
        name=document.project_name or source_path.stem,
        node_type=NodeType.ROOT,
        parent_id=None,
        absolute_path=root_path,
        relative_path=Path("."),
        source_cgs_path=source_path,
        target_ref_kind=RefKind.BRANCH,
        target_ref_name=document.default_branch,
        default_branch=document.default_branch,
        discovery_state=DiscoveryState.RESOLVED,
        remote_name=document.read("project.default_remote_name", "origin"),
    )

    registry = DependencyTreeRegistry()
    registry.add(root_entry)

    seen_relative_paths: set[Path] = set()
    root_identity_assigned = False
    for repo in document.repos:
        _validate_repo_shape(repo)
        if _is_root_repo_spec(repo, document.project_name, root_identity_assigned):
            _apply_repo_identity(root_entry, repo, document.default_branch)
            root_identity_assigned = True
            continue

        relative_path = _normalise_relative_path(repo)
        register_relative_path(
            seen_relative_paths,
            relative_path,
            error_type=ConfigValidationError,
            context="root",
        )

        entry = RepoRegistryEntry(
            repo_id=make_repo_id(ROOT_REPO_ID, relative_path, str(repo["project_name"])),
            name=str(repo["project_name"]),
            node_type=NodeType.LEAF,
            parent_id=ROOT_REPO_ID,
            absolute_path=(root_path / relative_path).resolve(),
            relative_path=relative_path,
            source_cgs_path=source_path,
            target_ref_kind=RefKind.BRANCH,
            target_ref_name=str(repo.get("default_branch") or document.default_branch),
            fallback_branch=_as_optional_str(repo.get("fallback_branch")),
            discovery_state=_initial_discovery_state(repo.get("nested_config")),
            gitprovider=_parse_enum(GitProvider, repo.get("gitprovider"), GitProvider.GITHUB),
            project_owner_name=_as_optional_str(repo.get("project_owner_name")),
            project_name=_as_optional_str(repo.get("project_name")),
            group_name=_as_optional_str(repo.get("group_name")),
            gitprovider_url=_as_optional_str(repo.get("gitprovider_url")),
            access_protocol=_parse_enum(
                AccessProtocol,
                repo.get("access_protocol"),
                AccessProtocol.SSH,
            ),
            default_branch=str(repo.get("default_branch") or document.default_branch),
            nested_config=_as_optional_str(repo.get("nested_config")),
            remote_name=str(repo.get("remote_name") or document.read("project.default_remote_name", "origin")),
        )
        registry.add(entry)

    registry.recompute_tree_state()
    return registry


def promote_to_parent(
    registry: DependencyTreeRegistry,
    repo_id: str,
    source_cgs_path: Path | None = None,
) -> RepoRegistryEntry:
    entry = registry.get(repo_id)
    entry.node_type = NodeType.PARENT
    if source_cgs_path is not None:
        entry.source_cgs_path = source_cgs_path
    return entry


def build_tree_state(registry: DependencyTreeRegistry) -> ProjectTreeState:
    return ProjectTreeState(
        lifecycle_state=registry.recompute_tree_state(),
        is_ready=registry.is_ready(),
        registry_complete=registry.registry_complete,
    )


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


def register_relative_path(
    seen_relative_paths: set[Path],
    relative_path: Path,
    *,
    error_type: type[Exception],
    context: str,
) -> None:
    if relative_path in seen_relative_paths:
        raise error_type(f"duplicate relative_path '{relative_path}' under {context}")
    seen_relative_paths.add(relative_path)


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


def _parse_enum(enum_type: type[StrEnum], value: Any, default: StrEnum) -> Any:
    if value is None:
        return default
    return enum_type(str(value))

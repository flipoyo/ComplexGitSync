from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path


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


class WorktreeState(StrEnum):
    CLEAN = "CLEAN"
    DIRTY = "DIRTY"


class InteractionMode(StrEnum):
    INTERACTIVE = "interactive"
    DIRECT = "direct"


class OutputProfile(StrEnum):
    VERBOSE = "verbose"
    WHISPER_SYNC = "whisper_sync"


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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RepoNode:
    repo_id: str
    name: str
    absolute_path: Path
    parent_id: str | None = None
    relative_path: Path | None = None
    source_cgs_path: Path | None = None
    node_type: NodeType = NodeType.LEAF


@dataclass(frozen=True)
class ParentRepo(RepoNode):
    node_type: NodeType = NodeType.PARENT


@dataclass(frozen=True)
class LeafRepo(RepoNode):
    node_type: NodeType = NodeType.LEAF


@dataclass(frozen=True)
class RequestedRef:
    name: str
    kind: RefKind = RefKind.AUTO


@dataclass(frozen=True)
class ResolvedRef:
    name: str
    kind: RefKind
    commit_sha: str | None = None


@dataclass(frozen=True)
class FallbackDecision:
    repo_name: str
    requested_ref: str
    fallback_branch: str
    reason: str
    accepted: bool
    automatic: bool = False


@dataclass(frozen=True)
class RuntimeOptions:
    interaction: InteractionMode = InteractionMode.INTERACTIVE
    profile: OutputProfile = OutputProfile.VERBOSE
    prompt_scope: str = "per-event"
    warn_on_fallback: bool = True
    allow_mixed_resolution: bool = True
    nested_config_discovery: bool = True
    log_level: str = "info"


@dataclass(frozen=True)
class RepoRefPolicy:
    default_branch: str | None = None
    fallback_branch: str | None = None


@dataclass(frozen=True)
class RepoSpec:
    name: str
    path: str
    ssh_url: str
    https_url: str
    default_branch: str | None = None
    fallback_branch: str | None = None
    nested_config: str = "auto"
    transport: str | None = None
    enabled: bool = True
    remote_name: str | None = None
    ref_policy: RepoRefPolicy = field(default_factory=RepoRefPolicy)
    runtime: RuntimeOptions = field(default_factory=RuntimeOptions)
    source_cgs_path: Path | None = None

    @property
    def relative_path(self) -> Path:
        return Path(self.path)


@dataclass(frozen=True)
class ProjectArchitecture:
    name: str
    default_branch: str
    config_path: Path
    repos: tuple[RepoSpec, ...]
    runtime: RuntimeOptions = field(default_factory=RuntimeOptions)
    transport: str | None = None
    default_remote_name: str = "origin"
    log_dir: Path | None = None

    @property
    def root_path(self) -> Path:
        return self.config_path.parent.resolve()


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
    worktree_state: WorktreeState | None = None
    is_reachable: bool = True
    ssh_url: str | None = None
    https_url: str | None = None
    default_branch: str | None = None
    nested_config: str | None = None
    remote_name: str | None = None

    def copy(self) -> "RepoRegistryEntry":
        return RepoRegistryEntry(**self.__dict__)


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
            if not entry.absolute_path:
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
            if not entry.resolved_ref_kind or not entry.resolved_ref_name:
                return False
        return True

    def recompute_tree_state(self) -> TreeLifecycleState:
        if not self.entries:
            self.lifecycle_state = TreeLifecycleState.UNLOADED
        elif any(entry.repo_lifecycle_state == RepoLifecycleState.ERROR for entry in self.entries.values()):
            self.lifecycle_state = TreeLifecycleState.ERROR
        elif self.is_ready():
            self.lifecycle_state = TreeLifecycleState.READY
        elif any(entry.repo_lifecycle_state == RepoLifecycleState.PENDING for entry in self.entries.values()):
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


@dataclass(frozen=True)
class GitTreeStateSnapshot:
    project_name: str
    root_absolute_path: Path
    registry: DependencyTreeRegistry
    runtime: RuntimeOptions = field(default_factory=RuntimeOptions)
    generated_at: datetime = field(default_factory=utc_now)
    command_origin: str = "unknown"
    source_cgs_path: Path | None = None
    release_name: str | None = None
    branch_origin: str | None = None
    tag_origin: str | None = None


@dataclass
class LoadedSession:
    registry: DependencyTreeRegistry = field(default_factory=DependencyTreeRegistry)
    architecture: ProjectArchitecture | None = None
    snapshot: GitTreeStateSnapshot | None = None
    source_path: Path | None = None
    tree_state: TreeLifecycleState = TreeLifecycleState.UNLOADED

    @property
    def is_ready(self) -> bool:
        return self.registry.is_ready()

    def refresh_tree_state(self) -> TreeLifecycleState:
        self.tree_state = self.registry.recompute_tree_state()
        return self.tree_state


@dataclass(frozen=True)
class RepoOutcome:
    repo_id: str
    name: str
    status: str
    detail: str | None = None


@dataclass(frozen=True)
class OperationResult:
    pre_tree_state: TreeLifecycleState
    post_tree_state: TreeLifecycleState
    per_repo_outcomes: tuple[RepoOutcome, ...] = ()
    applied_fallbacks: tuple[FallbackDecision, ...] = ()
    discovery_changes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    log_path: Path | None = None
    output_gts_path: Path | None = None

"""Mutable registry entry for a single repository in the dependency tree."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .access_protocol import AccessProtocol
from .discovery_state import DiscoveryState
from .git_provider import GitProvider
from .node_type import NodeType
from .ref_kind import RefKind
from .repo_lifecycle_state import RepoLifecycleState
from .sync_state import SyncState


@dataclass
class RepoRegistryEntry:
    """Mutable runtime record for a single repository within the dependency tree.

    Each field tracks either the static identity declared in the ``.cgs`` file
    or the dynamic state observed during synchronisation operations.  The
    registry is the authoritative in-memory model; ``.gts`` snapshots are
    derived from it.
    """

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

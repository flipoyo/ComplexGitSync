from __future__ import annotations

from pathlib import Path

from .models import (
    DependencyTreeRegistry,
    DiscoveryState,
    NodeType,
    ProjectArchitecture,
    ProjectTreeState,
    RefKind,
    RepoLifecycleState,
    RepoRegistryEntry,
    SyncState,
)


ROOT_REPO_ID = "root"


def make_repo_id(parent_id: str | None, relative_path: Path | str | None, name: str) -> str:
    if parent_id is None:
        return ROOT_REPO_ID
    path_part = str(relative_path) if relative_path else name
    return f"{parent_id}:{path_part}"


def build_registry_from_architecture(architecture: ProjectArchitecture) -> DependencyTreeRegistry:
    registry = DependencyTreeRegistry()
    registry.add(
        RepoRegistryEntry(
            repo_id=ROOT_REPO_ID,
            name=architecture.name,
            node_type=NodeType.ROOT,
            parent_id=None,
            absolute_path=architecture.root_path,
            source_cgs_path=architecture.config_path,
            target_ref_kind=RefKind.BRANCH,
            target_ref_name=architecture.default_branch,
            default_branch=architecture.default_branch,
            discovery_state=DiscoveryState.RESOLVED,
        )
    )
    for spec in architecture.repos:
        relative_path = spec.relative_path
        registry.add(
            RepoRegistryEntry(
                repo_id=make_repo_id(ROOT_REPO_ID, relative_path, spec.name),
                name=spec.name,
                node_type=NodeType.LEAF,
                parent_id=ROOT_REPO_ID,
                absolute_path=(architecture.root_path / relative_path).resolve(),
                relative_path=relative_path,
                source_cgs_path=spec.source_cgs_path or architecture.config_path,
                target_ref_kind=RefKind.BRANCH,
                target_ref_name=spec.default_branch or architecture.default_branch,
                fallback_branch=spec.fallback_branch,
                ssh_url=spec.ssh_url,
                https_url=spec.https_url,
                default_branch=spec.default_branch or architecture.default_branch,
                nested_config=spec.nested_config,
                remote_name=spec.remote_name or architecture.default_remote_name,
            )
        )
    registry.recompute_tree_state()
    return registry


def promote_to_parent(
    registry: DependencyTreeRegistry, repo_id: str, source_cgs_path: Path | None = None
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


def reachable_entries(registry: DependencyTreeRegistry) -> list[RepoRegistryEntry]:
    return [entry for entry in registry.values() if entry.is_reachable]


def leaf_first_entries(registry: DependencyTreeRegistry) -> list[RepoRegistryEntry]:
    entries = reachable_entries(registry)
    return sorted(entries, key=lambda entry: (entry.repo_id.count(":"), str(entry.absolute_path)), reverse=True)


def parent_first_entries(registry: DependencyTreeRegistry) -> list[RepoRegistryEntry]:
    entries = reachable_entries(registry)
    return sorted(entries, key=lambda entry: (entry.repo_id.count(":"), str(entry.absolute_path)))


def apply_missing_state(entry: RepoRegistryEntry) -> None:
    entry.is_reachable = False
    entry.repo_lifecycle_state = RepoLifecycleState.MISSING
    entry.sync_state = SyncState.PENDING
    entry.discovery_state = DiscoveryState.MISSING
    entry.commit_sha = None
    entry.current_ref_kind = None
    entry.current_ref_name = None
    entry.resolved_ref_kind = None
    entry.resolved_ref_name = None

from __future__ import annotations

from pathlib import Path

from .documents import CgsDocument
from .errors import NestedConfigDiscoveryError
from .registry import (
    DependencyTreeRegistry,
    DiscoveryState,
    NodeType,
    RepoRegistryEntry,
    ROOT_REPO_ID,
    _apply_repo_identity,
    _initial_discovery_state,
    _normalise_relative_path,
    _parse_enum,
    _validate_repo_shape,
    make_repo_id,
    promote_to_parent,
)
from .access_protocol import AccessProtocol
from .git_provider import GitProvider


def discover_nested_configs(registry: DependencyTreeRegistry) -> tuple[str, ...]:
    changes: list[str] = []
    pending_entries = [
        entry
        for entry in registry.values()
        if entry.repo_id != ROOT_REPO_ID and entry.nested_config not in {None, "disabled"}
    ]

    for entry in pending_entries:
        if not entry.absolute_path.exists():
            entry.discovery_state = DiscoveryState.MISSING
            continue

        nested_path = _resolve_nested_config_path(entry.absolute_path, entry.nested_config or "auto")
        if nested_path is None:
            entry.discovery_state = DiscoveryState.MISSING
            continue

        nested_document = CgsDocument.from_toml(nested_path)
        promote_to_parent(registry, entry.repo_id, nested_path)
        entry.discovery_state = DiscoveryState.RESOLVED

        root_identity_assigned = False
        existing_child_paths = {
            child.relative_path for child in registry.children_of(entry.repo_id) if child.relative_path is not None
        }
        for repo in nested_document.repos:
            _validate_repo_shape(repo)
            if not root_identity_assigned and repo.get("project_name") == nested_document.project_name:
                _apply_repo_identity(entry, repo, nested_document.default_branch)
                root_identity_assigned = True
                continue

            relative_path = _normalise_relative_path(repo)
            if relative_path in existing_child_paths:
                raise NestedConfigDiscoveryError(
                    f"Duplicate nested relative_path '{relative_path}' under {entry.absolute_path}"
                )
            existing_child_paths.add(relative_path)

            child_id = make_repo_id(entry.repo_id, relative_path, str(repo["project_name"]))
            if child_id in registry.entries:
                continue

            registry.add(
                RepoRegistryEntry(
                    repo_id=child_id,
                    name=str(repo["project_name"]),
                    node_type=NodeType.LEAF,
                    parent_id=entry.repo_id,
                    absolute_path=(entry.absolute_path / relative_path).resolve(),
                    relative_path=relative_path,
                    source_cgs_path=nested_path,
                    target_ref_kind=entry.target_ref_kind,
                    target_ref_name=str(repo.get("default_branch") or nested_document.default_branch),
                    fallback_branch=str(repo.get("fallback_branch")) if repo.get("fallback_branch") else None,
                    discovery_state=_initial_discovery_state(repo.get("nested_config")),
                    gitprovider=_parse_enum(GitProvider, repo.get("gitprovider"), GitProvider.GITHUB),
                    project_owner_name=str(repo.get("project_owner_name"))
                    if repo.get("project_owner_name")
                    else None,
                    project_name=str(repo.get("project_name")) if repo.get("project_name") else None,
                    group_name=str(repo.get("group_name")) if repo.get("group_name") else None,
                    gitprovider_url=str(repo.get("gitprovider_url"))
                    if repo.get("gitprovider_url")
                    else None,
                    access_protocol=_parse_enum(
                        AccessProtocol,
                        repo.get("access_protocol"),
                        AccessProtocol.SSH,
                    ),
                    default_branch=str(repo.get("default_branch") or nested_document.default_branch),
                    nested_config=str(repo.get("nested_config")) if repo.get("nested_config") else None,
                    remote_name=str(repo.get("remote_name") or entry.remote_name or "origin"),
                )
            )
            changes.append(f"discovered:{child_id}")

    registry.recompute_tree_state()
    return tuple(changes)


def _resolve_nested_config_path(repo_root: Path, nested_config: str) -> Path | None:
    if nested_config == "disabled":
        return None
    if nested_config != "auto":
        candidate = (repo_root / nested_config).resolve()
        if repo_root not in candidate.parents and candidate != repo_root:
            raise NestedConfigDiscoveryError(f"nested_config escapes repo root: {candidate}")
        return candidate if candidate.is_file() else None

    matches = sorted(repo_root.glob("*.cgs"))
    if not matches:
        return None
    if len(matches) > 1:
        raise NestedConfigDiscoveryError(f"Ambiguous nested .cgs discovery in {repo_root}")
    return matches[0].resolve()

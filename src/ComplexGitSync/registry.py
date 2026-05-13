"""Registry domain model, builder functions, and backward-compatible re-exports.

Domain classes now each live in their own module (DevSpecs § Object-Oriented
Design).  This module re-exports all registry types and retains the builder
functions that translate between document objects and the live registry.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePath, PurePosixPath
from typing import Any

from .access_protocol import AccessProtocol
from .dependency_tree_registry import DependencyTreeRegistry
from .discovery_state import DiscoveryState
from .documents import CgsDocument, GtsDocument
from .errors import ConfigValidationError
from .git_provider import GitProvider
from .node_type import NodeType
from .project_tree_state import ProjectTreeState
from .ref_kind import RefKind
from .repo_lifecycle_state import RepoLifecycleState
from .repo_node import RepoNode
from .repo_registry_entry import RepoRegistryEntry
from .sync_state import SyncState
from .tree_lifecycle_state import TreeLifecycleState

ROOT_REPO_ID = "root"

__all__ = [
    "ROOT_REPO_ID",
    "DependencyTreeRegistry",
    "DiscoveryState",
    "NodeType",
    "ProjectTreeState",
    "RefKind",
    "RepoLifecycleState",
    "RepoNode",
    "RepoRegistryEntry",
    "SyncState",
    "TreeLifecycleState",
    "build_gts_document_from_registry",
    "build_registry_from_cgs_document",
    "build_registry_from_gts_document",
    "build_tree_state",
    "make_repo_id",
    "promote_to_parent",
    "register_relative_path",
]


# ---------------------------------------------------------------------------
# Public builder / utility functions
# ---------------------------------------------------------------------------


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
    *,
    project_root: Path | str | None = None,
) -> DependencyTreeRegistry:
    source_path = Path(config_path).resolve()
    root_path = (
        Path(project_root).resolve() if project_root is not None else source_path.parent.resolve()
    )
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


def build_registry_from_gts_document(document: GtsDocument) -> DependencyTreeRegistry:
    registry = DependencyTreeRegistry()
    path_to_repo_id: dict[Path, str] = {}
    project_source_cgs_path = document.read("project.source_cgs_path")

    repo_states = sorted(
        document.repo_states,
        key=lambda repo: (len(Path(str(repo["absolute_path"])).parts), str(repo["absolute_path"])),
    )

    for repo_state in repo_states:
        absolute_path = Path(str(repo_state["absolute_path"])).resolve()
        parent_absolute_path = (
            Path(str(repo_state["parent_absolute_path"])).resolve()
            if repo_state.get("parent_absolute_path")
            else None
        )
        is_root = parent_absolute_path is None
        parent_id = None if is_root else path_to_repo_id[parent_absolute_path]
        repo_id = (
            ROOT_REPO_ID
            if is_root
            else make_repo_id(parent_id, repo_state.get("relative_path"), str(repo_state["name"]))
        )

        entry = RepoRegistryEntry(
            repo_id=repo_id,
            name=str(repo_state["name"]),
            node_type=NodeType.ROOT if is_root else _parse_gts_node_type(str(repo_state.get("node_type", "leaf"))),
            parent_id=parent_id,
            absolute_path=absolute_path,
            relative_path=(Path(str(repo_state["relative_path"])) if repo_state.get("relative_path") is not None else None),
            source_cgs_path=(
                Path(str(repo_state["source_cgs_path"])).resolve()
                if repo_state.get("source_cgs_path")
                else (Path(str(project_source_cgs_path)).resolve() if project_source_cgs_path else None)
            ),
            current_ref_kind=_parse_optional_enum(RefKind, repo_state.get("current_ref_kind")),
            current_ref_name=_as_optional_str(repo_state.get("current_ref_name")),
            target_ref_kind=_parse_optional_enum(RefKind, repo_state.get("target_ref_kind")),
            target_ref_name=_as_optional_str(repo_state.get("target_ref_name")),
            resolved_ref_kind=_parse_optional_enum(RefKind, repo_state.get("resolved_ref_kind")),
            resolved_ref_name=_as_optional_str(repo_state.get("resolved_ref_name")),
            commit_sha=_as_optional_str(repo_state.get("commit_sha")),
            repo_lifecycle_state=RepoLifecycleState(str(repo_state["repo_lifecycle_state"])),
            sync_state=SyncState(str(repo_state["sync_state"])),
            discovery_state=DiscoveryState(str(repo_state.get("discovery_state", DiscoveryState.RESOLVED.value))),
            fallback_branch=_as_optional_str(repo_state.get("fallback_branch")),
            fallback_applied=bool(repo_state.get("fallback_applied", False)),
            fallback_reason=_as_optional_str(repo_state.get("fallback_reason")),
            worktree_state=_as_optional_str(repo_state.get("worktree_state")),
            is_reachable=bool(repo_state.get("is_reachable", True)),
            project_owner_name=_as_optional_str(repo_state.get("project_owner_name")),
            project_name=_as_optional_str(repo_state.get("project_name")),
            default_branch=_as_optional_str(repo_state.get("target_ref_name")),
        )
        registry.add(entry)
        path_to_repo_id[absolute_path] = repo_id

    registry.recompute_tree_state()
    return registry


def build_gts_document_from_registry(
    registry: DependencyTreeRegistry,
    *,
    command_origin: str,
    source_cgs_path: Path | None,
) -> GtsDocument:
    root_entry = registry.get(ROOT_REPO_ID)
    tree_state = build_tree_state(registry)
    data: dict[str, Any] = {
        "document": {
            "format_version": "1.0",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "command_origin": command_origin,
        },
        "project": {
            "name": root_entry.name,
            "root_absolute_path": str(root_entry.absolute_path),
        },
        "tree_state": {
            "lifecycle_state": tree_state.lifecycle_state.value,
            "is_ready": tree_state.is_ready,
            "registry_complete": tree_state.registry_complete,
        },
        "repo_state": [],
    }
    if source_cgs_path is not None:
        data["project"]["source_cgs_path"] = str(source_cgs_path)

    for entry in sorted(registry.values(), key=lambda item: item.repo_id):
        repo_data: dict[str, Any] = {
            "name": entry.name,
            "node_type": entry.node_type.value,
            "absolute_path": str(entry.absolute_path),
            "relative_path": str(entry.relative_path) if entry.relative_path is not None else None,
            "repo_lifecycle_state": entry.repo_lifecycle_state.value,
            "sync_state": entry.sync_state.value,
            "current_ref_kind": entry.current_ref_kind.value if entry.current_ref_kind else None,
            "current_ref_name": entry.current_ref_name,
            "target_ref_kind": entry.target_ref_kind.value if entry.target_ref_kind else None,
            "target_ref_name": entry.target_ref_name,
            "resolved_ref_kind": entry.resolved_ref_kind.value if entry.resolved_ref_kind else None,
            "resolved_ref_name": entry.resolved_ref_name,
            "commit_sha": entry.commit_sha,
            "discovery_state": entry.discovery_state.value,
            "fallback_branch": entry.fallback_branch,
            "fallback_applied": entry.fallback_applied,
            "fallback_reason": entry.fallback_reason,
            "worktree_state": entry.worktree_state,
            "is_reachable": entry.is_reachable,
            "source_cgs_path": str(entry.source_cgs_path) if entry.source_cgs_path else None,
            "project_owner_name": entry.project_owner_name,
            "project_name": entry.project_name,
        }
        if entry.parent_id is not None:
            repo_data["parent_absolute_path"] = str(registry.get(entry.parent_id).absolute_path)
        data["repo_state"].append({key: value for key, value in repo_data.items() if value is not None})

    return GtsDocument.from_dict(data)


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


# ---------------------------------------------------------------------------
# Private helpers (also imported by discovery.py)
# ---------------------------------------------------------------------------


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


def _parse_optional_enum(enum_type: type, value: Any) -> Any:
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


def _parse_enum(enum_type: type, value: Any, default: Any) -> Any:
    if value is None:
        return default
    return enum_type(str(value))

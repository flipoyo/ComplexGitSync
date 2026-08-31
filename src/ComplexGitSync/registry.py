"""registry — translates .cgs/.gts documents to/from WorkingGitTree.

Ring: 2
Contract: given a parsed ``.cgs`` (``CgsDocument``) or ``.gts``
    (``GtsDocument``) document, build the in-memory ``WorkingGitTree``
    registry it describes; given a live registry, build the ``.gts``
    document (including its canonical snapshot hash and, for freeze
    command origins, its freeze manifest) that captures it. Pure
    translation — no ``subprocess``, no network calls; the only I/O is the
    env-marker path expansion inherited from the ``.gts``/``.cgs`` wire
    format itself (``$HOME``-style markers), which is why this module sits
    at Ring 2 rather than Ring 0/1.
Imports: cgs_format, errors, git_repo, git_tree, gts_document

Extracted from ``orchestre.py`` (Wave 2, P5-registry of
``AgentSpec/20260828_Isolation_DevPlanTicket.md``). ``orchestre.py`` still
carries its own copy of ``build_registry_from_cgs_document``,
``build_registry_from_gts_document``, and ``build_gts_document_from_registry``
until the separate P5-registry-integrate step deletes them there and
re-points callers — this module does not change that file.

Duplicated-helper note (same shape as ``gts_document.py``'s own note on the
ref-token helpers): ``_resolve_repo_target_ref`` and the env-marker path
helpers (``_path_to_environment_marker`` and friends) are used in
``orchestre.py`` by code outside this module's scope too (nested-config
discovery, ``ComplexGitSyncClient.load_gts``, snapshot writing) — since this
module must not import from ``orchestre.py`` (Ring 3, upward) and no Ring-1
``paths.py`` exists yet to hold the env-marker logic, both are duplicated
here as tiny, stable, pure/near-pure functions tied to a frozen wire format,
not forked business logic. ``_repo_ref_kind``/``_write_compact_refs`` are
new thin wrappers built on top of ``gts_document.py``'s
``_repo_ref_pair``/``_ref_token`` — imported from there rather than
duplicated, per this ticket's guidance to prefer importing the *same*
function from the Ring-0 module now that the other caller (this module) can
import downward from it.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__ as CGS_VERSION
from .cgs_format import CgsDocument
from .errors import ConfigValidationError
from .git_repo import (
    AccessProtocol,
    DiscoveryState,
    GitProvider,
    NodeType,
    RefKind,
    RepoLifecycleState,
    SyncState,
    WorkingRepo,
)
from .git_tree import (
    ROOT_REPO_ID,
    WorkingGitTree,
    _apply_repo_identity,
    _as_optional_str,
    _initial_discovery_state,
    _is_root_repo_spec,
    _normalise_relative_path,
    _parse_enum,
    _parse_gts_node_type,
    _parse_optional_enum,
    _validate_repo_shape,
    build_tree_state,
    format_view_tree,
    make_repo_id,
    normalize_node_types,
    register_relative_path,
)
from .gts_document import (
    _FREEZE_COMMAND_ORIGINS,
    GtsDocument,
    _ref_token,
    _repo_ref_name,
    _repo_ref_pair,
)

# ============================================================
#  Environment-marker path helpers
#
#  Duplicated from orchestre.py — see the module docstring above for why
#  these are copies, not imports (no Ring-1 paths.py exists yet to import
#  them from, and orchestre.py itself has other, non-extracted callers).
# ============================================================


def _get_path_environment_markers() -> tuple[tuple[str, Path], ...]:
    markers: list[tuple[str, Path]] = []
    seen_paths: set[str] = set()

    def add_marker(token: str, raw_value: str | None) -> None:
        if not raw_value:
            return
        resolved = Path(raw_value).expanduser().resolve()
        key = os.path.normcase(str(resolved))
        if key in seen_paths:
            return
        seen_paths.add(key)
        markers.append((token, resolved))

    add_marker("$HOME", os.environ.get("HOME"))
    add_marker("%USERPROFILE%", os.environ.get("USERPROFILE"))
    homedrive = os.environ.get("HOMEDRIVE")
    homepath = os.environ.get("HOMEPATH")
    if homedrive and homepath:
        add_marker("%HOMEDRIVE%%HOMEPATH%", f"{homedrive}{homepath}")
    return tuple(markers)


def _path_to_environment_marker(path: Path | str) -> str:
    resolved_path = Path(path).expanduser().resolve()
    for token, base_path in _get_path_environment_markers():
        try:
            relative = resolved_path.relative_to(base_path)
        except ValueError:
            continue
        if relative == Path("."):
            return token
        return f"{token}/{relative.as_posix()}"
    return str(resolved_path)


def _preferred_path_separators() -> tuple[str, ...]:
    separators: list[str] = []
    seen: set[str] = set()
    for separator in (os.sep, os.altsep, "/", "\\"):
        if separator and separator not in seen:
            seen.add(separator)
            separators.append(separator)
    return tuple(separators)


def _expand_environment_markers(raw_path: str) -> str:
    def _replace_prefixed_marker(value: str, marker: str, replacement: str) -> str:
        if value == marker:
            return replacement
        for separator in _preferred_path_separators():
            prefix = f"{marker}{separator}"
            if value.startswith(prefix):
                suffix = value[len(prefix):]
                return f"{replacement}{separator}{suffix}"
        return value

    expanded = raw_path
    home = os.environ.get("HOME")
    if home:
        expanded = _replace_prefixed_marker(expanded, "$HOME", home)
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        expanded = _replace_prefixed_marker(expanded, "%USERPROFILE%", userprofile)
    homedrive = os.environ.get("HOMEDRIVE")
    homepath = os.environ.get("HOMEPATH")
    if homedrive and homepath:
        expanded = _replace_prefixed_marker(
            expanded,
            "%HOMEDRIVE%%HOMEPATH%",
            f"{homedrive}{homepath}",
        )
    return expanded


def _resolve_document_path(raw_path: str) -> Path:
    return Path(_expand_environment_markers(raw_path)).expanduser().resolve()


# ============================================================
#  Ref-token helpers built on gts_document.py's shared primitives
# ============================================================


def _repo_ref_kind(repo: dict[str, Any], prefix: str) -> str | None:
    return _repo_ref_pair(repo, prefix)[0]


def _write_compact_refs(repo_data: dict[str, Any], entry: WorkingRepo) -> None:
    current = _ref_token(entry.current_ref_kind, entry.current_ref_name)
    target = _ref_token(entry.target_ref_kind, entry.target_ref_name)
    resolved = _ref_token(entry.resolved_ref_kind, entry.resolved_ref_name)
    refs = [ref for ref in (current, target, resolved) if ref is not None]
    if refs and len(set(refs)) == 1:
        repo_data["ref"] = refs[0]
        return
    if current is not None:
        repo_data["current_ref"] = current
    if target is not None:
        repo_data["target_ref"] = target
    if resolved is not None:
        repo_data["resolved_ref"] = resolved


def _resolve_repo_target_ref(
    repo: dict[str, Any],
    *,
    document_default_branch: str | None,
) -> tuple[RefKind, str | None]:
    tag = _as_optional_str(repo.get("tag"))
    if tag:
        return (RefKind.TAG, tag)
    branch = _as_optional_str(repo.get("branch")) or _as_optional_str(repo.get("default_branch"))
    if branch is None:
        branch = document_default_branch or "main"
    return (RefKind.BRANCH, branch)


# ============================================================
#  Registry builders — translate documents ↔ WorkingGitTree
# ============================================================


def build_registry_from_cgs_document(
    document: CgsDocument,
    config_path: Path | str,
    *,
    project_root: Path | str | None = None,
) -> WorkingGitTree:
    """Build a :class:`WorkingGitTree` from a ``.cgs`` document."""
    source_path = Path(config_path).resolve()
    root_path = (
        Path(project_root).resolve() if project_root is not None else source_path.parent.resolve()
    )
    root_entry = WorkingRepo(
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

    registry = WorkingGitTree()
    registry.add(root_entry)

    seen_relative_paths: set[Path] = set()
    root_identity_assigned = False
    for repo in document.repos:
        _validate_repo_shape(repo)
        if _is_root_repo_spec(repo, document.project_name, root_identity_assigned):
            _apply_repo_identity(root_entry, repo, document.default_branch)
            # The source .cgs for the project root is already loaded.  The
            # authoring default ``nested_config = auto`` applies to its
            # descendants and must not make the root pending again.
            root_entry.discovery_state = DiscoveryState.RESOLVED
            root_identity_assigned = True
            continue

        relative_path = _normalise_relative_path(repo)
        register_relative_path(
            seen_relative_paths,
            relative_path,
            error_type=ConfigValidationError,
            context="root",
        )

        target_kind, target_name = _resolve_repo_target_ref(
            repo,
            document_default_branch=document.default_branch,
        )
        entry = WorkingRepo(
            repo_id=make_repo_id(ROOT_REPO_ID, relative_path, str(repo["project_name"])),
            name=str(repo["project_name"]),
            node_type=NodeType.LEAF,
            parent_id=ROOT_REPO_ID,
            absolute_path=(root_path / relative_path).resolve(),
            relative_path=relative_path,
            source_cgs_path=source_path,
            target_ref_kind=target_kind,
            target_ref_name=target_name,
            fallback_branch=_as_optional_str(repo.get("fallback_branch")),
            discovery_state=_initial_discovery_state(repo.get("nested_config")),
            gitprovider=_parse_enum(GitProvider, repo.get("gitprovider"), GitProvider.GITHUB),
            project_owner_name=_as_optional_str(repo.get("project_owner_name")),
            project_name=_as_optional_str(repo.get("project_name")),
            repo_name=(
                _as_optional_str(repo.get("repo_name"))
                if repo.get("repo_name") is not None
                else _as_optional_str(repo.get("project_name"))
            ),
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

    normalize_node_types(registry)
    registry.recompute_tree_state()
    document.attach_serialization_context(registry)
    return registry


def build_registry_from_gts_document(document: GtsDocument) -> WorkingGitTree:
    """Build a :class:`WorkingGitTree` from a ``.gts`` snapshot document."""
    registry = WorkingGitTree()
    path_to_repo_id: dict[Path, str] = {}
    project_source_cgs_path = document.read("project.source_cgs_path")

    repo_states = sorted(
        document.repo_states,
        key=lambda repo: (len(Path(str(repo["absolute_path"])).parts), str(repo["absolute_path"])),
    )

    for repo_state in repo_states:
        absolute_path = _resolve_document_path(str(repo_state["absolute_path"]))
        parent_absolute_path = (
            _resolve_document_path(str(repo_state["parent_absolute_path"]))
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

        entry = WorkingRepo(
            repo_id=repo_id,
            name=str(repo_state["name"]),
            node_type=NodeType.ROOT if is_root else _parse_gts_node_type(str(repo_state.get("node_type", "leaf"))),
            parent_id=parent_id,
            absolute_path=absolute_path,
            relative_path=(Path(str(repo_state["relative_path"])) if repo_state.get("relative_path") is not None else None),
            source_cgs_path=(
                _resolve_document_path(str(repo_state["source_cgs_path"]))
                if repo_state.get("source_cgs_path")
                else (_resolve_document_path(str(project_source_cgs_path)) if project_source_cgs_path else None)
            ),
            current_ref_kind=_parse_optional_enum(RefKind, _repo_ref_kind(repo_state, "current")),
            current_ref_name=_repo_ref_name(repo_state, "current"),
            target_ref_kind=_parse_optional_enum(RefKind, _repo_ref_kind(repo_state, "target")),
            target_ref_name=_repo_ref_name(repo_state, "target"),
            resolved_ref_kind=_parse_optional_enum(RefKind, _repo_ref_kind(repo_state, "resolved")),
            resolved_ref_name=_repo_ref_name(repo_state, "resolved"),
            commit_sha=_as_optional_str(repo_state.get("commit_sha")),
            repo_lifecycle_state=RepoLifecycleState(str(repo_state["repo_lifecycle_state"])),
            sync_state=SyncState(str(repo_state["sync_state"])),
            discovery_state=DiscoveryState(str(repo_state.get("discovery_state", DiscoveryState.RESOLVED.value))),
            fallback_branch=_as_optional_str(repo_state.get("fallback_branch", "main")),
            fallback_applied=bool(repo_state.get("fallback_applied", False)),
            fallback_reason=_as_optional_str(repo_state.get("fallback_reason")),
            worktree_state=_as_optional_str(repo_state.get("worktree_state")),
            is_reachable=bool(repo_state.get("is_reachable", True)),
            project_owner_name=_as_optional_str(repo_state.get("project_owner_name")),
            project_name=_as_optional_str(repo_state.get("project_name")),
            repo_name=(
                _as_optional_str(repo_state.get("repo_name"))
                if repo_state.get("repo_name") is not None
                else _as_optional_str(repo_state.get("project_name"))
            ),
            default_branch=_repo_ref_name(repo_state, "target"),
        )
        registry.add(entry)
        path_to_repo_id[absolute_path] = repo_id

    normalize_node_types(registry)
    registry.recompute_tree_state()
    return registry


def build_gts_document_from_registry(
    registry: WorkingGitTree,
    *,
    command_origin: str,
    source_cgs_path: Path | None,
    freeze_name: str | None = None,
) -> GtsDocument:
    """Build a :class:`GtsDocument` from the live *registry*."""
    root_entry = registry.get(ROOT_REPO_ID)
    tree_state = build_tree_state(registry)
    data: dict[str, Any] = {
        "document": {
            "CGS_VERSION": CGS_VERSION,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "command_origin": command_origin,
        },
        "project": {
            "name": root_entry.name,
            "root_absolute_path": _path_to_environment_marker(root_entry.absolute_path),
        },
        "tree_state": {
            "lifecycle_state": tree_state.lifecycle_state.value,
            "is_ready": tree_state.is_ready,
            "registry_complete": tree_state.registry_complete,
        },
        "tree": {
            "lines": format_view_tree(registry).splitlines(),
        },
        "repo_state": [],
    }
    if source_cgs_path is not None:
        data["project"]["source_cgs_path"] = _path_to_environment_marker(source_cgs_path)
    if command_origin in _FREEZE_COMMAND_ORIGINS:
        data["freeze_manifest"] = _build_freeze_manifest(registry, freeze_name=freeze_name)

    for entry in sorted(registry.values(), key=lambda item: item.repo_id):
        repo_data: dict[str, Any] = {
            "name": entry.name,
            "node_type": entry.node_type.value,
            "absolute_path": _path_to_environment_marker(entry.absolute_path),
            "relative_path": str(entry.relative_path) if entry.relative_path is not None else None,
            "repo_lifecycle_state": entry.repo_lifecycle_state.value,
            "sync_state": entry.sync_state.value,
            "commit_sha": entry.commit_sha,
            "fallback_reason": entry.fallback_reason,
            "worktree_state": entry.worktree_state,
            "source_cgs_path": (
                _path_to_environment_marker(entry.source_cgs_path) if entry.source_cgs_path else None
            ),
            "project_owner_name": entry.project_owner_name,
            "project_name": entry.project_name,
            "repo_name": entry.repo_name,
        }
        _write_compact_refs(repo_data, entry)
        if entry.discovery_state != DiscoveryState.RESOLVED:
            repo_data["discovery_state"] = entry.discovery_state.value
        if entry.fallback_branch and entry.fallback_branch != "main":
            repo_data["fallback_branch"] = entry.fallback_branch
        if entry.fallback_applied:
            repo_data["fallback_applied"] = entry.fallback_applied
        if not entry.is_reachable:
            repo_data["is_reachable"] = entry.is_reachable
        if entry.parent_id is not None:
            repo_data["parent_absolute_path"] = _path_to_environment_marker(
                registry.get(entry.parent_id).absolute_path
            )
        data["repo_state"].append({key: value for key, value in repo_data.items() if value is not None})

    document = GtsDocument.from_dict(data)
    document.ensure_snapshot_hash()
    document.validate()
    return document


def _build_freeze_manifest(
    registry: WorkingGitTree,
    *,
    freeze_name: str | None = None,
) -> dict[str, Any]:
    root_entry = registry.get(ROOT_REPO_ID)
    tag_name = (
        freeze_name
        or root_entry.resolved_ref_name
        or root_entry.target_ref_name
        or root_entry.current_ref_name
        or ""
    )
    return {
        "schema_version": "1.0",
        "immutable_snapshot": True,
        "workspace_validated": True,
        "ledger_checkpoint": True,
        "synchronized_ref_kind": RefKind.TAG.value,
        "synchronized_ref_name": tag_name,
        "release-name": tag_name,
        "restore_operation": "launch_state",
    }


__all__ = [
    "build_registry_from_cgs_document",
    "build_registry_from_gts_document",
    "build_gts_document_from_registry",
]

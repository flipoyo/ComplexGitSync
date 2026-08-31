"""discovery — nested .cgs auto-discovery and .gitmodules parsing.

Ring: 1 (filesystem only, no subprocess)
Contract: given a WorkingGitTree with pending nested_config entries, resolve
    and promote each one's nested .cgs into the parent registry in place; and,
    independently, parse .gitmodules file content into structured entries.
Imports: cgs_format, errors, git_repo, git_tree
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cgs_format import CgsDocument
from .errors import NestedConfigDiscoveryError
from .git_repo import (
    AccessProtocol,
    DiscoveryState,
    GitProvider,
    NodeType,
    RefKind,
    WorkingRepo,
)
from .git_tree import (
    ROOT_REPO_ID,
    WorkingGitTree,
    _apply_repo_identity,
    _as_optional_str,
    _initial_discovery_state,
    _normalise_relative_path,
    _parse_enum,
    _validate_repo_shape,
    make_repo_id,
    normalize_node_types,
    promote_to_parent,
    register_relative_path,
)

# ============================================================
#  Nested .cgs auto-discovery
# ============================================================


def discover_nested_configs(registry: WorkingGitTree) -> tuple[str, ...]:
    """Discover nested ``.cgs`` files in already-cloned repositories."""
    changes: list[str] = []
    pending_entries = [
        entry
        for entry in registry.values()
        if entry.repo_id != ROOT_REPO_ID
        and entry.nested_config not in {None, "disabled"}
        and entry.discovery_state != DiscoveryState.RESOLVED
    ]

    # Pre-build a set of all known absolute paths for O(1) circularity detection.
    # Updated in-place as new entries are added during this call.
    registered_paths: set[Path] = {e.absolute_path for e in registry.values() if e.absolute_path is not None}

    for entry in pending_entries:
        if not entry.absolute_path.exists():
            entry.discovery_state = DiscoveryState.MISSING
            continue

        effective_nested_config = entry.nested_config or "auto"
        nested_path = _resolve_nested_config_path(entry.absolute_path, effective_nested_config)
        if nested_path is None:
            # "auto" finding zero *.cgs files is a normal leaf, not a
            # mistake — only an explicit path that doesn't exist is a real
            # error, since the user asserted a specific file must be there.
            entry.discovery_state = (
                DiscoveryState.RESOLVED if effective_nested_config == "auto" else DiscoveryState.MISSING
            )
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
                # This nested document has just been resolved for ``entry``.
                entry.discovery_state = DiscoveryState.RESOLVED
                root_identity_assigned = True
                continue

            relative_path = _normalise_relative_path(repo)
            register_relative_path(
                existing_child_paths,
                relative_path,
                error_type=NestedConfigDiscoveryError,
                context=str(entry.absolute_path),
            )

            child_id = make_repo_id(entry.repo_id, relative_path, str(repo["project_name"]))
            if child_id in registry.repos:
                continue

            child_absolute_path = (entry.absolute_path / relative_path).resolve()
            # Skip children whose absolute path already exists in the registry.
            # This prevents circularities at discovery time: if a parent's nested
            # .cgs references another parent (already registered under a different
            # repo_id), we do not create a duplicate entry here.  The standalone
            # fix_circularities() function handles any pre-existing duplicates that
            # were not prevented by this guard (e.g., loaded from an older .gts).
            if child_absolute_path in registered_paths:
                continue

            target_kind, target_name = _resolve_repo_target_ref(
                repo,
                document_default_branch=nested_document.default_branch,
            )
            new_entry = registry.add(
                WorkingRepo(
                    repo_id=child_id,
                    name=str(repo["project_name"]),
                    node_type=NodeType.LEAF,
                    parent_id=entry.repo_id,
                    absolute_path=child_absolute_path,
                    relative_path=relative_path,
                    source_cgs_path=nested_path,
                    target_ref_kind=target_kind,
                    target_ref_name=target_name,
                    fallback_branch=str(repo.get("fallback_branch")) if repo.get("fallback_branch") else None,
                    discovery_state=_initial_discovery_state(repo.get("nested_config")),
                    gitprovider=_parse_enum(GitProvider, repo.get("gitprovider"), GitProvider.GITHUB),
                    project_owner_name=str(repo.get("project_owner_name"))
                    if repo.get("project_owner_name")
                    else None,
                    project_name=str(repo.get("project_name")) if repo.get("project_name") else None,
                    repo_name=(
                        str(repo.get("repo_name"))
                        if repo.get("repo_name") is not None
                        else (str(repo.get("project_name")) if repo.get("project_name") is not None else None)
                    ),
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
            registered_paths.add(new_entry.absolute_path)
            changes.append(f"discovered:{child_id}")

    normalize_node_types(registry)
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


def _resolve_repo_target_ref(
    repo: dict[str, Any],
    *,
    document_default_branch: str | None,
) -> tuple[RefKind, str | None]:
    # NOTE: this helper is intentionally duplicated from orchestre.py rather
    # than imported. It is pure (no I/O) and is also called from
    # ``build_registry_from_cgs_document`` in orchestre.py, a function this
    # work package does not own (it is Wave 2's P5-registry target). Moving
    # it here and importing it back into orchestre.py would require editing
    # orchestre.py, which is out of scope for this Lane-A work package; a
    # later integration step should consolidate the two copies once
    # registry.py lands.
    tag = _as_optional_str(repo.get("tag"))
    if tag:
        return (RefKind.TAG, tag)
    branch = _as_optional_str(repo.get("branch")) or _as_optional_str(repo.get("default_branch"))
    if branch is None:
        branch = document_default_branch or "main"
    return (RefKind.BRANCH, branch)


# ============================================================
#  .gitmodules → nested-clone conversion (pure parsing)
# ============================================================


@dataclass(frozen=True, slots=True)
class SubmoduleEntry:
    """One git submodule entry parsed from ``.gitmodules``."""

    name: str
    path: str
    url: str
    branch: str


@dataclass(frozen=True, slots=True)
class ImportSubmodulesReport:
    """Result returned by :meth:`ComplexGitSync.orchestre.ComplexGitSyncClient.import_submodules`.

    Attributes
    ----------
    submodules:
        All submodule entries found in ``.gitmodules``.
    applied:
        ``True`` when the conversion was actually performed (``apply=True``).
    converted:
        Names of submodules that were converted (same as ``submodules`` when
        ``applied`` is ``True``; empty tuple when dry-run).
    cgs_entries:
        Authoring-form ``repos`` tables for the converted submodules — ready
        to pass to :meth:`ComplexGitSync.orchestre.ComplexGitSyncClient.configure`.
    """

    submodules: tuple[SubmoduleEntry, ...]
    applied: bool
    converted: tuple[str, ...]
    cgs_entries: tuple[dict, ...]


def _parse_gitmodules(content: str) -> list[SubmoduleEntry]:
    """Parse ``.gitmodules`` file content into :class:`SubmoduleEntry` objects.

    Handles the standard git config INI-like format::

        [submodule "name"]
            path = some/path
            url  = https://example.com/owner/repo.git
            branch = main      # optional
    """
    parser = configparser.RawConfigParser()
    parser.read_string(content)

    result: list[SubmoduleEntry] = []
    for section in parser.sections():
        name_match = re.match(r'^submodule\s+"(.+)"$', section)
        if name_match is None:
            continue
        name = name_match.group(1)
        path = parser.get(section, "path", fallback="").strip()
        url = parser.get(section, "url", fallback="").strip()
        branch = parser.get(section, "branch", fallback="main").strip() or "main"
        if path and url:
            result.append(SubmoduleEntry(name=name, path=path, url=url, branch=branch))
    return result

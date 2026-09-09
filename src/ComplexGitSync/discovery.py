"""discovery — nested .cgs auto-discovery and .gitmodules parsing.

Ring: 1 (filesystem only, no subprocess)
Contract: given a WorkingGitTree with pending nested_config entries, resolve
    and promote each one's nested .cgs into the parent registry in place; and,
    independently, parse .gitmodules file content into structured entries.
Imports: cgs_format, errors, git_branch, git_repo, git_tree
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass
from pathlib import Path

from .cgs_format import CgsDocument
from .errors import NestedConfigDiscoveryError
from .git_branch import resolve_declared_ref
from .git_repo import (
    AccessProtocol,
    DiscoveryState,
    GitProvider,
    NodeType,
    WorkingRepo,
)
from .git_tree import (
    ROOT_REPO_ID,
    WorkingGitTree,
    _apply_repo_identity,
    _initial_discovery_state,
    _normalise_relative_path,
    _parse_enum,
    _validate_repo_shape,
    make_repo_id,
    normalize_node_types,
    promote_to_parent,
    propagate_pinning,
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

            target = resolve_declared_ref(
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
                    target_ref_kind=target.kind,
                    target_ref_name=target.name,
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
                        AccessProtocol, repo.get("access_protocol"), AccessProtocol.SSH
                    ),
                    default_branch=str(repo.get("default_branch") or nested_document.default_branch),
                    nested_config=str(repo.get("nested_config")) if repo.get("nested_config") else None,
                    pinned=bool(repo.get("pinned", False)),
                    writable=bool(repo.get("writable", False)),
                    remote_name=str(repo.get("remote_name") or entry.remote_name or "origin"),
                )
            )
            registered_paths.add(new_entry.absolute_path)
            changes.append(f"discovered:{child_id}")

    normalize_node_types(registry)
    propagate_pinning(registry)
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
        # A repository that may be mounted inside another tree must keep
        # exactly one .cgs at its root: "auto" has no way to choose between
        # two. Name both the files and the escape hatch, because the author
        # hitting this is usually mounting someone else's repository and has
        # no idea which file was meant.
        names = ", ".join(path.name for path in matches)
        raise NestedConfigDiscoveryError(
            f"Ambiguous nested .cgs discovery in {repo_root}: found {names}. "
            f"nested_config = \"auto\" needs exactly one .cgs at a repository "
            f"root. Name the one you mean on this repository's entry — "
            f"nested_config = \"<file>.cgs\" — or set nested_config = "
            f"\"disabled\" to stop descending into it."
        )
    return matches[0].resolve()


# ============================================================
#  .gitmodules → nested-clone conversion (pure parsing)
# ============================================================


@dataclass(frozen=True, slots=True)
class SubmoduleEntry:
    """One git submodule entry parsed from ``.gitmodules``.

    *path* is written in ``.gitmodules`` relative to the repository that
    declares it, so it means nothing on its own once more than one
    repository is involved. *owner_root* records which repository that is,
    so a caller can always say where the submodule really sits.
    ``_parse_gitmodules`` leaves it unset, since parsing text alone cannot
    know; the caller that read the file fills it in.
    """

    name: str
    path: str
    url: str
    branch: str
    owner_root: Path | None = None


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
    scan_root:
        The repository the command was pointed at. Every reported path can
        be expressed from here, which is what makes a multi-level report
        readable — see :attr:`SubmoduleEntry.owner_root`.
    """

    submodules: tuple[SubmoduleEntry, ...]
    applied: bool
    converted: tuple[str, ...]
    scan_root: Path | None = None

    def path_from_scan_root(self, entry: SubmoduleEntry) -> str:
        """Return *entry*'s location counted from :attr:`scan_root`.

        Falls back to the raw ``.gitmodules`` path when either root is
        unknown, which is the best that can be said in that case.
        """
        if self.scan_root is None or entry.owner_root is None:
            return entry.path
        return ((entry.owner_root / entry.path).relative_to(self.scan_root)).as_posix()

    def gitmodules_from_scan_root(self, entry: SubmoduleEntry) -> str:
        """Return the ``.gitmodules`` file that declared *entry*, from :attr:`scan_root`."""
        if self.scan_root is None or entry.owner_root is None:
            return ".gitmodules"
        owner = entry.owner_root.relative_to(self.scan_root).as_posix()
        return ".gitmodules" if owner == "." else f"{owner}/.gitmodules"


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
        # Not git_branch.DEFAULT_BRANCH: this is Git's own .gitmodules
        # default for a submodule that names no branch, not ComplexGitSync's
        # .cgs fallback chain. The two happen to spell the same word today;
        # they are not the same decision, and changing our default must not
        # silently change how we read someone else's .gitmodules.
        branch = parser.get(section, "branch", fallback="main").strip() or "main"
        if path and url:
            result.append(SubmoduleEntry(name=name, path=path, url=url, branch=branch))
    return result

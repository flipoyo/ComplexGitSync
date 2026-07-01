"""Orchestration hub for ComplexGitSync.

This module is the **Orchestre anchor** — the authoritative source for all
document I/O, infrastructure services, registry builders, nested config
discovery, and the public client API.

Classes defined here (Tier 2 — Actions):
    ConfigDocument          Base class for all document types
    CgsDocument             .cgs authoring spec parser/validator
    GtsDocument             .gts state snapshot parser/validator
    GocDocument             .goc command script parser/validator
    CommandRunLogger        Structured JSON event logger for a command run
    RuntimeStateStore       Persistent snapshot-pointer registry (.cgs → .gts)
    GitRunner               Git subprocess wrapper

Classes defined here (Tier 3 — Client / API):
    Orchestre               Coordination layer owning one GitTree
    ComplexGitSyncClient    Public facade; gates all actions on TreeLifecycleState

Builder / discovery functions defined here:
    build_registry_from_cgs_document
    build_registry_from_gts_document
    build_gts_document_from_registry
    discover_nested_configs
    create_run_logger
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

import tomli_w

from .errors import (
    ConfigValidationError,
    GitSyncError,
    NestedConfigDiscoveryError,
)
from .git_repo import (
    AccessProtocol,
    DiscoveryState,
    GitProvider,
    GitRepo,
    NodeType,
    RefKind,
    RepoLifecycleState,
    RepoNode,
    RepoRegistryEntry,
    SyncState,
)
from .git_tree import (
    ROOT_REPO_ID,
    DependencyTreeRegistry,
    GitTree,
    ProjectTreeState,
    TreeLifecycleState,
    _apply_repo_identity,
    _as_optional_str,
    _initial_discovery_state,
    _is_root_repo_spec,
    _normalise_relative_path,
    _normalize_repo_id_segment,
    _parse_enum,
    _parse_gts_node_type,
    _parse_optional_enum,
    _validate_repo_shape,
    build_tree_state,
    fix_circularities as _fix_circularities,
    format_project_tree,
    format_view_operation,
    format_view_tree,
    format_repo_tree_outline,
    iter_tree,
    iter_tree_leaf_first,
    make_repo_id,
    promote_to_parent,
    register_relative_path,
    topological_sort as _topological_sort,
)
from .operations import (
    validate_branch_topology as _validate_branch_topology,
    BranchTopologyReport,
)

# ============================================================
#  Document layer — ConfigDocument base + subclasses
# ============================================================

_MISSING = object()
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_FREEZE_COMMAND_ORIGINS = frozenset({"freeze", "freeze_release", "freeze_state"})


def _dot_get(data: dict[str, Any], key: str, default: Any = None) -> Any:
    """Return the value at the dot-separated *key* path inside *data*."""
    parts = key.split(".")
    node: Any = data
    for part in parts:
        if not isinstance(node, dict):
            return default
        node = node.get(part, _MISSING)
        if node is _MISSING:
            return default
    return node


def _collect_errors(checks: list[tuple[bool, str]]) -> list[str]:
    return [msg for ok, msg in checks if not ok]


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


def _preferred_path_separators() -> tuple[str, ...]:
    separators: list[str] = []
    seen: set[str] = set()
    for separator in (os.sep, os.altsep, "/", "\\"):
        if separator and separator not in seen:
            seen.add(separator)
            separators.append(separator)
    return tuple(separators)


def _local_status_from_porcelain(status_lines: list[str]) -> str:
    if not status_lines:
        return "clean"
    staged = any(line[:2] != "??" and line[0] != " " for line in status_lines)
    unstaged = any(line[:2] == "??" or (len(line) > 1 and line[1] != " ") for line in status_lines)
    if staged and unstaged:
        return "staged+dirty"
    if staged:
        return "staged"
    return "dirty"


def _status_tracking_label(sync_state: SyncState | None) -> str:
    if sync_state is None:
        return "unknown"
    if sync_state == SyncState.ALIGNED:
        return "synced"
    if sync_state == SyncState.AHEAD:
        return "ahead"
    if sync_state == SyncState.BEHIND:
        return "behind"
    if sync_state == SyncState.DIVERGED:
        return "diverged"
    return sync_state.value.lower()


def _short_sha(value: str | None) -> str:
    return value[:8] if value else "-"


def _status_display_path(entry: RepoRegistryEntry, root_path: Path) -> str:
    try:
        relative = entry.absolute_path.relative_to(root_path)
    except ValueError:
        return str(entry.relative_path or entry.absolute_path)
    if relative == Path("."):
        return "."
    return relative.as_posix()


def _status_line_path(status_line: str) -> Path | None:
    if len(status_line) < 4:
        return None
    raw_path = status_line[3:]
    if " -> " in raw_path:
        raw_path = raw_path.rsplit(" -> ", 1)[1]
    raw_path = raw_path.strip().strip('"')
    return Path(raw_path) if raw_path else None


def _status_line_targets_any(status_line: str, paths: set[Path]) -> bool:
    status_path = _status_line_path(status_line)
    if status_path is None:
        return False
    return any(status_path == path or _path_is_relative_to(status_path, path) for path in paths)


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _unmanaged_gitlink_paths(
    registry: DependencyTreeRegistry,
    entry: RepoRegistryEntry,
    git_runner: Any,
) -> set[Path]:
    try:
        gitlinks = git_runner.tracked_gitlink_paths(entry.absolute_path)
    except (AttributeError, GitSyncError):
        return set()

    managed_children: set[Path] = set()
    for child in registry.children_of(entry.repo_id):
        try:
            managed_children.add(child.absolute_path.relative_to(entry.absolute_path))
        except ValueError:
            continue
    return {path for path in gitlinks if path not in managed_children}


def _render_status_table(rows: list[tuple[str, str, str, str, str, str, str]]) -> str:
    headers = ("REPOSITORY", "PATH", "BRANCH", "LOCAL", "UPSTREAM", "HEAD", "RECORDED")
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def render_row(columns: Sequence[str]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(columns))

    lines = [render_row(headers), "-" * (sum(widths) + 12)]
    lines.extend(render_row(row) for row in rows)
    return "\n".join(lines)


class ConfigDocument:
    """Base class for all ComplexGitSync configuration document types.

    Every subclass wraps a raw dictionary and exposes:

    Instance methods
    ~~~~~~~~~~~~~~~~
    * ``read(key, default=None)`` – dot-path traversal into the underlying dict
    * ``get(key, default=None)``  – alias for ``read``
    * ``print()``                 – write a human-readable representation to stdout
    * ``to_dict()``               – return a deep copy of the underlying dict
    * ``validate()``              – raise ``ConfigValidationError`` on schema violations

    Class-method factories
    ~~~~~~~~~~~~~~~~~~~~~~
    * ``from_dict(data)``    – create and validate from a plain dict
    * ``from_toml(path)``    – load from a ``.toml`` / ``.cgs`` / ``.gts`` / ``.goc`` file
    * ``from_json(path)``    – load from a ``.json`` file
    * ``from_yaml(path)``    – load from a ``.yml`` / ``.yaml`` file (requires PyYAML)

    Serializers
    ~~~~~~~~~~~
    * ``to_toml(path)``       – write as TOML
    * ``to_json(path)``       – write as JSON
    * ``to_yaml(path)``       – write as YAML (requires PyYAML)
    """

    FORMAT_VERSION: str = "1.0"
    DOCUMENT_KIND: str = "base"

    def __init__(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise TypeError(f"ConfigDocument data must be a dict, got {type(data).__name__!r}.")
        self._data: dict[str, Any] = data

    def read(self, key: str, default: Any = None) -> Any:
        """Return the value at the dot-separated *key* path, or *default*."""
        return _dot_get(self._data, key, default)

    def get(self, key: str, default: Any = None) -> Any:
        """Alias for :meth:`read`."""
        return self.read(key, default)

    def print(self) -> None:
        """Print the document as TOML to *stdout*."""
        print(tomli_w.dumps(self._data))

    def to_dict(self) -> dict[str, Any]:
        """Return a deep copy of the underlying dictionary."""
        return copy.deepcopy(self._data)

    def validate(self) -> None:
        """Validate required fields.  Raises :exc:`ConfigValidationError` on failure."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfigDocument":
        """Create a document from a plain dictionary and validate it."""
        doc = cls(data)
        doc.validate()
        return doc

    @classmethod
    def from_toml(cls, path: Path | str) -> "ConfigDocument":
        """Load a document from a TOML file (includes ``.cgs``, ``.gts``, ``.goc``)."""
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        return cls.from_dict(data)

    @classmethod
    def from_json(cls, path: Path | str) -> "ConfigDocument":
        """Load a document from a JSON file."""
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_dict(data)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "ConfigDocument":
        """Load a document from a YAML file.

        Requires ``PyYAML``. Install the package with the ``yaml`` extra.
        """
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for YAML support.  "
                "Install ComplexGitSync with the yaml extra using pixi."
            ) from exc
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return cls.from_dict(data)

    def to_toml(self, path: Path | str) -> None:
        """Write the document to a TOML file."""
        with open(path, "wb") as fh:
            tomli_w.dump(self._data, fh)

    def to_json(self, path: Path | str, *, indent: int = 2) -> None:
        """Write the document to a JSON file."""
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=indent)

    def to_yaml(self, path: Path | str) -> None:
        """Write the document to a YAML file.

        Requires ``PyYAML``. Install the package with the ``yaml`` extra.
        """
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for YAML support.  "
                "Install ComplexGitSync with the yaml extra using pixi."
            ) from exc
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(self._data, fh, default_flow_style=False, allow_unicode=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}(kind={self.DOCUMENT_KIND!r})"


class CgsDocument(ConfigDocument):
    """Parser and validator for ``.cgs`` authoring spec files.

    A ``.cgs`` file is a TOML document that describes the **static** project
    topology: which repositories belong to the tree, how they relate, and what
    runtime defaults apply.  It is **never** a runtime snapshot.
    """

    DOCUMENT_KIND = "cgs"

    _REQUIRED_DOCUMENT_KEYS = ("format_version",)
    _REQUIRED_PROJECT_KEYS = ("name", "default_branch")
    _REQUIRED_REPO_KEYS = ("project_owner_name", "project_name")
    _VALID_GITPROVIDERS = frozenset(("github", "gitlab", "custom"))
    _VALID_ACCESS_PROTOCOLS = frozenset(("ssh", "https"))
    _VALID_NESTED_CONFIG_SPECIAL = frozenset(("auto", "disabled"))

    RUNTIME_DEFAULTS: dict[str, Any] = {
        "interaction": "interactive",
        "profile": "verbose",
        "prompt_scope": "per-event",
        "warn_on_fallback": True,
        "allow_mixed_resolution": True,
        "nested_config_discovery": True,
        "log_level": "info",
    }

    def validate(self) -> None:  # noqa: C901
        errors: list[str] = []

        for key in self._REQUIRED_DOCUMENT_KEYS:
            if self.read(f"document.{key}") is None:
                errors.append(f"[document] missing required key: '{key}'")

        for key in self._REQUIRED_PROJECT_KEYS:
            if self.read(f"project.{key}") is None:
                errors.append(f"[project] missing required key: '{key}'")

        repos = self._data.get("repos", [])
        if not isinstance(repos, list):
            errors.append("'repos' must be an array of tables ([[repos]])")
        else:
            for idx, repo in enumerate(repos):
                if not isinstance(repo, dict):
                    errors.append(f"repos[{idx}] must be a table")
                    continue
                for key in self._REQUIRED_REPO_KEYS:
                    if not repo.get(key):
                        errors.append(f"repos[{idx}] missing required key: '{key}'")
                gitprovider = repo.get("gitprovider", "github")
                if gitprovider not in self._VALID_GITPROVIDERS:
                    errors.append(
                        f"repos[{idx}].gitprovider invalid: {gitprovider!r} "
                        f"(choose from: {sorted(self._VALID_GITPROVIDERS)})"
                    )
                access_protocol = repo.get("access_protocol", "ssh")
                if access_protocol not in self._VALID_ACCESS_PROTOCOLS:
                    errors.append(
                        f"repos[{idx}].access_protocol invalid: {access_protocol!r} "
                        f"(choose from: {sorted(self._VALID_ACCESS_PROTOCOLS)})"
                    )
                nested = repo.get("nested_config")
                if nested is not None and nested not in self._VALID_NESTED_CONFIG_SPECIAL:
                    if not str(nested).endswith(".cgs"):
                        errors.append(
                            f"repos[{idx}].nested_config must be 'auto', 'disabled', "
                            f"or a .cgs relative path; got: {nested!r}"
                        )
                branch = _as_optional_str(repo.get("branch")) or _as_optional_str(repo.get("default_branch"))
                tag = _as_optional_str(repo.get("tag"))
                if branch and tag:
                    probe = GitRepo(
                        project_owner_name=str(repo.get("project_owner_name")),
                        project_name=str(repo.get("project_name")),
                        repo_name=(
                            _as_optional_str(repo.get("repo_name"))
                            if repo.get("repo_name") is not None
                            else str(repo.get("project_name"))
                        ),
                        gitprovider=_parse_enum(GitProvider, repo.get("gitprovider"), GitProvider.GITHUB),
                        group_name=_as_optional_str(repo.get("group_name")),
                        gitprovider_url=_as_optional_str(repo.get("gitprovider_url")),
                        access_protocol=_parse_enum(
                            AccessProtocol,
                            repo.get("access_protocol"),
                            AccessProtocol.SSH,
                        ),
                    )
                    branch_hash = probe._get_hash(branch=branch)
                    tag_hash = probe._get_hash(branch=branch, tag=tag)
                    if branch_hash != tag_hash:
                        errors.append("incompatibilities between branch (hash) and tag(val) in .cgs")

        if errors:
            raise ConfigValidationError(
                "Invalid .cgs document:\n" + "\n".join(f"  • {e}" for e in errors)
            )

    @property
    def project_name(self) -> str | None:
        return self.read("project.name")

    @property
    def default_branch(self) -> str | None:
        return self.read("project.default_branch")

    @property
    def repos(self) -> list[dict[str, Any]]:
        return list(self._data.get("repos", []))

    def runtime_setting(self, key: str) -> Any:
        return self._data.get("runtime", {}).get(key, self.RUNTIME_DEFAULTS.get(key))


class GtsDocument(ConfigDocument):
    """Parser and validator for ``.gts`` Git Tree State snapshot files.

    A ``.gts`` file is a TOML document **generated** by ComplexGitSync.  It
    captures the exact state of the full repository tree — including absolute
    paths and commit SHAs.  It is **never** hand-edited.
    """

    DOCUMENT_KIND = "gts"
    CURRENT_SCHEMA_VERSION = "1.1"
    HASH_ALGORITHM = "sha256"
    _SUPPORTED_HASH_ALGORITHMS = frozenset((HASH_ALGORITHM,))

    _REQUIRED_DOCUMENT_KEYS = ("format_version", "generated_at", "command_origin")
    _REQUIRED_PROJECT_KEYS = ("name", "root_absolute_path")
    _REQUIRED_TREE_STATE_KEYS = ("lifecycle_state", "is_ready", "registry_complete")
    _REQUIRED_REPO_STATE_KEYS = (
        "name",
        "node_type",
        "absolute_path",
        "repo_lifecycle_state",
        "sync_state",
    )

    def validate(self) -> None:
        errors: list[str] = []

        for key in self._REQUIRED_DOCUMENT_KEYS:
            if self.read(f"document.{key}") is None:
                errors.append(f"[document] missing required key: '{key}'")

        for key in self._REQUIRED_PROJECT_KEYS:
            if self.read(f"project.{key}") is None:
                errors.append(f"[project] missing required key: '{key}'")

        for key in self._REQUIRED_TREE_STATE_KEYS:
            if self.read(f"tree_state.{key}") is None:
                errors.append(f"[tree_state] missing required key: '{key}'")

        repo_states = self._data.get("repo_state", [])
        if not isinstance(repo_states, list):
            errors.append("'repo_state' must be an array of tables ([[repo_state]])")
        else:
            for idx, repo in enumerate(repo_states):
                if not isinstance(repo, dict):
                    errors.append(f"repo_state[{idx}] must be a table")
                    continue
                for key in self._REQUIRED_REPO_STATE_KEYS:
                    if not repo.get(key):
                        errors.append(f"repo_state[{idx}] missing required key: '{key}'")
                node_type: NodeType | None = None
                try:
                    node_type = _parse_gts_node_type(repo.get("node_type"))
                except ConfigValidationError as exc:
                    node_type = None
                    errors.append(f"repo_state[{idx}] invalid node_type: {exc}")
                project_root_path = self.read("project.root_absolute_path")
                is_project_root_repo = (
                    isinstance(project_root_path, str)
                    and str(repo.get("absolute_path", "")) == project_root_path
                )
                requires_parent_path = node_type != NodeType.ROOT and not is_project_root_repo
                if requires_parent_path and not repo.get("parent_absolute_path"):
                    errors.append(f"repo_state[{idx}] missing required key: 'parent_absolute_path'")
                has_ref_name = bool(
                    repo.get("current_ref_name")
                    or repo.get("target_ref_name")
                    or repo.get("resolved_ref_name")
                )
                if not has_ref_name:
                    errors.append(
                        f"repo_state[{idx}] must include at least one ref name ('current_ref_name', 'target_ref_name', or 'resolved_ref_name')"
                    )
                lifecycle = str(repo.get("repo_lifecycle_state", ""))
                if lifecycle in {
                    RepoLifecycleState.READY.value,
                    RepoLifecycleState.FALLBACK_READY.value,
                } and not repo.get("commit_sha"):
                    errors.append(
                        f"repo_state[{idx}] missing required key for READY repository: 'commit_sha'"
                    )

        hash_algorithm = self.read("document.hash_algorithm", self.HASH_ALGORITHM)
        if not isinstance(hash_algorithm, str) or hash_algorithm not in self._SUPPORTED_HASH_ALGORITHMS:
            errors.append(
                f"[document] unsupported hash_algorithm '{hash_algorithm}' (supported: {', '.join(sorted(self._SUPPORTED_HASH_ALGORITHMS))})"
            )

        snapshot_hash = self.read("document.snapshot_hash")
        if snapshot_hash is not None:
            if not isinstance(snapshot_hash, str) or _SHA256_HEX_RE.fullmatch(snapshot_hash) is None:
                errors.append("[document] snapshot_hash must be a lowercase hexadecimal SHA-256 digest")
            elif snapshot_hash != self.compute_snapshot_hash():
                errors.append("[document] snapshot_hash does not match canonical .gts content hash")

        command_origin = self.read("document.command_origin")
        if command_origin in _FREEZE_COMMAND_ORIGINS:
            freeze_manifest = self._data.get("freeze_manifest")
            if not isinstance(freeze_manifest, dict):
                errors.append("[freeze_manifest] missing required table for freeze snapshots")
            else:
                if freeze_manifest.get("schema_version") != "1.0":
                    errors.append("[freeze_manifest] schema_version must be '1.0'")
                if freeze_manifest.get("restore_operation") != "launch_state":
                    errors.append("[freeze_manifest] restore_operation must be 'launch_state'")
                if freeze_manifest.get("synchronized_ref_kind") != RefKind.TAG.value:
                    errors.append("[freeze_manifest] synchronized_ref_kind must be 'tag'")
                synchronized_ref_name = freeze_manifest.get("synchronized_ref_name")
                if not isinstance(synchronized_ref_name, str) or not synchronized_ref_name.strip():
                    errors.append("[freeze_manifest] synchronized_ref_name must be a non-empty string")
                for invariant_key in (
                    "immutable_snapshot",
                    "workspace_validated",
                    "ledger_checkpoint",
                ):
                    if freeze_manifest.get(invariant_key) is not True:
                        errors.append(f"[freeze_manifest] {invariant_key} must be true")

        if errors:
            raise ConfigValidationError(
                "Invalid .gts document:\n" + "\n".join(f"  • {e}" for e in errors)
            )

    @property
    def lifecycle_state(self) -> str | None:
        return self.read("tree_state.lifecycle_state")

    @property
    def is_ready(self) -> bool:
        return bool(self.read("tree_state.is_ready", False))

    @property
    def repo_states(self) -> list[dict[str, Any]]:
        return list(self._data.get("repo_state", []))

    @property
    def schema_version(self) -> str:
        value = self.read("document.schema_version")
        if isinstance(value, str) and value:
            return value
        return self.CURRENT_SCHEMA_VERSION

    @property
    def snapshot_hash(self) -> str | None:
        value = self.read("document.snapshot_hash")
        return value if isinstance(value, str) and value else None

    def compute_snapshot_hash(self) -> str:
        canonical_json = json.dumps(
            self._build_canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def ensure_snapshot_hash(self) -> str:
        document = self._data.setdefault("document", {})
        document["schema_version"] = self.CURRENT_SCHEMA_VERSION
        document["hash_algorithm"] = self.HASH_ALGORITHM
        digest = self.compute_snapshot_hash()
        document["snapshot_hash"] = digest
        return digest

    def _build_canonical_payload(self) -> dict[str, Any]:
        project = self._data.get("project", {})
        tree_state = self._data.get("tree_state", {})
        repo_states = self._data.get("repo_state", [])
        freeze_manifest = self._data.get("freeze_manifest", {})
        canonical_repo_states = []
        for repo in repo_states if isinstance(repo_states, list) else []:
            if not isinstance(repo, dict):
                continue
            canonical_repo_states.append(
                {
                    "name": repo.get("name"),
                    "node_type": repo.get("node_type"),
                    "absolute_path": repo.get("absolute_path"),
                    "relative_path": repo.get("relative_path"),
                    "parent_absolute_path": repo.get("parent_absolute_path"),
                    "repo_lifecycle_state": repo.get("repo_lifecycle_state"),
                    "sync_state": repo.get("sync_state"),
                    "current_ref_kind": repo.get("current_ref_kind"),
                    "current_ref_name": repo.get("current_ref_name"),
                    "target_ref_kind": repo.get("target_ref_kind"),
                    "target_ref_name": repo.get("target_ref_name"),
                    "resolved_ref_kind": repo.get("resolved_ref_kind"),
                    "resolved_ref_name": repo.get("resolved_ref_name"),
                    "commit_sha": repo.get("commit_sha"),
                    "project_owner_name": repo.get("project_owner_name"),
                    "project_name": repo.get("project_name"),
                    "repo_name": repo.get("repo_name"),
                    "fallback_branch": repo.get("fallback_branch"),
                    "fallback_applied": repo.get("fallback_applied"),
                    "fallback_reason": repo.get("fallback_reason"),
                    "discovery_state": repo.get("discovery_state"),
                    "worktree_state": repo.get("worktree_state"),
                    "is_reachable": repo.get("is_reachable"),
                    "source_cgs_path": repo.get("source_cgs_path"),
                }
            )
        # Canonical ordering: lexicographic sort on (absolute_path, name).
        canonical_repo_states.sort(
            key=lambda repo: (
                str(repo.get("absolute_path", "")),
                str(repo.get("name", "")),
            )
        )
        payload = {
            "document": {
                "schema_version": self.schema_version,
                "hash_algorithm": self.read("document.hash_algorithm", self.HASH_ALGORITHM),
            },
            "project": {
                "name": project.get("name"),
                "root_absolute_path": project.get("root_absolute_path"),
                "source_cgs_path": project.get("source_cgs_path"),
            },
            "tree_state": {
                "lifecycle_state": tree_state.get("lifecycle_state"),
                "is_ready": tree_state.get("is_ready"),
                "registry_complete": tree_state.get("registry_complete"),
            },
            "repo_state": canonical_repo_states,
        }
        if isinstance(freeze_manifest, dict):
            payload["freeze_manifest"] = {
                "schema_version": freeze_manifest.get("schema_version"),
                "immutable_snapshot": freeze_manifest.get("immutable_snapshot"),
                "workspace_validated": freeze_manifest.get("workspace_validated"),
                "ledger_checkpoint": freeze_manifest.get("ledger_checkpoint"),
                "synchronized_ref_kind": freeze_manifest.get("synchronized_ref_kind"),
                "synchronized_ref_name": freeze_manifest.get("synchronized_ref_name"),
                "restore_operation": freeze_manifest.get("restore_operation"),
            }
        return payload


_VALID_GOC_COMMANDS = frozenset(
    {
        "clone",
        "checkout",
        "pull",
        "add",
        "commit",
        "push",
        "tag",
        "freeze",
    }
)


class GocDocument(ConfigDocument):
    """Parser and validator for ``.goc`` Git Orchestration Command files.

    A ``.goc`` file is a TOML document that defines a **sequence of
    ``cgitsync`` commands** to execute against a project.
    """

    DOCUMENT_KIND = "goc"

    _REQUIRED_DOCUMENT_KEYS = ("format_version",)
    _REQUIRED_PROJECT_KEYS = ("source",)
    _VALID_INTERACTIONS = frozenset(("interactive", "direct"))
    _VALID_PROFILES = frozenset(("verbose", "whisper_sync"))
    _VALID_TRANSPORTS = frozenset(("ssh", "https"))
    _VALID_PROJECT_GITPROVIDERS = frozenset(("github", "gitlab"))
    _DEFAULT_PROJECT_GITPROVIDER = "github"

    SESSION_DEFAULTS: dict[str, str] = {
        "interaction": "interactive",
        "profile": "verbose",
        "transport": "ssh",
    }

    def validate(self) -> None:
        errors: list[str] = []

        for key in self._REQUIRED_DOCUMENT_KEYS:
            if self.read(f"document.{key}") is None:
                errors.append(f"[document] missing required key: '{key}'")

        for key in self._REQUIRED_PROJECT_KEYS:
            if self.read(f"project.{key}") is None:
                errors.append(f"[project] missing required key: '{key}'")

        project = self._data.get("project", {})
        if not isinstance(project, dict):
            errors.append("[project] must be a table")
            project = {}

        source = self.read("project.source", "")
        if source and not (str(source).endswith(".cgs") or str(source).endswith(".gts")):
            errors.append(
                f"[project].source must be a .cgs or .gts path; got: {source!r}"
            )

        provider = str(project.get("gitprovider", self._DEFAULT_PROJECT_GITPROVIDER))
        if project.get("gitprovider") is not None and provider not in self._VALID_PROJECT_GITPROVIDERS:
            errors.append(
                f"[project].gitprovider invalid: {provider!r} "
                f"(choose from: {sorted(self._VALID_PROJECT_GITPROVIDERS)})"
            )

        identity_fields_present = any(
            project.get(key)
            for key in ("repo_name", "project_owner_name", "group_name", "gitprovider_url")
        )
        if identity_fields_present:
            if not project.get("repo_name"):
                errors.append("[project] missing required key for address composition: 'repo_name'")
            if provider == "github" and not project.get("project_owner_name"):
                errors.append(
                    "[project].project_owner_name is required when [project].gitprovider is 'github'"
                )
            if provider == "gitlab" and not (project.get("group_name") or project.get("project_owner_name")):
                errors.append(
                    "[project].group_name or [project].project_owner_name is required when "
                    "[project].gitprovider is 'gitlab'"
                )

        interaction = self.read("session.interaction", self.SESSION_DEFAULTS["interaction"])
        if interaction not in self._VALID_INTERACTIONS:
            errors.append(
                f"[session].interaction invalid: {interaction!r} "
                f"(choose from: {sorted(self._VALID_INTERACTIONS)})"
            )

        profile = self.read("session.profile", self.SESSION_DEFAULTS["profile"])
        if profile not in self._VALID_PROFILES:
            errors.append(
                f"[session].profile invalid: {profile!r} "
                f"(choose from: {sorted(self._VALID_PROFILES)})"
            )

        transport = self.read("session.transport", self.SESSION_DEFAULTS["transport"])
        if transport not in self._VALID_TRANSPORTS:
            errors.append(
                f"[session].transport invalid: {transport!r} "
                f"(choose from: {sorted(self._VALID_TRANSPORTS)})"
            )

        actions = self._data.get("actions", [])
        if not isinstance(actions, list) or len(actions) == 0:
            errors.append("'actions' must be a non-empty array of tables ([[actions]])")
        else:
            for idx, action in enumerate(actions):
                if not isinstance(action, dict):
                    errors.append(f"actions[{idx}] must be a table")
                    continue
                cmd = action.get("command")
                if not cmd:
                    errors.append(f"actions[{idx}] missing required key: 'command'")
                elif cmd not in _VALID_GOC_COMMANDS:
                    errors.append(
                        f"actions[{idx}].command unknown: {cmd!r} "
                        f"(valid commands: {sorted(_VALID_GOC_COMMANDS)})"
                    )

        if errors:
            raise ConfigValidationError(
                "Invalid .goc document:\n" + "\n".join(f"  • {e}" for e in errors)
            )

    @property
    def project_source(self) -> str | None:
        return self.read("project.source")

    @property
    def project_name(self) -> str | None:
        return self.read("project.name")

    @property
    def project_repo_name(self) -> str | None:
        return self.read("project.repo_name")

    @property
    def project_gitprovider_address(self) -> str | None:
        return self._compose_project_gitprovider_address()

    @property
    def interaction(self) -> str:
        return self.read("session.interaction", self.SESSION_DEFAULTS["interaction"])

    @property
    def profile(self) -> str:
        return self.read("session.profile", self.SESSION_DEFAULTS["profile"])

    @property
    def transport(self) -> str:
        return self.read("session.transport", self.SESSION_DEFAULTS["transport"])

    @property
    def actions(self) -> list[dict[str, Any]]:
        return list(self._data.get("actions", []))

    def session_setting(self, key: str) -> Any:
        return self._data.get("session", {}).get(key, self.SESSION_DEFAULTS.get(key))

    def _compose_project_gitprovider_address(self) -> str | None:
        project = self._data.get("project", {})
        if not isinstance(project, dict):
            return None
        repo_name = project.get("repo_name")
        if not repo_name:
            return None
        provider = str(project.get("gitprovider", self._DEFAULT_PROJECT_GITPROVIDER))
        host = self._resolve_provider_host(provider, project.get("gitprovider_url"))
        if provider == "github":
            namespace = project.get("project_owner_name")
        elif provider == "gitlab":
            namespace = project.get("group_name") or project.get("project_owner_name")
        else:
            return None
        if not namespace:
            return None
        if self.transport == "ssh":
            return f"git@{host}:{namespace}/{repo_name}.git"
        return f"https://{host}/{namespace}/{repo_name}.git"

    @staticmethod
    def _resolve_provider_host(gitprovider: str, gitprovider_url: Any) -> str:
        if gitprovider_url:
            base = str(gitprovider_url).strip()
            parsed = urlsplit(base if "://" in base else f"https://{base}")
            host = parsed.netloc or parsed.path.strip("/").split("/", 1)[0]
            if host:
                return host
        if gitprovider == "gitlab":
            return "gitlab.com"
        return "github.com"


# ============================================================
#  Infrastructure — CommandRunLogger, RuntimeStateStore, GitRunner
# ============================================================


class CommandRunLogger:
    """Structured JSON logger for a single ComplexGitSync command run."""

    def __init__(self, logger: logging.Logger, *, log_path: Path | None = None) -> None:
        self._logger = logger
        self.log_path = log_path

    def log_event(self, event: str, *, level: int = logging.INFO, **fields: object) -> None:
        """Log *event* together with arbitrary keyword *fields* as a JSON record."""
        record: dict[str, Any] = {"event": event}
        for key, value in fields.items():
            if isinstance(value, (str, int, float, bool, type(None))):
                record[key] = value
            else:
                record[key] = str(value)
        self._logger.log(level, json.dumps(record, default=str))


def create_run_logger(
    command_name: str,
    *,
    profile: str = "quiet",
    source_path: Path | None = None,
    project_root: Path | None = None,
    project_log_dir: Any = None,
) -> CommandRunLogger:
    """Create a :class:`CommandRunLogger` for a specific command invocation."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_filename = f"{timestamp}-{command_name}.log"
    log_dir = _resolve_log_dir(project_root, project_log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename

    logger_name = f"ComplexGitSync.run.{command_name}.{timestamp}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(fh)

    console_level = logging.INFO if profile == "verbose" else logging.WARNING
    ch = logging.StreamHandler()
    ch.setLevel(console_level)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    return CommandRunLogger(logger, log_path=log_path)


def _resolve_log_dir(project_root: Path | None, project_log_dir: Any) -> Path:
    if project_root is not None and project_log_dir:
        return (project_root / str(project_log_dir)).resolve()
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "ComplexGitSync" / "logs"
    return Path.home() / ".local" / "state" / "ComplexGitSync" / "logs"


class RuntimeStateStore:
    """Persistent registry that maps ``.cgs`` files to their latest ``.gts`` snapshots."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is None:
            base_dir = _resolve_state_base_dir()
        self.base_dir = Path(base_dir)

    def latest_snapshot_for(self, cgs_path: Path | str) -> Path | None:
        """Return the path to the latest snapshot for *cgs_path*, or ``None``."""
        record_path = self._record_path(Path(cgs_path).resolve())
        if not record_path.is_file():
            return None
        snapshot_path = Path(record_path.read_text(encoding="utf-8").strip())
        if snapshot_path.is_file():
            return snapshot_path
        return None

    def record_snapshot(self, cgs_path: Path | str, snapshot_path: Path | str) -> None:
        """Record *snapshot_path* as the latest snapshot for *cgs_path*."""
        record_path = self._record_path(Path(cgs_path).resolve())
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(str(Path(snapshot_path).resolve()), encoding="utf-8")

    def _record_path(self, resolved_cgs_path: Path) -> Path:
        key = hashlib.sha256(str(resolved_cgs_path).encode()).hexdigest()[:24]
        return self.base_dir / f"{key}.ptr"


def _resolve_state_base_dir() -> Path:
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "ComplexGitSync" / "snapshots"
    return Path.home() / ".local" / "state" / "ComplexGitSync" / "snapshots"


class LocalGitRegister:
    """Project-local ``.lgr`` register for generated ``.gts`` snapshots.

    The TOML structure keeps:
    - a ``[register]`` section for the current snapshot pointer, and
    - a ``[[snapshots]]`` list for stable ``gts-XXXXXX`` identifiers.

    Snapshot entries are deduplicated by snapshot file hash.
    """

    _HASH_CHUNK_SIZE = 65536

    def __init__(self, register_path: Path | str) -> None:
        self.register_path = Path(register_path)

    def record_snapshot(self, snapshot_path: Path | str) -> str:
        resolved_snapshot_path = Path(snapshot_path).resolve()
        snapshot_hash = self._hash_snapshot_file(resolved_snapshot_path)

        data = self._load()
        snapshots = data.setdefault("snapshots", [])
        snapshot_index = {
            str(entry.get("snapshot_hash")): entry
            for entry in snapshots
            if isinstance(entry, dict) and entry.get("snapshot_hash")
        }
        existing = snapshot_index.get(snapshot_hash)

        if existing is None:
            snapshot_id = self._next_snapshot_id(snapshots)
            recorded_at = (
                datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
            snapshots.append(
                {
                    "id": snapshot_id,
                    "snapshot_hash": snapshot_hash,
                    "snapshot_path": _path_to_environment_marker(resolved_snapshot_path),
                    "recorded_at": recorded_at,
                }
            )
        else:
            snapshot_id = str(existing["id"])
            existing["snapshot_path"] = _path_to_environment_marker(resolved_snapshot_path)

        register = data.setdefault("register", {})
        register["current_snapshot_id"] = snapshot_id
        register["current_snapshot_hash"] = snapshot_hash
        register["current_snapshot_path"] = _path_to_environment_marker(resolved_snapshot_path)

        self.register_path.parent.mkdir(parents=True, exist_ok=True)
        self.register_path.write_text(tomli_w.dumps(data), encoding="utf-8")
        return snapshot_id

    def _load(self) -> dict[str, Any]:
        if not self.register_path.is_file():
            return {"register": {}, "snapshots": []}
        return tomllib.loads(self.register_path.read_text(encoding="utf-8"))

    def _next_snapshot_id(self, snapshots: list[dict[str, Any]]) -> str:
        """Return the next sequential local id in ``gts-XXXXXX`` format."""
        max_id = 0
        for entry in snapshots:
            raw_id = str(entry.get("id", ""))
            if raw_id.startswith("gts-"):
                try:
                    max_id = max(max_id, int(raw_id.removeprefix("gts-")))
                except ValueError:
                    continue
        return f"gts-{max_id + 1:06d}"

    def _hash_snapshot_file(self, snapshot_path: Path) -> str:
        """Compute a canonical snapshot hash for ``snapshot_path``."""
        try:
            document = GtsDocument.from_toml(snapshot_path)
        except (OSError, tomllib.TOMLDecodeError, ConfigValidationError):
            digest = hashlib.sha256()
            with snapshot_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(self._HASH_CHUNK_SIZE), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        if document.snapshot_hash:
            return document.snapshot_hash
        return document.compute_snapshot_hash()


def _get_actor() -> str:
    """Return the current system user name, or ``'unknown'`` on failure."""
    try:
        import getpass

        return getpass.getuser()
    except Exception:  # pragma: no cover
        return "unknown"


def _topological_sort_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return *events* in topological order (parents before children).

    Uses Kahn's BFS algorithm on the ``parent_sync_ids`` graph.
    Events without a valid ``sync_id`` are appended last, preserving their
    original relative order.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        if isinstance(event, dict):
            sid = str(event.get("sync_id", ""))
            if sid:
                by_id[sid] = event

    in_degree: dict[str, int] = {sid: 0 for sid in by_id}
    children: dict[str, list[str]] = {sid: [] for sid in by_id}

    for sid, event in by_id.items():
        for parent_id in event.get("parent_sync_ids", []):
            parent_str = str(parent_id)
            if parent_str in by_id:
                in_degree[sid] += 1
                children[parent_str].append(sid)

    queue: list[str] = sorted(sid for sid, deg in in_degree.items() if deg == 0)
    result: list[dict[str, Any]] = []
    while queue:
        current = queue.pop(0)
        result.append(by_id[current])
        for child in sorted(children.get(current, [])):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    # Append any events not reachable via the DAG (malformed entries)
    seen: set[str] = {str(e.get("sync_id", "")) for e in result}
    for event in events:
        if not isinstance(event, dict) or str(event.get("sync_id", "")) not in seen:
            result.append(event)

    return result


class SyncLedger:
    """Append-only DAG ledger for synchronisation operations in the ``.lgr`` file.

    Extends the :class:`LocalGitRegister` format with a ``[[ledger]]``
    section that records each synchronisation operation as an immutable
    DAG event.  Events are linked via ``parent_sync_ids`` to form a
    directed acyclic graph that reconstructs workspace evolution history.

    Schema for each ledger event:

    .. code-block:: toml

        [[ledger]]
        sync_id         = "lgr-000001"
        parent_sync_ids = []              # empty list for the first event
        operation       = "clone"
        timestamp       = "2026-05-20T19:48:50.159Z"
        actor           = "user"
        workspace_hash  = "<sha256>"      # document.snapshot_hash from .gts
        gts_snapshot_id = "gts-000001"    # links to [[snapshots]] entry
        affected_repos  = ["demo", "dep"]

    ``workspace_hash`` is the canonical SHA-256 digest of the ``.gts``
    snapshot (``GtsDocument.snapshot_hash``), linking each event directly
    to the immutable workspace state it records.
    """

    def __init__(self, register_path: Path | str) -> None:
        self.register_path = Path(register_path)

    def record_event(
        self,
        *,
        operation: str,
        workspace_hash: str,
        gts_snapshot_id: str,
        affected_repos: list[str],
        actor: str | None = None,
    ) -> str:
        """Append an immutable event to the ledger and return the new ``sync_id``.

        Parameters
        ----------
        operation:
            The synchronisation operation that produced this event (e.g.
            ``"clone"``, ``"freeze_release"``, ``"checkout"``).
        workspace_hash:
            The canonical SHA-256 snapshot hash (``GtsDocument.snapshot_hash``)
            that identifies the workspace state after the operation.
        gts_snapshot_id:
            The local snapshot id (``gts-XXXXXX``) assigned by the
            :class:`LocalGitRegister` for the same ``.gts`` file.
        affected_repos:
            Ordered list of repository names involved in the operation.
        actor:
            The system user or process that triggered the operation.  When
            ``None``, the current OS user name is detected automatically.
        """
        data = self._load()
        events: list[dict[str, Any]] = data.setdefault("ledger", [])

        sync_id = self._next_event_id(events)
        parent_ids: list[str] = (
            [str(events[-1]["sync_id"])] if events and isinstance(events[-1], dict) and events[-1].get("sync_id") else []
        )

        timestamp = (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        resolved_actor = actor if actor is not None else _get_actor()

        events.append(
            {
                "sync_id": sync_id,
                "parent_sync_ids": parent_ids,
                "operation": operation,
                "timestamp": timestamp,
                "actor": resolved_actor,
                "workspace_hash": workspace_hash,
                "gts_snapshot_id": gts_snapshot_id,
                "affected_repos": affected_repos,
            }
        )

        self.register_path.parent.mkdir(parents=True, exist_ok=True)
        self.register_path.write_text(tomli_w.dumps(data), encoding="utf-8")
        return sync_id

    def history(self) -> list[dict[str, Any]]:
        """Return all ledger events in topological DAG order (parents first)."""
        data = self._load()
        return _topological_sort_events(list(data.get("ledger", [])))

    def replay(self) -> list[dict[str, Any]]:
        """Return events in topological order for deterministic replay.

        Alias for :meth:`history`.  Iterating the result in sequence
        reconstructs the workspace evolution from first operation to last.
        """
        return self.history()

    def _load(self) -> dict[str, Any]:
        if not self.register_path.is_file():
            return {"register": {}, "snapshots": [], "ledger": []}
        return tomllib.loads(self.register_path.read_text(encoding="utf-8"))

    def _next_event_id(self, events: list[dict[str, Any]]) -> str:
        """Return the next sequential event id in ``lgr-XXXXXX`` format."""
        max_id = 0
        for entry in events:
            raw_id = str(entry.get("sync_id", ""))
            if raw_id.startswith("lgr-"):
                try:
                    max_id = max(max_id, int(raw_id.removeprefix("lgr-")))
                except ValueError:
                    continue
        return f"lgr-{max_id + 1:06d}"


@dataclass(slots=True)
class GitRunner:
    """Git subprocess wrapper — executes git commands for clone/checkout/push actions."""

    executable: str = "git"

    def remote_branch_exists(self, remote_url: str, branch: str) -> bool:
        return self._remote_ref_exists(remote_url, "--heads", branch)

    def remote_tag_exists(self, remote_url: str, tag: str) -> bool:
        return self._remote_ref_exists(remote_url, "--tags", tag)

    def _remote_ref_exists(self, remote_url: str, ref_selector: str, ref_name: str) -> bool:
        completed = self._run("ls-remote", ref_selector, remote_url, ref_name)
        return bool(completed.stdout.strip())

    def clone(self, remote_url: str, destination: Path | str, *, branch: str) -> None:
        destination_path = Path(destination)
        if destination_path.exists():
            if not destination_path.is_dir() or any(destination_path.iterdir()):
                raise GitSyncError(
                    f"Clone destination already exists and is not empty: {destination_path}"
                )
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        args: list[str] = []
        if self._uses_file_transport(remote_url):
            args.extend(["-c", "protocol.file.allow=always"])
        args.extend(
            ["clone", "--branch", branch, "--single-branch", remote_url, str(destination_path)]
        )
        self._run(*args)

    def rev_parse_head(self, repo_path: Path | str) -> str:
        return self._run("rev-parse", "HEAD", cwd=repo_path).stdout.strip()

    def current_branch(self, repo_path: Path | str) -> str | None:
        branch = self._run("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_path).stdout.strip()
        return None if branch == "HEAD" else branch

    def local_branch_exists(self, repo_path: Path | str, branch: str) -> bool:
        """Return ``True`` if *branch* exists as a local branch in *repo_path*."""
        try:
            self._run("rev-parse", "--verify", f"refs/heads/{branch}", cwd=repo_path)
            return True
        except GitSyncError:
            return False

    def create_branch(self, repo_path: Path | str, branch: str) -> None:
        """Create *branch* in *repo_path* without switching to it (``git branch``)."""
        self._run("branch", branch, cwd=repo_path)

    def checkout(self, repo_path: Path | str, branch: str) -> None:
        """Switch *repo_path* to *branch* (``git checkout``)."""
        self._run("checkout", branch, cwd=repo_path)

    def has_uncommitted_changes(self, repo_path: Path | str) -> bool:
        """Return ``True`` if *repo_path* has any tracked or staged modifications."""
        result = self._run("status", "--porcelain", cwd=repo_path)
        return bool(result.stdout.strip())

    def status_porcelain(self, repo_path: Path | str) -> list[str]:
        """Return ``git status --porcelain`` lines for *repo_path*."""
        result = self._run("status", "--porcelain", cwd=repo_path)
        return [line for line in result.stdout.splitlines() if line.strip()]

    def tracked_gitlink_paths(self, repo_path: Path | str) -> set[Path]:
        """Return paths tracked as gitlinks (mode ``160000``) in *repo_path*."""
        result = self._run("ls-files", "--stage", cwd=repo_path)
        gitlinks: set[Path] = set()
        for line in result.stdout.splitlines():
            if not line.startswith("160000 "):
                continue
            try:
                path = line.split("\t", 1)[1]
            except IndexError:
                continue
            gitlinks.add(Path(path))
        return gitlinks

    def has_staged_changes(self, repo_path: Path | str) -> bool:
        """Return ``True`` if *repo_path* has changes staged for the next commit."""
        result = self._run("diff", "--cached", "--name-only", cwd=repo_path)
        return bool(result.stdout.strip())

    def stage_all(self, repo_path: Path | str) -> None:
        """Stage all changes in *repo_path* (``git add --all``)."""
        self._run("add", "--all", cwd=repo_path)

    def commit(self, repo_path: Path | str, message: str) -> None:
        """Commit staged changes in *repo_path* with *message* (``git commit``)."""
        self._run("commit", "-m", message, cwd=repo_path)

    def push(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
        set_upstream: bool = False,
    ) -> None:
        """Push *remote* (and optionally *ref_name*) in *repo_path* (``git push``)."""
        args = ["push"]
        if set_upstream:
            args.append("-u")
        args.append(remote)
        if ref_name:
            args.append(ref_name)
        self._run(*args, cwd=repo_path)

    def pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None:
        """Pull *remote* (and optionally *ref_name*) in *repo_path* (``git pull --ff-only``)."""
        args = ["pull", "--ff-only", remote]
        if ref_name:
            args.append(ref_name)
        self._run(*args, cwd=repo_path)

    def create_tag(self, repo_path: Path | str, tag_name: str) -> None:
        """Create *tag_name* in *repo_path*."""
        self._run("tag", tag_name, cwd=repo_path)

    def add_submodule(
        self,
        repo_path: Path | str,
        remote_url: str,
        relative_path: Path | str,
        *,
        branch: str,
    ) -> None:
        """Add a submodule in *repo_path* at *relative_path* pinned to *branch*."""
        args: list[str] = []
        if self._uses_file_transport(remote_url):
            args.extend(["-c", "protocol.file.allow=always"])
        args.extend(
            [
                "submodule",
                "add",
                "-b",
                branch,
                remote_url,
                str(relative_path),
            ]
        )
        self._run(*args, cwd=repo_path)

    def update_submodule(self, repo_path: Path | str, relative_path: Path | str) -> None:
        """Sync and update a tracked submodule path from its parent repository."""
        submodule_path = str(relative_path)
        self._run("submodule", "sync", "--", submodule_path, cwd=repo_path)
        self._run(
            "submodule",
            "update",
            "--init",
            "--remote",
            "--",
            submodule_path,
            cwd=repo_path,
        )

    def remote_exists(self, repo_path: Path | str, remote: str = "origin") -> bool:
        """Return ``True`` when *remote* exists in *repo_path*."""
        try:
            self._run("remote", "get-url", remote, cwd=repo_path)
            return True
        except GitSyncError:
            return False

    def tag_exists(self, repo_path: Path | str, tag_name: str) -> bool:
        """Return ``True`` when *tag_name* already exists in *repo_path*."""
        completed = subprocess.run(
            [self.executable, "show-ref", "--verify", "--quiet", f"refs/tags/{tag_name}"],
            cwd=str(repo_path),
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode == 0:
            return True
        if completed.returncode == 1:
            return False
        command = f"{self.executable} show-ref --verify refs/tags/{tag_name}"
        details = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise GitSyncError(f"Git command failed ({command}): {details}")

    def has_unresolved_merge(self, repo_path: Path | str) -> bool:
        """Return ``True`` when *repo_path* has an in-progress merge conflict."""
        completed = subprocess.run(
            [self.executable, "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode == 0:
            return True
        if completed.returncode == 1:
            return False
        command = f"{self.executable} rev-parse --verify --quiet MERGE_HEAD"
        details = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise GitSyncError(f"Git command failed ({command}): {details}")

    def branch_tracking_state(self, repo_path: Path | str) -> SyncState | None:
        """Return upstream tracking state for the current branch in *repo_path*."""
        upstream = subprocess.run(
            [self.executable, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            cwd=str(repo_path),
            capture_output=True,
            check=False,
            text=True,
        )
        if upstream.returncode != 0:
            return None
        counts = self._run("rev-list", "--left-right", "--count", "HEAD...@{upstream}", cwd=repo_path)
        ahead_raw, behind_raw = counts.stdout.strip().split()
        ahead = int(ahead_raw)
        behind = int(behind_raw)
        if ahead and behind:
            return SyncState.DIVERGED
        if ahead:
            return SyncState.AHEAD
        if behind:
            return SyncState.BEHIND
        return SyncState.ALIGNED

    def has_upstream(self, repo_path: Path | str) -> bool:
        """Return ``True`` when the current branch has an upstream configured."""
        upstream = subprocess.run(
            [self.executable, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            cwd=str(repo_path),
            capture_output=True,
            check=False,
            text=True,
        )
        return upstream.returncode == 0

    def is_submodule(self, repo_path: Path | str, relative_path: Path | str) -> bool:
        """Return ``True`` when *relative_path* is tracked as a git submodule."""
        result = self._run("ls-files", "--stage", "--", str(relative_path), cwd=repo_path)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return False
        mode = lines[0].split(maxsplit=1)[0]
        return mode == "160000"

    def _run(
        self,
        *args: str,
        cwd: Path | str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [self.executable, *args],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            command = " ".join([self.executable, *args])
            details = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
            raise GitSyncError(f"Git command failed ({command}): {details}")
        return completed

    @staticmethod
    def _uses_file_transport(remote_url: str) -> bool:
        parsed = urlsplit(remote_url)
        if parsed.scheme == "file":
            return True
        if (
            len(parsed.scheme) == 1
            and len(remote_url) >= 2
            and remote_url[1] == ":"
            and parsed.scheme.isalpha()
        ):
            return True
        if parsed.scheme:
            return False
        return bool(remote_url) and not remote_url.startswith("git@")


# ============================================================
#  Registry builders — translate documents ↔ DependencyTreeRegistry
# ============================================================


def build_registry_from_cgs_document(
    document: CgsDocument,
    config_path: Path | str,
    *,
    project_root: Path | str | None = None,
) -> DependencyTreeRegistry:
    """Build a :class:`DependencyTreeRegistry` from a ``.cgs`` document."""
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

        target_kind, target_name = _resolve_repo_target_ref(
            repo,
            document_default_branch=document.default_branch,
        )
        entry = RepoRegistryEntry(
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

    registry.recompute_tree_state()
    return registry


def build_registry_from_gts_document(document: GtsDocument) -> DependencyTreeRegistry:
    """Build a :class:`DependencyTreeRegistry` from a ``.gts`` snapshot document."""
    registry = DependencyTreeRegistry()
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

        entry = RepoRegistryEntry(
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
            repo_name=(
                _as_optional_str(repo_state.get("repo_name"))
                if repo_state.get("repo_name") is not None
                else _as_optional_str(repo_state.get("project_name"))
            ),
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
    """Build a :class:`GtsDocument` from the live *registry*."""
    root_entry = registry.get(ROOT_REPO_ID)
    tree_state = build_tree_state(registry)
    data: dict[str, Any] = {
        "document": {
            "format_version": "1.0",
            "schema_version": GtsDocument.CURRENT_SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "command_origin": command_origin,
            "hash_algorithm": GtsDocument.HASH_ALGORITHM,
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
        "repo_state": [],
    }
    if source_cgs_path is not None:
        data["project"]["source_cgs_path"] = _path_to_environment_marker(source_cgs_path)
    if command_origin in _FREEZE_COMMAND_ORIGINS:
        data["freeze_manifest"] = _build_freeze_manifest(registry)

    for entry in sorted(registry.values(), key=lambda item: item.repo_id):
        repo_data: dict[str, Any] = {
            "name": entry.name,
            "node_type": entry.node_type.value,
            "absolute_path": _path_to_environment_marker(entry.absolute_path),
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
            "source_cgs_path": (
                _path_to_environment_marker(entry.source_cgs_path) if entry.source_cgs_path else None
            ),
            "project_owner_name": entry.project_owner_name,
            "project_name": entry.project_name,
            "repo_name": entry.repo_name,
        }
        if entry.parent_id is not None:
            repo_data["parent_absolute_path"] = _path_to_environment_marker(
                registry.get(entry.parent_id).absolute_path
            )
        data["repo_state"].append({key: value for key, value in repo_data.items() if value is not None})

    document = GtsDocument.from_dict(data)
    document.ensure_snapshot_hash()
    document.validate()
    return document


def _build_freeze_manifest(registry: DependencyTreeRegistry) -> dict[str, Any]:
    root_entry = registry.get(ROOT_REPO_ID)
    tag_name = (
        root_entry.resolved_ref_name
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
        "restore_operation": "launch_state",
    }


# ============================================================
#  Nested config discovery
# ============================================================


def discover_nested_configs(registry: DependencyTreeRegistry) -> tuple[str, ...]:
    """Discover nested ``.cgs`` files in already-cloned repositories."""
    changes: list[str] = []
    pending_entries = [
        entry
        for entry in registry.values()
        if entry.repo_id != ROOT_REPO_ID and entry.nested_config not in {None, "disabled"}
    ]

    # Pre-build a set of all known absolute paths for O(1) circularity detection.
    # Updated in-place as new entries are added during this call.
    registered_paths: set[Path] = {e.absolute_path for e in registry.values() if e.absolute_path is not None}

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
            register_relative_path(
                existing_child_paths,
                relative_path,
                error_type=NestedConfigDiscoveryError,
                context=str(entry.absolute_path),
            )

            child_id = make_repo_id(entry.repo_id, relative_path, str(repo["project_name"]))
            if child_id in registry.entries:
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
                RepoRegistryEntry(
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
    tag = _as_optional_str(repo.get("tag"))
    if tag:
        return (RefKind.TAG, tag)
    branch = _as_optional_str(repo.get("branch")) or _as_optional_str(repo.get("default_branch"))
    if branch is None:
        branch = document_default_branch or "main"
    return (RefKind.BRANCH, branch)


# ============================================================
#  Orchestre — coordination layer
# ============================================================


@dataclass(slots=True)
class Orchestre:
    """Coordination layer — owns exactly one :class:`GitTree`.

    Acts as the bridge between the GitTree core model and the
    :class:`ComplexGitSyncClient` public API.
    """

    git_tree: GitTree = field(default_factory=GitTree)

    def register_repo(self, repo: GitRepo) -> None:
        self.git_tree.add_repo(repo)


# ============================================================
#  ComplexGitSyncClient — public API facade (Tier 3)
# ============================================================


@dataclass
class ComplexGitSyncClient:
    """Client facade exposing the documented lifecycle surface.

    Every action method checks the current :class:`TreeLifecycleState` before
    executing.  Mutation actions (commit, push, tag, freeze_release) require a
    READY tree and will raise :exc:`~.errors.TreeNotReadyError` otherwise.

    The canonical user-facing lifecycle is::

        initialise(.cgs)  → clone all repos → READY  (new project)
        initialise(.gts)  → restore snapshot → READY  (existing project)
        pull(.cgs/.gts)   → resync existing tree
        checkout(branch)
        add()
        git(tree, "commit", msg)
        git(tree, "push")
        git(tree, "tag", name)
        freeze(name)      → emit the next .gts id

    ``load()`` accepts both ``.cgs`` and ``.gts`` sources for direct Python
    API access.  Compatibility aliases: ``read`` → ``load``,
    ``verify`` → ``validate``.
    """

    orchestre: Orchestre = field(default_factory=Orchestre)
    git_runner: GitRunner = field(default_factory=GitRunner)
    state_store: RuntimeStateStore = field(default_factory=RuntimeStateStore)
    registry: DependencyTreeRegistry | None = None
    source_path: Path | None = None
    loaded_snapshot_path: Path | None = None
    run_logger: CommandRunLogger | None = None

    def is_loaded(self) -> bool:
        return self.registry is not None or bool(self.orchestre.git_tree.repos)

    def load_cgs(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> DependencyTreeRegistry:
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        self.registry = build_registry_from_cgs_document(document, source_path)
        self.orchestre.git_tree.git.bind_registry(self.registry)
        self.source_path = source_path
        self.loaded_snapshot_path = None
        if discover_nested:
            discovered = self.discover_nested_configs()
            self._log_nested_discovery(discovered)
        self._log_tree_transition(previous_tree_state, self.registry.lifecycle_state, reason="load_cgs")
        return self.registry

    def initialise(
        self,
        source: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> DependencyTreeRegistry:
        """Unified initialisation entry point (lifecycle step 1).

        Dispatches based on source file extension:

        - ``.cgs`` source: initialises the workspace using CGSPATH/CGSHOME
          semantics (calls :meth:`initialise_cgs`).  The output path is
          CGSPATH, and CGSHOME is derived as ``CGSPATH/<project_name>`` after
          reading the ``.cgs``.  The root repository at CGSHOME is treated as
          already existing and is never recloned.  All ComplexGitSync state is
          written under ``CGSHOME/.cgitsync/state/``.
        - ``.gts`` source: restores from a saved snapshot (calls
          :meth:`load_gts`).  Use this for existing projects that already have
          a ``.gts`` state file.

        Both paths end in a ``READY`` tree or raise explicitly.

        Parameters
        ----------
        source:
            Path to a ``.cgs`` authoring spec (clone mode) or a ``.gts``
            snapshot (restore mode).
        output_path:
            CGSPATH — parent directory used to derive CGSHOME as
            ``CGSPATH/<project_name>`` after the ``.cgs`` is read.  Defaults to
            ``../..`` relative to the current working directory
            (``CWD=$CGSHOME/ComplexGitSync``).
        """
        resolved = Path(source).resolve()
        if resolved.suffix == ".cgs":
            return self.initialise_cgs(resolved, output_path=output_path)
        if resolved.suffix == ".gts":
            return self.load_gts(resolved)
        raise ValueError(
            f"Unsupported source format '{resolved.suffix}' for {resolved!s}; expected .cgs or .gts."
        )

    def initialise_cgs(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> DependencyTreeRegistry:
        """Initialise a workspace using CGSPATH/CGSHOME semantics.

        ``output_path`` is CGSPATH.  The ``.cgs`` file is read first, CGSHOME
        is derived as ``CGSPATH/<project_name>``, and that root repository is
        treated as already existing.  The clone sequence runs only for the
        dependencies declared in the ``.cgs`` document.

        All ComplexGitSync state is stored under
        ``CGSHOME/.cgitsync/state/``.

        Parameters
        ----------
        config_path:
            Path to the ``.cgs`` authoring spec.
        output_path:
            CGSPATH — parent directory used to derive CGSHOME as
            ``CGSPATH/<project_name>``.  When *None*, defaults to ``../..``
            relative to the current working directory
            (``CWD=$CGSHOME/ComplexGitSync``), unless ``CGSHOME`` is set.
        """
        previous_tree_state = (
            self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        )
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        cgshome = self.resolve_cgshome(document, source_path, output_path=output_path)
        project_root = cgshome

        self.registry = build_registry_from_cgs_document(
            document,
            source_path,
            project_root=project_root,
        )
        self.orchestre.git_tree.git.bind_registry(self.registry)
        self.source_path = source_path

        root_entry = self.registry.get(ROOT_REPO_ID)
        self._attach_existing_root(root_entry, project_root)

        # Root is already checked out at CGSHOME; initialise clones only the
        # dependencies declared by the .cgs.
        sync_stack: set[Path] = {project_root}

        while True:
            cloned_any = False
            for entry in self._pending_clone_entries(sync_stack):
                sync_stack.add(entry.absolute_path)
                self._clone_registry_entry(entry)
                cloned_any = True

            discovered = self.discover_nested_configs()
            self._log_nested_discovery(discovered)
            if not cloned_any and not discovered:
                break

        fixed = self.fix_circularities()
        if fixed:
            self._log_circularity_fixes(fixed)
        self._assert_nested_discovery_complete()
        self.registry.recompute_tree_state()
        if not self.registry.is_ready():
            raise GitSyncError("Initialise did not produce a READY tree.")

        # Write the snapshot under CGSHOME.
        snapshot_name = f"{self.source_path.stem if self.source_path else root_entry.name}.gts"
        snapshot_output = cgshome / ".cgitsync" / "state" / snapshot_name
        snapshot_path = self.write_gts_snapshot(
            command_origin="clone", output_path=snapshot_output
        )
        self.state_store.record_snapshot(source_path, snapshot_path)
        self._log_tree_transition(
            previous_tree_state, self.registry.lifecycle_state, reason="initialise_cgs"
        )
        return self.registry

    def resolve_cgshome(
        self,
        document: CgsDocument,
        source_path: Path,
        *,
        output_path: str | Path | None = None,
    ) -> Path:
        """Resolve CGSHOME from CGSPATH, the environment, or CWD."""
        if output_path is not None:
            cgspath = Path(output_path).expanduser().resolve()
            return (cgspath / (document.project_name or source_path.stem)).resolve()
        env_cgshome = os.environ.get("CGSHOME")
        if env_cgshome:
            return Path(env_cgshome).expanduser().resolve()
        cgspath = (Path.cwd() / "../..").resolve()
        return (cgspath / (document.project_name or source_path.stem)).resolve()

    def resolve_initialise_cgshome(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> Path:
        """Read a .cgs file and resolve the CGSHOME initialise will use."""
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        return self.resolve_cgshome(document, source_path, output_path=output_path)

    def load(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> DependencyTreeRegistry:
        """Load a ``.cgs`` or ``.gts`` source into the registry.

        Accepts both file types:

        - ``.gts`` snapshot: loaded directly via :meth:`load_gts`.
        - ``.cgs`` specification: parsed via :meth:`load_cgs` and writes a
          ``.gts`` snapshot for later use with ``print`` and other commands.

        Parameters
        ----------
        source_path:
            Path to a ``.cgs`` authoring file or a ``.gts`` snapshot.
        discover_nested:
            When ``True``, run nested ``.cgs`` discovery for ``.cgs`` sources.
        """
        resolved = Path(source_path).resolve()
        if resolved.suffix == ".gts":
            return self.load_gts(resolved)
        registry = self.load_cgs(resolved, discover_nested=discover_nested)
        snapshot_path = self.write_gts_snapshot(command_origin="load")
        self.state_store.record_snapshot(resolved, snapshot_path)
        return registry

    def read(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> DependencyTreeRegistry:
        """Compatibility alias for :meth:`load`.

        ``load`` is the canonical name; ``read`` is retained for
        backward compatibility.
        """
        return self.load(config_path, discover_nested=discover_nested)

    def expand(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = True,
    ) -> str:
        """Expand the dependency tree (lifecycle step 2: LOADED → PENDING).

        Loads the source (``.cgs`` or ``.gts``), runs nested ``.cgs``
        discovery from parents to leaves (recursive), resolves any circularities
        that arise when leaves reference repos already registered as parents, and
        returns a formatted text rendering of the dependency tree.

        Parameters
        ----------
        source_path:
            Path to the ``.cgs`` specification or a previously-written
            ``.gts`` snapshot.
        discover_nested:
            When ``True`` (default) run nested ``.cgs`` discovery for child
            repositories that have not yet been resolved.
        """
        resolved = Path(source_path).resolve()
        if resolved.suffix == ".gts":
            self.load_gts(resolved)
        else:
            self.load_cgs(resolved, discover_nested=discover_nested)
            fixed = self.fix_circularities()
            if fixed:
                self._log_circularity_fixes(fixed)
            snapshot_path = self.write_gts_snapshot(command_origin="expand")
            self.state_store.record_snapshot(resolved, snapshot_path)
        return self.format_project_tree()

    def fix_circularities(self) -> tuple[str, ...]:
        """Resolve circularities in the loaded dependency tree (step 2.5).

        Detects and removes duplicate registry entries that arise when a leaf
        declared inside one parent's nested ``.cgs`` refers to the same physical
        repository as another parent already registered in the tree.  The
        canonical entry (the one sitting highest in the tree hierarchy, i.e. with
        the fewest ``:``-separated segments in its ``repo_id``) is kept; all
        lower-priority duplicates are removed.

        This method is called automatically inside :meth:`expand` (for ``.cgs``
        sources) and at the end of :meth:`clone_cgs`.  It can also be invoked
        manually between :meth:`expand` and :meth:`validate` when building a
        custom lifecycle pipeline.

        Returns
        -------
        tuple[str, ...]
            One entry per removed duplicate, each in the form
            ``"fixed_circularity:<removed_id>→<canonical_id>"``.
        """
        registry = self.get_dependency_registry()
        return _fix_circularities(registry)

    def validate(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> ProjectTreeState:
        """Validate the dependency tree state (lifecycle step 3: PENDING → READY).

        Loads the source (``.cgs`` or ``.gts``), recomputes the tree lifecycle
        state, and returns a :class:`~.git_tree.ProjectTreeState` describing
        readiness.  Every :class:`~.git_repo.GitRepo` must be in ``READY``
        state for the tree to be considered ``READY``.

        ``validate`` is the canonical lifecycle step-3 name; :meth:`verify` is
        a compatibility alias.

        Parameters
        ----------
        source_path:
            Path to the ``.cgs`` specification or a ``.gts`` snapshot.
        discover_nested:
            When ``True``, run nested ``.cgs`` discovery for ``.cgs`` sources.
        """
        resolved = Path(source_path).resolve()
        if resolved.suffix == ".gts":
            self.load_gts(resolved)
        else:
            self.load_cgs(resolved, discover_nested=discover_nested)
            snapshot_path = self.write_gts_snapshot(command_origin="validate")
            self.state_store.record_snapshot(resolved, snapshot_path)
        return self.get_tree_state()

    def verify(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> ProjectTreeState:
        """Compatibility alias for :meth:`validate`.

        ``validate`` is the canonical lifecycle step-3 name; ``verify`` is
        retained for backward compatibility.
        """
        return self.validate(source_path, discover_nested=discover_nested)

    def load_gts(self, snapshot_path: str | Path) -> DependencyTreeRegistry:
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        resolved_snapshot_path = Path(snapshot_path).resolve()
        document = GtsDocument.from_toml(resolved_snapshot_path)
        self.registry = build_registry_from_gts_document(document)
        self.orchestre.git_tree.git.bind_registry(self.registry)
        self.source_path = (
            _resolve_document_path(str(document.read("project.source_cgs_path")))
            if document.read("project.source_cgs_path")
            else resolved_snapshot_path
        )
        self.loaded_snapshot_path = resolved_snapshot_path
        self._log_event(
            "gts_load",
            snapshot_path=resolved_snapshot_path,
            source_cgs_path=self.source_path if self.source_path.suffix == ".cgs" else None,
        )
        self._log_tree_transition(previous_tree_state, self.registry.lifecycle_state, reason="load_gts")
        return self.registry

    def load_runtime_or_cgs(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> DependencyTreeRegistry:
        source_path = Path(config_path).resolve()
        snapshot_path = self.state_store.latest_snapshot_for(source_path)
        if snapshot_path is not None and snapshot_path.stat().st_mtime >= source_path.stat().st_mtime:
            return self.load_gts(snapshot_path)
        return self.load_cgs(source_path, discover_nested=discover_nested)

    def load_source(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
        prefer_runtime_for_cgs: bool = True,
    ) -> DependencyTreeRegistry:
        resolved_source = Path(source_path).resolve()
        if resolved_source.suffix == ".gts":
            return self.load_gts(resolved_source)
        if resolved_source.suffix == ".cgs":
            if prefer_runtime_for_cgs:
                return self.load_runtime_or_cgs(resolved_source, discover_nested=discover_nested)
            return self.load_cgs(resolved_source, discover_nested=discover_nested)
        raise ValueError(
            f"Unsupported source format for {resolved_source!s}; expected .cgs or .gts."
        )

    def resolve_clone_root(
        self,
        config_path: str | Path,
        *,
        target_dir: str | Path | None = None,
        output_path: str | Path | None = None,
    ) -> Path:
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        return self._resolve_project_root(document, source_path, target_dir, output_path)

    def clone_cgs(
        self,
        config_path: str | Path,
        *,
        target_dir: str | Path | None = None,
        output_path: str | Path | None = None,
    ) -> DependencyTreeRegistry:
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        project_root = self._resolve_project_root(document, source_path, target_dir, output_path)

        self.registry = build_registry_from_cgs_document(
            document,
            source_path,
            project_root=project_root,
        )
        self.orchestre.git_tree.git.bind_registry(self.registry)
        self.source_path = source_path

        # Sync stack: tracks absolute paths that have already entered the clone
        # pipeline.  If a repository's path appears in the stack, any subsequent
        # reference to it (created by nested-config discovery during the same
        # run) is treated as a mount point and skipped rather than cloned again.
        # This provides defence-in-depth against infinite-recursion edge cases
        # that may arise before fix_circularities() has had a chance to clean up
        # the registry.
        sync_stack: set[Path] = set()

        while True:
            cloned_any = False
            for entry in self._pending_clone_entries(sync_stack):
                sync_stack.add(entry.absolute_path)
                self._clone_registry_entry(entry)
                cloned_any = True

            discovered = self.discover_nested_configs()
            self._log_nested_discovery(discovered)
            if not cloned_any and not discovered:
                break

        fixed = self.fix_circularities()
        if fixed:
            self._log_circularity_fixes(fixed)
        self._assert_nested_discovery_complete()
        self.registry.recompute_tree_state()
        if not self.registry.is_ready():
            raise GitSyncError("Clone did not produce a READY tree.")
        snapshot_path = self.write_gts_snapshot(command_origin="clone")
        self.state_store.record_snapshot(source_path, snapshot_path)
        self._log_tree_transition(previous_tree_state, self.registry.lifecycle_state, reason="clone_cgs")
        return self.registry

    def clone(
        self,
        config_path: str | Path,
        *,
        target_dir: str | Path | None = None,
        output_path: str | Path | None = None,
    ) -> DependencyTreeRegistry:
        """Clone the repositories required by the current loaded tree state."""
        return self.clone_cgs(config_path, target_dir=target_dir, output_path=output_path)

    def restart(self, config_path: str | Path) -> DependencyTreeRegistry:
        """Legacy pull-like helper for resynchronizing an already-cloned tree.

        Loads the ``.cgs`` configuration, discovers nested configs, then
        checks out the root repository's current branch across the whole tree
        parent-first.  Ends in ``READY`` or raises
        :exc:`~ComplexGitSync.errors.GitSyncError`.
        """
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        resolved_path = Path(config_path).resolve()
        self._log_event("restart_start", config_path=resolved_path)
        registry = self.load_cgs(resolved_path, discover_nested=True)
        self.orchestre.git_tree.git.pull(self.git_runner)
        if not registry.is_ready():
            raise GitSyncError("restart did not produce a READY tree.")
        snapshot_path = self.write_gts_snapshot(command_origin="restart")
        self.state_store.record_snapshot(resolved_path, snapshot_path)
        self._log_tree_transition(previous_tree_state, registry.lifecycle_state, reason="restart")
        self._log_event("restart_end", config_path=resolved_path)
        return registry

    def pull(self, source_path: str | Path) -> DependencyTreeRegistry:
        """Compatibility lifecycle method for resynchronizing from ``.cgs`` or ``.gts``."""
        resolved_source = Path(source_path).resolve()
        if resolved_source.suffix == ".cgs":
            return self.restart(resolved_source)
        if resolved_source.suffix == ".gts":
            return self.launch_release(resolved_source)
        raise ValueError(
            f"Unsupported source format '{resolved_source.suffix}' for {resolved_source!s}; expected .cgs or .gts."
        )

    def orchestrate(
        self,
        plan_path: str | Path,
        *,
        stop_on_error: bool = True,
    ) -> list[dict[str, Any]]:
        """Execute a ``.goc`` orchestration plan through public client methods."""
        resolved_plan = Path(plan_path).resolve()
        document = GocDocument.from_toml(resolved_plan)
        source = self._resolve_goc_project_source(document, resolved_plan)
        report: list[dict[str, Any]] = []

        for index, action in enumerate(document.actions):
            raw_command = action.get("command")
            if not isinstance(raw_command, str) or not raw_command.strip():
                command_error = ValueError("action.command must be a non-empty string.")
                report.append(
                    {
                        "index": index,
                        "command": raw_command,
                        "status": "error",
                        "error": str(command_error),
                    }
                )
                if stop_on_error:
                    raise GitSyncError(
                        f".goc action failed at index {index} ({raw_command!r}): {command_error}"
                    ) from command_error
                continue
            command = raw_command.strip().lower()
            raw_args = action.get("args")
            args = raw_args if isinstance(raw_args, dict) else {}
            try:
                result = self._execute_goc_action(command, source, args)
                report.append(
                    {
                        "index": index,
                        "command": command,
                        "status": "ok",
                        "result": self._summarize_goc_result(result),
                    }
                )
            except Exception as exc:
                report.append(
                    {
                        "index": index,
                        "command": command,
                        "status": "error",
                        "error": str(exc),
                    }
                )
                if stop_on_error:
                    raise GitSyncError(
                        f".goc action failed at index {index} ({command!r}): {exc}"
                    ) from exc
        return report

    def _resolve_goc_project_source(self, document: GocDocument, plan_path: Path) -> Path:
        source = document.project_source
        if not source:
            raise ValueError("Invalid .goc document: [project].source is required.")
        source_path = Path(_expand_environment_markers(source)).expanduser()
        if not source_path.is_absolute():
            source_path = (plan_path.parent / source_path).resolve()
        else:
            source_path = source_path.resolve()
        return source_path

    def _execute_goc_action(
        self,
        command: str,
        source: Path,
        args: dict[str, Any],
    ) -> Any:
        if command not in _VALID_GOC_COMMANDS:
            raise ValueError(f"Unsupported .goc action command: {command!r}")

        if command == "clone":
            if source.suffix != ".cgs":
                raise ValueError("clone requires a .cgs source.")
            target_dir = args.get("target_dir")
            if target_dir is not None:
                return self.git(self.registry, "clone", str(source), str(target_dir))
            return self.git(self.registry, "clone", str(source))
        if command == "pull":
            return self.git(self.registry, "pull", str(source))

        if self.registry is None:
            self.load_source(source, discover_nested=bool(args.get("discover_nested", False)))

        active_registry = self.get_dependency_registry()
        if command == "checkout":
            ref_value = self._read_goc_arg(args, "ref", alias="branch")
            if ref_value is None or (isinstance(ref_value, str) and ref_value == ""):
                raise ValueError("checkout action requires args.ref (or args.branch).")
            return self.git(active_registry, "checkout", str(ref_value))
        if command == "add":
            return self.git(active_registry, "add")
        if command == "commit":
            message_value = args.get("message")
            if message_value is None or (isinstance(message_value, str) and message_value == ""):
                raise ValueError("commit action requires args.message.")
            return self.git(active_registry, "commit", str(message_value))
        if command == "push":
            return self.git(active_registry, "push")
        if command == "tag":
            tag_value = self._read_goc_arg(args, "name", alias="tag")
            if tag_value is None or (isinstance(tag_value, str) and tag_value == ""):
                raise ValueError("tag action requires args.name (or args.tag).")
            return self.git(active_registry, "tag", str(tag_value))
        if command == "freeze":
            freeze_value = self._read_goc_arg(args, "name", alias="tag")
            if freeze_value is None or (isinstance(freeze_value, str) and freeze_value == ""):
                raise ValueError("freeze action requires args.name (or args.tag).")
            return self.git(active_registry, "freeze", str(freeze_value))

    @staticmethod
    def _read_goc_arg(args: dict[str, Any], key: str, *, alias: str | None = None) -> Any:
        def _present(value: Any) -> bool:
            return value is not None and (not isinstance(value, str) or value != "")

        if alias is None:
            return args.get(key)
        key_value = args.get(key)
        alias_value = args.get(alias)
        key_present = key in args and _present(key_value)
        alias_present = alias in args and _present(alias_value)
        if key_present and alias_present:
            raise ValueError(
                f".goc action args must not define both '{key}' and '{alias}' simultaneously."
            )
        if key_present:
            return key_value
        if alias_present:
            return alias_value
        return None

    @staticmethod
    def _summarize_goc_result(result: Any) -> Any:
        if isinstance(result, DependencyTreeRegistry):
            return {
                "lifecycle_state": result.lifecycle_state.value,
                "repo_count": len(result.entries),
            }
        if isinstance(result, ProjectTreeState):
            return {
                "lifecycle_state": result.lifecycle_state.value,
                "is_ready": result.is_ready,
                "registry_complete": result.registry_complete,
            }
        if isinstance(result, Path):
            return str(result)
        if isinstance(result, (str, int, float, bool)) or result is None:
            return result
        return str(result)

    def checkout(
        self,
        branch_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
    ) -> DependencyTreeRegistry:
        """Check out *branch_name* across the full tree from a READY ``.gts`` state.

        Requires a ``READY`` registry.  After a successful execution the
        registry remains ``READY`` and a ``.gts`` snapshot is written.

        Steps delegated to :meth:`~ComplexGitSync.git_tree.GitTreeGitCommands.checkout`:

        1. :func:`~ComplexGitSync.operations.propagate_global_branch` — set
           the target ref on every entry.
        2. :func:`~ComplexGitSync.operations.create_global_branch` — create
           the branch locally where missing.
        3. ``git checkout`` on every repo, parent-first.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("checkout_start", branch_name=branch_name, ref_kind=ref_kind)
        self.orchestre.git_tree.git.checkout(
            self.git_runner,
            branch_name,
            ref_kind=ref_kind,
        )
        snapshot_path = self.write_gts_snapshot(command_origin="checkout")
        if self.source_path is not None:
            self.state_store.record_snapshot(self.source_path, snapshot_path)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="checkout")
        self._log_event("checkout_end", branch_name=branch_name, ref_kind=ref_kind)
        return registry

    def branch(
        self,
        branch_name: str,
    ) -> DependencyTreeRegistry:
        """Create *branch_name* across the full tree without checkout."""
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("branch_start", branch_name=branch_name)
        self.orchestre.git_tree.git.branch(self.git_runner, branch_name)
        snapshot_path = self.write_gts_snapshot(command_origin="branch")
        if self.source_path is not None:
            self.state_store.record_snapshot(self.source_path, snapshot_path)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="branch")
        self._log_event("branch_end", branch_name=branch_name)
        return registry

    def commit(
        self,
        message: str,
        *,
        stage_all: bool = True,
    ) -> DependencyTreeRegistry:
        """Commit changes across the full tree, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  Repos with
        no staged changes are silently skipped.  After a successful execution
        the registry remains ``READY``.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("commit_start", message=message, stage_all=stage_all)
        self.orchestre.git_tree.git.commit(
            self.git_runner,
            message,
            stage_all=stage_all,
        )
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="commit")
        self._log_event("commit_end", message=message)
        return registry

    def add(self) -> DependencyTreeRegistry:
        """Stage all changes across the full tree, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  After a
        successful execution the registry remains ``READY``.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("add_start")
        self.orchestre.git_tree.git.add(self.git_runner)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="add")
        self._log_event("add_end")
        return registry

    def push(self) -> DependencyTreeRegistry:
        """Push all repos to their remotes, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  After a
        successful execution the registry remains ``READY`` and refreshes the
        stored commit hashes in the runtime tree state.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("push_start")
        self.orchestre.git_tree.git.push(self.git_runner)
        snapshot_path = self.write_gts_snapshot(command_origin="push")
        if self.source_path is not None:
            self.state_store.record_snapshot(self.source_path, snapshot_path)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="push")
        self._log_event("push_end")
        return registry

    def tag(self, tag_name: str) -> DependencyTreeRegistry:
        """Create and push *tag_name* across the full tree, leaf-first.

        The runtime tree state is refreshed so the recorded tag target remains
        aligned with the synchronized repositories.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("tag_start", tag_name=tag_name)
        self.orchestre.git_tree.git.tag(self.git_runner, tag_name)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="tag")
        self._log_event("tag_end", tag_name=tag_name)
        return registry

    def git(
        self,
        gittree: "DependencyTreeRegistry | None",
        command: str,
        *args: str,
    ) -> DependencyTreeRegistry:
        """Dispatch a git command across the full tree (lifecycle step 5).

        This is the unified git interface.  It dispatches *command* to the
        appropriate tree-wide operation and returns the updated registry.
        Ordering is command-specific (for example, ``pull``/``branch``/``checkout``
        run parent-first while ``push`` runs leaf-first).

        Parameters
        ----------
        gittree:
            The :class:`~.git_tree.DependencyTreeRegistry` to operate on.
            Pass ``None`` to use the currently loaded registry.  Passing a
            registry replaces the active registry for the duration of the call.
        command:
            One of ``"clone"``, ``"pull"``, ``"checkout"``, ``"branch"``,
            ``"add"``, ``"commit"``, ``"push"``, ``"tag"``, or ``"freeze"``.
        *args:
            Command-specific positional arguments:

            - ``"clone"``: one argument — path to ``.cgs``; optional second
              argument sets ``target_dir``.
            - ``"pull"``: one argument — path to ``.cgs`` or ``.gts`` source.
            - ``"checkout"``: one argument — branch/tag name to switch to.
            - ``"branch"``: one argument — branch name to create (no checkout).
            - ``"add"``: no arguments.  Stages all changes tree-wide.
            - ``"commit"``: one argument — the commit message.  The message
              conventionally ends with ``CGS#VERSION``.
            - ``"push"``: no arguments.  Updates the stored hash in the
              ``GitTree`` for each repository.
            - ``"tag"``: one argument — the tag name.  Updates the stored tag
              in the ``GitTree`` for each repository.
            - ``"freeze"``: one argument — state/release tag name.

        Examples
        --------
        ::

            client.git(registry, "commit", "release: v1.0 CGS#1")
            client.git(registry, "push")
            client.git(registry, "tag", "v1.0")
        """
        if isinstance(gittree, DependencyTreeRegistry):
            self.registry = gittree
            self.orchestre.git_tree.git.bind_registry(gittree)
        command = command.lower()

        def _required_arg(index: int, label: str) -> str:
            if len(args) <= index or not args[index]:
                raise ValueError(f"{command} requires {label} argument.")
            return args[index]

        if command == "clone":
            source = _required_arg(0, "source path")
            target_dir = args[1] if len(args) > 1 else None
            return self.clone(source, target_dir=target_dir)
        if command == "pull":
            source = _required_arg(0, "source path")
            return self.pull(source)
        if command == "checkout":
            branch_name = _required_arg(0, "branch name")
            return self.checkout(branch_name)
        if command == "branch":
            branch_name = _required_arg(0, "branch name")
            return self.branch(branch_name)
        if command == "add":
            return self.add()
        if command == "commit":
            message = _required_arg(0, "message")
            return self.commit(message)
        if command == "push":
            return self.push()
        if command == "tag":
            tag_name = _required_arg(0, "tag name")
            return self.tag(tag_name)
        if command == "freeze":
            name = _required_arg(0, "tag name")
            return self.freeze(name)
        raise ValueError(
            f"Unknown git command '{command}'. Supported commands: 'clone', 'pull', "
            "'checkout', 'branch', 'add', 'commit', 'push', 'tag', 'freeze'."
        )


    def freeze_release(
        self,
        tag_name: str,
        *,
        output_gts: str | Path | None = None,
        message: str | None = None,
        stage_all: bool = True,
    ) -> DependencyTreeRegistry:
        """Freeze a release by committing, tagging, and pushing leaf-first.

        In lifecycle terms this emits the next persisted ``.gts`` state for the
        synchronized tree.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event(
            "freeze_release_start",
            tag_name=tag_name,
            output_gts=output_gts,
            stage_all=stage_all,
        )
        self.orchestre.git_tree.git.freeze(
            self.git_runner,
            tag_name,
            message=message,
            stage_all=stage_all,
        )
        snapshot_path = self.write_gts_snapshot(
            command_origin="freeze_release",
            output_path=output_gts,
        )
        if self.source_path is not None:
            self.state_store.record_snapshot(self.source_path, snapshot_path)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="freeze_release")
        self._log_event("freeze_release_end", tag_name=tag_name, output_gts=snapshot_path)
        return registry

    def freeze_state(
        self,
        state_name: str,
        *,
        output_gts: str | Path | None = None,
        message: str | None = None,
        stage_all: bool = True,
    ) -> DependencyTreeRegistry:
        """Freeze an internal development state from a ``READY`` tree.

        Parameters mirror :meth:`freeze_release`:

        - ``state_name``: shared tag name applied across all repositories.
        - ``output_gts``: optional snapshot path for the emitted ``.gts`` file.
        - ``message``: optional commit message override.
        - ``stage_all``: stage all changes before committing when ``True``.

        Behavior is identical to release freezing (commit/tag/push leaf-first),
        but intended for internal development states.
        """
        return self.freeze_release(
            state_name,
            output_gts=output_gts,
            message=message,
            stage_all=stage_all,
        )

    def launch_release(self, snapshot_path: str | Path) -> DependencyTreeRegistry:
        """Compatibility helper that restores a recorded ``.gts`` state.

        The primary lifecycle documentation treats this as checkout from saved
        state rather than as an additional top-level lifecycle step.
        """
        loaded_registry = self.load_gts(snapshot_path)
        previous_state = loaded_registry.lifecycle_state
        self._log_event("launch_release_start", snapshot_path=Path(snapshot_path).resolve())

        for entry in iter_tree(loaded_registry):
            ref_name = self._determine_launch_ref(entry)

            if not entry.absolute_path.exists():
                remote_url = self._build_remote_url(entry)
                if not remote_url:
                    raise GitSyncError(f"No remote URL available for repository {entry.name}.")
                self._log_event(
                    "launch_release_clone",
                    repo_name=entry.name,
                    absolute_path=entry.absolute_path,
                    ref_name=ref_name,
                )
                self.orchestre.git_tree.git.clone(
                    self.git_runner,
                    remote_url,
                    entry.absolute_path,
                    branch=ref_name,
                )

            self._log_event(
                "launch_release_checkout",
                repo_name=entry.name,
                absolute_path=entry.absolute_path,
                ref_name=ref_name,
            )
            self.git_runner.checkout(entry.absolute_path, ref_name)
            resolved_kind = entry.resolved_ref_kind or entry.target_ref_kind or RefKind.BRANCH
            entry.current_ref_kind = resolved_kind
            entry.current_ref_name = ref_name
            entry.target_ref_kind = resolved_kind
            entry.target_ref_name = ref_name
            entry.resolved_ref_kind = resolved_kind
            entry.resolved_ref_name = ref_name
            entry.commit_sha = self.git_runner.rev_parse_head(entry.absolute_path)
            entry.repo_lifecycle_state = RepoLifecycleState.READY
            entry.sync_state = SyncState.ALIGNED
            entry.fallback_applied = False
            entry.fallback_reason = None
            entry.worktree_state = "CLEAN"

        loaded_registry.recompute_tree_state()
        if not loaded_registry.is_ready():
            raise GitSyncError("launch_release did not produce a READY tree.")

        self._log_tree_transition(previous_state, loaded_registry.lifecycle_state, reason="launch_release")
        self._log_event("launch_release_end", snapshot_path=Path(snapshot_path).resolve())
        return loaded_registry

    def launch_state(self, snapshot_path: str | Path) -> DependencyTreeRegistry:
        """Compatibility helper that restores an internal ``.gts`` state.

        Loads ``snapshot_path``, performs due clone and checkout actions, and
        enforces a ``READY`` tree on successful completion.
        """
        return self.launch_release(snapshot_path)

    def freeze(
        self,
        name: str,
        *,
        output_gts: str | Path | None = None,
        message: str | None = None,
        stage_all: bool = True,
    ) -> DependencyTreeRegistry:
        """Freeze a tree state and emit the next ``.gts`` snapshot id."""
        return self.freeze_release(
            name,
            output_gts=output_gts,
            message=message,
            stage_all=stage_all,
        )

    def launch(self, snapshot_path: str | Path) -> DependencyTreeRegistry:
        """Compatibility wrapper for restoring a recorded ``.gts`` state."""
        return self.launch_release(snapshot_path)

    def get_dependency_registry(self) -> DependencyTreeRegistry:
        if self.registry is None:
            raise RuntimeError("No ComplexGitSync registry is loaded.")
        self.orchestre.git_tree.git.bind_registry(self.registry)
        return self.registry

    def get_tree_state(self) -> ProjectTreeState:
        return build_tree_state(self.get_dependency_registry())

    def discover_nested_configs(self) -> tuple[str, ...]:
        return discover_nested_configs(self.get_dependency_registry())

    def format_project_tree(self, *, verbose: bool = True) -> str:
        return format_project_tree(self.get_dependency_registry(), verbose=verbose)

    def format_repo_tree(self) -> str:
        return format_repo_tree_outline(self.get_dependency_registry())

    def view_tree(
        self,
        *,
        depth: int | None = None,
        collapse: tuple[str, ...] = (),
    ) -> str:
        return format_view_tree(
            self.get_dependency_registry(),
            depth=depth,
            collapse=collapse,
        )

    def view_operation(self) -> str:
        return format_view_operation(self.get_dependency_registry())

    def status(self) -> str:
        registry = self.get_dependency_registry()
        rows: list[tuple[str, str, str, str, str, str, str]] = []
        root_path = registry.get(ROOT_REPO_ID).absolute_path
        dirty_count = 0
        staged_count = 0
        ahead_count = 0
        behind_count = 0
        error_count = 0
        recorded_mismatch_count = 0

        for entry in iter_tree_leaf_first(registry):
            repo_status = self._repo_status_row(registry, entry, root_path)
            rows.append(repo_status)
            local_state = repo_status[3]
            upstream_state = repo_status[4]
            if local_state != "clean":
                dirty_count += 1
            if "staged" in local_state:
                staged_count += 1
            if upstream_state == "ahead":
                ahead_count += 1
            elif upstream_state == "behind":
                behind_count += 1
            elif upstream_state == "diverged":
                ahead_count += 1
                behind_count += 1
            if repo_status[5].endswith("*"):
                recorded_mismatch_count += 1
            if upstream_state == "error" or local_state == "error":
                error_count += 1

        tree_state = build_tree_state(registry)
        lines = [
            (
                "summary "
                f"ready={str(tree_state.is_ready).lower()} "
                f"complete={str(tree_state.registry_complete).lower()} "
                f"repos={len(rows)} "
                f"dirty={dirty_count} "
                f"staged={staged_count} "
                f"ahead={ahead_count} "
                f"behind={behind_count} "
                f"recorded_mismatch={recorded_mismatch_count} "
                f"errors={error_count}"
            )
        ]
        lines.append(_render_status_table(rows))
        if recorded_mismatch_count:
            lines.append("legend: HEAD ending with * differs from the commit recorded in the loaded .gts")
        return "\n".join(lines)

    def _repo_status_row(
        self,
        registry: DependencyTreeRegistry,
        entry: RepoRegistryEntry,
        root_path: Path,
    ) -> tuple[str, str, str, str, str, str, str]:
        display_path = _status_display_path(entry, root_path)
        try:
            branch = self.git_runner.current_branch(entry.absolute_path) or "detached"
            head = self.git_runner.rev_parse_head(entry.absolute_path)
            status_lines = self._managed_status_lines(registry, entry)
            tracking_state = self.git_runner.branch_tracking_state(entry.absolute_path)
        except GitSyncError:
            return (
                entry.name,
                display_path,
                entry.current_ref_name or "-",
                "error",
                "error",
                "-",
                _short_sha(entry.commit_sha),
            )

        local_state = _local_status_from_porcelain(status_lines)
        upstream_state = _status_tracking_label(tracking_state)
        recorded = _short_sha(entry.commit_sha)
        head_short = _short_sha(head)
        if entry.commit_sha and head and entry.commit_sha != head:
            head_short = f"{head_short}*"
        return (
            entry.name,
            display_path,
            branch,
            local_state,
            upstream_state,
            head_short,
            recorded,
        )

    def _managed_status_lines(
        self,
        registry: DependencyTreeRegistry,
        entry: RepoRegistryEntry,
    ) -> list[str]:
        status_lines = self.git_runner.status_porcelain(entry.absolute_path)
        unmanaged_gitlinks = _unmanaged_gitlink_paths(registry, entry, self.git_runner)
        if not unmanaged_gitlinks:
            return status_lines
        return [
            line
            for line in status_lines
            if not _status_line_targets_any(line, unmanaged_gitlinks)
        ]

    def describe_cgs(self) -> str:
        registry = self.get_dependency_registry()
        tree_state = build_tree_state(registry)
        summary = {
            "source_path": str(self.source_path) if self.source_path else None,
            "project_name": registry.get("root").name,
            "lifecycle_state": tree_state.lifecycle_state.value,
            "registry_complete": tree_state.registry_complete,
            "repo_count": len(registry.entries),
        }
        return json.dumps(summary, indent=2, sort_keys=True)

    def print(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
        prefer_runtime_for_cgs: bool = True,
    ) -> str:
        """Return a printable JSON summary for ``.cgs`` or ``.gts`` sources."""
        resolved_source = Path(source_path).resolve()
        if resolved_source.suffix == ".gts":
            document = GtsDocument.from_toml(resolved_source)
            self.load_gts(resolved_source)
            return json.dumps(
                {
                    "document_kind": "gts",
                    "project_name": document.read("project.name"),
                    "lifecycle_state": document.lifecycle_state,
                    "is_ready": document.is_ready,
                    "repo_count": len(document.repo_states),
                },
                indent=2,
                sort_keys=True,
            )
        if resolved_source.suffix == ".cgs":
            self.load_source(
                resolved_source,
                discover_nested=discover_nested,
                prefer_runtime_for_cgs=prefer_runtime_for_cgs,
            )
            return self.describe_cgs()
        raise ValueError(
            f"Unsupported source format '{resolved_source.suffix}' for {resolved_source!s}; expected .cgs or .gts."
        )

    def write_gts_snapshot(
        self,
        *,
        command_origin: str,
        output_path: str | Path | None = None,
    ) -> Path:
        registry = self.get_dependency_registry()
        root_entry = registry.get("root")
        if output_path is None:
            snapshot_name = f"{(self.source_path.stem if self.source_path else root_entry.name)}.gts"
            latest_output_path = root_entry.absolute_path / ".cgitsync" / "state" / snapshot_name
            resolved_output_path = latest_output_path
        else:
            latest_output_path = None
            resolved_output_path = Path(output_path).resolve()

        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        document = build_gts_document_from_registry(
            registry,
            command_origin=command_origin,
            source_cgs_path=self.source_path,
        )
        document.to_toml(resolved_output_path)
        self.loaded_snapshot_path = resolved_output_path
        self._log_event(
            "gts_write",
            snapshot_path=resolved_output_path,
            source_cgs_path=self.source_path,
            tree_lifecycle_state=registry.lifecycle_state,
        )
        register_path = root_entry.absolute_path / f"{root_entry.name}.lgr"
        if root_entry.absolute_path.exists():
            register_id = LocalGitRegister(register_path).record_snapshot(resolved_output_path)
            if latest_output_path is not None:
                immutable_output_path = resolved_output_path.parent / f"{register_id}.gts"
                if immutable_output_path != resolved_output_path:
                    document.to_toml(immutable_output_path)
                    resolved_output_path = immutable_output_path
                    register_id = LocalGitRegister(register_path).record_snapshot(resolved_output_path)
                    document.to_toml(latest_output_path)
                self.loaded_snapshot_path = resolved_output_path
            self._log_event(
                "lgr_update",
                register_path=register_path,
                snapshot_path=resolved_output_path,
                snapshot_id=register_id,
            )
            workspace_hash = document.snapshot_hash or document.compute_snapshot_hash()
            affected_repos = sorted(entry.name for entry in registry.values())
            ledger_id = SyncLedger(register_path).record_event(
                operation=command_origin,
                workspace_hash=workspace_hash,
                gts_snapshot_id=register_id,
                affected_repos=affected_repos,
            )
            self._log_event(
                "ledger_event",
                register_path=register_path,
                sync_id=ledger_id,
                operation=command_origin,
                workspace_hash=workspace_hash,
                gts_snapshot_id=register_id,
            )
        return resolved_output_path

    def get_ledger_history(self, register_path: str | Path) -> list[dict[str, Any]]:
        """Return all ledger events for *register_path* in topological DAG order.

        Parameters
        ----------
        register_path:
            Path to the project-local ``.lgr`` register file (e.g.
            ``<project-root>/demo.lgr``).

        Returns
        -------
        list[dict[str, Any]]
            Ledger events ordered parents-first.  Each event contains the
            fields defined by the ``.lgr`` ledger schema: ``sync_id``,
            ``parent_sync_ids``, ``operation``, ``timestamp``, ``actor``,
            ``workspace_hash``, ``gts_snapshot_id``, and ``affected_repos``.
        """
        return SyncLedger(register_path).history()

    def replay_ledger(self, register_path: str | Path) -> list[dict[str, Any]]:
        """Return ledger events in topological order for deterministic replay.

        Reconstructs the workspace evolution history from the first recorded
        sync operation to the last.  Alias for :meth:`get_ledger_history`.

        Parameters
        ----------
        register_path:
            Path to the project-local ``.lgr`` register file.
        """
        return SyncLedger(register_path).replay()

    def validate_branch_topology(self) -> BranchTopologyReport:
        """Inspect and validate the workspace branch topology.

        Reports whether all repositories are on the same branch as the root,
        categorises any divergence (allowed tag-divergence vs blocking
        misalignment), and returns a deterministic inspectable report.

        The registry must be loaded (any lifecycle state), but does not need
        to be ``READY``.  This method does not mutate the registry and issues
        no git write commands.

        Branch Topology Propagation Rules (T35)
        ----------------------------------------
        1. **Reference branch**: The root repository's current branch is the
           canonical reference for all repos in the tree.
        2. **Leaf-to-root inheritance**: Branch targeting flows root-first via
           :func:`~ComplexGitSync.operations.propagate_global_branch` and
           :func:`~ComplexGitSync.operations.create_global_branch`.  This
           method verifies that the on-disk state is coherent with that rule.
        3. **Allowed divergence**: Repos whose ``resolved_ref_kind`` is
           ``TAG`` are flagged as ``tag_divergence`` but are considered
           non-blocking — they represent a frozen (released) state.
        4. **Incoherent states**: A repo on a different branch from the root
           (``misaligned_branch``) or in an unexpected detached HEAD state
           (``detached_head``) makes the topology incoherent.

        Returns
        -------
        BranchTopologyReport
            A deterministic, inspectable snapshot of the workspace branch
            topology.  Call :meth:`~BranchTopologyReport.format` to render
            a human-readable summary.
        """
        registry = self.get_dependency_registry()
        self._log_event("validate_branch_topology_start")
        report = _validate_branch_topology(registry, self.git_runner)
        self._log_event(
            "validate_branch_topology_end",
            reference_branch=report.reference_branch,
            is_coherent=report.is_coherent,
            conflict_count=len(report.conflicts),
        )
        return report

    def configure(self, output_path: str | Path | None = None) -> CgsDocument:
        """Create a .cgs project specification file from terminal prompts.
        
        Uses GitTree.from_prompt() to collect project metadata and repository
        information interactively, then converts to a CgsDocument.
        
        Parameters
        ----------
        output_path : str | Path | None
            Path to write the .cgs file. If None, user will be prompted.
        
        Returns
        -------
        CgsDocument
            The configured .cgs document, ready to be written with to_toml().
        """
        from .git_tree import GitTree
        
        # Create GitTree via prompts
        git_tree = GitTree.from_prompt()
        
        # Convert to CgsDocument
        cgs_document = git_tree.to_cgs()
        
        # Write the file if output_path is provided
        if output_path:
            output_file = Path(output_path) if isinstance(output_path, str) else output_path
            cgs_document.to_toml(output_file)
            print(f"\n.cgs file written to: {output_file.resolve()}")
        
        return cgs_document

    def _resolve_project_root(
        self,
        document: CgsDocument,
        source_path: Path,
        target_dir: str | Path | None,
        output_path: str | Path | None = None,
    ) -> Path:
        if target_dir is not None:
            return Path(target_dir).resolve()

        base_dir = Path(output_path).resolve() if output_path is not None else Path.cwd()
        default_root = (base_dir / (document.project_name or source_path.stem)).resolve()
        if not default_root.exists() or (default_root.is_dir() and not any(default_root.iterdir())):
            return default_root
        raise GitSyncError(
            f"Clone destination already exists and is not empty: {default_root}\n"
            f"Choose a different --target-dir or ensure the directory is empty."
        )

    def _pending_clone_entries(
        self,
        sync_stack: set[Path] | None = None,
    ) -> list[RepoRegistryEntry]:
        """Return registry entries that are due for cloning.

        Entries are excluded from the result when:

        * Their ``repo_lifecycle_state`` is not ``DECLARED`` (already cloned
          or in error).
        * Their ``is_external_reference`` flag is ``True`` — these represent
          cycle-breaking back-edges and must not be cloned recursively.
        * Their ``absolute_path`` is already present in *sync_stack* — the
          path is already being processed in the current clone run, so any
          additional reference to it is treated as a mount point only.
        """
        registry = self.get_dependency_registry()
        return sorted(
            [
                entry
                for entry in registry.values()
                if entry.repo_lifecycle_state == RepoLifecycleState.DECLARED
                and not entry.is_external_reference
                and (sync_stack is None or entry.absolute_path not in sync_stack)
            ],
            key=lambda entry: (len(entry.absolute_path.parts), str(entry.absolute_path)),
        )

    def _attach_existing_root(
        self, entry: RepoRegistryEntry, project_root: Path
    ) -> None:
        """Mark an already-existing repository as the READY root without cloning it.

        Reads the current branch and commit SHA from the local repository at
        *project_root* and updates *entry* in-place so that
        :meth:`is_ready` recognises it as a valid tree node.
        """
        try:
            current_branch = self.git_runner.current_branch(project_root)
            commit_sha = self.git_runner.rev_parse_head(project_root)
        except GitSyncError as exc:
            self._log_event(
                "attach_root_git_info_failed",
                project_root=project_root,
                error=str(exc),
            )
            current_branch = None
            commit_sha = ""

        ref_name = current_branch or entry.target_ref_name or entry.default_branch or "main"
        entry.current_ref_kind = RefKind.BRANCH
        entry.current_ref_name = ref_name
        entry.resolved_ref_kind = RefKind.BRANCH
        entry.resolved_ref_name = ref_name
        entry.commit_sha = commit_sha
        entry.repo_lifecycle_state = RepoLifecycleState.READY
        entry.sync_state = SyncState.ALIGNED
        entry.worktree_state = "CLEAN"

    def _clone_registry_entry(self, entry: RepoRegistryEntry) -> None:
        previous_state = entry.repo_lifecycle_state
        previous_sync_state = entry.sync_state
        remote_url = self._build_remote_url(entry)
        selected_ref, selected_ref_kind = self._select_clone_ref(entry, remote_url)
        if self._is_populated_nested_destination(entry):
            try:
                shutil.rmtree(entry.absolute_path)
            except OSError as exc:
                raise GitSyncError(
                    f"Unable to clear nested clone destination for {entry.name} at {entry.absolute_path}: {exc}"
                ) from exc

        registry = self.get_dependency_registry()
        if entry.parent_id is None:
            self.orchestre.git_tree.git.clone(
                self.git_runner,
                remote_url,
                entry.absolute_path,
                branch=selected_ref,
            )
        else:
            parent = registry.get(entry.parent_id)
            try:
                relative_path = entry.absolute_path.relative_to(parent.absolute_path)
            except ValueError as exc:
                raise GitSyncError(
                    f"Repository {entry.name} at {entry.absolute_path} is not under its parent path "
                    f"{parent.absolute_path}."
                ) from exc
            self.git_runner.add_submodule(
                parent.absolute_path,
                remote_url,
                relative_path,
                branch=selected_ref,
            )
            if not self.git_runner.is_submodule(parent.absolute_path, relative_path):
                raise GitSyncError(
                    f"Submodule constraint violated: {parent.name}/{relative_path.as_posix()} "
                    f"is not tracked as a git submodule."
                )
        current_ref = self.git_runner.current_branch(entry.absolute_path) or selected_ref
        fallback_applied = current_ref != (entry.target_ref_name or selected_ref)

        entry.current_ref_kind = selected_ref_kind
        entry.current_ref_name = current_ref if selected_ref_kind == RefKind.BRANCH else selected_ref
        entry.resolved_ref_kind = selected_ref_kind
        entry.resolved_ref_name = current_ref if selected_ref_kind == RefKind.BRANCH else selected_ref
        entry.commit_sha = self.git_runner.rev_parse_head(entry.absolute_path)
        entry.fallback_applied = fallback_applied
        entry.fallback_reason = (
            f"branch '{entry.target_ref_name}' not found on remote; cloned '{current_ref}' instead"
            if fallback_applied
            else None
        )
        entry.repo_lifecycle_state = (
            RepoLifecycleState.FALLBACK_READY if fallback_applied else RepoLifecycleState.READY
        )
        entry.sync_state = SyncState.FALLBACK_APPLIED if fallback_applied else SyncState.ALIGNED
        entry.worktree_state = "CLEAN"
        if fallback_applied:
            self._log_event(
                "fallback_applied",
                repo_name=entry.name,
                absolute_path=entry.absolute_path,
                target_ref_kind=entry.target_ref_kind,
                target_ref_name=entry.target_ref_name,
                resolved_ref_kind=entry.resolved_ref_kind,
                resolved_ref_name=entry.resolved_ref_name,
                fallback_branch=entry.fallback_branch,
                fallback_reason=entry.fallback_reason,
            )
        self._log_repo_transition(entry, previous_state, previous_sync_state)

    def _is_populated_nested_destination(self, entry: RepoRegistryEntry) -> bool:
        return (
            entry.parent_id is not None
            and entry.absolute_path.is_dir()
            and next(entry.absolute_path.iterdir(), None) is not None
        )

    def _select_clone_ref(self, entry: RepoRegistryEntry, remote_url: str) -> tuple[str, RefKind]:
        if entry.target_ref_kind == RefKind.TAG and entry.target_ref_name:
            if self.git_runner.remote_tag_exists(remote_url, entry.target_ref_name):
                return (entry.target_ref_name, RefKind.TAG)
            raise GitSyncError(
                f"No cloneable tag found for {entry.name}: expected '{entry.target_ref_name}' on {remote_url}"
            )

        target_branch = entry.target_ref_name or entry.default_branch
        if target_branch and self.git_runner.remote_branch_exists(remote_url, target_branch):
            return (target_branch, RefKind.BRANCH)

        fallback_branch = entry.fallback_branch
        if fallback_branch and self.git_runner.remote_branch_exists(remote_url, fallback_branch):
            return (fallback_branch, RefKind.BRANCH)

        expected = [branch for branch in (target_branch, fallback_branch) if branch]
        raise GitSyncError(
            f"No cloneable branch found for {entry.name}: expected one of {expected} on {remote_url}"
        )

    def _build_remote_url(self, entry: RepoRegistryEntry) -> str:
        from .git_repo import RepoAddress
        address = RepoAddress(
            gitprovider=entry.gitprovider,
            project_name=entry.project_name or entry.name,
            repo_name=entry.repo_name or entry.project_name or entry.name,
            project_owner_name=entry.project_owner_name,
            group_name=entry.group_name,
            gitprovider_url=entry.gitprovider_url,
        )
        return address.to_url(entry.access_protocol)

    def _determine_launch_ref(self, entry: RepoRegistryEntry) -> str:
        """Return the most precise known ref for launch-release checkout."""
        ref_name = (
            entry.resolved_ref_name
            or entry.target_ref_name
            or entry.current_ref_name
            or entry.default_branch
        )
        if not ref_name:
            raise GitSyncError(f"No launch ref available for repository {entry.name}.")
        return ref_name

    def _assert_nested_discovery_complete(self) -> None:
        for entry in self.get_dependency_registry().values():
            if entry.nested_config in {None, "disabled"}:
                continue
            if entry.discovery_state != DiscoveryState.RESOLVED:
                raise GitSyncError(
                    f"Nested configuration for {entry.name} is not resolved: {entry.discovery_state.value}"
                )

    def _log_event(self, event: str, *, level: int = logging.INFO, **fields: object) -> None:
        if self.run_logger is None:
            return
        self.run_logger.log_event(event, level=level, **fields)

    def _log_tree_transition(
        self,
        previous_state: TreeLifecycleState,
        current_state: TreeLifecycleState,
        *,
        reason: str,
    ) -> None:
        if previous_state == current_state:
            return
        self._log_event(
            "tree_state_transition",
            previous_tree_state=previous_state,
            tree_lifecycle_state=current_state,
            reason=reason,
        )

    def _log_repo_transition(
        self,
        entry: RepoRegistryEntry,
        previous_state: RepoLifecycleState,
        previous_sync_state: SyncState,
    ) -> None:
        if previous_state == entry.repo_lifecycle_state and previous_sync_state == entry.sync_state:
            return
        self._log_event(
            "repo_state_transition",
            repo_name=entry.name,
            absolute_path=entry.absolute_path,
            previous_repo_lifecycle_state=previous_state,
            repo_lifecycle_state=entry.repo_lifecycle_state,
            previous_sync_state=previous_sync_state,
            sync_state=entry.sync_state,
            current_ref_kind=entry.current_ref_kind,
            current_ref_name=entry.current_ref_name,
            target_ref_kind=entry.target_ref_kind,
            target_ref_name=entry.target_ref_name,
            resolved_ref_kind=entry.resolved_ref_kind,
            resolved_ref_name=entry.resolved_ref_name,
            commit_sha=entry.commit_sha,
            fallback_branch=entry.fallback_branch,
            fallback_reason=entry.fallback_reason,
        )

    def _log_nested_discovery(self, discovered: tuple[str, ...]) -> None:
        registry = self.registry
        if registry is None:
            return
        for change in discovered:
            _, _, repo_id = change.partition(":")
            if repo_id not in registry.entries:
                continue
            entry = registry.get(repo_id)
            self._log_event(
                "nested_cgs_discovery",
                repo_name=entry.name,
                absolute_path=entry.absolute_path,
                source_cgs_path=entry.source_cgs_path,
                discovery_state=entry.discovery_state,
            )

    def _log_circularity_fixes(self, fixed: tuple[str, ...]) -> None:
        for change in fixed:
            # format: "fixed_circularity:<removed_id>→<canonical_id>"
            _, _, rest = change.partition("fixed_circularity:")
            removed_id, _, canonical_id = rest.partition("→")
            self._log_event(
                "circularity_fixed",
                removed_repo_id=removed_id,
                canonical_repo_id=canonical_id,
            )

"""Orchestration hub for ComplexGitSync.

This module is the **Orchestre anchor** — the authoritative source for runtime
document handling, infrastructure services, registry builders, nested config
discovery, and the public client API. The ``.cgs`` authoring format is defined
in :mod:`ComplexGitSync.cgs_format`.

Classes defined here (Tier 2 — Actions):
    GtsDocument             .gts state snapshot parser/validator
    CommandRunLogger        Structured JSON event logger for a command run
    RuntimeStateStore       Persistent snapshot-pointer registry (.cgs → .gts)
    MemoryBinding           External SSH-Git Memory endpoint binding
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

import configparser
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import tomli_w

from . import __version__ as CGS_VERSION
from .cgs_format import CgsDocument, parse_repo_id
from .config_document import ConfigDocument
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
    SyncState,
    WorkingRepo,
)
from .git_tree import (
    ROOT_REPO_ID,
    GitTree,
    ProjectTreeState,
    TreeLifecycleState,
    WorkingGitTree,
    _apply_repo_identity,
    _as_optional_str,
    _initial_discovery_state,
    _is_root_repo_spec,
    _normalise_relative_path,
    _parse_enum,
    _parse_gts_node_type,
    _parse_optional_enum,
    _update_gitignore_file,
    _validate_repo_shape,
    build_tree_state,
    format_project_tree,
    format_repo_tree_outline,
    format_view_operation,
    format_view_tree,
    iter_tree,
    iter_tree_leaf_first,
    make_repo_id,
    normalize_node_types,
    promote_to_parent,
    register_relative_path,
    sync_gitignore,
)
from .git_tree import (
    fix_circularities as _fix_circularities,
)
from .L0 import new_time_l0_anchor
from .master import MasterConfig
from .operations import (
    BranchTopologyReport,
)
from .operations import (
    validate_branch_topology as _validate_branch_topology,
)

# ============================================================
#  Runtime document layer — .gts
# ============================================================

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_STATE_ID_RE = re.compile(r"^state\(([0-9a-f]{64})\)$")
_STATE_DIR_RE = re.compile(r"^state\(([0-9a-f]{64})\)_(\d+)$")
_FREEZE_COMMAND_ORIGINS = frozenset({"freeze", "freeze_release", "freeze_state"})


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


def _rebase_path_under_root(path: Path | str, *, old_root: Path, new_root: Path) -> Path:
    resolved_path = Path(path).expanduser().resolve()
    try:
        return (new_root.resolve() / resolved_path.relative_to(old_root.resolve())).resolve()
    except ValueError:
        return resolved_path


def _preferred_path_separators() -> tuple[str, ...]:
    separators: list[str] = []
    seen: set[str] = set()
    for separator in (os.sep, os.altsep, "/", "\\"):
        if separator and separator not in seen:
            seen.add(separator)
            separators.append(separator)
    return tuple(separators)


def _format_state_id(state_hash: str) -> str:
    if _SHA256_HEX_RE.fullmatch(state_hash) is None:
        raise ValueError("state_hash must be a lowercase hexadecimal SHA-256 digest")
    return f"state({state_hash})"


def _parse_state_hash(state_id: str) -> str | None:
    match = _STATE_ID_RE.fullmatch(state_id)
    return match.group(1) if match else None


def _state_directory_name(state_hash: str, state_order: int) -> str:
    if state_order < 0:
        raise ValueError("state_order must be non-negative")
    return f"{_format_state_id(state_hash)}_{state_order}"


def _temporary_state_directory_name(state_hash: str, state_order: int) -> str:
    if state_order < 0:
        raise ValueError("state_order must be non-negative")
    return f".tmp-{_state_directory_name(state_hash, state_order)}"


def _state_order_from_directory_name(name: str) -> int | None:
    match = _STATE_DIR_RE.fullmatch(name)
    return int(match.group(2)) if match else None


def _next_state_directory_order(cgitsync_dir: Path, state_hash: str) -> int:
    _format_state_id(state_hash)
    max_order = -1
    if cgitsync_dir.is_dir():
        for entry in cgitsync_dir.iterdir():
            if not entry.is_dir():
                continue
            match = _STATE_DIR_RE.fullmatch(entry.name)
            if match is None or match.group(1) != state_hash:
                continue
            max_order = max(max_order, int(match.group(2)))
    return max_order + 1


@dataclass(frozen=True, slots=True)
class MemoryStateDirectory:
    state_hash: str
    state_order: int
    final_path: Path
    temporary_path: Path


def _resolve_memory_state_directory(cgitsync_dir: Path, state_hash: str) -> MemoryStateDirectory:
    state_order = _next_state_directory_order(cgitsync_dir, state_hash)
    while True:
        final_path = cgitsync_dir / _state_directory_name(state_hash, state_order)
        temporary_path = cgitsync_dir / _temporary_state_directory_name(state_hash, state_order)
        if not final_path.exists() and not temporary_path.exists():
            return MemoryStateDirectory(
                state_hash=state_hash,
                state_order=state_order,
                final_path=final_path,
                temporary_path=temporary_path,
            )
        state_order += 1


def _state_snapshot_candidates(cgitsync_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    if cgitsync_dir.is_dir():
        for state_dir in sorted(cgitsync_dir.iterdir(), key=lambda path: path.name):
            if not state_dir.is_dir() or _STATE_DIR_RE.fullmatch(state_dir.name) is None:
                continue
            candidates.extend(sorted(state_dir.glob("*.gts")))
    legacy_state_dir = cgitsync_dir / "state"
    if legacy_state_dir.is_dir():
        candidates.extend(sorted(legacy_state_dir.glob("*.gts")))
    return candidates


def _state_snapshot_candidates_for_id(cgitsync_dir: Path, state_id: str) -> list[Path]:
    state_hash = _parse_state_hash(state_id)
    if state_hash is None or not cgitsync_dir.is_dir():
        return []
    candidates: list[Path] = []
    for state_dir in sorted(cgitsync_dir.glob(f"{state_id}_*"), key=lambda path: path.name):
        if state_dir.is_dir() and _STATE_DIR_RE.fullmatch(state_dir.name) is not None:
            candidates.extend(sorted(state_dir.glob("*.gts")))
    return candidates


def _state_artifact_candidates(cgitsync_dir: Path, filename: str) -> list[Path]:
    candidates: list[Path] = []
    if not cgitsync_dir.is_dir():
        return candidates
    for state_dir in sorted(cgitsync_dir.iterdir(), key=lambda path: path.name):
        if not state_dir.is_dir() or _STATE_DIR_RE.fullmatch(state_dir.name) is None:
            continue
        candidate = state_dir / filename
        if candidate.is_file():
            candidates.append(candidate)
    return candidates


def _latest_state_artifact(cgitsync_dir: Path, filename: str) -> Path | None:
    candidates = _state_artifact_candidates(cgitsync_dir, filename)
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


_MEMORY_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_MEMORY_REMOTE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_MEMORY_SERVICE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*")
_MEMORY_SSH_URL_RE = re.compile(
    r"^git@(?P<host>[A-Za-z0-9][A-Za-z0-9.-]*):/srv/git/(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\.git$"
)
DEFAULT_MEMORY_SERVICE = "forge43.io"
DEFAULT_MEMORY_REMOTE_NAME = "forge43"
MEMORY_CONFIG_FILENAME = "memory.toml"


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


def _status_tracking_label(
    sync_state: SyncState | None,
    tracking_counts: tuple[int, int] | None = None,
) -> str:
    if sync_state is None:
        return "unknown"
    if sync_state == SyncState.ALIGNED:
        return "synced"
    if sync_state == SyncState.AHEAD:
        if tracking_counts is not None:
            return f"ahead(+{tracking_counts[0]})"
        return "ahead"
    if sync_state == SyncState.BEHIND:
        if tracking_counts is not None:
            return f"behind(-{tracking_counts[1]})"
        return "behind"
    if sync_state == SyncState.DIVERGED:
        if tracking_counts is not None:
            return f"diverged(+{tracking_counts[0]}/-{tracking_counts[1]})"
        return "diverged"
    return sync_state.value.lower()


def _short_sha(value: str | None) -> str:
    return value[:8] if value else "-"


def _status_display_path(entry: WorkingRepo, root_path: Path) -> str:
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


def _status_line_is_untracked(status_line: str) -> bool:
    return status_line.startswith("?? ")


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _unmanaged_gitlink_paths(
    registry: WorkingGitTree,
    entry: WorkingRepo,
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


def _render_status_table(rows: list[tuple[str, str, str, str, str, str, str, str]]) -> str:
    headers = (
        "REPOSITORY",
        "PATH",
        "LOCAL_BRANCH",
        "UPSTREAM_BRANCH",
        "LOCAL",
        "SYNC",
        "HEAD",
        "RECORDED",
    )
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def render_row(columns: Sequence[str]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(columns))

    lines = [render_row(headers), "-" * (sum(widths) + 12)]
    lines.extend(render_row(row) for row in rows)
    return "\n".join(lines)


def _ref_token(ref_kind: RefKind | str | None, ref_name: str | None) -> str | None:
    if ref_kind is None or not ref_name:
        return None
    kind = ref_kind.value if isinstance(ref_kind, RefKind) else str(ref_kind)
    return f"{kind}:{ref_name}"


def _split_ref_token(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, dict):
        return _as_optional_str(value.get("kind")), _as_optional_str(value.get("name"))
    if isinstance(value, str) and ":" in value:
        kind, name = value.split(":", 1)
        return _as_optional_str(kind), _as_optional_str(name)
    return None, None


def _repo_ref_pair(repo: dict[str, Any], prefix: str) -> tuple[str | None, str | None]:
    compact_value = repo.get(f"{prefix}_ref")
    if compact_value is None and prefix in {"current", "target", "resolved"}:
        compact_value = repo.get("ref")
    kind, name = _split_ref_token(compact_value)
    if kind or name:
        return kind, name
    return _as_optional_str(repo.get(f"{prefix}_ref_kind")), _as_optional_str(repo.get(f"{prefix}_ref_name"))


def _repo_ref_kind(repo: dict[str, Any], prefix: str) -> str | None:
    return _repo_ref_pair(repo, prefix)[0]


def _repo_ref_name(repo: dict[str, Any], prefix: str) -> str | None:
    return _repo_ref_pair(repo, prefix)[1]


def _repo_ref_token(repo: dict[str, Any], prefix: str) -> str | None:
    return _ref_token(*_repo_ref_pair(repo, prefix))


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

    _REQUIRED_DOCUMENT_KEYS = ("generated_at", "command_origin")
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
        if self.read("document.CGS_VERSION") is None and self.read("document.format_version") is None:
            errors.append("[document] missing required key: 'CGS_VERSION'")

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
                has_ref_name = any(
                    _repo_ref_name(repo, prefix)
                    for prefix in ("current", "target", "resolved")
                )
                if not has_ref_name:
                    errors.append(
                        f"repo_state[{idx}] must include at least one ref ('ref', 'current_ref', 'target_ref', or 'resolved_ref')"
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
                release_name = freeze_manifest.get("release-name")
                if release_name is not None:
                    if not isinstance(release_name, str) or not release_name.strip():
                        errors.append("[freeze_manifest] release-name must be a non-empty string")
                    elif isinstance(synchronized_ref_name, str) and release_name != synchronized_ref_name:
                        errors.append("[freeze_manifest] release-name must match synchronized_ref_name")
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
        value = self.read("document.CGS_VERSION")
        if isinstance(value, str) and value:
            return value
        return CGS_VERSION

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
        document["CGS_VERSION"] = str(document.get("CGS_VERSION") or CGS_VERSION)
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
                    "current_ref": _repo_ref_token(repo, "current"),
                    "target_ref": _repo_ref_token(repo, "target"),
                    "resolved_ref": _repo_ref_token(repo, "resolved"),
                    "commit_sha": repo.get("commit_sha"),
                    "project_owner_name": repo.get("project_owner_name"),
                    "project_name": repo.get("project_name"),
                    "repo_name": repo.get("repo_name"),
                    "fallback_branch": repo.get("fallback_branch", "main"),
                    "fallback_applied": bool(repo.get("fallback_applied", False)),
                    "fallback_reason": repo.get("fallback_reason"),
                    "discovery_state": repo.get("discovery_state", DiscoveryState.RESOLVED.value),
                    "worktree_state": repo.get("worktree_state"),
                    "is_reachable": bool(repo.get("is_reachable", True)),
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
                "CGS_VERSION": self.schema_version,
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
                "release-name": freeze_manifest.get("release-name"),
                "restore_operation": freeze_manifest.get("restore_operation"),
            }
        return payload


# ============================================================
#  Infrastructure — CommandRunLogger, RuntimeStateStore, GitRunner
# ============================================================


class CommandRunLogger:
    """Structured JSON logger for a single ComplexGitSync command run."""

    def __init__(self, logger: logging.Logger, *, log_path: Path | None = None) -> None:
        self._logger = logger
        self.log_path = log_path
        self._buffered_lines: list[str] = []

    def log_event(self, event: str, *, level: int = logging.INFO, **fields: object) -> None:
        """Log *event* together with arbitrary keyword *fields* as a JSON record."""
        record: dict[str, Any] = {
            "operation": self._operation_for_event(event, fields),
            "event": event,
        }
        for key, value in fields.items():
            if isinstance(value, (str, int, float, bool, type(None))):
                record[key] = value
            else:
                record[key] = str(value)
        line = json.dumps(record, default=str)
        self._buffered_lines.append(line)
        self._logger.log(level, line)
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")

    def bind_log_file(self, log_path: Path | str) -> None:
        """Write buffered records to *log_path* and append future records there."""
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(
            "".join(f"{line}\n" for line in self._buffered_lines),
            encoding="utf-8",
        )

    @staticmethod
    def _operation_for_event(event: str, fields: dict[str, object]) -> str:
        if event.startswith("memory_"):
            return "CGS-MEM"
        if event == "nested_cgs_discovery":
            return "GT-DISCOVER"
        if event in {"repo_state_transition", "tree_state_transition"}:
            return "GT-CLONE"
        if event in {"circularity_fixed", "validate_branch_topology_start", "validate_branch_topology_end"}:
            return "GT-VALIDATE"
        if event.startswith("fs_purge_"):
            return "FS-PURGE"
        if event == "command_start" or event == "command_end":
            command = str(fields.get("command", "command")).replace("_", "-").upper()
            if command in {"VALIDATE", "VALIDATE-TOPOLOGY"}:
                return "GT-VALIDATE"
            if command == "PURGE":
                return "FS-PURGE"
            if command in {"INITIALISE", "CLEAN-INIT", "CLONE", "PULL"}:
                return "GT-CLONE"
            return f"CGS-{command}"
        return "CGS-RUN"


def create_run_logger(
    command_name: str,
    *,
    profile: str = "quiet",
    source_path: Path | None = None,
    project_root: Path | None = None,
    project_log_dir: Any = None,
) -> CommandRunLogger:
    """Create a :class:`CommandRunLogger` for a specific command invocation."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    logger_name = f"ComplexGitSync.run.{command_name}.{timestamp}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    console_level = logging.INFO if profile == "verbose" else logging.WARNING
    ch = logging.StreamHandler()
    ch.setLevel(console_level)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    return CommandRunLogger(logger)


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
    - a ``[[snapshots]]`` list for public ``state(HASH(.@))`` identifiers.

    The private TIME-L0 anchor never leaves the local execution context.
    ``snapshot_hash`` remains the canonical hash of the ``.gts`` payload, but it
    does not participate in State identity.
    """

    _HASH_CHUNK_SIZE = 65536

    def __init__(self, register_path: Path | str) -> None:
        self.register_path = Path(register_path)

    def record_snapshot(
        self,
        snapshot_path: Path | str,
        *,
        state_hash: str | None = None,
        state_order: int | None = None,
        recorded_snapshot_path: Path | str | None = None,
    ) -> str:
        resolved_snapshot_path = Path(snapshot_path).resolve()
        snapshot_hash = self._hash_snapshot_file(resolved_snapshot_path)
        public_snapshot_path = (
            Path(recorded_snapshot_path).resolve()
            if recorded_snapshot_path is not None
            else resolved_snapshot_path
        )
        snapshot_path_marker = _path_to_environment_marker(public_snapshot_path)

        data = self._load()
        snapshots = data.setdefault("snapshots", [])
        state_anchor = new_time_l0_anchor() if state_hash is None else None
        public_state_hash = state_hash if state_hash is not None else state_anchor.state_hash
        snapshot_id = _format_state_id(public_state_hash)
        if state_order is None:
            state_order = self._next_state_order(snapshots, public_state_hash)
        recorded_at = (
            datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        snapshots.append(
            {
                "id": snapshot_id,
                "state_hash": public_state_hash,
                "state_order": state_order,
                "snapshot_hash": snapshot_hash,
                "snapshot_path": snapshot_path_marker,
                "recorded_at": recorded_at,
            }
        )

        register = data.setdefault("register", {})
        register["current_snapshot_id"] = snapshot_id
        register["current_state_hash"] = public_state_hash
        register["current_snapshot_hash"] = snapshot_hash
        register["current_snapshot_path"] = snapshot_path_marker

        self.register_path.parent.mkdir(parents=True, exist_ok=True)
        self.register_path.write_text(tomli_w.dumps(data), encoding="utf-8")
        return snapshot_id

    def _load(self) -> dict[str, Any]:
        if not self.register_path.is_file():
            return {"register": {}, "snapshots": []}
        return tomllib.loads(self.register_path.read_text(encoding="utf-8"))

    def _next_state_order(self, snapshots: list[dict[str, Any]], state_hash: str) -> int:
        """Return the next local ordering suffix for State directories."""
        max_order = -1
        for entry in snapshots:
            if not isinstance(entry, dict):
                continue
            entry_state_hash = entry.get("state_hash")
            if not isinstance(entry_state_hash, str):
                entry_state_hash = _parse_state_hash(str(entry.get("id", "")))
            if entry_state_hash != state_hash:
                continue
            raw_order = entry.get("state_order")
            if isinstance(raw_order, int):
                max_order = max(max_order, raw_order)
                continue
            raw_id = str(entry.get("id", ""))
            if raw_id.startswith("gts-"):
                try:
                    max_order = max(max_order, int(raw_id.removeprefix("gts-")) - 1)
                except ValueError:
                    continue
        register_parent = self.register_path.parent
        cgitsync_dir = (
            register_parent.parent
            if _STATE_DIR_RE.fullmatch(register_parent.name) is not None
            else register_parent / ".cgitsync"
        )
        return max(max_order + 1, _next_state_directory_order(cgitsync_dir, state_hash))

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
        gts_snapshot_id = "state(<hash>)" # links to [[snapshots]] entry
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
            The public State id (``state(HASH(.@))``) assigned by the
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
            datetime.now(UTC)
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


@dataclass(frozen=True, slots=True)
class MemoryBinding:
    """External SSH-Git Memory endpoint binding for one artefact."""

    name: str
    service: str
    alias: str
    remote_name: str
    remote_url: str

    @classmethod
    def for_name(
        cls,
        name: str,
        *,
        service: str = DEFAULT_MEMORY_SERVICE,
        remote_name: str = DEFAULT_MEMORY_REMOTE_NAME,
    ) -> MemoryBinding:
        validated_name = _validate_memory_name(name)
        validated_service = _validate_memory_service(service)
        validated_remote_name = _validate_memory_remote_name(remote_name)
        service_alias = validated_service.split(".", 1)[0]
        alias = f"@{service_alias}@{validated_name}"
        remote_url = f"git@{validated_service}:/srv/git/{validated_name}.git"
        return cls(
            name=validated_name,
            service=validated_service,
            alias=alias,
            remote_name=validated_remote_name,
            remote_url=remote_url,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryBinding:
        binding = cls(
            name=str(data.get("name", "")),
            service=str(data.get("service", "")),
            alias=str(data.get("alias", "")),
            remote_name=str(data.get("remote_name", "")),
            remote_url=str(data.get("remote_url", "")),
        )
        binding.validate()
        return binding

    def validate(self) -> None:
        _validate_memory_name(self.name)
        _validate_memory_service(self.service)
        _validate_memory_remote_name(self.remote_name)
        expected = MemoryBinding.for_name(
            self.name,
            service=self.service,
            remote_name=self.remote_name,
        )
        if self.alias != expected.alias:
            raise ConfigValidationError(
                f"Memory alias drift: expected {expected.alias!r}, got {self.alias!r}"
            )
        if self.remote_url != expected.remote_url:
            raise ConfigValidationError(
                f"Memory remote URL drift: expected {expected.remote_url!r}, got {self.remote_url!r}"
            )
        _validate_memory_ssh_url(self.remote_url, name=self.name, service=self.service)

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "service": self.service,
            "alias": self.alias,
            "remote_name": self.remote_name,
            "remote_url": self.remote_url,
        }


@dataclass(frozen=True, slots=True)
class MemoryRememberResult:
    """Result returned by ``@name.remember`` binding operations."""

    binding: MemoryBinding
    config_path: Path
    project_root: Path
    remote_validated: bool


@dataclass(frozen=True, slots=True)
class MemoryMemorizeResult:
    """Result returned by ``@name.memorize`` persistence operations."""

    binding: MemoryBinding
    current_memory_path: Path
    project_root: Path
    memory_repository_path: Path
    state_hash: str
    state_order: int
    commit_created: bool
    pushed: bool
    verified: bool
    local_ref: str | None
    remote_ref: str | None
    status: str


@dataclass(frozen=True, slots=True)
class MemoryRetrieveResult:
    """Result returned by ``@name.retrieve`` retrieval operations."""

    binding: MemoryBinding
    project_root: Path
    memory_repository_path: Path
    cgitsync_path: Path
    state_paths: tuple[Path, ...]
    local_ref: str
    remote_ref: str
    verified: bool
    status: str


@dataclass(frozen=True, slots=True)
class MemoryReloadResult:
    """Result returned by ``@name.reload`` execution-context restore operations."""

    binding: MemoryBinding
    retrieve_result: MemoryRetrieveResult
    project_root: Path
    cgitsync_path: Path
    state_path: Path
    snapshot_path: Path
    source_cgs_path: Path | None
    registry: WorkingGitTree
    status: str


class MemoryBindingStore:
    """Persist and load Memory bindings under ``CGSHOME/.cgitsync``."""

    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root).expanduser().resolve()

    @property
    def config_path(self) -> Path:
        return self.project_root / ".cgitsync" / MEMORY_CONFIG_FILENAME

    def save(self, binding: MemoryBinding) -> Path:
        binding.validate()
        validated_at = (
            datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        data = {
            "memory": {
                "schema_version": "1.0",
                **binding.to_dict(),
                "validated_at": validated_at,
            }
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(tomli_w.dumps(data), encoding="utf-8")
        return self.config_path

    def load(self) -> MemoryBinding:
        if not self.config_path.is_file():
            raise GitSyncError(f"No Memory binding found at {self.config_path}")
        data = tomllib.loads(self.config_path.read_text(encoding="utf-8"))
        raw_binding = data.get("memory", {})
        if not isinstance(raw_binding, dict):
            raise ConfigValidationError(f"Invalid Memory binding file: {self.config_path}")
        return MemoryBinding.from_dict(raw_binding)


def _validate_memory_name(name: str) -> str:
    if not isinstance(name, str) or _MEMORY_NAME_RE.fullmatch(name) is None:
        raise ConfigValidationError(
            "Memory artefact name must contain only letters, numbers, '.', '_', or '-', "
            "and must start with a letter or number."
        )
    return name


def _validate_memory_service(service: str) -> str:
    if not isinstance(service, str) or _MEMORY_SERVICE_RE.fullmatch(service) is None:
        raise ConfigValidationError("Memory service must be a hostname.")
    if service != DEFAULT_MEMORY_SERVICE:
        raise ConfigValidationError(
            f"Unsupported Memory service {service!r}; expected {DEFAULT_MEMORY_SERVICE!r}."
        )
    return service


def _validate_memory_remote_name(remote_name: str) -> str:
    if not isinstance(remote_name, str) or _MEMORY_REMOTE_NAME_RE.fullmatch(remote_name) is None:
        raise ConfigValidationError(
            "Memory remote name must contain only letters, numbers, '.', '_', or '-', "
            "and must start with a letter or number."
        )
    return remote_name


def _validate_memory_ssh_url(remote_url: str, *, name: str, service: str) -> str:
    match = _MEMORY_SSH_URL_RE.fullmatch(remote_url)
    if match is None:
        raise ConfigValidationError(
            "Memory remote URL must use SSH scp syntax: git@forge43.io:/srv/git/<NAME>.git"
        )
    if match.group("host") != service:
        raise ConfigValidationError(
            f"Memory remote host mismatch: expected {service!r}, got {match.group('host')!r}."
        )
    if match.group("name") != name:
        raise ConfigValidationError(
            f"Memory remote repository mismatch: expected {name!r}, got {match.group('name')!r}."
        )
    return remote_url


def _resolve_memory_repository_base_dir() -> Path:
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "ComplexGitSync" / "memory-repositories"
    return Path.home() / ".local" / "state" / "ComplexGitSync" / "memory-repositories"


def _memory_repository_path(project_root: Path) -> Path:
    key = hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()[:24]
    return _resolve_memory_repository_base_dir() / key


def _validate_current_memory_path(current_memory_path: Path | str) -> tuple[Path, str, int]:
    resolved = Path(current_memory_path).expanduser().resolve()
    if not resolved.is_dir():
        raise GitSyncError(f"current_memory_path does not exist or is not a directory: {resolved}")
    if resolved.name.startswith(".tmp-"):
        raise GitSyncError(f"current_memory_path is not finalized: {resolved}")
    match = _STATE_DIR_RE.fullmatch(resolved.name)
    if match is None:
        raise GitSyncError(
            "current_memory_path must be a canonical .cgitsync/state(<hash>)_i directory."
        )
    if resolved.parent.name != ".cgitsync":
        raise GitSyncError(
            "current_memory_path must live directly under a project .cgitsync directory."
        )
    required_patterns = {
        ".cgs": "*.cgs",
        ".gts": "*.gts",
        ".lgr": "*.lgr",
        ".log": "*.log",
    }
    missing = [
        suffix
        for suffix, pattern in required_patterns.items()
        if not any(candidate.is_file() for candidate in resolved.glob(pattern))
    ]
    if missing:
        raise GitSyncError(
            f"current_memory_path is incomplete; missing Memory artefact(s): {', '.join(missing)}"
        )
    return resolved, match.group(1), int(match.group(2))


def _validate_memory_cgitsync_tree(cgitsync_dir: Path | str) -> tuple[Path, ...]:
    resolved = Path(cgitsync_dir).expanduser().resolve()
    if not resolved.is_dir():
        raise GitSyncError(f"Retrieved Memory is missing expected .cgitsync root: {resolved}")
    if resolved.name != ".cgitsync":
        raise GitSyncError(f"Retrieved Memory root must be named .cgitsync: {resolved}")
    invalid_entries: list[Path] = []
    state_paths = tuple(
        sorted(
            (
                candidate
                for candidate in resolved.iterdir()
                if candidate.is_dir() and _STATE_DIR_RE.fullmatch(candidate.name) is not None
            ),
            key=lambda path: path.name,
        )
    )
    for candidate in resolved.iterdir():
        if candidate.is_dir():
            if _STATE_DIR_RE.fullmatch(candidate.name) is None:
                invalid_entries.append(candidate)
            continue
        if candidate.is_file() and candidate.name == MEMORY_CONFIG_FILENAME:
            continue
        invalid_entries.append(candidate)
    if invalid_entries:
        rendered = ", ".join(
            path.name for path in sorted(invalid_entries, key=lambda path: path.name)
        )
        plural = "y" if len(invalid_entries) == 1 else "ies"
        raise GitSyncError(
            f"Retrieved Memory contains invalid .cgitsync entr{plural}: {rendered}"
        )
    if not state_paths:
        raise GitSyncError(f"Retrieved Memory contains no canonical State directories: {resolved}")
    for state_path in state_paths:
        _validate_current_memory_path(state_path)
    return state_paths


@dataclass(frozen=True, slots=True)
class MemoryReloadSelection:
    """Selected State artefacts read from a recovered MemoryFS tree."""

    state_path: Path
    snapshot_path: Path
    source_cgs_path: Path | None


def _select_latest_memory_state(
    cgitsync_dir: Path | str,
    state_paths: Sequence[Path],
) -> MemoryReloadSelection:
    """Select the latest reloadable State from a recovered ``.cgitsync`` tree."""
    resolved_cgitsync = Path(cgitsync_dir).expanduser().resolve()
    if not state_paths:
        raise GitSyncError("Retrieved Memory contains no State to reload.")

    candidates: list[tuple[tuple[int, str, float, str], MemoryReloadSelection]] = []
    for state_path in state_paths:
        resolved_state = Path(state_path).expanduser().resolve()
        lgr_candidates = sorted(resolved_state.glob("*.lgr"))
        if not lgr_candidates:
            raise GitSyncError(f"Retrieved State is missing a Memory register: {resolved_state}")
        lgr_path = lgr_candidates[0]
        try:
            register_data = tomllib.loads(lgr_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise GitSyncError(f"Retrieved Memory register is invalid: {lgr_path}") from exc

        snapshot_path = _resolve_memory_register_snapshot_path(
            register_data,
            recovered_cgitsync_dir=resolved_cgitsync,
            fallback_state_dir=resolved_state,
        )
        if snapshot_path is None:
            gts_candidates = sorted(resolved_state.glob("*.gts"))
            if not gts_candidates:
                raise GitSyncError(f"Retrieved State is missing a .gts snapshot: {resolved_state}")
            snapshot_path = gts_candidates[-1].resolve()

        source_cgs_candidates = sorted(resolved_state.glob("*.cgs"))
        source_cgs_path = (
            source_cgs_candidates[0].resolve() if source_cgs_candidates else None
        )
        snapshots = register_data.get("snapshots", [])
        snapshots_count = len(snapshots) if isinstance(snapshots, list) else 0
        latest_recorded_at = ""
        if isinstance(snapshots, list):
            for entry in snapshots:
                if isinstance(entry, dict) and isinstance(entry.get("recorded_at"), str):
                    latest_recorded_at = max(latest_recorded_at, entry["recorded_at"])
        sort_key = (
            snapshots_count,
            latest_recorded_at,
            resolved_state.stat().st_mtime,
            resolved_state.name,
        )
        candidates.append(
            (
                sort_key,
                MemoryReloadSelection(
                    state_path=resolved_state,
                    snapshot_path=snapshot_path,
                    source_cgs_path=source_cgs_path,
                ),
            )
        )

    if not candidates:
        raise GitSyncError("Retrieved Memory contains no reloadable State.")
    return max(candidates, key=lambda item: item[0])[1]


def _resolve_memory_register_snapshot_path(
    register_data: dict[str, Any],
    *,
    recovered_cgitsync_dir: Path,
    fallback_state_dir: Path,
) -> Path | None:
    """Resolve an ``.lgr`` current snapshot path into the recovered MemoryFS tree."""
    raw_current = register_data.get("register", {}).get("current_snapshot_path")
    if not isinstance(raw_current, str) or not raw_current:
        return None

    expanded = Path(_expand_environment_markers(raw_current)).expanduser()
    if not expanded.is_absolute():
        candidate = (fallback_state_dir / expanded).resolve()
        if candidate.is_file() and recovered_cgitsync_dir in candidate.parents:
            return candidate

    parts = expanded.parts
    if ".cgitsync" in parts:
        marker_index = len(parts) - 1 - parts[::-1].index(".cgitsync")
        relative_parts = parts[marker_index + 1:]
        if relative_parts:
            candidate = recovered_cgitsync_dir.joinpath(*relative_parts).resolve()
            if candidate.is_file():
                return candidate

    resolved = expanded.resolve()
    if resolved.is_file() and recovered_cgitsync_dir in resolved.parents:
        return resolved
    return None


def _memory_copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name == ".git" or name.startswith(".tmp-state(") or name.startswith(".tmp-")
    }


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

    def validate_memory_remote(self, remote_url: str) -> str:
        """Validate read-only SSH access to a Memory bare repository."""
        completed = self._run("ls-remote", remote_url)
        return completed.stdout.strip()

    def remote_head(self, remote_url: str, branch: str = "main") -> str | None:
        """Return the remote branch head SHA, or ``None`` when absent."""
        completed = self._run("ls-remote", "--heads", remote_url, branch)
        output = completed.stdout.strip()
        if not output:
            return None
        return output.split(maxsplit=1)[0]

    def init_repository(self, repo_path: Path | str) -> None:
        """Initialise *repo_path* as a Git worktree."""
        repo = Path(repo_path)
        repo.mkdir(parents=True, exist_ok=True)
        self._run("init", cwd=repo)

    def is_git_repository(self, repo_path: Path | str) -> bool:
        """Return ``True`` when *repo_path* is a Git worktree."""
        try:
            result = self._run("rev-parse", "--is-inside-work-tree", cwd=repo_path)
        except GitSyncError:
            return False
        return result.stdout.strip() == "true"

    def checkout_branch(self, repo_path: Path | str, branch: str) -> None:
        """Create or reset the current branch to *branch*."""
        self._run("checkout", "-B", branch, cwd=repo_path)

    def remote_get_url(self, repo_path: Path | str, remote_name: str = "origin") -> str | None:
        """Return the URL configured for *remote_name*, or ``None`` when unset.

        Used by :meth:`ComplexGitSyncClient.discover_repos` to recover a
        checked-out repository's upstream address. A repository with no such
        remote is a normal, reportable condition — not an error — so the
        missing case is returned as ``None`` rather than raised.
        """
        try:
            url = self._run("remote", "get-url", remote_name, cwd=repo_path).stdout.strip()
        except GitSyncError:
            return None
        return url or None

    def configure_remote(self, repo_path: Path | str, remote_name: str, remote_url: str) -> None:
        """Add or update *remote_name* in *repo_path*."""
        try:
            existing = self._run("remote", "get-url", remote_name, cwd=repo_path).stdout.strip()
        except GitSyncError:
            self._run("remote", "add", remote_name, remote_url, cwd=repo_path)
            return
        if existing != remote_url:
            self._run("remote", "set-url", remote_name, remote_url, cwd=repo_path)

    def fetch_branch(self, repo_path: Path | str, remote_name: str, branch: str) -> None:
        """Fetch *remote_name/branch* into ``FETCH_HEAD``."""
        self._run("fetch", remote_name, branch, cwd=repo_path)

    def reset_to_fetch_head(self, repo_path: Path | str) -> None:
        """Reset *repo_path* to the most recent ``FETCH_HEAD``."""
        self._run("reset", "--hard", "FETCH_HEAD", cwd=repo_path)

    def fsck_full(self, repo_path: Path | str) -> None:
        """Verify the integrity of *repo_path* with ``git fsck --full``."""
        self._run("fsck", "--full", cwd=repo_path)

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

    def stage_path(self, repo_path: Path | str, relative_path: str) -> None:
        """Stage a single path in *repo_path* (``git add -- <relative_path>``)."""
        self._run("add", "--", relative_path, cwd=repo_path)

    def commit(
        self,
        repo_path: Path | str,
        message: str,
        *,
        user_name: str | None = None,
        user_email: str | None = None,
    ) -> None:
        """Commit staged changes in *repo_path* with *message* (``git commit``)."""
        args: list[str] = []
        if user_name is not None:
            args.extend(["-c", f"user.name={user_name}"])
        if user_email is not None:
            args.extend(["-c", f"user.email={user_email}"])
        args.extend(["commit", "-m", message])
        self._run(*args, cwd=repo_path)

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

    def force_pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None:
        """Force the local branch to match *remote/ref_name* and clean untracked files."""
        selected_ref = ref_name or self.current_branch(repo_path) or "main"
        self._run("fetch", remote, selected_ref, cwd=repo_path)
        self._run("checkout", "-B", selected_ref, "FETCH_HEAD", cwd=repo_path)
        self.clean_untracked(repo_path)

    def reset_hard(self, repo_path: Path | str, ref_name: str = "HEAD") -> None:
        """Discard local tracked changes in *repo_path*."""
        self._run("reset", "--hard", ref_name, cwd=repo_path)

    def clean_untracked(self, repo_path: Path | str) -> None:
        """Remove untracked files and directories in *repo_path*."""
        self._run("clean", "-fd", cwd=repo_path)

    def rm_cached(self, repo_path: Path | str, path: str) -> None:
        """Remove *path* from the index (``git rm --cached``), keeping the working tree.

        Drops a tracked gitlink without deleting the child's working tree or
        its ``.git`` directory, preserving any local history inside the child.
        """
        self._run("rm", "--cached", path, cwd=repo_path)

    def create_tag(self, repo_path: Path | str, tag_name: str) -> None:
        """Create *tag_name* in *repo_path*."""
        self._run("tag", tag_name, cwd=repo_path)

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
        counts = self.branch_tracking_counts(repo_path)
        if counts is None:
            return None
        ahead, behind = counts
        if ahead and behind:
            return SyncState.DIVERGED
        if ahead:
            return SyncState.AHEAD
        if behind:
            return SyncState.BEHIND
        return SyncState.ALIGNED

    def upstream_ref(self, repo_path: Path | str) -> str | None:
        """Return the upstream ref for the current branch, e.g. ``origin/main``."""
        upstream = subprocess.run(
            [self.executable, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            cwd=str(repo_path),
            capture_output=True,
            check=False,
            text=True,
        )
        if upstream.returncode != 0:
            return None
        return upstream.stdout.strip() or None

    def branch_tracking_counts(self, repo_path: Path | str) -> tuple[int, int] | None:
        """Return ``(ahead, behind)`` counts against upstream for the current branch."""
        if self.upstream_ref(repo_path) is None:
            return None
        counts = self._run("rev-list", "--left-right", "--count", "HEAD...@{upstream}", cwd=repo_path)
        ahead_raw, behind_raw = counts.stdout.strip().split()
        return (int(ahead_raw), int(behind_raw))

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


def _release_snapshot_slug(release_name: str) -> str:
    """Return a filesystem-friendly release suffix for immutable .gts files."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", release_name.strip()).strip(".-_")
    return slug or "release"


# ============================================================
#  Nested config discovery
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


@dataclass(frozen=True, slots=True)
class SubmoduleEntry:
    """One git submodule entry parsed from ``.gitmodules``."""

    name: str
    path: str
    url: str
    branch: str


@dataclass(frozen=True, slots=True)
class ImportSubmodulesReport:
    """Result returned by :meth:`ComplexGitSyncClient.import_submodules`.

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
        to pass to :meth:`ComplexGitSyncClient.configure`.
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


def _url_to_repo_identifier(url: str) -> str:
    """Convert a git remote URL to a ComplexGitSync ``provider:owner/repo`` identifier.

    Supports HTTPS and SSH URL forms for GitHub and GitLab.  Custom-host
    URLs are passed through as-is (using the bare hostname as the provider
    token), which will fail :func:`~ComplexGitSync.cgs_format.parse_repo_id`
    validation downstream if the provider is not registered — the caller is
    responsible for handling that case.

    Examples
    --------
    >>> _url_to_repo_identifier("https://github.com/owner/repo.git")
    'github:owner/repo'
    >>> _url_to_repo_identifier("git@gitlab.com:group/sub/repo.git")
    'gitlab:group/sub/repo'
    """
    _PROVIDER_MAP = {
        "github.com": "github",
        "gitlab.com": "gitlab",
        "codeberg.org": "codeberg",
    }

    url = url.strip()
    if url.endswith(".git"):
        url = url[:-4]

    # SSH format: git@hostname:path/to/repo
    ssh_match = re.match(r"^git@([^:]+):(.+)$", url)
    if ssh_match:
        hostname = ssh_match.group(1).lower()
        path = ssh_match.group(2).strip("/")
        provider = _PROVIDER_MAP.get(hostname, hostname)
        return f"{provider}:{path}"

    # HTTPS/HTTP format: https://hostname/path/to/repo
    parsed = urlsplit(url)
    if parsed.scheme in ("https", "http") and parsed.netloc:
        hostname = parsed.netloc.lower()
        path = parsed.path.strip("/")
        provider = _PROVIDER_MAP.get(hostname, hostname)
        return f"{provider}:{path}"

    # Unknown format — return stripped URL and let downstream validation fail
    return url


DEFAULT_DISCOVER_MAX_DEPTH = 5


def _walk_git_repositories(root: Path, *, max_depth: int) -> list[Path]:
    """Return every directory under *root* that holds a ``.git``, root first.

    Used by :meth:`ComplexGitSyncClient.discover_repos`. Two rules matter:

    * A ``.git`` **file** counts, not just a directory. Git stores a
      submodule's real git directory under ``<parent>/.git/modules/<name>``
      and leaves a ``.git`` *file* in the working copy pointing at it.
    * ``.git`` is never descended into, so those ``modules/<name>``
      directories cannot be mistaken for a second copy of a repository that
      was already reported from its working-tree location.

    Depth is counted from *root* (itself depth 0). Nested repositories are
    reported in addition to their parent, not instead of it: a parent and
    its children are exactly the tree ComplexGitSync manages.
    """
    found: list[Path] = []

    def _descend(directory: Path, depth: int) -> None:
        if (directory / ".git").exists():
            found.append(directory)
        if depth >= max_depth:
            return
        try:
            children = sorted(directory.iterdir())
        except (PermissionError, OSError):
            return
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue
            if child.name == ".git":
                continue
            _descend(child, depth + 1)

    _descend(root, 0)
    return found


@dataclass(frozen=True, slots=True)
class DiscoveredRepo:
    """One git repository found on disk by :meth:`ComplexGitSyncClient.discover_repos`.

    Attributes
    ----------
    relative_path:
        Location relative to the scanned root — ``"."`` for the root
        repository itself. Taken directly from the filesystem walk, never
        inferred from a repository name.
    absolute_path:
        Resolved location on disk.
    remote_url:
        ``origin``'s URL, or ``None`` when the repository has no ``origin``.
    identifier:
        Canonical ``provider:owner/repository`` shorthand, or ``None`` when
        *remote_url* is missing or could not be parsed into one.
    branch:
        Currently checked-out branch, or ``None`` on a detached HEAD.
    has_cgs:
        ``True`` when the repository already contains its own ``*.cgs``.
        Drives ``nested_config`` in the generated draft: a child without one
        must be ``"disabled"``, or nested discovery looks for a file that
        does not exist and the clone fails.
    """

    relative_path: str
    absolute_path: Path
    remote_url: str | None
    identifier: str | None
    branch: str | None
    has_cgs: bool


@dataclass(frozen=True, slots=True)
class DiscoverReport:
    """Result returned by :meth:`ComplexGitSyncClient.discover_repos`.

    Attributes
    ----------
    root:
        The scanned directory.
    repos:
        Every git repository found, root first, then children ordered by
        ``relative_path``.
    cgs_entries:
        Authoring-form ``repos`` tables for the repositories that could be
        fully resolved — ready to pass to
        :meth:`ComplexGitSyncClient.configure`.
    warnings:
        Human-readable notes about repositories that were found but could
        *not* be turned into a ``.cgs`` entry (no ``origin``, or a remote
        URL that is not a recognised ``provider:owner/repository``). These
        are reported for a human to resolve, never guessed at.
    project_name:
        Name proposed for the draft document, taken from the root
        repository's own name when it is resolvable, else the directory name.
    """

    root: Path
    repos: tuple[DiscoveredRepo, ...]
    cgs_entries: tuple[dict, ...]
    warnings: tuple[str, ...]
    project_name: str


@dataclass(frozen=True, slots=True)
class GitignoreSyncEntry:
    """One repo whose ``.gitignore`` was created or modified by ``sync_gitignore()``."""

    repo_id: str
    name: str
    absolute_path: Path
    added_paths: tuple[str, ...]
    committed: bool = False


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

        configure(project, repositories) → CgsDocument  (offline)
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
    API access.
    """

    orchestre: Orchestre = field(default_factory=Orchestre)
    git_runner: GitRunner = field(default_factory=GitRunner)
    state_store: RuntimeStateStore = field(default_factory=RuntimeStateStore)
    registry: WorkingGitTree | None = None
    source_path: Path | None = None
    loaded_snapshot_path: Path | None = None
    last_memory_result: MemoryMemorizeResult | None = None
    last_gitignore_sync: tuple[GitignoreSyncEntry, ...] = ()
    run_logger: CommandRunLogger | None = None
    _memory_trigger_suppression_depth: int = 0

    def is_loaded(self) -> bool:
        return self.registry is not None or bool(self.orchestre.git_tree.repos)

    def configure(
        self,
        project: str | dict[str, Any],
        repositories: Sequence[str | dict[str, Any]],
        *,
        output_path: str | Path | None = None,
    ) -> CgsDocument:
        """Create a canonical ``.cgs`` document without interactive input.

        This public Python facade accepts the same authoring values collected
        by the CLI. Parsing, default normalization, and static validation are
        delegated to :class:`CgsDocument`; optional serialization is delegated
        to its ``to_toml()`` method. No Git or network operation is performed.

        Parameters
        ----------
        project:
            A project-name string or an authoring project table.
        repositories:
            Repository identifiers or advanced authoring tables.
        output_path:
            Optional destination for concise ``.cgs`` TOML. When omitted, the
            validated document is returned without writing a file.
        """
        document = CgsDocument.from_dict(
            {
                "project": project,
                "repos": list(repositories),
            }
        )
        if output_path is not None:
            document.to_toml(Path(output_path))
        return document

    def import_submodules(
        self,
        repo_root: str | Path,
        *,
        apply: bool = False,
        output: str | Path | None = None,
    ) -> ImportSubmodulesReport:
        """Report or convert git submodules in *repo_root* to plain nested clones.

        Parses ``<repo_root>/.gitmodules`` and, for each declared submodule:

        * **Dry-run** (``apply=False``, the default): returns an
          :class:`ImportSubmodulesReport` describing what would change
          — submodule names, paths, URLs, branches, and the ``.cgs`` entries
          that would be generated — without touching the repository.
        * **Apply** (``apply=True``): for each submodule in turn —

          1. Verifies the working tree at ``<repo_root>/<path>`` is clean
             (``git status --porcelain`` empty) and raises
             :exc:`~ComplexGitSync.errors.GitSyncError` if it is not —
             the same check the preflight machinery in ``operations.py``
             performs for every mutation operation.
          2. Runs ``git rm --cached <path>`` in *repo_root*, dropping the
             gitlink from the index while preserving the child's working
             tree and ``.git`` directory (no re-clone, no local history
             lost).
          3. Removes the submodule's stanza from ``.gitmodules`` (deletes
             the file entirely when all stanzas are removed), then stages
             the updated file.
          4. Calls the existing :func:`~ComplexGitSync.git_tree._update_gitignore_file`
             helper (``git_tree.py``) to append ``<path>`` to
             ``<repo_root>/.gitignore`` — the same step the ``.gitignore``
             lifecycle sync performs for every parent-child relationship.

        After applying, the converted submodule entries are available as
        ``report.cgs_entries``.  When *output* is given, a validated
        :class:`~ComplexGitSync.cgs_format.CgsDocument` containing those
        entries is written to that path — suitable for manual integration
        into the project's main ``.cgs`` file.

        Parameters
        ----------
        repo_root:
            Absolute (or resolvable) path to the local git repository that
            contains a ``.gitmodules`` file.
        apply:
            When ``False`` (default) the method is a pure read: it reports
            what would change without modifying anything. Set to ``True`` to
            perform the conversion.
        output:
            Optional path for the emitted ``.cgs`` document.  Only written
            when *apply* is ``True``.  Ignored in dry-run mode.

        Returns
        -------
        ImportSubmodulesReport
            Always returned, whether or not *apply* was set.
        """
        root = Path(repo_root).resolve()
        gitmodules_path = root / ".gitmodules"

        if not gitmodules_path.is_file():
            self._log_event(
                "import_submodules_no_gitmodules",
                repo_root=str(root),
                apply=apply,
            )
            return ImportSubmodulesReport(
                submodules=(),
                applied=False,
                converted=(),
                cgs_entries=(),
            )

        content = gitmodules_path.read_text(encoding="utf-8")
        submodules = tuple(_parse_gitmodules(content))

        self._log_event(
            "import_submodules_start",
            repo_root=str(root),
            submodule_count=len(submodules),
            apply=apply,
        )

        if not submodules:
            return ImportSubmodulesReport(
                submodules=(),
                applied=False,
                converted=(),
                cgs_entries=(),
            )

        # Build .cgs entries from submodule data (used for both dry-run report
        # and the apply path).
        cgs_entries: list[dict] = []
        for sub in submodules:
            try:
                identifier = _url_to_repo_identifier(sub.url)
            except Exception:
                identifier = sub.url  # leave invalid; downstream validate() will catch it
            entry: dict = {
                "repository": identifier,
                "relative_path": sub.path,
                "fallback_branch": sub.branch,
                "nested_config": "disabled",
            }
            cgs_entries.append(entry)

        if not apply:
            return ImportSubmodulesReport(
                submodules=submodules,
                applied=False,
                converted=(),
                cgs_entries=tuple(cgs_entries),
            )

        # --- apply=True: perform the conversion ---

        # 1. Preflight: every child working tree must be clean.
        for sub in submodules:
            child_path = root / sub.path
            if not child_path.exists():
                continue
            dirty_lines = self.git_runner.status_porcelain(child_path)
            if dirty_lines:
                raise GitSyncError(
                    f"import-submodules preflight failed: submodule '{sub.name}' "
                    f"at '{sub.path}' has uncommitted changes — stage or stash them first.\n"
                    + "\n".join(dirty_lines)
                )

        # 2. Per submodule: git rm --cached <path>, update .gitmodules, update .gitignore
        converted: list[str] = []
        for sub in submodules:
            self.git_runner.rm_cached(root, sub.path)
            converted.append(sub.name)
            _update_gitignore_file(root, [sub.path])
            self._log_event(
                "import_submodules_converted",
                repo_root=str(root),
                submodule_name=sub.name,
                submodule_path=sub.path,
                submodule_url=sub.url,
                submodule_branch=sub.branch,
            )

        # 3. Rewrite / remove .gitmodules — rebuild from remaining (unconverted)
        #    stanzas. Since we convert ALL submodules here, the file is removed.
        remaining_entries = [
            s for s in _parse_gitmodules(content) if s.name not in converted
        ]
        if remaining_entries:
            # Write back a .gitmodules with only the unconverted stanzas
            cfg = configparser.RawConfigParser()
            for sub in remaining_entries:
                section = f'submodule "{sub.name}"'
                cfg.add_section(section)
                cfg.set(section, "path", sub.path)
                cfg.set(section, "url", sub.url)
                if sub.branch != "main":
                    cfg.set(section, "branch", sub.branch)
            import io
            buf = io.StringIO()
            cfg.write(buf)
            gitmodules_path.write_text(buf.getvalue(), encoding="utf-8")
            self.git_runner.stage_path(root, ".gitmodules")
        else:
            # All submodules converted — remove .gitmodules entirely
            self.git_runner._run("rm", "--cached", ".gitmodules", cwd=root)
            gitmodules_path.unlink(missing_ok=True)

        # 4. Emit .cgs document when an output path is specified.
        if output is not None:
            project_name = root.name
            self.configure(
                project_name,
                cgs_entries,
                output_path=output,
            )

        return ImportSubmodulesReport(
            submodules=submodules,
            applied=True,
            converted=tuple(converted),
            cgs_entries=tuple(cgs_entries),
        )

    def discover_repos(
        self,
        root_dir: str | Path | None = None,
        *,
        max_depth: int = DEFAULT_DISCOVER_MAX_DEPTH,
        output: str | Path | None = None,
    ) -> DiscoverReport:
        """Scan *root_dir* for git repositories and draft a ``.cgs`` from what is there.

        This is the entry point for adopting a project that exists on disk
        but has no ``.cgs`` describing it yet. It is a **pure read** of the
        filesystem and of each repository's git config: nothing is cloned,
        fetched, staged, or modified, and no network call is made.

        The walk descends from *root_dir* up to *max_depth* levels, treating
        every directory that contains a ``.git`` entry as a repository. It
        never descends *into* a ``.git`` directory — for a submodule the real
        git directory lives at ``<parent>/.git/modules/<name>`` while the
        child's own ``.git`` is a file, so walking into it would report the
        same repository twice.

        For each repository found, ``origin``'s URL is read and converted to
        the canonical ``provider:owner/repository`` shorthand through the
        *existing* :func:`~ComplexGitSync.cgs_format.parse_repo_id` grammar —
        this method adds no second parser. ``relative_path`` comes straight
        from the walk rather than from a repository name, so a child mounted
        at ``external/Thing`` is recorded there and not at ``Thing``.

        Repositories with no ``origin``, or whose remote URL does not resolve
        to a registered provider, are reported in ``warnings`` and left out
        of ``cgs_entries``. They are never guessed at: a draft that silently
        invented an address would be worse than one that says what it could
        not determine.

        Only what is **checked out at scan time** can be found. In particular
        a repository cloned without ``--recurse-submodules`` leaves its
        submodule paths as empty directories, and those are correctly not
        reported here; recovering them from git metadata instead is
        :meth:`import_submodules`' job.

        Parameters
        ----------
        root_dir:
            Directory to scan. Defaults to the current working directory.
        max_depth:
            Maximum directory depth to descend below *root_dir*
            (default :data:`DEFAULT_DISCOVER_MAX_DEPTH`). The root itself is
            depth 0.
        output:
            Optional path to write the drafted ``.cgs`` to. When omitted,
            the draft is only returned — matching the "report first, write
            only when asked" posture of ``--commit-gitignore`` and
            ``import-submodules --apply``.

        Returns
        -------
        DiscoverReport
            The repositories found, the draft ``.cgs`` entries, and any
            warnings.
        """
        root = Path(root_dir).resolve() if root_dir is not None else Path.cwd().resolve()
        if not root.is_dir():
            raise GitSyncError(f"discover: not a directory: {root}")

        repos: list[DiscoveredRepo] = []
        warnings: list[str] = []

        for repo_path in _walk_git_repositories(root, max_depth=max_depth):
            relative = repo_path.relative_to(root).as_posix() if repo_path != root else "."
            remote_url = self.git_runner.remote_get_url(repo_path)
            try:
                branch = self.git_runner.current_branch(repo_path)
            except GitSyncError:
                # A repository with no commits yet has no resolvable HEAD.
                # That is a perfectly ordinary thing to find on disk, so it
                # must not abort the scan — report it as branch-less, the
                # same as a detached HEAD.
                branch = None
            has_cgs = any(repo_path.glob("*.cgs"))

            identifier: str | None = None
            if remote_url is None:
                warnings.append(
                    f"{relative}: no 'origin' remote — cannot determine an address; "
                    f"add one, or add this repository to the .cgs by hand."
                )
            else:
                candidate = _url_to_repo_identifier(remote_url)
                try:
                    parse_repo_id(candidate)
                except ValueError as exc:
                    warnings.append(
                        f"{relative}: remote {remote_url!r} does not map to a known "
                        f"provider:owner/repository ({exc}); add this repository to "
                        f"the .cgs by hand, or declare a custom provider for it."
                    )
                else:
                    identifier = candidate

            repos.append(
                DiscoveredRepo(
                    relative_path=relative,
                    absolute_path=repo_path,
                    remote_url=remote_url,
                    identifier=identifier,
                    branch=branch,
                    has_cgs=has_cgs,
                )
            )

        root_repo = next((r for r in repos if r.relative_path == "."), None)
        project_name = root.name
        if root_repo is not None and root_repo.identifier is not None:
            project_name = root_repo.identifier.rsplit("/", 1)[-1]

        cgs_entries: list[dict] = []
        for repo in repos:
            if repo.identifier is None:
                continue
            entry: dict = {
                "repository": repo.identifier,
                "relative_path": repo.relative_path,
            }
            if repo.branch:
                entry["fallback_branch"] = repo.branch
            # A repository with no .cgs of its own must not be left on the
            # default "auto", which would make nested discovery hunt for a
            # file that does not exist and fail the clone.
            if not repo.has_cgs:
                entry["nested_config"] = "disabled"
            cgs_entries.append(entry)

        self._log_event(
            "discover_repos",
            root=str(root),
            repo_count=len(repos),
            entry_count=len(cgs_entries),
            warning_count=len(warnings),
            max_depth=max_depth,
            output=str(output) if output is not None else None,
        )

        if output is not None:
            if not cgs_entries:
                raise GitSyncError(
                    f"discover: no resolvable git repository found under {root} — "
                    f"nothing to write."
                )
            self.configure(project_name, cgs_entries, output_path=output)

        return DiscoverReport(
            root=root,
            repos=tuple(repos),
            cgs_entries=tuple(cgs_entries),
            warnings=tuple(warnings),
            project_name=project_name,
        )

    def _sync_gitignore_lifecycle(
        self,
        *,
        pre_pull: bool = True,
        force_pull_fallback: bool = False,
        commit: bool = False,
    ) -> tuple[GitignoreSyncEntry, ...]:
        """Run the ``.gitignore`` lifecycle sync (DevPlanTicket Milestones 1-2).

        Every repo with children is safely pulled (parent-first, via
        :func:`iter_tree`) before its ``.gitignore`` is written, so the
        write starts from an up-to-date base. If the safe pull fails for
        any such repo:

        - by default (*force_pull_fallback* False), no ``.gitignore`` is
          written at all and this raises :exc:`~.errors.GitSyncError`
          immediately — no forcing, no silent degradation;
        - with *force_pull_fallback* True (``--force-gitignore-sync``),
          that one repo falls back to :meth:`GitRunner.force_pull`
          (fetch + ``checkout -B <branch> FETCH_HEAD`` + ``clean -fd``)
          instead of erroring out. This never force-*pushes* — that
          remains forbidden regardless of any flag.

        Returns one :class:`GitignoreSyncEntry` per repo whose
        ``.gitignore`` was actually created or modified, and also records
        them on :attr:`last_gitignore_sync` for the CLI to report.

        *pre_pull* can be set to ``False`` when the caller already pulled
        every repo in the tree immediately beforehand (e.g. ``restart()``'s
        own tree-wide pull already satisfies this step; repeating it here
        would just be a redundant no-op fast-forward per repo).

        *commit* gates Phase C (``--commit-gitignore``): when ``False``
        (the default), nothing is staged, committed, or pushed — the sync
        only writes the file and reports what changed. When ``True``, each
        changed repo has its ``.gitignore`` staged (and only that file),
        committed, and pushed — see :meth:`_commit_and_push_gitignore_sync`.
        """
        registry = self.registry
        assert registry is not None

        if pre_pull:
            for entry in iter_tree(registry):
                if not registry.children_of(entry.repo_id):
                    continue
                current_branch = self.git_runner.current_branch(entry.absolute_path)
                if current_branch is None:
                    current_branch = entry.resolved_ref_name or entry.target_ref_name or "main"
                try:
                    self.git_runner.pull(entry.absolute_path, ref_name=current_branch)
                except GitSyncError as exc:
                    if not force_pull_fallback:
                        raise GitSyncError(
                            f"gitignore sync preflight failed: could not safely pull {entry.name!r} "
                            f"({entry.absolute_path}) before writing its .gitignore: {exc}"
                        ) from exc
                    self.git_runner.force_pull(entry.absolute_path, ref_name=current_branch)

        pending_paths: dict[str, tuple[str, ...]] = {}
        for entry in iter_tree(registry):
            children = registry.children_of(entry.repo_id)
            if not children:
                continue
            gitignore_path = entry.absolute_path / ".gitignore"
            try:
                existing_lines = gitignore_path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                existing_lines = []
            relative_paths = sorted(
                child.absolute_path.relative_to(entry.absolute_path).as_posix() for child in children
            )
            missing = tuple(path for path in relative_paths if path not in existing_lines)
            if missing:
                pending_paths[entry.repo_id] = missing

        changed_repo_ids = sync_gitignore(registry)

        synced_entries = tuple(
            GitignoreSyncEntry(
                repo_id=repo_id,
                name=registry.get(repo_id).name,
                absolute_path=registry.get(repo_id).absolute_path,
                added_paths=pending_paths.get(repo_id, ()),
                committed=commit,
            )
            for repo_id in changed_repo_ids
        )
        for record in synced_entries:
            self._log_event(
                "gitignore_sync_updated",
                repo_id=record.repo_id,
                repo_name=record.name,
                absolute_path=record.absolute_path,
                added_paths=record.added_paths,
            )
        if commit and synced_entries:
            self._commit_and_push_gitignore_sync(synced_entries)
        self.last_gitignore_sync = synced_entries
        return synced_entries

    def _commit_and_push_gitignore_sync(self, entries: tuple[GitignoreSyncEntry, ...]) -> None:
        """Phase C (DevPlanTicket Milestone 2, ``--commit-gitignore``).

        Only called once the caller has explicitly approved it. For each
        entry: stage ``.gitignore`` alone (never ``git add --all`` — this
        must not sweep in unrelated dirty work already in progress),
        commit with a message listing exactly which children were added,
        then push. Never force-pushes.
        """
        for record in entries:
            current_branch = self.git_runner.current_branch(record.absolute_path)
            self.git_runner.stage_path(record.absolute_path, ".gitignore")
            message_lines = [
                "chore(cgitsync): sync .gitignore for nested repo tree",
                "",
                "Added:",
            ]
            message_lines.extend(f"  {path}" for path in record.added_paths)
            user_name, user_email = MasterConfig.resolve_identity(record.absolute_path, self.git_runner)
            self.git_runner.commit(
                record.absolute_path,
                "\n".join(message_lines),
                user_name=user_name,
                user_email=user_email,
            )
            self.git_runner.push(record.absolute_path, ref_name=current_branch)
            self._log_event(
                "gitignore_sync_committed",
                repo_id=record.repo_id,
                repo_name=record.name,
                absolute_path=record.absolute_path,
                added_paths=record.added_paths,
            )

    def load_cgs(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> WorkingGitTree:
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        self.registry = build_registry_from_cgs_document(document, source_path)
        self.orchestre.git_tree.git.bind_tree(self.registry)
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
    ) -> WorkingGitTree:
        """Unified initialisation entry point (lifecycle step 1).

        Dispatches based on source file extension:

        - ``.cgs`` source: initialises the workspace using CGSPATH/CGSHOME
          semantics (calls :meth:`initialise_cgs`).  The output path is
          CGSPATH, and CGSHOME is derived as ``CGSPATH/<project_name>`` after
          reading the ``.cgs``.  The root repository at CGSHOME is treated as
          already existing and is never recloned.  All ComplexGitSync state is
          written under ``CGSHOME/.cgitsync/state(<hash>)_n/``.
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
        clean_before_clone: bool = False,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
    ) -> WorkingGitTree:
        """Initialise a workspace using CGSPATH/CGSHOME semantics.

        ``output_path`` is CGSPATH.  The ``.cgs`` file is read first, CGSHOME
        is derived as ``CGSPATH/<project_name>``, and that root repository is
        treated as already existing.  The clone sequence runs only for the
        dependencies declared in the ``.cgs`` document.

        All ComplexGitSync state is stored under
        ``CGSHOME/.cgitsync/state(<hash>)_n/``.

        Parameters
        ----------
        config_path:
            Path to the ``.cgs`` authoring spec.
        output_path:
            CGSPATH — parent directory used to derive CGSHOME as
            ``CGSPATH/<project_name>``.  When *None*, defaults to ``../..``
            relative to the current working directory
            (``CWD=$CGSHOME/ComplexGitSync``), unless ``CGSHOME`` is set.
        commit_gitignore:
            Explicit approval (``--commit-gitignore``) to stage, commit, and
            push any ``.gitignore`` the lifecycle sync updates. Default
            ``False``: the sync only writes the file and reports it.
        force_gitignore_sync:
            Opt-in (``--force-gitignore-sync``) fallback to pull-force
            semantics for a repo whose safe pull fails before its
            ``.gitignore`` is synced, instead of raising. Never force-pushes.
        git_user_name, git_user_email:
            Override the Git identity used for ComplexGitSync-authored
            commits (``--git-user-name``/``--git-user-email``). Persisted to
            ``CGSHOME/.cgitsync/master.toml`` via :class:`~.master.MasterConfig`
            so later invocations on this workspace pick it up without
            repeating the flag. ``None`` (the default) leaves whatever is
            already configured/persisted, or local git config, untouched.
        """
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        return self.initialise_cgs_document(
            document,
            source_path=source_path,
            output_path=output_path,
            clean_before_clone=clean_before_clone,
            commit_gitignore=commit_gitignore,
            force_gitignore_sync=force_gitignore_sync,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
        )

    def initialise_cgs_document(
        self,
        document: CgsDocument,
        *,
        source_path: str | Path,
        output_path: str | Path | None = None,
        clean_before_clone: bool = False,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
    ) -> WorkingGitTree:
        """Initialise from an already-normalized, validated ``CgsDocument``.

        ``source_path`` is the logical origin used for relative paths, state
        metadata, and logging. It need not exist for direct CLI authoring.
        See :meth:`initialise_cgs` for ``commit_gitignore``/
        ``force_gitignore_sync``/``git_user_name``/``git_user_email``.
        """
        document.validate()
        previous_tree_state = (
            self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        )
        source_path = Path(source_path).resolve()
        cgshome = self.resolve_cgshome(document, source_path, output_path=output_path)
        MasterConfig.load(cgshome)
        if git_user_name is not None or git_user_email is not None:
            MasterConfig.persist(cgshome, user_name=git_user_name, user_email=git_user_email)
        project_root = cgshome

        self.registry = build_registry_from_cgs_document(
            document,
            source_path,
            project_root=project_root,
        )
        self.orchestre.git_tree.git.bind_tree(self.registry)
        self.source_path = source_path

        root_entry = self.registry.get(ROOT_REPO_ID)
        self._attach_existing_root(root_entry, project_root)

        if clean_before_clone:
            self._purge_registry_workspace(self.registry)

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
        self._sync_gitignore_lifecycle(
            force_pull_fallback=force_gitignore_sync,
            commit=commit_gitignore,
        )
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

    def clean_initialise_cgs(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
    ) -> WorkingGitTree:
        """Initialise a .cgs workspace after purging generated clone state."""
        return self.initialise_cgs(
            config_path,
            output_path=output_path,
            clean_before_clone=True,
            commit_gitignore=commit_gitignore,
            force_gitignore_sync=force_gitignore_sync,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
        )

    def clean_init(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
    ) -> WorkingGitTree:
        """Initialise a .cgs workspace after purging generated clone state."""
        return self.clean_initialise_cgs(
            config_path,
            output_path=output_path,
            commit_gitignore=commit_gitignore,
            force_gitignore_sync=force_gitignore_sync,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
        )

    def purge_cgs(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> tuple[Path, ...]:
        """Remove immediate child repos and project ledgers from CGSHOME."""
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        cgshome = self.resolve_cgshome(document, source_path, output_path=output_path)
        self.registry = build_registry_from_cgs_document(
            document,
            source_path,
            project_root=cgshome,
        )
        self.orchestre.git_tree.git.bind_tree(self.registry)
        self.source_path = source_path
        return self._purge_registry_workspace(self.registry)

    def purge(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> tuple[Path, ...]:
        """Remove generated clone state for a .cgs workspace."""
        return self.purge_cgs(config_path, output_path=output_path)

    def _purge_registry_workspace(self, registry: WorkingGitTree) -> tuple[Path, ...]:
        root_entry = registry.get(ROOT_REPO_ID)
        root_path = root_entry.absolute_path
        removed: list[Path] = []
        self._log_event("fs_purge_start", root_path=root_path)

        for entry in sorted(registry.values(), key=lambda candidate: candidate.name):
            if entry.parent_id != ROOT_REPO_ID:
                continue
            if entry.absolute_path.parent != root_path:
                continue
            if entry.absolute_path == root_path:
                continue
            if self._remove_workspace_path(entry.absolute_path):
                removed.append(entry.absolute_path)
                self._log_event("fs_purge_removed", path=entry.absolute_path)

        for lgr_path in sorted(root_path.glob("*.lgr")):
            if self._remove_workspace_path(lgr_path):
                removed.append(lgr_path)
                self._log_event("fs_purge_removed", path=lgr_path)

        self._log_event("fs_purge_end", root_path=root_path, removed_count=len(removed))
        return tuple(removed)

    @staticmethod
    def _remove_workspace_path(path: Path) -> bool:
        if path.is_dir():
            shutil.rmtree(path)
            return True
        if path.exists():
            path.unlink()
            return True
        return False

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

    def remember(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
        service: str = DEFAULT_MEMORY_SERVICE,
        remote_name: str = DEFAULT_MEMORY_REMOTE_NAME,
    ) -> MemoryRememberResult:
        """Bind a ``.cgs`` artefact to its external SSH-Git Memory endpoint."""
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        project_root = self.resolve_cgshome(document, source_path, output_path=output_path)
        binding = MemoryBinding.for_name(
            document.project_name or source_path.stem,
            service=service,
            remote_name=remote_name,
        )
        self.git_runner.validate_memory_remote(binding.remote_url)
        store = MemoryBindingStore(project_root)
        config_file = store.save(binding)
        self._log_event(
            "memory_remember",
            name=binding.name,
            alias=binding.alias,
            remote_name=binding.remote_name,
            remote_url=binding.remote_url,
            project_root=project_root,
            config_path=config_file,
            remote_validated=True,
        )
        return MemoryRememberResult(
            binding=binding,
            config_path=config_file,
            project_root=project_root,
            remote_validated=True,
        )

    def load_memory_binding(self, project_root: str | Path) -> MemoryBinding:
        """Load a persisted external Memory binding from ``CGSHOME``."""
        return MemoryBindingStore(project_root).load()

    def retrieve(
        self,
        name: str,
        *,
        output_path: str | Path | None = None,
        branch: str = "main",
        service: str = DEFAULT_MEMORY_SERVICE,
        remote_name: str = DEFAULT_MEMORY_REMOTE_NAME,
    ) -> MemoryRetrieveResult:
        """Retrieve an external SSH-Git Memory repository into a local CGSHOME."""
        binding = MemoryBinding.for_name(name, service=service, remote_name=remote_name)
        if output_path is not None:
            project_root = (Path(output_path).expanduser().resolve() / binding.name).resolve()
        elif os.environ.get("CGSHOME"):
            project_root = Path(os.environ["CGSHOME"]).expanduser().resolve()
        else:
            project_root = (Path.cwd() / binding.name).resolve()

        self.git_runner.validate_memory_remote(binding.remote_url)
        remote_ref = self.git_runner.remote_head(binding.remote_url, branch)
        if remote_ref is None:
            raise GitSyncError(
                f"No remote Memory revision found for {binding.alias} on branch {branch!r}."
            )

        memory_repo = _memory_repository_path(project_root)
        if not memory_repo.exists():
            self.git_runner.clone(binding.remote_url, memory_repo, branch=branch)
        elif self.git_runner.is_git_repository(memory_repo):
            self.git_runner.configure_remote(memory_repo, binding.remote_name, binding.remote_url)
            self.git_runner.fetch_branch(memory_repo, binding.remote_name, branch)
            self.git_runner.reset_to_fetch_head(memory_repo)
            self.git_runner.checkout_branch(memory_repo, branch)
        else:
            if any(memory_repo.iterdir()):
                raise GitSyncError(
                    f"Memory repository path exists but is not a Git worktree: {memory_repo}"
                )
            self.git_runner.clone(binding.remote_url, memory_repo, branch=branch)

        self.git_runner.configure_remote(memory_repo, binding.remote_name, binding.remote_url)
        self.git_runner.fsck_full(memory_repo)
        local_ref = self.git_runner.rev_parse_head(memory_repo)
        remote_after = self.git_runner.remote_head(binding.remote_url, branch)
        verified = remote_after == local_ref
        if not verified or remote_after is None:
            raise GitSyncError(
                "Memory retrieval verification failed: remote ref does not match local Memory commit."
            )

        source_cgitsync = memory_repo / ".cgitsync"
        _validate_memory_cgitsync_tree(source_cgitsync)
        project_root.mkdir(parents=True, exist_ok=True)
        target_cgitsync = project_root / ".cgitsync"
        if target_cgitsync.exists():
            if any(target_cgitsync.iterdir()):
                raise GitSyncError(
                    f"Cannot retrieve Memory into non-empty .cgitsync directory: {target_cgitsync}"
                )
            target_cgitsync.rmdir()
        shutil.copytree(source_cgitsync, target_cgitsync, ignore=_memory_copy_ignore)
        MemoryBindingStore(project_root).save(binding)
        state_paths = _validate_memory_cgitsync_tree(target_cgitsync)

        result = MemoryRetrieveResult(
            binding=binding,
            project_root=project_root,
            memory_repository_path=memory_repo,
            cgitsync_path=target_cgitsync,
            state_paths=state_paths,
            local_ref=local_ref,
            remote_ref=remote_after,
            verified=True,
            status="retrieved",
        )
        self._log_event(
            "memory_retrieve",
            name=binding.name,
            alias=binding.alias,
            remote_name=binding.remote_name,
            remote_url=binding.remote_url,
            project_root=project_root,
            memory_repository_path=memory_repo,
            cgitsync_path=target_cgitsync,
            state_count=len(state_paths),
            local_ref=local_ref,
            remote_ref=remote_after,
            verified=True,
            status=result.status,
        )
        return result

    def reload(
        self,
        name: str,
        *,
        output_path: str | Path | None = None,
        branch: str = "main",
        service: str = DEFAULT_MEMORY_SERVICE,
        remote_name: str = DEFAULT_MEMORY_REMOTE_NAME,
    ) -> MemoryReloadResult:
        """Restore execution context from externally retrieved Memory."""
        retrieve_result = self.retrieve(
            name,
            output_path=output_path,
            branch=branch,
            service=service,
            remote_name=remote_name,
        )
        state_paths = _validate_memory_cgitsync_tree(retrieve_result.cgitsync_path)
        selection = _select_latest_memory_state(retrieve_result.cgitsync_path, state_paths)
        registry = self.load_gts(selection.snapshot_path)
        self._rebase_registry_to_project_root(
            registry,
            project_root=retrieve_result.project_root,
            source_cgs_path=selection.source_cgs_path,
        )
        self.loaded_snapshot_path = selection.snapshot_path
        self.source_path = selection.source_cgs_path or selection.snapshot_path
        if selection.source_cgs_path is not None:
            self.state_store.record_snapshot(selection.source_cgs_path, selection.snapshot_path)

        result = MemoryReloadResult(
            binding=retrieve_result.binding,
            retrieve_result=retrieve_result,
            project_root=retrieve_result.project_root,
            cgitsync_path=retrieve_result.cgitsync_path,
            state_path=selection.state_path,
            snapshot_path=selection.snapshot_path,
            source_cgs_path=selection.source_cgs_path,
            registry=registry,
            status="reloaded",
        )
        self._log_event(
            "memory_reload",
            name=result.binding.name,
            alias=result.binding.alias,
            project_root=result.project_root,
            cgitsync_path=result.cgitsync_path,
            state_path=result.state_path,
            snapshot_path=result.snapshot_path,
            source_cgs_path=result.source_cgs_path,
            status=result.status,
        )
        return result

    def _rebase_registry_to_project_root(
        self,
        registry: WorkingGitTree,
        *,
        project_root: Path,
        source_cgs_path: Path | None,
    ) -> None:
        """Re-root a loaded snapshot registry under the recovered project root."""
        root_entry = registry.get(ROOT_REPO_ID)
        previous_root = root_entry.absolute_path
        root_entry.absolute_path = project_root.resolve()
        root_entry.relative_path = Path(".")
        if source_cgs_path is not None:
            root_entry.source_cgs_path = source_cgs_path.resolve()

        for entry in iter_tree(registry):
            if entry.repo_id == ROOT_REPO_ID or entry.parent_id is None:
                continue
            parent = registry.get(entry.parent_id)
            relative_path = entry.relative_path
            if relative_path is None:
                try:
                    relative_path = entry.absolute_path.relative_to(previous_root)
                except ValueError:
                    relative_path = Path(entry.name)
            entry.absolute_path = (parent.absolute_path / relative_path).resolve()
            if entry.source_cgs_path is not None:
                entry.source_cgs_path = _rebase_path_under_root(
                    entry.source_cgs_path,
                    old_root=previous_root,
                    new_root=project_root,
                )
        registry.recompute_tree_state()
        self.orchestre.git_tree.git.bind_tree(registry)

    def _trigger_memorize_after_success(
        self,
        current_memory_path: str | Path,
        *,
        trigger: str,
    ) -> MemoryMemorizeResult | None:
        """Persist the current Memory State when the project has a binding."""
        memory_path = Path(current_memory_path).resolve()
        project_root = memory_path.parent.parent
        if self._memory_trigger_suppression_depth > 0:
            self.last_memory_result = None
            self._log_event(
                "memory_memorize_skipped",
                trigger=trigger,
                reason="suppressed",
                current_memory_path=memory_path,
                project_root=project_root,
            )
            return None

        binding_store = MemoryBindingStore(project_root)
        if not binding_store.config_path.is_file():
            self.last_memory_result = None
            self._log_event(
                "memory_memorize_skipped",
                trigger=trigger,
                reason="missing_binding",
                current_memory_path=memory_path,
                project_root=project_root,
            )
            return None

        self._log_event(
            "memory_memorize_trigger",
            trigger=trigger,
            current_memory_path=memory_path,
            project_root=project_root,
        )
        result = self.memorize(memory_path)
        self.last_memory_result = result
        self._log_event(
            "memory_memorize_triggered",
            trigger=trigger,
            current_memory_path=memory_path,
            status=result.status,
            commit_created=result.commit_created,
            pushed=result.pushed,
            verified=result.verified,
        )
        return result

    def memorize(
        self,
        current_memory_path: str | Path,
        *,
        branch: str = "main",
    ) -> MemoryMemorizeResult:
        """Persist a finalized local Memory State to the configured SSH-Git remote."""
        memory_path, state_hash, state_order = _validate_current_memory_path(current_memory_path)
        cgitsync_dir = memory_path.parent
        project_root = cgitsync_dir.parent
        binding = MemoryBindingStore(project_root).load()
        binding.validate()
        self.git_runner.validate_memory_remote(binding.remote_url)

        memory_repo = _memory_repository_path(project_root)
        if not memory_repo.exists():
            self.git_runner.init_repository(memory_repo)
        elif not self.git_runner.is_git_repository(memory_repo):
            if any(memory_repo.iterdir()):
                raise GitSyncError(f"Memory repository path exists but is not a Git worktree: {memory_repo}")
            self.git_runner.init_repository(memory_repo)

        self.git_runner.checkout_branch(memory_repo, branch)
        self.git_runner.configure_remote(memory_repo, binding.remote_name, binding.remote_url)
        remote_before = self.git_runner.remote_head(binding.remote_url, branch)
        if remote_before is not None:
            self.git_runner.fetch_branch(memory_repo, binding.remote_name, branch)
            self.git_runner.reset_to_fetch_head(memory_repo)
            self.git_runner.checkout_branch(memory_repo, branch)

        target_cgitsync = memory_repo / ".cgitsync"
        if target_cgitsync.exists():
            shutil.rmtree(target_cgitsync)
        shutil.copytree(cgitsync_dir, target_cgitsync, ignore=_memory_copy_ignore)

        self.git_runner.stage_all(memory_repo)
        commit_created = False
        pushed = False
        if self.git_runner.has_staged_changes(memory_repo):
            commit_message = (
                f"memory({binding.name}): persist state {state_hash[:8]} iteration {state_order}"
            )
            self.git_runner.commit(memory_repo, commit_message)
            commit_created = True
            self.git_runner.push(
                memory_repo,
                remote=binding.remote_name,
                ref_name=f"{branch}:{branch}",
            )
            pushed = True

        local_ref = self.git_runner.rev_parse_head(memory_repo)
        remote_after = self.git_runner.remote_head(binding.remote_url, branch)
        verified = remote_after == local_ref
        if not verified:
            raise GitSyncError(
                "Memory persistence verification failed: remote ref does not match local Memory commit."
            )
        status = "persisted" if commit_created else "unchanged"
        result = MemoryMemorizeResult(
            binding=binding,
            current_memory_path=memory_path,
            project_root=project_root,
            memory_repository_path=memory_repo,
            state_hash=state_hash,
            state_order=state_order,
            commit_created=commit_created,
            pushed=pushed,
            verified=verified,
            local_ref=local_ref,
            remote_ref=remote_after,
            status=status,
        )
        self._log_event(
            "memory_memorize",
            name=binding.name,
            alias=binding.alias,
            remote_name=binding.remote_name,
            remote_url=binding.remote_url,
            current_memory_path=memory_path,
            memory_repository_path=memory_repo,
            state_hash=state_hash,
            state_order=state_order,
            commit_created=commit_created,
            pushed=pushed,
            verified=verified,
            local_ref=local_ref,
            remote_ref=remote_after,
            status=status,
        )
        return result

    def load(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> WorkingGitTree:
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
        fixed = _fix_circularities(registry)
        normalize_node_types(registry)
        registry.recompute_tree_state()
        return fixed

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

    def load_gts(self, snapshot_path: str | Path) -> WorkingGitTree:
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        resolved_snapshot_path = Path(snapshot_path).resolve()
        document = GtsDocument.from_toml(resolved_snapshot_path)
        self.registry = build_registry_from_gts_document(document)
        self.orchestre.git_tree.git.bind_tree(self.registry)
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
    ) -> WorkingGitTree:
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
    ) -> WorkingGitTree:
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
    ) -> WorkingGitTree:
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        project_root = self._resolve_project_root(document, source_path, target_dir, output_path)

        self.registry = build_registry_from_cgs_document(
            document,
            source_path,
            project_root=project_root,
        )
        self.orchestre.git_tree.git.bind_tree(self.registry)
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
    ) -> WorkingGitTree:
        """Clone a project tree from a ``.cgs`` source."""
        return self.clone_cgs(config_path, target_dir=target_dir, output_path=output_path)

    def resolve_bootstrap_root(
        self,
        project_name: str,
        *,
        cgs_path: str | Path | None = None,
    ) -> Path:
        """Resolve the isolated CGSHOME a :meth:`bootstrap` run will clone into.

        ``project_name`` always forms the final path segment, regardless of
        the ``.cgs`` document's own ``project_name`` field, so the
        destination is explicit rather than inferred. When *cgs_path* is
        omitted, it defaults to a fresh ``$HOME/.cgs/CGS<timestamp>/``
        directory (``$HOME/.cgs`` is created if missing) so a bootstrapped
        project never lands inside the ComplexGitSync clone itself — running
        ComplexGitSync standalone must never mix its own repo with the
        project state it manages.
        """
        if not project_name:
            raise ValueError("bootstrap requires a non-empty project_name.")
        if cgs_path is not None:
            cgspath = Path(cgs_path).expanduser().resolve()
        else:
            cgs_root = (Path.home() / ".cgs").expanduser().resolve()
            cgs_root.mkdir(parents=True, exist_ok=True)
            cgspath = cgs_root / f"CGS{datetime.now(UTC):%Y%m%d%H%M%S}"
        return (cgspath / project_name).resolve()

    def bootstrap(
        self,
        config_path: str | Path,
        project_name: str,
        *,
        cgs_path: str | Path | None = None,
    ) -> WorkingGitTree:
        """Bootstrap a brand-new workspace tree from a standalone ComplexGitSync clone.

        Unlike :meth:`initialise_cgs` (which assumes CGSHOME already exists,
        with ComplexGitSync itself cloned inside it), this clones the full
        tree — including the root — from scratch, so ComplexGitSync can be
        run from its own clone (e.g. installed once, used across many
        projects) without ever writing project state into it. See
        :meth:`resolve_bootstrap_root` for how the destination is derived
        from *project_name* and *cgs_path*.

        Parameters
        ----------
        config_path:
            Path to the ``.cgs`` authoring spec.
        project_name:
            Required name for the workspace; forms the last path segment of
            CGSHOME regardless of the ``.cgs`` document's own project name.
        cgs_path:
            CGSPATH override. When *None*, defaults to a fresh
            ``$HOME/.cgs/CGS<timestamp>/`` directory.
        """
        source_path = Path(config_path).resolve()
        if source_path.suffix != ".cgs":
            raise ValueError(
                f"bootstrap requires a .cgs source, got '{source_path.suffix}' for {source_path!s}."
            )
        target_dir = self.resolve_bootstrap_root(project_name, cgs_path=cgs_path)
        return self.clone_cgs(source_path, target_dir=target_dir)

    def restart(
        self,
        config_path: str | Path,
        *,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
    ) -> WorkingGitTree:
        """Resynchronize an already-cloned tree from a ``.cgs`` file.

        Loads the ``.cgs`` configuration, discovers nested configs, then
        checks out the root repository's current branch across the whole tree
        parent-first.  Ends in ``READY`` or raises
        :exc:`~ComplexGitSync.errors.GitSyncError`. See
        :meth:`ComplexGitSyncClient.initialise_cgs` for
        ``commit_gitignore``/``force_gitignore_sync``/``git_user_name``/
        ``git_user_email``.
        """
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        resolved_path = Path(config_path).resolve()
        self._log_event("restart_start", config_path=resolved_path)
        restart_cgshome = self.resolve_initialise_cgshome(resolved_path)
        MasterConfig.load(restart_cgshome)
        if git_user_name is not None or git_user_email is not None:
            MasterConfig.persist(restart_cgshome, user_name=git_user_name, user_email=git_user_email)
        registry = self.load_cgs(resolved_path, discover_nested=True)
        self.orchestre.git_tree.git.pull(self.git_runner)
        self._sync_gitignore_lifecycle(
            pre_pull=False,
            force_pull_fallback=force_gitignore_sync,
            commit=commit_gitignore,
        )
        if not registry.is_ready():
            raise GitSyncError("restart did not produce a READY tree.")
        snapshot_path = self.write_gts_snapshot(command_origin="restart")
        self.state_store.record_snapshot(resolved_path, snapshot_path)
        self._log_tree_transition(previous_tree_state, registry.lifecycle_state, reason="restart")
        self._log_event("restart_end", config_path=resolved_path)
        return registry

    def pull(
        self,
        source_path: str | Path,
        *,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
    ) -> WorkingGitTree:
        """Resynchronize from a ``.cgs`` spec or restore from a ``.gts`` snapshot.

        ``commit_gitignore``/``force_gitignore_sync``/``git_user_name``/
        ``git_user_email`` only apply to ``.cgs`` sources (dispatched to
        :meth:`restart`) — a ``.gts`` source runs no discovery, so there is
        nothing new for the ``.gitignore`` lifecycle sync to find.
        """
        resolved_source = Path(source_path).resolve()
        if resolved_source.suffix == ".cgs":
            return self.restart(
                resolved_source,
                commit_gitignore=commit_gitignore,
                force_gitignore_sync=force_gitignore_sync,
                git_user_name=git_user_name,
                git_user_email=git_user_email,
            )
        if resolved_source.suffix == ".gts":
            previous_tree_state = (
                self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
            )
            self._log_event("pull_start", snapshot_path=resolved_source)
            registry = self.load_gts(resolved_source)
            registry_values = registry.values() if hasattr(registry, "values") else ()
            if any(not entry.absolute_path.exists() for entry in registry_values):
                registry = self._restore_gts_snapshot(resolved_source)
            else:
                self.orchestre.git_tree.git.pull(self.git_runner)
            if not registry.is_ready():
                raise GitSyncError("pull did not produce a READY tree.")
            snapshot_path = self.write_gts_snapshot(command_origin="pull")
            self.state_store.record_snapshot(resolved_source, snapshot_path)
            self._log_tree_transition(previous_tree_state, registry.lifecycle_state, reason="pull")
            self._log_event("pull_end", snapshot_path=resolved_source, output_gts=snapshot_path)
            return registry
        raise ValueError(
            f"Unsupported source format '{resolved_source.suffix}' for {resolved_source!s}; expected .cgs or .gts."
        )

    def pull_force(self, source_path: str | Path) -> WorkingGitTree:
        """Destructively resynchronize from a ``.cgs`` spec or ``.gts`` snapshot."""
        resolved_source = Path(source_path).resolve()
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        self._log_event("pull_force_start", source_path=resolved_source)
        if resolved_source.suffix == ".cgs":
            registry = self.load_cgs(resolved_source, discover_nested=True)
        elif resolved_source.suffix == ".gts":
            registry = self.load_gts(resolved_source)
        else:
            raise ValueError(
                f"Unsupported source format '{resolved_source.suffix}' for {resolved_source!s}; expected .cgs or .gts."
            )
        self.orchestre.git_tree.git.pull_force(self.git_runner)
        if not registry.is_ready():
            raise GitSyncError("pull-force did not produce a READY tree.")
        snapshot_path = self.write_gts_snapshot(command_origin="pull-force")
        self.state_store.record_snapshot(resolved_source, snapshot_path)
        self._log_tree_transition(previous_tree_state, registry.lifecycle_state, reason="pull-force")
        self._log_event("pull_force_end", source_path=resolved_source, output_gts=snapshot_path)
        return registry

    def checkout(
        self,
        branch_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
    ) -> WorkingGitTree:
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
    ) -> WorkingGitTree:
        """Create *branch_name* across the full tree without checkout."""
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("branch_start", branch_name=branch_name)
        self.orchestre.git_tree.git.branch(self.git_runner, branch_name)
        if ROOT_REPO_ID in registry.repos:
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
    ) -> WorkingGitTree:
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

    def add(self) -> WorkingGitTree:
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

    def push(self) -> WorkingGitTree:
        """Push all repos to their remotes, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  After a
        successful execution the registry remains ``READY`` and refreshes the
        stored commit hashes in the runtime tree state.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self.last_memory_result = None
        self._log_event("push_start")
        self.orchestre.git_tree.git.push(self.git_runner)
        snapshot_path = self.write_gts_snapshot(command_origin="push")
        if self.source_path is not None:
            self.state_store.record_snapshot(self.source_path, snapshot_path)
        memory_result = self._trigger_memorize_after_success(
            snapshot_path.parent,
            trigger="push.success",
        )
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="push")
        self._log_event(
            "push_end",
            memory_status=(memory_result.status if memory_result is not None else "unbound"),
        )
        return registry

    def tag(self, tag_name: str) -> WorkingGitTree:
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
        gittree: WorkingGitTree | None,
        command: str,
        *args: str,
    ) -> WorkingGitTree:
        """Dispatch a git command across the full tree (lifecycle step 5).

        This is the unified git interface.  It dispatches *command* to the
        appropriate tree-wide operation and returns the updated registry.
        Ordering is command-specific (for example, ``pull``/``branch``/``checkout``
        run parent-first while ``push`` runs leaf-first).

        Parameters
        ----------
        gittree:
            The :class:`~.git_tree.WorkingGitTree` to operate on.
            Pass ``None`` to use the currently loaded registry.  Passing a
            registry replaces the active registry for the duration of the call.
        command:
            One of ``"pull"``, ``"checkout"``, ``"branch"``, ``"add"``,
            ``"commit"``, ``"push"``, ``"tag"``, or ``"freeze"``.
        *args:
            Command-specific positional arguments:

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
        if isinstance(gittree, WorkingGitTree):
            self.registry = gittree
            self.orchestre.git_tree.git.bind_tree(gittree)
        command = command.lower()

        def _required_arg(index: int, label: str) -> str:
            if len(args) <= index or not args[index]:
                raise ValueError(f"{command} requires {label} argument.")
            return args[index]

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
            f"Unknown git command '{command}'. Supported commands: 'pull', 'checkout', "
            "'branch', 'add', 'commit', 'push', 'tag', 'freeze'."
        )


    def _freeze_tag(
        self,
        tag_name: str,
        *,
        output_gts: str | Path | None = None,
        message: str | None = None,
        stage_all: bool = True,
    ) -> WorkingGitTree:
        """Freeze a release by committing, tagging, and pushing leaf-first.

        In lifecycle terms this emits the next persisted ``.gts`` state for the
        synchronized tree.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self.last_memory_result = None
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
            freeze_name=tag_name,
        )
        if self.source_path is not None:
            self.state_store.record_snapshot(self.source_path, snapshot_path)
        memory_result = self._trigger_memorize_after_success(
            snapshot_path.parent,
            trigger="freeze-release.success",
        )
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="freeze_release")
        self._log_event(
            "freeze_release_end",
            tag_name=tag_name,
            output_gts=snapshot_path,
            memory_status=(memory_result.status if memory_result is not None else "unbound"),
        )
        return registry

    def freeze_release(
        self,
        release_name: str,
        commit_message: str | None = None,
        *,
        output_gts: str | Path | None = None,
        message: str | None = None,
        stage_all: bool = True,
        force: bool = False,
    ) -> WorkingGitTree:
        """Run the minimalist release workflow from a READY tree.

        The workflow is intentionally composed from public tree operations:
        ``add -> commit -> pull/pull-force -> push -> freeze``.
        """
        resolved_message = commit_message or message or release_name
        if self.source_path is None:
            raise GitSyncError("freeze-release requires a loaded .cgs/.gts source path.")

        self._log_event(
            "freeze_release_workflow_start",
            release_name=release_name,
            force=force,
            stage_all=stage_all,
        )
        self.add()
        self.commit(resolved_message, stage_all=False)
        if force:
            self.pull_force(self.source_path)
        else:
            self.pull(self.source_path)
        self._memory_trigger_suppression_depth += 1
        try:
            self.push()
        finally:
            self._memory_trigger_suppression_depth -= 1
        registry = self.freeze(
            release_name,
            output_gts=output_gts,
            message=resolved_message,
            stage_all=stage_all,
        )
        self._log_event("freeze_release_workflow_end", release_name=release_name, force=force)
        return registry

    def freeze_state(
        self,
        state_name: str,
        *,
        output_gts: str | Path | None = None,
        message: str | None = None,
        stage_all: bool = True,
    ) -> WorkingGitTree:
        """Freeze an internal development state from a ``READY`` tree.

        Parameters mirror :meth:`freeze_release`:

        - ``state_name``: shared tag name applied across all repositories.
        - ``output_gts``: optional snapshot path for the emitted ``.gts`` file.
        - ``message``: optional commit message override.
        - ``stage_all``: stage all changes before committing when ``True``.

        Behavior is identical to release freezing (commit/tag/push leaf-first),
        but intended for internal development states.
        """
        return self._freeze_tag(
            state_name,
            output_gts=output_gts,
            message=message,
            stage_all=stage_all,
        )

    def launch_release(self, release_name: str) -> WorkingGitTree:
        """Check out a frozen release tag across the current READY tree."""
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("launch_release_start", release_name=release_name)
        self.orchestre.git_tree.git.checkout(
            self.git_runner,
            release_name,
            ref_kind=RefKind.TAG,
        )
        snapshot_path = self.write_gts_snapshot(command_origin="launch_release")
        if self.source_path is not None:
            self.state_store.record_snapshot(self.source_path, snapshot_path)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="launch_release")
        self._log_event("launch_release_end", release_name=release_name, output_gts=snapshot_path)
        return registry

    def _restore_gts_snapshot(self, snapshot_path: str | Path) -> WorkingGitTree:
        """Restore a recorded ``.gts`` state, cloning missing repositories as needed."""
        loaded_registry = self.load_gts(snapshot_path)
        previous_state = loaded_registry.lifecycle_state
        self._log_event("restore_gts_snapshot_start", snapshot_path=Path(snapshot_path).resolve())

        for entry in iter_tree(loaded_registry):
            ref_name = self._determine_launch_ref(entry)

            if not entry.absolute_path.exists() or not (entry.absolute_path / ".git").exists():
                remote_url = self._build_remote_url(entry)
                if not remote_url:
                    raise GitSyncError(f"No remote URL available for repository {entry.name}.")
                self._log_event(
                    "restore_gts_snapshot_clone",
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
                "restore_gts_snapshot_checkout",
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
            raise GitSyncError("snapshot restore did not produce a READY tree.")

        self._log_tree_transition(previous_state, loaded_registry.lifecycle_state, reason="restore_gts_snapshot")
        self._log_event("restore_gts_snapshot_end", snapshot_path=Path(snapshot_path).resolve())
        return loaded_registry

    def launch_state(self, snapshot_path: str | Path) -> WorkingGitTree:
        """Restore an internal ``.gts`` state."""
        return self._restore_gts_snapshot(snapshot_path)

    def freeze(
        self,
        name: str,
        *,
        output_gts: str | Path | None = None,
        message: str | None = None,
        stage_all: bool = True,
    ) -> WorkingGitTree:
        """Freeze a tree state and emit the next ``.gts`` snapshot id."""
        return self._freeze_tag(
            name,
            output_gts=output_gts,
            message=message,
            stage_all=stage_all,
        )

    def get_dependency_registry(self) -> WorkingGitTree:
        if self.registry is None:
            raise RuntimeError("No ComplexGitSync registry is loaded.")
        self.orchestre.git_tree.git.bind_tree(self.registry)
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
        rows: list[tuple[str, str, str, str, str, str, str, str]] = []
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
            local_state = repo_status[4]
            upstream_state = repo_status[5]
            if local_state != "clean":
                dirty_count += 1
            if "staged" in local_state:
                staged_count += 1
            if upstream_state.startswith("ahead"):
                ahead_count += 1
            elif upstream_state.startswith("behind"):
                behind_count += 1
            elif upstream_state.startswith("diverged"):
                ahead_count += 1
                behind_count += 1
            if repo_status[6].endswith("*"):
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
        registry: WorkingGitTree,
        entry: WorkingRepo,
        root_path: Path,
    ) -> tuple[str, str, str, str, str, str, str, str]:
        display_path = _status_display_path(entry, root_path)
        try:
            branch = self.git_runner.current_branch(entry.absolute_path) or "detached"
            head = self.git_runner.rev_parse_head(entry.absolute_path)
            status_lines = self._managed_status_lines(registry, entry)
            upstream_ref = self.git_runner.upstream_ref(entry.absolute_path)
            tracking_counts = self.git_runner.branch_tracking_counts(entry.absolute_path)
            tracking_state = self.git_runner.branch_tracking_state(entry.absolute_path)
        except GitSyncError:
            return (
                entry.name,
                display_path,
                entry.current_ref_name or "-",
                "-",
                "error",
                "error",
                "-",
                _short_sha(entry.commit_sha),
            )

        local_state = _local_status_from_porcelain(status_lines)
        upstream_state = _status_tracking_label(tracking_state, tracking_counts)
        recorded = _short_sha(entry.commit_sha)
        head_short = _short_sha(head)
        if entry.commit_sha and head and entry.commit_sha != head:
            head_short = f"{head_short}*"
        return (
            entry.name,
            display_path,
            branch,
            upstream_ref or "-",
            local_state,
            upstream_state,
            head_short,
            recorded,
        )

    def _managed_status_lines(
        self,
        registry: WorkingGitTree,
        entry: WorkingRepo,
    ) -> list[str]:
        status_lines = self.git_runner.status_porcelain(entry.absolute_path)
        managed_paths = self._cgitsync_managed_status_paths(registry, entry)
        managed_paths.update(_unmanaged_gitlink_paths(registry, entry, self.git_runner))
        return [
            line
            for line in status_lines
            if not _status_line_targets_any(line, managed_paths)
            and not (
                _status_line_is_untracked(line)
                and _status_line_path(line) == Path(".gitignore")
            )
        ]

    def _cgitsync_managed_status_paths(
        self,
        registry: WorkingGitTree,
        entry: WorkingRepo,
    ) -> set[Path]:
        managed_paths: set[Path] = {Path(".cgitsync")}
        if entry.parent_id is None:
            managed_paths.add(Path(f"{entry.name}.lgr"))
        for child in registry.children_of(entry.repo_id):
            try:
                managed_paths.add(child.absolute_path.relative_to(entry.absolute_path))
            except ValueError:
                continue
        return managed_paths

    def describe_cgs(self) -> str:
        registry = self.get_dependency_registry()
        tree_state = build_tree_state(registry)
        summary = {
            "source_path": str(self.source_path) if self.source_path else None,
            "project_name": registry.get("root").name,
            "lifecycle_state": tree_state.lifecycle_state.value,
            "registry_complete": tree_state.registry_complete,
            "repo_count": len(registry.repos),
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
        freeze_name: str | None = None,
    ) -> Path:
        registry = self.get_dependency_registry()
        root_entry = registry.get("root")
        document = build_gts_document_from_registry(
            registry,
            command_origin=command_origin,
            source_cgs_path=self.source_path,
            freeze_name=freeze_name,
        )
        if self.source_path is not None and self.source_path.suffix == ".cgs":
            snapshot_stem = self.source_path.stem
        else:
            snapshot_stem = root_entry.name
        snapshot_name = f"{snapshot_stem}.gts"
        state_anchor = new_time_l0_anchor()
        canonical_state_hash = state_anchor.state_hash
        cgitsync_dir = root_entry.absolute_path / ".cgitsync"
        cgitsync_dir.mkdir(parents=True, exist_ok=True)
        memory_state = _resolve_memory_state_directory(cgitsync_dir, canonical_state_hash)
        memory_state.temporary_path.mkdir(parents=True, exist_ok=False)

        final_output_path = memory_state.final_path / snapshot_name
        staged_output_path = memory_state.temporary_path / snapshot_name
        document.to_toml(staged_output_path)

        if self.source_path is not None and self.source_path.suffix == ".cgs" and self.source_path.is_file():
            shutil.copy2(self.source_path, memory_state.temporary_path / self.source_path.name)

        self._log_event(
            "gts_write",
            snapshot_path=final_output_path,
            source_cgs_path=self.source_path,
            tree_lifecycle_state=registry.lifecycle_state,
        )

        register_filename = f"{root_entry.name}.lgr"
        staged_register_path = memory_state.temporary_path / register_filename
        final_register_path = memory_state.final_path / register_filename
        previous_register_path = _latest_state_artifact(cgitsync_dir, register_filename)
        legacy_register_path = root_entry.absolute_path / register_filename
        if previous_register_path is None and legacy_register_path.is_file():
            previous_register_path = legacy_register_path
        if previous_register_path is not None:
            shutil.copy2(previous_register_path, staged_register_path)

        register_id = LocalGitRegister(staged_register_path).record_snapshot(
            staged_output_path,
            state_hash=canonical_state_hash,
            state_order=memory_state.state_order,
            recorded_snapshot_path=final_output_path,
        )
        self._log_event(
            "lgr_update",
            register_path=final_register_path,
            snapshot_path=final_output_path,
            snapshot_id=register_id,
        )
        workspace_hash = document.snapshot_hash or document.compute_snapshot_hash()
        affected_repos = sorted(entry.name for entry in registry.values())
        ledger_id = SyncLedger(staged_register_path).record_event(
            operation=command_origin,
            workspace_hash=workspace_hash,
            gts_snapshot_id=register_id,
            affected_repos=affected_repos,
        )
        self._log_event(
            "ledger_event",
            register_path=final_register_path,
            sync_id=ledger_id,
            operation=command_origin,
            workspace_hash=workspace_hash,
            gts_snapshot_id=register_id,
        )
        staged_log_path = memory_state.temporary_path / f"{snapshot_stem}.log"
        final_log_path = memory_state.final_path / f"{snapshot_stem}.log"
        if self.run_logger is None:
            staged_log_path.write_text(
                json.dumps(
                    {
                        "event": "memory_state_finalized",
                        "command_origin": command_origin,
                        "state_id": _format_state_id(canonical_state_hash),
                        "state_order": memory_state.state_order,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

        memory_state.temporary_path.rename(memory_state.final_path)
        if legacy_register_path.is_file():
            legacy_register_path.unlink()
        self.loaded_snapshot_path = final_output_path
        if self.run_logger is not None:
            self.run_logger.bind_log_file(final_log_path)
        return final_output_path

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

    def validate_topology(self) -> BranchTopologyReport:
        """Inspect and validate the workspace branch topology."""
        return self.validate_branch_topology()

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
    ) -> list[WorkingRepo]:
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
        self, entry: WorkingRepo, project_root: Path
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

    def _clone_registry_entry(self, entry: WorkingRepo) -> None:
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

        if entry.parent_id is not None:
            parent = self.get_dependency_registry().get(entry.parent_id)
            try:
                entry.absolute_path.relative_to(parent.absolute_path)
            except ValueError as exc:
                raise GitSyncError(
                    f"Repository {entry.name} at {entry.absolute_path} is not under its parent path "
                    f"{parent.absolute_path}."
                ) from exc
        self.orchestre.git_tree.git.clone(
            self.git_runner,
            remote_url,
            entry.absolute_path,
            branch=selected_ref,
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

    def _is_populated_nested_destination(self, entry: WorkingRepo) -> bool:
        return (
            entry.parent_id is not None
            and entry.absolute_path.is_dir()
            and next(entry.absolute_path.iterdir(), None) is not None
        )

    def _select_clone_ref(self, entry: WorkingRepo, remote_url: str) -> tuple[str, RefKind]:
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

    def _build_remote_url(self, entry: WorkingRepo) -> str:
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

    def _determine_launch_ref(self, entry: WorkingRepo) -> str:
        """Return the most precise known ref for saved-state checkout."""
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
        entry: WorkingRepo,
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
            if repo_id not in registry.repos:
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

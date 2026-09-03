"""orchestre — orchestration hub for ComplexGitSync.

Ring: 3 (imports downward from every Ring 0–2 module; owns the public
    ComplexGitSyncClient facade — see AgentSpec/IsolationPlan.md §1)
Contract: coordinate one GitTree's lifecycle end to end — load/validate/
    clone/sync/freeze — gating every mutating action on TreeLifecycleState;
    delegate document parsing, path resolution, state-directory allocation,
    registry translation, discovery, and status rendering to the Ring 0–2
    modules below rather than re-implementing them.
Imports: cgs_format, discovery, errors, git_repo, git_runner, git_tree,
    gts_document, integrity, ledger_entry, ledger_store, master,
    operations, paths, registry, state_store, status_render

This module is the **Orchestre anchor** — the authoritative source for the
public client API and the infrastructure services (structured run logging,
the local .lgr register/sync ledger) too small or too entangled with
ComplexGitSyncClient's own state to extract on their own. Wave 1/2 of the
isolation plan (AgentSpec/20260828_Isolation_DevPlanTicket.md) moved
everything else out: GtsDocument → gts_document.py, GitRunner → git_runner.py,
the registry builders → registry.py, nested-config/.gitmodules discovery →
discovery.py, the state-directory allocator → state_store.py, path/CGSHOME
resolution → paths.py, pure status-table rendering → status_render.py, the
CLI's default-snapshot resolution → snapshot_resolver.py, and the
hash-chained register mechanics → ledger_entry.py/integrity.py/
ledger_store.py (not yet wired into SyncLedger's actual write path — see
ComplexGitSyncClient.verify()'s docstring).

Classes still defined here (Tier 2 — Actions):
    CommandRunLogger        Structured JSON event logger for a command run
    RuntimeStateStore       Persistent snapshot-pointer registry (.cgs → .gts)
    SystemClock             Real ledger_entry.ClockProtocol implementation
    LocalGitRegister        The (still single-file, not yet ledger_store-backed) .lgr writer
    SyncLedger              Append-only sync-event ledger sharing LocalGitRegister's file

Classes defined here (Tier 3 — Client / API):
    Orchestre               Coordination layer owning one GitTree
    ComplexGitSyncClient    Public facade; gates all actions on TreeLifecycleState

A handful of private ref-token/status helpers (``_repo_ref_*``,
``_status_*``, ``_unmanaged_gitlink_paths``) stayed here rather than moving
to ``registry.py``/``status_render.py`` because they call ``self.git_runner``
directly — real Git I/O, not pure formatting.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import time
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import tomli_w

from .cgs_format import CgsDocument, parse_repo_id
from .discovery import (
    ImportSubmodulesReport,
    SubmoduleEntry,
    _parse_gitmodules,
    discover_nested_configs,
)
from .errors import (
    ConfigValidationError,
    GitSyncError,
)
from .git_repo import (
    AccessProtocol,
    DiscoveryState,
    GitRepo,
    RefKind,
    RepoLifecycleState,
    SyncState,
    WorkingRepo,
    repo_remote_url,
)
from .git_runner import GitRunner
from .git_tree import (
    ROOT_REPO_ID,
    GitTree,
    ProjectTreeState,
    TreeLifecycleState,
    WorkingGitTree,
    _as_optional_str,
    _update_gitignore_file,
    build_tree_state,
    cgitsync_managed_state_paths,
    format_project_tree,
    format_repo_tree_outline,
    format_view_operation,
    format_view_tree,
    innermost_containing_path,
    iter_tree,
    iter_tree_leaf_first,
    normalize_node_types,
    sync_gitignore,
)
from .git_tree import (
    fix_circularities as _fix_circularities,
)
from .gts_document import GtsDocument
from .integrity import Finding, VerificationReport, verify_chain
from .ledger_entry import new_time_l0_anchor
from .ledger_store import read_all_entries, read_head, recompute_head, verify_and_repair_head
from .master import MasterConfig
from .operations import (
    BranchTopologyReport,
)
from .operations import (
    validate_branch_topology as _validate_branch_topology,
)
from .paths import _path_to_environment_marker, _resolve_document_path, _resolve_project_root
from .paths import resolve_bootstrap_root as _resolve_bootstrap_root
from .paths import resolve_cgshome as _resolve_cgshome
from .paths import resolve_initialise_cgshome as _resolve_initialise_cgshome
from .registry import (
    build_gts_document_from_registry,
    build_registry_from_cgs_document,
    build_registry_from_gts_document,
)
from .state_store import (
    _STATE_DIR_RE,
    _format_state_id,
    _latest_state_artifact,
    _next_state_directory_order,
    _parse_state_hash,
    _resolve_memory_state_directory,
)
from .status_render import (
    _render_status_table,
    _status_display_path,
    _status_line_is_untracked,
    _status_line_path,
    _status_line_targets_any,
)

# ============================================================
#  Runtime document layer — .gts
# ============================================================

_FREEZE_COMMAND_ORIGINS = frozenset({"freeze", "freeze_release", "freeze_state"})


def _collect_errors(checks: list[tuple[bool, str]]) -> list[str]:
    return [msg for ok, msg in checks if not ok]


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


class SystemClock:
    """Real :class:`~.ledger_entry.ClockProtocol` implementation.

    The only place in ``orchestre.py`` that reads the wall clock, PID, or an
    entropy source directly for TIME-L0 anchor generation — every other
    caller goes through :func:`~.ledger_entry.new_time_l0_anchor`, which
    stays deterministic and testable because it only ever sees this
    Protocol, never the real ``datetime``/``time``/``os``/``secrets``
    modules itself.
    """

    def now(self) -> datetime:
        return datetime.now(UTC)

    def time_ns(self) -> int:
        return time.time_ns()

    def pid(self) -> int:
        return os.getpid()

    def token_hex(self, nbytes: int) -> str:
        return secrets.token_hex(nbytes)


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
        state_anchor = new_time_l0_anchor(SystemClock()) if state_hash is None else None
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


def _release_snapshot_slug(release_name: str) -> str:
    """Return a filesystem-friendly release suffix for immutable .gts files."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", release_name.strip()).strip(".-_")
    return slug or "release"


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


def _blocking_worktree_dirt(status_lines: Sequence[str]) -> list[str]:
    """Filter ``git status --porcelain`` lines down to the ones that block a conversion.

    Everything blocks except the repository's own ``.gitignore``. That one
    file is written by ComplexGitSync itself, in every repository that
    holds a child (:func:`~ComplexGitSync.git_tree.sync_gitignore`), so it
    is routinely dirty in exactly the tree ``import-submodules`` is asked
    to convert — ``initialise`` writes it moments before, and refusing over
    it would deadlock the one working order (see
    ``AgentSpec/archive/20260903_InitFromSubmodules_DevPlanTicket.md``). Exempting it is
    safe: the conversion only runs ``git rm --cached`` in the *holding*
    repository, which never touches the child's working tree at all. The
    check exists to protect real, unsaved work in a child, and it still
    catches every bit of that.
    """
    blocking: list[str] = []
    for line in status_lines:
        # "XY path", with the rename form "XY old -> new"; only the plain
        # single-path form can name a top-level .gitignore.
        path = line[3:].strip() if len(line) > 3 else ""
        if path == ".gitignore":
            continue
        blocking.append(line)
    return blocking


DEFAULT_DISCOVER_MAX_DEPTH = 5


def _walk_git_repositories(root: Path, *, max_depth: int) -> tuple[list[Path], bool]:
    """Return every directory under *root* that holds a ``.git``, root first.

    Returns the repositories found and whether the walk stopped early. It
    stopped early when *max_depth* was reached while directories were still
    left to look into: there may be more repositories below, and the caller
    should say so rather than present a partial answer as a complete one.

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
    stopped_early = False

    def _subdirectories(directory: Path) -> list[Path]:
        try:
            children = sorted(directory.iterdir())
        except (PermissionError, OSError):
            return []
        return [
            child
            for child in children
            if child.is_dir() and not child.is_symlink() and child.name != ".git"
        ]

    def _descend(directory: Path, depth: int) -> None:
        nonlocal stopped_early
        if (directory / ".git").exists():
            found.append(directory)
        children = _subdirectories(directory)
        if depth >= max_depth:
            stopped_early = stopped_early or bool(children)
            return
        for child in children:
            _descend(child, depth + 1)

    _descend(root, 0)
    return found, stopped_early


def _as_posix_or_none(path: Path | None) -> str | None:
    return None if path is None else path.as_posix()


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
        Informational only: the generated draft leaves ``nested_config``
        unset either way, since the default ``"auto"`` already resolves
        cleanly whether or not a nested ``.cgs`` is present.
    parent_relative_path:
        The scanned repository this one sits *inside*, as its own
        *relative_path*; ``None`` when it sits directly under the scanned
        root. A repository holding another one is a parent, not a leaf, and
        the drafted ``.cgs`` is read back that way — see
        ``registry.build_registry_from_cgs_document``.
    """

    relative_path: str
    absolute_path: Path
    remote_url: str | None
    identifier: str | None
    branch: str | None
    has_cgs: bool
    parent_relative_path: str | None = None




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
class InitFromSubmodulesReport:
    """Result returned by :meth:`ComplexGitSyncClient.init_from_submodules`.

    Attributes
    ----------
    root:
        The checkout the command was pointed at, which is also CGSHOME
        once the tree is initialised.
    discover:
        The :class:`DiscoverReport` from step 1, kept so a caller can show
        the same tree and warnings ``discover`` itself would have printed.
    cgs_path:
        The ``.cgs`` used — the one written from the discovery, or the
        pre-existing file passed in.
    cgs_written:
        ``True`` when *cgs_path* was authored by this call, ``False`` when
        an existing file was reused.
    import_report:
        The :class:`~ComplexGitSync.discovery.ImportSubmodulesReport` from
        the conversion step, or ``None`` on a dry run that never reached it.
    tree:
        The ``READY`` tree ``initialise`` produced, or ``None`` on a dry run.
    dry_run:
        ``True`` when nothing was written, cloned, or converted.
    """

    root: Path
    discover: DiscoverReport
    cgs_path: Path
    cgs_written: bool
    import_report: ImportSubmodulesReport | None = None
    tree: WorkingGitTree | None = None
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class GitignoreSyncEntry:
    """One repo whose ``.gitignore`` was created or modified by ``sync_gitignore()``."""

    repo_id: str
    name: str
    absolute_path: Path
    added_paths: tuple[str, ...]
    committed: bool = False


_SSH_AUTH_FAILURE_MARKERS = (
    "Permission denied (publickey)",
    "Could not read from remote repository",
    "Host key verification failed",
)


def _looks_like_ssh_auth_failure(git_error_message: str) -> bool:
    """Heuristic match on ``git``'s own stderr for a likely SSH auth failure.

    ``git``'s exact wording is not a stable API, so a missed match just
    degrades to the plain :class:`~.errors.GitSyncError` from before this
    hint existed — never a worse error than that.
    """
    return any(marker in git_error_message for marker in _SSH_AUTH_FAILURE_MARKERS)


_HTTPS_AUTH_FAILURE_MARKERS = (
    # GitLab, verified firsthand against the live cawaqsviz remote
    # (ProtocolSwitchOnPush_DevPlanTicket §0.4) — a real captured response,
    # not guessed wording.
    "HTTP Basic: Access denied",
    # cgitsync's own signature for "a credential was needed and the ambient
    # environment had none to offer" (GIT_TERMINAL_PROMPT=0 / GIT_ASKPASS —
    # see git_runner.py's _non_interactive_git_env) — provider-agnostic,
    # fires for any HTTPS remote regardless of host.
    "could not read Username",
    "terminal prompts disabled",
    # GitHub's and Codeberg's own HTTPS-auth-failure wording is unverified
    # — ProtocolSwitchOnPush_DevPlanTicket §1.3: ship what's confirmed,
    # never guess unverified wording. A missed match here just degrades to
    # the plain GitSyncError, same as an unmatched SSH failure above.
)


def _looks_like_https_auth_failure(git_error_message: str) -> bool:
    """Heuristic match on ``git``'s own stderr for a likely HTTPS auth
    failure — same caveats as :func:`_looks_like_ssh_auth_failure`."""
    return any(marker in git_error_message for marker in _HTTPS_AUTH_FAILURE_MARKERS)


def _protocol_switch_hint(git_error_message: str, *, command: str) -> str | None:
    """Return an actionable ``--force-protocol`` hint for *git_error_message*,
    or ``None`` when it matches neither known failure shape.

    Reads which failure shape matched rather than trusting any repo's
    recorded ``access_protocol`` — that value can be stale or simply
    unknown (an *adopted*, not cloned, root's remote is whatever the user
    set it to outside cgitsync entirely; see
    ProtocolSwitchOnPush_DevPlanTicket §0.2). Suggests the opposite of
    whichever marker set matched, never both.
    """
    if _looks_like_ssh_auth_failure(git_error_message):
        return (
            f"hint: this looks like an SSH authentication failure — pass "
            f"--force-protocol https to '{command}' if the repository is "
            f"public, or configure an SSH key/agent for this runner "
            f"otherwise."
        )
    if _looks_like_https_auth_failure(git_error_message):
        return (
            f"hint: this looks like an HTTPS authentication failure — pass "
            f"--force-protocol ssh to '{command}' if you have an SSH key "
            f"registered with the provider, or configure an HTTPS "
            f"credential helper otherwise."
        )
    return None


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
    last_gitignore_sync: tuple[GitignoreSyncEntry, ...] = ()
    run_logger: CommandRunLogger | None = None
    _forced_access_protocol: AccessProtocol | None = field(default=None, init=False, repr=False)

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
        recursive: bool = False,
    ) -> ImportSubmodulesReport:
        """Report or convert git submodules in *repo_root* to plain nested clones.

        With *recursive* (default ``False``, matching ``git submodule
        update``'s own flag name and meaning): also converts any submodule
        that itself has its own checked-out ``.gitmodules``, at any depth,
        root first — the opposite of this codebase's usual leaf-first
        mutation order, and deliberately so: converting a submodule stages
        changes in its own working tree, and if a deeper level converted
        first, the parent level's own preflight (is the submodule's
        working tree clean?) would then reject its own conversion over
        dirt the deeper conversion itself just made. Converting parent
        first has no such problem — ``git rm --cached <path>`` only
        touches the parent's own index, never the submodule's working
        tree — see :meth:`_gitmodules_levels_root_first` for the full
        reasoning. A submodule path that was never checked out (``git
        submodule update --init`` not run for it) has no ``.git`` to read
        a nested ``.gitmodules`` from and is invisible either way — the
        same ceiling :meth:`discover_repos` already documents for itself.
        Without *recursive*, behavior is unchanged: exactly
        ``<repo_root>/.gitmodules``, one level.

        Parses ``<repo_root>/.gitmodules`` and, for each declared submodule:

        * **Dry-run** (``apply=False``, the default): returns an
          :class:`ImportSubmodulesReport` describing what would change —
          submodule names, paths, URLs, branches — without touching the
          repository.
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

        This is the whole job: turning gitlinks into plain clones on disk.
        It does not author a ``.cgs`` — that would need the root's own
        identity too, which ``.gitmodules`` never records, and a project
        checkout worth importing already has one (:meth:`discover_repos`)
        or is worth writing by hand. Run :meth:`discover_repos` on the same
        checkout, before or after applying, to get one.

        Parameters
        ----------
        repo_root:
            Absolute (or resolvable) path to the local git repository that
            contains a ``.gitmodules`` file.
        apply:
            When ``False`` (default) the method is a pure read: it reports
            what would change without modifying anything. Set to ``True`` to
            perform the conversion.

        Returns
        -------
        ImportSubmodulesReport
            Always returned, whether or not *apply* was set. With
            *recursive*, one flat report combining every level — the same
            shape :meth:`discover_repos` already uses for its own report,
            regardless of how deep a repository was found.
        """
        root = Path(repo_root).resolve()
        if not recursive:
            return self._import_submodules_one_level(root, apply=apply)

        submodules: list[SubmoduleEntry] = []
        converted: list[str] = []
        applied_any = False
        for level_root in self._gitmodules_levels_root_first(root):
            level_report = self._import_submodules_one_level(level_root, apply=apply)
            submodules.extend(level_report.submodules)
            converted.extend(level_report.converted)
            applied_any = applied_any or level_report.applied
        return ImportSubmodulesReport(
            submodules=tuple(submodules),
            applied=applied_any,
            converted=tuple(converted),
            scan_root=root,
        )

    def _gitmodules_levels_root_first(
        self, level_root: Path, *, _visited: set[Path] | None = None
    ) -> list[Path]:
        """Every directory under *level_root* (itself included) with a
        checked-out ``.gitmodules``, root first.

        Root first, not leaf first, despite this codebase's usual
        leaf-first mutation order (``push``/``commit``/…): converting a
        submodule stages changes in *its own* working tree (the removed
        ``.gitmodules`` stanza, the new ``.gitignore`` line) — if a deeper
        level converted first, the *parent* level's own preflight (``is
        <submodule path> clean?``) would then see that staged dirt and
        reject its own conversion. Converting parent-first never has this
        problem: ``git rm --cached <path>`` only touches the parent's own
        index, never the submodule's working tree, so a still-unconverted
        child submodule is exactly as clean immediately after its parent
        converts as it was before.

        A submodule path with no ``.git`` (never ``git submodule update
        --init``'d) has nothing to recurse into and is skipped — matching
        :meth:`discover_repos`'s own "only what is checked out" ceiling.
        """
        visited = _visited if _visited is not None else set()
        resolved = level_root.resolve()
        if resolved in visited or not (resolved / ".gitmodules").is_file():
            return []
        visited.add(resolved)

        levels: list[Path] = [resolved]
        content = (resolved / ".gitmodules").read_text(encoding="utf-8")
        for sub in _parse_gitmodules(content):
            child_path = resolved / sub.path
            if not (child_path / ".git").exists():
                continue
            levels.extend(self._gitmodules_levels_root_first(child_path, _visited=visited))
        return levels

    # Pre-existing complexity debt from before C90 was enabled (P6,
    # AgentSpec/20260828_Isolation_DevPlanTicket.md) — flagged, not fixed
    # under this ticket, since a real refactor of the submodule-conversion
    # flow risks behaviour change under time pressure. New code is enforced
    # at 12.
    def _import_submodules_one_level(  # noqa: C901
        self, root: Path, *, apply: bool
    ) -> ImportSubmodulesReport:
        """The single-level conversion :meth:`import_submodules` always did —
        the unit it composes over per level when *recursive* is set."""
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
                scan_root=root,
            )

        content = gitmodules_path.read_text(encoding="utf-8")
        # Record which repository declared each submodule. Its ``path`` is
        # written relative to that repository, so without this the entry
        # cannot be placed once several levels are reported together.
        submodules = tuple(
            replace(entry, owner_root=root) for entry in _parse_gitmodules(content)
        )

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
                scan_root=root,
            )

        if not apply:
            return ImportSubmodulesReport(
                submodules=submodules,
                applied=False,
                converted=(),
                scan_root=root,
            )

        # --- apply=True: perform the conversion ---

        # 1. Preflight: every child working tree must be clean.
        for sub in submodules:
            child_path = root / sub.path
            if not child_path.exists():
                continue
            dirty_lines = _blocking_worktree_dirt(self.git_runner.status_porcelain(child_path))
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

        return ImportSubmodulesReport(
            submodules=submodules,
            applied=True,
            converted=tuple(converted),
            scan_root=root,
        )

    def init_from_submodules(
        self,
        repo_root: str | Path,
        *,
        cgs_path: str | Path | None = None,
        max_depth: int = DEFAULT_DISCOVER_MAX_DEPTH,
        dry_run: bool = False,
        force: bool = False,
        force_access_protocol: str | None = None,
    ) -> InitFromSubmodulesReport:
        """Adopt a submodule-based checkout in one call: discover, initialise, convert.

        This is the whole of Tutorial 3's steps 3-5, in the one order that
        works. Point it at a checkout that was cloned and ``git submodule
        update --init --recursive``'d by hand, and it produces a ``READY``
        ComplexGitSync tree whose submodules have become plain nested
        clones, staged but not committed.

        The sequence, each step delegating to the method that already owns
        it:

        1. :meth:`discover_repos` on *repo_root* — a pure read that drafts
           the ``.cgs`` from what is checked out.
        2. Write that draft to ``<repo_root>/<project>.cgs``, unless
           *cgs_path* names a file that already exists, which is used
           as-is instead.
        3. :meth:`initialise_cgs` with ``output_path = repo_root.parent``,
           so CGSHOME resolves to *repo_root* itself.
        4. :meth:`import_submodules` with ``apply=True, recursive=True``
           on CGSHOME.

        **Why the conversion comes last.** :meth:`initialise_cgs` adopts
        the root in place but deletes and re-clones every *other*
        repository straight from its remote, and those remotes still
        declare submodules. Converting first would therefore be undone for
        every non-root repository the moment step 3 ran. Nor can a second
        conversion pass repair that: ``import_submodules(recursive=True)``
        walks the submodule graph declared by the root's own
        ``.gitmodules``, so once the root is converted, no deeper level is
        reachable any more.

        Committing is deliberately left to the caller. The conversion
        touches every repository holding a submodule, and some of them
        belong to other people — ``branch``/``checkout``/``add``/``commit``
        stay explicit, separate steps.

        Parameters
        ----------
        repo_root:
            The checkout to adopt. Its directory name must match the
            project name the discovery derives, since that is what makes
            CGSHOME resolve back to this same directory.
        cgs_path:
            Use this ``.cgs`` instead of writing one. When it does not
            exist, the draft is written there rather than to the default
            location.
        max_depth:
            Passed to :meth:`discover_repos`.
        dry_run:
            Report the plan — the discovery and the submodules that would
            be converted — without writing, cloning, or converting
            anything.
        force:
            Proceed even when *repo_root* has no ``.gitmodules`` of its
            own. Without it, that case is refused: there is nothing to
            convert, while step 3 would still delete and re-clone every
            non-root repository, destroying any uncommitted work in them.
        force_access_protocol:
            ``"ssh"`` or ``"https"``, forwarded to :meth:`initialise_cgs`
            for the clone step. The discovery step reads each repository's
            configured remote as it is and is unaffected.

        Returns
        -------
        InitFromSubmodulesReport
            The discovery, the ``.cgs`` used, the conversion report, and
            the resulting tree.
        """
        root = Path(repo_root).resolve()
        if not root.is_dir():
            raise GitSyncError(f"init-from-submodules: not a directory: {root}")

        report = self.discover_repos(root, max_depth=max_depth)
        target_cgs = (
            Path(cgs_path).resolve()
            if cgs_path is not None
            else root / f"{report.project_name}.cgs"
        )
        reuse_existing = cgs_path is not None and target_cgs.is_file()
        # A supplied .cgs is the authority on the project name; the
        # discovery is then only a report of what is on disk.
        project_name = (
            CgsDocument.from_toml(target_cgs).project_name or root.name
            if reuse_existing
            else report.project_name
        )
        self._assert_adoptable(
            root,
            report,
            project_name=project_name,
            reuse_existing=reuse_existing,
            force=force,
        )

        self._log_event(
            "init_from_submodules_start",
            repo_root=str(root),
            project_name=report.project_name,
            repo_count=len(report.repos),
            cgs_path=str(target_cgs),
            reuse_existing_cgs=reuse_existing,
            dry_run=dry_run,
        )

        if dry_run:
            return InitFromSubmodulesReport(
                root=root,
                discover=report,
                cgs_path=target_cgs,
                cgs_written=False,
                import_report=self.import_submodules(root, apply=False, recursive=True),
                dry_run=True,
            )

        if not reuse_existing:
            self.configure(report.project_name, list(report.cgs_entries), output_path=target_cgs)

        tree = self.initialise_cgs(
            target_cgs,
            output_path=root.parent,
            force_access_protocol=force_access_protocol,
        )
        try:
            import_report = self.import_submodules(root, apply=True, recursive=True)
        except GitSyncError as exc:
            raise GitSyncError(
                f"{exc}\n"
                f"hint: the tree at {root} is initialised but its submodules are "
                f"not converted yet — every repository is exactly as its remote "
                f"declares it. Fix the cause above, then finish the job with "
                f"'cgitsync import-submodules {root} --recursive --apply'."
            ) from exc

        self._log_event(
            "init_from_submodules_done",
            repo_root=str(root),
            cgs_path=str(target_cgs),
            converted_count=len(import_report.converted),
        )
        return InitFromSubmodulesReport(
            root=root,
            discover=report,
            cgs_path=target_cgs,
            cgs_written=not reuse_existing,
            import_report=import_report,
            tree=tree,
            dry_run=False,
        )

    def _assert_adoptable(
        self,
        root: Path,
        report: DiscoverReport,
        *,
        project_name: str,
        reuse_existing: bool,
        force: bool,
    ) -> None:
        """Refuse an adoption that cannot work, before anything is written.

        Three ways :meth:`init_from_submodules` would otherwise fail
        halfway through, each cheaper to detect here:

        * The discovery resolved no repository at all, so there is no
          ``.cgs`` to write. Skipped when the caller supplied one
          (*reuse_existing*): the discovery is then only a report.
        * *project_name* and the directory name disagree, so ``initialise``
          would resolve CGSHOME to a sibling directory that does not exist
          — Tutorial 3's easiest mistake.
        * *root* has no ``.gitmodules``, meaning either an already-adopted
          tree or one that never used submodules. There would be nothing
          to convert, while the clone step would still re-clone (and so
          discard) every non-root repository. ``force`` overrides this one.
        """
        if not reuse_existing and not report.cgs_entries:
            raise GitSyncError(
                f"init-from-submodules: no resolvable git repository found under "
                f"{root} — nothing to adopt."
            )
        if project_name != root.name:
            raise GitSyncError(
                f"init-from-submodules: the project name is '{project_name}' but the "
                f"directory is named '{root.name}'. CGSHOME is resolved as "
                f"<parent>/<project name>, so these must match. Rename the directory "
                f"to '{root.parent / project_name}' and run this again."
            )
        if force or (root / ".gitmodules").is_file():
            return
        raise GitSyncError(
            f"init-from-submodules: no .gitmodules in {root}, so there is nothing to "
            f"convert — this tree looks already adopted, or never used submodules. "
            f"Running anyway would still delete and re-clone every non-root "
            f"repository from its remote, losing any uncommitted work in them. "
            f"Pass --force if that is really what you want, or use 'initialise' for "
            f"a tree that already has a .cgs."
        )

    # Pre-existing complexity debt from before C90 was enabled (P6,
    # AgentSpec/20260828_Isolation_DevPlanTicket.md) — flagged, not fixed
    # under this ticket, since a real refactor of the filesystem-walking
    # discovery flow risks behaviour change under time pressure. New code
    # is enforced at 12.
    def discover_repos(  # noqa: C901
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

        found_paths, stopped_early = _walk_git_repositories(root, max_depth=max_depth)
        if stopped_early:
            warnings.append(
                f"the scan stopped at --max-depth {max_depth} with directories left "
                f"to look into; any repository deeper than that was not seen. "
                f"Re-run with a larger --max-depth to be sure."
            )

        for repo_path in found_paths:
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
                    # The walk is root-first, so anything holding this
                    # repository has already been seen.
                    parent_relative_path=_as_posix_or_none(
                        innermost_containing_path((found.relative_path for found in repos), relative)
                    ),
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
            # A repository with no .cgs of its own resolves cleanly on the
            # default "auto" (zero matches -> RESOLVED), so it is left
            # unset here rather than pinned to "disabled".
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

    # Pre-existing complexity debt from before C90 was enabled (P6,
    # AgentSpec/20260828_Isolation_DevPlanTicket.md) — flagged, not fixed
    # under this ticket, since a real refactor of the .gitignore sync flow
    # risks behaviour change under time pressure. New code is enforced at
    # 12.
    def _sync_gitignore_lifecycle(  # noqa: C901
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
            relative_paths = {
                child.absolute_path.relative_to(entry.absolute_path).as_posix() for child in children
            }
            if entry.parent_id is None:
                for managed_path in cgitsync_managed_state_paths(entry):
                    as_posix = managed_path.as_posix()
                    relative_paths.add(f"{as_posix}/" if as_posix == ".cgitsync" else as_posix)
            if not relative_paths:
                continue
            gitignore_path = entry.absolute_path / ".gitignore"
            try:
                existing_lines = gitignore_path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                existing_lines = []
            missing = tuple(sorted(path for path in relative_paths if path not in existing_lines))
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
        force_access_protocol: str | None = None,
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
        force_access_protocol:
            ``"ssh"`` or ``"https"`` (``--force-protocol``). Overrides every
            cloned repo's ``access_protocol`` in memory only — nothing on
            disk is read or written differently. Applies to every entry the
            clone loop touches, including ones discovered later from a
            nested ``.cgs`` in a different, separately-cloned repo. ``None``
            (the default) leaves each entry's own ``.cgs``-declared protocol
            untouched, exactly as today.
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
            force_access_protocol=force_access_protocol,
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
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Initialise from an already-normalized, validated ``CgsDocument``.

        ``source_path`` is the logical origin used for relative paths, state
        metadata, and logging. It need not exist for direct CLI authoring.
        See :meth:`initialise_cgs` for ``commit_gitignore``/
        ``force_gitignore_sync``/``git_user_name``/``git_user_email``/
        ``force_access_protocol``.
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
        self._forced_access_protocol = (
            AccessProtocol(force_access_protocol) if force_access_protocol else None
        )
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
        force_access_protocol: str | None = None,
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
            force_access_protocol=force_access_protocol,
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
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Initialise a .cgs workspace after purging generated clone state."""
        return self.clean_initialise_cgs(
            config_path,
            output_path=output_path,
            commit_gitignore=commit_gitignore,
            force_gitignore_sync=force_gitignore_sync,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
            force_access_protocol=force_access_protocol,
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
        return _resolve_cgshome(document, source_path, output_path=output_path)

    def resolve_initialise_cgshome(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> Path:
        """Read a .cgs file and resolve the CGSHOME initialise will use."""
        return _resolve_initialise_cgshome(config_path, output_path=output_path)

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
        return _resolve_project_root(document, source_path, target_dir, output_path)

    def clone_cgs(
        self,
        config_path: str | Path,
        *,
        target_dir: str | Path | None = None,
        output_path: str | Path | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        project_root = _resolve_project_root(document, source_path, target_dir, output_path)
        self._forced_access_protocol = (
            AccessProtocol(force_access_protocol) if force_access_protocol else None
        )

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
        # Every repo was just freshly cloned, so a safe-pull preflight (as
        # initialise_cgs_document runs before its own sync) can only be a
        # no-op fast-forward here -- skip it. See BootstrapGitignoreSync
        # DevPlanTicket: without this call, bootstrap/clone left every
        # parent-bearing repo's .gitignore missing its immediate children,
        # so plain `git status` saw each child as an embedded repository
        # (gitlink-shaped) instead of the plain independent clone it is.
        self._sync_gitignore_lifecycle(pre_pull=False, commit=False)
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
        return _resolve_bootstrap_root(project_name, cgs_path=cgs_path)

    def bootstrap(
        self,
        config_path: str | Path,
        project_name: str,
        *,
        cgs_path: str | Path | None = None,
        force_access_protocol: str | None = None,
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
        force_access_protocol:
            ``"ssh"`` or ``"https"`` (``--force-protocol``). See
            :meth:`initialise_cgs` for the full description — applies here
            identically, including to the root repo this command (unlike
            ``initialise``) also clones from scratch.
        """
        source_path = Path(config_path).resolve()
        if source_path.suffix != ".cgs":
            raise ValueError(
                f"bootstrap requires a .cgs source, got '{source_path.suffix}' for {source_path!s}."
            )
        target_dir = self.resolve_bootstrap_root(project_name, cgs_path=cgs_path)
        return self.clone_cgs(
            source_path, target_dir=target_dir, force_access_protocol=force_access_protocol
        )

    def restart(
        self,
        config_path: str | Path,
        *,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Resynchronize an already-cloned tree from a ``.cgs`` file.

        Loads the ``.cgs`` configuration, discovers nested configs, then
        checks out the root repository's current branch across the whole tree
        parent-first.  Ends in ``READY`` or raises
        :exc:`~ComplexGitSync.errors.GitSyncError`. See
        :meth:`ComplexGitSyncClient.initialise_cgs` for
        ``commit_gitignore``/``force_gitignore_sync``/``git_user_name``/
        ``git_user_email``, and :meth:`push` for ``force_access_protocol``.
        """
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        resolved_path = Path(config_path).resolve()
        self._log_event("restart_start", config_path=resolved_path)
        restart_cgshome = self.resolve_initialise_cgshome(resolved_path)
        MasterConfig.load(restart_cgshome)
        if git_user_name is not None or git_user_email is not None:
            MasterConfig.persist(restart_cgshome, user_name=git_user_name, user_email=git_user_email)
        registry = self.load_cgs(resolved_path, discover_nested=True)
        protocol = AccessProtocol(force_access_protocol) if force_access_protocol else None
        try:
            self.orchestre.git_tree.git.pull(self.git_runner, force_access_protocol=protocol)
        except GitSyncError as exc:
            hint = _protocol_switch_hint(str(exc), command="pull")
            if hint:
                raise GitSyncError(f"{exc}\n{hint}") from exc
            raise
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
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Resynchronize from a ``.cgs`` spec or restore from a ``.gts`` snapshot.

        ``commit_gitignore``/``force_gitignore_sync``/``git_user_name``/
        ``git_user_email`` only apply to ``.cgs`` sources (dispatched to
        :meth:`restart`) — a ``.gts`` source runs no discovery, so there is
        nothing new for the ``.gitignore`` lifecycle sync to find.
        ``force_access_protocol`` applies to both — see :meth:`push`.
        """
        resolved_source = Path(source_path).resolve()
        if resolved_source.suffix == ".cgs":
            return self.restart(
                resolved_source,
                commit_gitignore=commit_gitignore,
                force_gitignore_sync=force_gitignore_sync,
                git_user_name=git_user_name,
                git_user_email=git_user_email,
                force_access_protocol=force_access_protocol,
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
                protocol = AccessProtocol(force_access_protocol) if force_access_protocol else None
                try:
                    self.orchestre.git_tree.git.pull(self.git_runner, force_access_protocol=protocol)
                except GitSyncError as exc:
                    hint = _protocol_switch_hint(str(exc), command="pull")
                    if hint:
                        raise GitSyncError(f"{exc}\n{hint}") from exc
                    raise
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

    def pull_force(
        self, source_path: str | Path, *, force_access_protocol: str | None = None
    ) -> WorkingGitTree:
        """Destructively resynchronize from a ``.cgs`` spec or ``.gts`` snapshot.

        ``force_access_protocol`` — see :meth:`push`.
        """
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
        protocol = AccessProtocol(force_access_protocol) if force_access_protocol else None
        try:
            self.orchestre.git_tree.git.pull_force(self.git_runner, force_access_protocol=protocol)
        except GitSyncError as exc:
            hint = _protocol_switch_hint(str(exc), command="pull-force")
            if hint:
                raise GitSyncError(f"{exc}\n{hint}") from exc
            raise
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

    def add(self, paths: Sequence[str | Path] | None = None) -> WorkingGitTree:
        """Stage changes across the full tree, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  After a
        successful execution the registry remains ``READY``.

        With *paths* omitted (the default), every repo is staged in full —
        today's exact behaviour. With *paths* given, each one is resolved to
        its owning repo (see :func:`~.git_tree.resolve_repo_for_path`) and
        staged there individually, leaving every other repo untouched.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("add_start", paths=[str(p) for p in paths] if paths else None)
        self.orchestre.git_tree.git.add(self.git_runner, paths=paths)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="add")
        self._log_event("add_end")
        return registry

    def remove(self, paths: Sequence[str | Path]) -> WorkingGitTree:
        """Remove one or more tracked files, each from the repo that owns it.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise. Each path
        is resolved to its owning repo (see
        :func:`~.git_tree.resolve_repo_for_path`), removed from disk there,
        and the removal staged — a plain ``git rm``, distinct from
        :meth:`GitRunner.rm_cached` (index-only, built for the
        submodule-to-plain-clone conversion; this does not replace it).
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("rm_start", paths=[str(p) for p in paths])
        self.orchestre.git_tree.git.rm(self.git_runner, paths)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="rm")
        self._log_event("rm_end")
        return registry

    def push(self, *, force_access_protocol: str | None = None) -> WorkingGitTree:
        """Push all repos to their remotes, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  After a
        successful execution the registry remains ``READY`` and refreshes the
        stored commit hashes in the runtime tree state.

        ``force_access_protocol`` (``"ssh"`` or ``"https"``,
        ``--force-protocol``), when given, rewrites each repo's remote to
        that protocol before pushing, persisting the change (``git remote
        set-url``) rather than a one-off override — see
        ``AgentSpec/ProtocolSwitchOnPush_DevPlanTicket.md``. On a failure
        that looks like an auth problem, the error gains an actionable
        hint naming ``--force-protocol <the other one>``.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("push_start")
        protocol = AccessProtocol(force_access_protocol) if force_access_protocol else None
        try:
            self.orchestre.git_tree.git.push(self.git_runner, force_access_protocol=protocol)
        except GitSyncError as exc:
            hint = _protocol_switch_hint(str(exc), command="push")
            if hint:
                raise GitSyncError(f"{exc}\n{hint}") from exc
            raise
        snapshot_path = self.write_gts_snapshot(command_origin="push")
        if self.source_path is not None:
            self.state_store.record_snapshot(self.source_path, snapshot_path)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="push")
        self._log_event("push_end")
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
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="freeze_release")
        self._log_event(
            "freeze_release_end",
            tag_name=tag_name,
            output_gts=snapshot_path,
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
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Run the minimalist release workflow from a READY tree.

        The workflow is intentionally composed from public tree operations:
        ``add -> commit -> pull/pull-force -> push -> freeze``. The pull step
        is skipped (not attempted) when the current branch has no upstream
        yet — e.g. a branch just created and checked out this session, never
        pushed — since there is nothing to pull; see
        :meth:`GitRunner.has_upstream`, already used identically by
        :func:`operations.push_tree` to auto-detect this same case.

        ``force_access_protocol`` — see :meth:`push` — is forwarded to the
        ``pull``/``pull-force`` and ``push`` steps above; the remote
        rewrite it makes persists (``git remote set-url``), so the
        ``freeze`` step's own tag push, further below, picks it up too
        without needing the parameter itself.
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
        root_entry = self.get_dependency_registry().get(ROOT_REPO_ID)
        if self.git_runner.has_upstream(root_entry.absolute_path):
            if force:
                self.pull_force(self.source_path, force_access_protocol=force_access_protocol)
            else:
                self.pull(self.source_path, force_access_protocol=force_access_protocol)
        else:
            self._log_event(
                "freeze_release_pull_skipped",
                reason="current branch has no upstream yet — nothing to pull",
                absolute_path=root_entry.absolute_path,
            )
        self.push(force_access_protocol=force_access_protocol)
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

    def verify(self, cgshome: str | Path, *, repair: bool = False) -> VerificationReport:
        """Verify the hash-chained ``.cgitsync/lgr`` register for tamper-evidence.

        Checks chain linkage (``BROKEN_LINK``), entry-hash integrity
        (``BAD_ENTRY_HASH``), sequence gaps/duplicates (``SEQ_GAP``/
        ``SEQ_DUPLICATE``), and whether the cached ``HEAD`` pointer agrees
        with the recomputed true head (``HEAD_STALE``). A register with no
        entries yet is reported clean — nothing has been recorded, which is
        not itself a problem.

        Store-level checks (``MISSING_STATE``, ``ORPHAN_STATE``,
        ``STATE_DIGEST_MISMATCH`` — cross-referencing entries against the
        actual ``state(<hash>)_n/`` directories on disk) are not
        implemented yet; this is chain-and-HEAD verification only.

        With ``repair=True``, a stale ``HEAD`` cache is corrected in place.
        Entries themselves are never rewritten or deleted — a broken chain
        is reported, not silently healed (``IsolationPlan.md`` §2.6).
        """
        lgr_dir = Path(cgshome) / ".cgitsync" / "lgr"
        entries = read_all_entries(lgr_dir)
        report = verify_chain(entries)

        if entries:
            cached_head = read_head(lgr_dir)
            true_head = recompute_head(lgr_dir)
            if cached_head != true_head:
                report.findings.append((
                    entries[-1].seq,
                    Finding.HEAD_STALE,
                    f"cached HEAD={cached_head}, recomputed HEAD={true_head}",
                ))
            if repair:
                verify_and_repair_head(lgr_dir)

        return report

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
        managed_paths: set[Path] = set(cgitsync_managed_state_paths(entry))
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
        state_anchor = new_time_l0_anchor(SystemClock())
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
            if root_entry.current_ref_name:
                branch_slug = _release_snapshot_slug(root_entry.current_ref_name)
                stable_cgs_dir = cgitsync_dir / ".cgs"
                stable_cgs_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(
                    self.source_path,
                    stable_cgs_dir / f"{root_entry.name}-{branch_slug}.cgs",
                )

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
        effective_protocol = self._forced_access_protocol or entry.access_protocol
        try:
            self.orchestre.git_tree.git.clone(
                self.git_runner,
                remote_url,
                entry.absolute_path,
                branch=selected_ref,
            )
        except GitSyncError as exc:
            if effective_protocol == AccessProtocol.SSH and _looks_like_ssh_auth_failure(str(exc)):
                raise GitSyncError(
                    f"{exc}\n"
                    f"hint: this clone used ssh and failed authentication — pass "
                    f"--force-protocol https to 'initialise'/'bootstrap'/'clean-init' if "
                    f"{entry.name} is a public repo, or configure an SSH key/agent for "
                    f"this runner otherwise."
                ) from exc
            raise
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
        return repo_remote_url(entry, self._forced_access_protocol or entry.access_protocol)

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

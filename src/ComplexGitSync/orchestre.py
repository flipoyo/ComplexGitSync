"""orchestre — the Ring-3 orchestration hub and public client facade.

Contract: coordinate one GitTree lifecycle, gate mutations on TreeLifecycleState,
    and delegate formats, registry, discovery, paths, status, State, ledger, environment, and Git mechanics to their Ring 0–2 owners.
Imports: cgs_format, discovery, errors, git_repo, git_runner, git_tree,
    gts_document, memory, operations, paths, registry, status_render, tree_env, universal_clock
The archived Isolation ticket documents the module split.

Tier 2 retains CommandRunLogger, RuntimeStateStore, LocalGitRegister and SyncLedger; Tier 3 contains Orchestre and ComplexGitSyncClient. Private status/ref helpers stay here only when they directly need ``self.git_runner``.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tomllib
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from . import __build__, __version__, tree_env
from .cgs_format import CgsDocument, parse_repo_id, repo_identifier
from .clone_guard import (
    blocked_destinations,
    format_block_error,
    is_populated_destination,
)
from .discovery import (
    ImportSubmodulesReport,
    SubmoduleEntry,
    _parse_gitmodules,
    discover_nested_configs,
)
from .errors import (
    ComplexGitSyncError,
    ConfigValidationError,
    GitSyncError,
)
from .git_branch import DEFAULT_BRANCH, BranchResolution, resolve_entry_ref

if TYPE_CHECKING:
    from .autofix import RepairOutcome
from .git_repo import (
    AccessProtocol,
    DiscoveryState,
    GitProvider,
    GitRepo,
    RefKind,
    RepoLifecycleState,
    RepoScope,
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
    propagate_privacy,
    sync_gitignore,
)
from .git_tree import (
    fix_circularities as _fix_circularities,
)
from .git_tree_branch import GitTreeBranches, tree_project_name
from .gts_document import GtsDocument
from .json_render import dumps as json_dumps
from .json_render import empty_status_payload, status_payload, verify_payload
from .master import MasterConfig
from .memory import (
    Finding,
    HistoryState,
    SyncLedger,
    VerificationReport,
    resolve_state,
    verify_chain,
)
from .memory import environment as environment_store
from .memory import ledger_entry as memory_ledger_entry
from .memory import ledger_store as memory_ledger_store
from .memory.commit_log import (
    COMMIT_LOG_DIR_NAME,
    SCOPE_PRIVATE,
    SCOPE_PROJECT,
    CommitRecord,
    PublicationRecord,
    append_commits,
    append_publications,
    digest_of,
    digest_of_rows,
    read_commit_log,
)
from .memory.pending import (
    current_ledger_dir as _current_ledger_dir,
)
from .memory.pending import (
    memory_commit_log_rows as _memory_commit_log_rows,
)
from .memory.pending import (
    memory_dirs as _memory_dirs,
)
from .memory.pending import (
    memory_published_commits as _memory_published_commits,
)
from .memory.pending import (
    memory_read_commit_log as _memory_read_commit_log,
)
from .memory.pending import (
    memory_state_files as _memory_state_files,
)
from .memory.pending import (
    memory_state_hashes_with_logs as _memory_state_hashes_with_logs,
)
from .memory.pending import (
    memory_state_path as _memory_state_path,
)
from .memory.pending import (
    memory_timeline as _memory_timeline,
)
from .memory.pending import (
    memory_unpublished_commits as _memory_unpublished_commits,
)
from .memory.pending import (
    next_ledger_seq as _next_ledger_seq,
)
from .memory.pending import (
    read_ledger_entries as _read_all_ledger_entries,
)
from .memory.repository import (
    MOUNT_PATH,
    commit_message,
    creation_command,
    entry_already_present,
    format_mount_entry,
    insert_repo_entry,
    memory_mount_path,
    memory_pending_path,
    mount_entry,
    uncommitted_memory_paths,
)
from .memory.repository import (
    memory_branch as memory_branch_name,
)
from .memory.states import (
    _format_state_id,
    _latest_state_artifact,
    _parse_state_hash,
    state_path,
)
from .operations import (
    MERGE_INTO_ACTS,
    BranchTopologyReport,
    RepoOutcome,
    ResolveOutcome,
    paths_outside_scope,
)
from .operations import (
    validate_branch_topology as _validate_branch_topology,
)
from .paths import _resolve_project_root
from .paths import resolve_bootstrap_root as _resolve_bootstrap_root
from .paths import resolve_cgshome as _resolve_cgshome
from .paths import resolve_initialise_cgshome as _resolve_initialise_cgshome
from .provider import (
    creation_plan,
    looks_like_already_exists,
    looks_like_not_signed_in,
)
from .registry import (
    _path_from_tree,
    build_gts_document_from_registry,
    build_registry_from_cgs_document,
    build_registry_from_gts_document,
)
from .settings import UseCase, resolve_use_case
from .snapshot_resolver import discover_cgshome, discover_gts_path
from .status_render import (
    PROJECT_SCOPE_LABEL,
    SCOPE_LEGEND,
    SYNC_LEGEND,
    TREE_BRANCH_DETACHED,
    TREE_BRANCH_UNKNOWN,
    StatusCounts,
    _render_empty_workspace,
    _render_status_table,
    _status_display_path,
    _status_line_is_untracked,
    _status_line_path,
    _status_line_targets_any,
    _status_scope_label,
    _status_summary_counts,
    _status_tracking_label,
    _tree_branch_label,
)
from .toolchain import toolchain
from .universal_clock import ClockProtocol, SystemClock

# ============================================================
#  Runtime document layer — .gts
# ============================================================

_FREEZE_COMMAND_ORIGINS = frozenset({"freeze", "freeze_release", "freeze_state"})


def _collect_errors(checks: list[tuple[bool, str]]) -> list[str]:
    return [msg for ok, msg in checks if not ok]


def _as_write_outcomes(result: object) -> tuple[RepoOutcome, ...]:
    """Normalise a tree-write result into the outcome tuple the CLI reports.

    Every real operation returns one. A caller-supplied stand-in for the
    ``GitTree.git`` command facade — a test double, an embedder's own
    implementation — may return nothing, and a missing report must not
    become a crash *after* the write already happened.
    """
    if isinstance(result, tuple | list):
        return tuple(result)
    return ()


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
    clock: ClockProtocol | None = None,
) -> CommandRunLogger:
    """Create a :class:`CommandRunLogger` for a specific command invocation.

    ``clock`` names the run — real by default (:class:`SystemClock`), so a
    caller that cares about the exact timestamp in the logger name can
    inject a fixed one instead of two runs in the same second racing
    ``logging``'s global logger cache below.
    """
    timestamp = (clock or SystemClock()).now().strftime("%Y%m%dT%H%M%SZ")

    logger_name = f"ComplexGitSync.run.{command_name}.{timestamp}"
    logger = logging.getLogger(logger_name)
    # Two runs of one command inside the same second share this name, and
    # `logging` caches loggers globally — so without this the second run
    # keeps the first run's handler, writes to a stream that may already be
    # closed, and `logging` prints its own traceback to stderr. Handlers
    # would also accumulate, one per invocation, in any process that runs
    # more than one command.
    for stale in list(logger.handlers):
        logger.removeHandler(stale)
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
    ``.agent/.local/.localSpec/DevTickets/archive/20260903_InitFromSubmodules_DevPlanTicket.md``). Exempting it is
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


def _walk_git_repositories(
    root: Path, *, max_depth: int | None = None
) -> tuple[list[Path], bool]:
    """Return every directory under *root* that holds a ``.git``, root first.

    *max_depth* bounds the walk when given; ``None`` (the default) means
    unbounded — the walk runs until there is nothing left to look into.
    Returns the repositories found and whether a given *max_depth* stopped
    the walk early: reached while directories were still left to look
    into, meaning there may be more repositories below that were never
    seen. Unbounded, this is always ``False``.

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

    Iterative, with an explicit stack rather than recursion: an unbounded
    walk has no ceiling on how deep a real filesystem can go, and Python's
    call stack does (~1000 frames) — a tree deeper than that would raise
    ``RecursionError`` if this called itself once per level.
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

    # A stack visits depth-first, matching the previous recursive walk's
    # root-first, depth-first order; pushing each directory's children in
    # reverse means popping them back off in the original sorted order.
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        if (directory / ".git").exists():
            found.append(directory)
        children = _subdirectories(directory)
        if max_depth is not None and depth >= max_depth:
            stopped_early = stopped_early or bool(children)
            continue
        stack.extend((child, depth + 1) for child in reversed(children))

    return found, stopped_early


def _as_posix_or_none(path: Path | None) -> str | None:
    return None if path is None else path.as_posix()


def _scope_for(
    tree: WorkingGitTree,
    *,
    private: bool,
    command: str,
    default: RepoScope = RepoScope.ALL,
) -> RepoScope:
    """``--private`` narrows a command to the writable configuration repos.

    Without it, a command keeps whatever *default* it has always had — for
    the tree-wide readers and movers that is every repository, because a
    private/local one already resolves its own branch name. ``--private``
    is how a user acts on the configuration repositories alone, and it is
    refused rather than silently empty when the tree has none.
    """
    if not private:
        return default
    return resolve_command_scope(tree, private=True, command=command)


def resolve_command_scope(
    tree: WorkingGitTree,
    *,
    private: bool,
    command: str,
    all_writable: bool = False,
) -> RepoScope:
    """Pick the scope a write command runs at, and refuse an empty one.

    Three forms, and the bare one has not moved. Without a flag a write
    command touches only the repositories this project owns. With
    ``--private``, only the **writable** configuration repos — the ones the
    ``.cgs`` declares ``private = true, writable = true``. With ``--all``,
    both in a single pass, sharing one commit message the way
    ``freeze-release`` already does.

    ``--private`` raises rather than silently doing nothing when no
    repository qualifies, since a command that quietly touched nothing is
    exactly the failure this whole mechanism exists to prevent. ``--all``
    does **not**: most trees declare no writable configuration repository at
    all, and "do the project half, there was no other half" is a complete
    and correct answer there rather than a mistake to report. Same rule,
    two callers, two right answers — see the CLI's scope note, which says
    when the private half was empty.

    ``--all`` maps to :attr:`RepoScope.WRITABLE`, not :attr:`RepoScope.ALL`.
    The enum's ``ALL`` is wider: it includes the read-only configuration
    repositories, which no form of this flag ever writes to.
    """
    if private and all_writable:
        raise GitSyncError(
            f"{command}: --all and --private are mutually exclusive. --all already"
            f" reaches the writable configuration repositories alongside this"
            f" project's own; --private reaches them instead of it."
        )
    if all_writable:
        return RepoScope.WRITABLE
    if not private:
        return RepoScope.PROJECT
    if any(RepoScope.PRIVATE.includes(repo) for repo in tree.values()):
        return RepoScope.PRIVATE
    read_only = sorted(repo.name for repo in tree.values() if repo.effective_private)
    detail = (
        f" The private repositories in this tree are read-only: {', '.join(read_only)}."
        if read_only
        else " This tree declares no private repositories at all."
    )
    raise GitSyncError(
        f"{command} --private: no writable configuration repository in this tree.{detail}"
        f" A private repository is read-only unless its .cgs entry also says"
        f" writable = true."
    )


def _is_dot_named_mount(relative_path: str) -> bool:
    """True when any segment of *relative_path* is a dot-named directory.

    Used only to pick ``discover``'s default for ``private``. Being dot-named
    is a habit, not the rule — ``private`` means "shared with other projects",
    and ``docs/DocSpec`` is private without being hidden at any level. The
    habit is reliable enough to make a *default* out of, which the author
    then sees in the drafted ``.cgs`` and can delete.
    """
    return any(segment.startswith(".") for segment in relative_path.split("/") if segment != ".")


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


# Each marker records who writes it, because that decides whether it survives
# a non-English machine. OpenSSH ships no translations, so anything ssh prints
# is English everywhere; git translates its own prose, so a git-worded marker
# matches only because git_runner.py pins the message locale
# (.agent/.local/.localSpec/DevTickets/archive/20260911_GitLocaleIndependence_DevPlanTicket.md).
_SSH_AUTH_FAILURE_MARKERS = (
    # OpenSSH's own wording — locale-proof.
    "Permission denied (publickey)",
    "Host key verification failed",
    # Git's own wording. Verified 2026-09-11 against an unreachable ssh remote:
    # French renders it "Impossible de lire le depot distant." and matched
    # nothing until the locale pin landed.
    "Could not read from remote repository",
)


def _looks_like_ssh_auth_failure(git_error_message: str) -> bool:
    """Heuristic match on stderr for a likely SSH auth failure.

    No wording here is a stable API, so a missed match just degrades to the
    plain :class:`~.errors.GitSyncError` from before this hint existed.
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
    # GitHub, verified firsthand 2026-09-11 against a real HTTPS fetch of a
    # repository the ambient credentials cannot read. Server-sent, so GitHub
    # writes it in English whatever the machine's locale is — the sturdiest
    # kind of marker there is, and the reason to prefer these where a
    # provider offers one.
    "Invalid username or token",
    # Git's own wording for the same failure, measured in the same run:
    # French renders it "Echec d'authentification pour '...'". Neither this
    # line nor the one above matched before, so a GitHub HTTPS auth failure
    # produced no hint at all, in any language. This one now matches because
    # the message locale is pinned; the one above would match regardless.
    "Authentication failed for",
    # Codeberg's own wording is still unverified — ProtocolSwitchOnPush
    # §1.3: ship what's confirmed, never guess. A missed match here just
    # degrades to the plain GitSyncError, same as an unmatched SSH failure.
)


def _looks_like_https_auth_failure(git_error_message: str) -> bool:
    """The same, for HTTPS; see :func:`_looks_like_ssh_auth_failure`."""
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


def _verify_states_on_disk(
    workspace: Path,
    entries: Sequence[Any],
) -> list[tuple[int, Finding, str]]:
    """Cross-reference the chain against the States actually on disk.

    Three questions that became answerable only once a State was named by
    its content: an entry naming a State nobody can find
    (``MISSING_STATE``), a State nobody recorded (``ORPHAN_STATE``), and a
    stored snapshot whose content no longer hashes to the name it is filed
    under (``STATE_DIGEST_MISMATCH``).

    The third is the one the naming change bought outright: before, a
    State's name was a timestamp, so its contents could be edited freely and
    nothing about the name would disagree.
    """
    cgitsync_dir = workspace / ".cgitsync"
    findings: list[tuple[int, Finding, str]] = []
    recorded: dict[str, int] = {}

    for entry in entries:
        state_hash = _parse_state_hash(entry.state_id)
        if state_hash is None:
            continue
        recorded.setdefault(state_hash, entry.seq)
        snapshot = _memory_state_path(cgitsync_dir, state_hash)
        if snapshot is None:
            findings.append((
                entry.seq,
                Finding.MISSING_STATE,
                f"entry names {entry.state_id}, which is not on disk",
            ))
            continue
        try:
            document = GtsDocument.from_toml(snapshot)
            digest = document.compute_snapshot_hash()
        except (OSError, tomllib.TOMLDecodeError, ConfigValidationError) as exc:
            findings.append((
                entry.seq,
                Finding.STATE_DIGEST_MISMATCH,
                f"{snapshot.name} could not be read: {exc}",
            ))
            continue
        if digest != state_hash:
            findings.append((
                entry.seq,
                Finding.STATE_DIGEST_MISMATCH,
                f"{snapshot.name} now hashes to {digest}",
            ))

    for snapshot in _memory_state_files(cgitsync_dir):
        if snapshot.stem not in recorded:
            findings.append((
                0,
                Finding.ORPHAN_STATE,
                f"{snapshot.name} is on disk and no entry records it",
            ))
    return findings


def _identifier_of(remote_url: str) -> str:
    """The `.cgs` spelling of a remote URL, for a message that names a command.

    Best effort and used only in prose: a URL this cannot read back is
    printed as itself, which is still the thing the reader has to act on.
    """
    trimmed = remote_url.removesuffix(".git")
    if ":" in trimmed and "@" in trimmed:
        host, _, path = trimmed.partition(":")
        host = host.rpartition("@")[2]
    else:
        parts = trimmed.split("/")
        host, path = (parts[2], "/".join(parts[3:])) if len(parts) > 3 else ("", trimmed)
    provider = {"github.com": "github", "gitlab.com": "gitlab", "codeberg.org": "codeberg"}.get(
        host, ""
    )
    return f"{provider}:{path}" if provider and path else remote_url


def _remote_url_for_identifier(identifier: str) -> str:
    """The SSH remote URL a `.cgs` identifier points at.

    `repo_remote_url` builds a URL from a repository *object*; this is the
    same answer starting from the written form, which is what a command
    given ``github:flipoyo/.memory`` on a command line has. The identifier
    is parsed by `parse_repo_id` and by nothing else, as everywhere.
    """
    identity = parse_repo_id(identifier)
    return repo_remote_url(
        WorkingRepo(
            project_owner_name=identity["project_owner_name"],
            project_name=identity["project_name"],
            repo_name=identity["repo_name"],
            gitprovider=GitProvider(identity["gitprovider"]),
        ),
        AccessProtocol.SSH,
    )


def _verify_commit_logs(
    workspace: Path,
    entries: Sequence[Any],
) -> list[tuple[int, Finding, str]]:
    """Check the commit messages against the chain that vouched for them.

    Two questions. **Is the log still what the entry signed?** — an entry
    records the digest of the rows it wrote, so a row edited, added or
    removed afterwards no longer matches and is reported
    (``COMMIT_LOG_MISMATCH``). **Is there a log for a State nobody holds?**
    — messages kept under a State that is not on disk
    (``ORPHAN_COMMIT_LOG``).

    The second is reported and never repaired. Deleting a record because the
    thing beside it went missing is how a record stops being one — the same
    rule the ledger itself follows.
    """
    cgitsync_dir = workspace / ".cgitsync"
    folded_dir, pending_dir = _memory_dirs(cgitsync_dir)
    if not (folded_dir / COMMIT_LOG_DIR_NAME).is_dir() and not (pending_dir / COMMIT_LOG_DIR_NAME).is_dir():
        return []
    findings: list[tuple[int, Finding, str]] = []

    known = {entry.seq: entry for entry in entries}
    grouped = _memory_commit_log_rows(cgitsync_dir)
    for entry in entries:
        committed, published = grouped.get(entry.seq, ([], []))
        if not committed and not published:
            if entry.commit_log:
                findings.append((
                    entry.seq,
                    Finding.COMMIT_LOG_MISMATCH,
                    "entry records a commit log whose rows are gone",
                ))
            continue
        if not entry.commit_log:
            findings.append((
                entry.seq,
                Finding.COMMIT_LOG_MISMATCH,
                f"{len(committed) + len(published)} row(s) name an entry "
                "that recorded no commit log",
            ))
            continue
        try:
            digest = digest_of_rows(committed, published)
        except TypeError as exc:
            findings.append((
                entry.seq,
                Finding.COMMIT_LOG_MISMATCH,
                f"a commit row could not be read back: {exc}",
            ))
            continue
        if digest != entry.commit_log:
            findings.append((
                entry.seq,
                Finding.COMMIT_LOG_MISMATCH,
                f"rows now digest to {digest}, entry recorded {entry.commit_log}",
            ))

    for seq in sorted(set(grouped) - set(known)):
        committed, published = grouped[seq]
        findings.append((
            seq,
            Finding.COMMIT_LOG_MISMATCH,
            f"{len(committed) + len(published)} row(s) name entry {seq}, "
            "which the chain does not have",
        ))

    for state_hash in _memory_state_hashes_with_logs(cgitsync_dir):
        if _memory_state_path(cgitsync_dir, state_hash) is None:
            findings.append((
                0,
                Finding.ORPHAN_COMMIT_LOG,
                f"{state_hash[:12]} has commit messages and no State",
            ))
    return findings


def _workspace_of_snapshot(snapshot_path: Path) -> Path | None:
    """The workspace a snapshot belongs to: the directory holding its `.cgitsync`.

    A State lives at ``<workspace>/.cgitsync/state/<hash>.gts``, so the
    workspace is found by walking up — the same rule discovery uses, and the
    one answer that does not depend on an environment variable or a working
    directory. **Cloned onto another machine, this is what makes the memory
    resolve to the new tree rather than the old one's paths.**

    ``None`` for a snapshot that is not inside a workspace — a loose file
    somebody passed to ``--gts``. Guessing its own directory would be worse
    than saying nothing: the document then answers from its own recorded
    root, which is right, where a guess would silently rebuild the tree in
    the wrong place.
    """
    for candidate in (snapshot_path.parent, *snapshot_path.parents):
        if (candidate / ".cgitsync").is_dir():
            return candidate
    return None


def _write_file_atomically(destination: Path, write: Any) -> None:
    """Write *destination* through a temporary file in the same directory.

    The old layout got atomicity from building a whole state directory and
    renaming it into place. A State is one file now, so the same guarantee
    costs one rename: a reader never sees a half-written snapshot, and a
    crash leaves either the previous State or none, never a truncated one.

    *write* is called with the temporary path and must write the file.
    """
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        write(temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _next_reboot_cgs_version(cgs_dir: Path, project_name: str) -> int:
    """The `-v<N>` a `memory reboot` export should carry next.

    The topology before any reboot is implicitly `v1` and is never written
    under that name (`memory-dev_1-4_MemoryReboot_DevPlanTicket.md` §2), so
    an empty directory answers `2` — one more than the unwritten `v1` —
    and every later reboot answers one more than the highest version
    already sitting beside it. Never reused, never chosen: found by
    scanning, the same discipline a State's own name already follows.
    """
    pattern = re.compile(rf"^{re.escape(project_name)}-v(\d+)\.cgs$")
    highest = 1
    if cgs_dir.is_dir():
        for path in cgs_dir.iterdir():
            match = pattern.match(path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def _legacy_register_exists(workspace: Path) -> bool:
    """Whether *workspace* holds history in the single-file ``.lgr`` format.

    That format is what ``LocalGitRegister`` writes: one TOML file,
    rewritten whole on every operation, with a sequential id and no chain.
    It is readable and it is not verifiable — an edit to it leaves no trace
    — so a workspace that has one has history that ``verify`` must report as
    *legacy* rather than as nothing at all.

    Every workspace created before the hash-chained register is written is
    in exactly this state, which is why the answer matters more than it
    looks.
    """
    cgitsync_dir = workspace / ".cgitsync"
    # Three places one has ever lived: inside a state directory (copied
    # forward before every write), at the workspace root (older still), and
    # at `.cgitsync/<project>.lgr`, where the flat state layout put it.
    # Miss one and a workspace with history is told it has none, which is
    # the lie this whole answer exists to remove.
    for candidate in (
        cgitsync_dir.glob("state(*)_*/*.lgr"),
        cgitsync_dir.glob("*.lgr"),
        workspace.glob("*.lgr"),
    ):
        if any(candidate):
            return True
    return False


@dataclass(frozen=True, slots=True)
class _StatusView:
    """What one ``status`` run observed, before anybody renders it.

    ``is_empty`` is the workspace with no repositories in it — a project
    that has not started. It is carried as its own fact rather than inferred
    from ``rows`` being empty, because the two renderings answer it in very
    different ways and neither should have to guess.
    """

    workspace: Path
    use_case: str
    branch_label: str
    rows: list[tuple[str, str, str, str, str, str, str, str, str]]
    counts: StatusCounts
    tree_state: ProjectTreeState
    incoherent: list[str]
    is_empty: bool
    # Whether the workspace's own memory mount has anything not yet swept
    # into `memory push`. False (not just absent) whenever there is no
    # memory mount at all, so `status()` can print the hint with no further
    # check of its own.
    memory_dirty: bool = False


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
    #: Every dated fact this client writes reads the wall clock through
    #: here — `memory_push`'s commit moment, `memory_reboot`'s archive
    #: name — rather than `datetime.now(UTC)` directly, so a test can
    #: inject a fixed date instead of reaching for `monkeypatch`. Real by
    #: default; see `.agent/.local/.localSpec/DevTickets/openTickets/
    #: main_1-1_ClockSeam_DevPlanTicket.md` §2.
    clock: ClockProtocol = field(default_factory=SystemClock)
    registry: WorkingGitTree | None = None
    source_path: Path | None = None
    loaded_snapshot_path: Path | None = None
    last_gitignore_sync: tuple[GitignoreSyncEntry, ...] = ()
    # What the last add/commit/push actually did, per repository. Kept here
    # rather than returned, so these methods keep returning the tree the rest
    # of the API expects; the CLI reads it to report the repositories a sweep
    # skipped, the same way it reads last_gitignore_sync.
    last_write_outcomes: tuple[RepoOutcome, ...] = ()

    #: What the last ``push``/``tag``/``freeze`` folded and sent to this
    #: project's own memory, before doing anything else — ``memory_push``'s
    #: own result dict, or ``None`` when no memory is mounted or the fold
    #: could not proceed (warned, not raised: see ``_fold_memory_before_push``).
    #: Kept here for the same reason as ``last_write_outcomes``: a caller
    #: reading it after the fact, Python or CLI, needs no second call.
    last_memory_fold: dict[str, Any] | None = None

    #: The report the last ``verify_json`` produced, so a caller that needs
    #: both the rendered object and the verdict does not have to verify the
    #: chain twice — which with ``--repair`` would mean repairing twice.
    last_verify_report: VerificationReport | None = None
    run_logger: CommandRunLogger | None = None
    _forced_access_protocol: AccessProtocol | None = field(default=None, init=False, repr=False)
    _force_reclone: bool = field(default=False, init=False, repr=False)

    def is_loaded(self) -> bool:
        return self.registry is not None or bool(self.orchestre.git_tree.repos)

    def environment(self) -> tree_env.TreeEnvironment:
        """Observe the machine, tools, credentials, and manifests for the loaded tree."""
        tree = self.get_dependency_registry()
        tree_env.attach_source_context(tree)
        return tree_env.observe(self.git_runner, tree)

    def check_environment(self, document: CgsDocument | None = None) -> tree_env.Drift:
        """Compare the observed environment with one ``.cgs`` declaration."""
        if document is None:
            document = tree_env.source_document(self.get_dependency_registry())
            if document is None:
                raise GitSyncError(
                    "this State does not resolve to a .cgs; pass an explicit .cgs to env check."
                )
        tree = self.get_dependency_registry()
        document.attach_serialization_context(tree)
        observed = tree_env.observe(self.git_runner, tree)
        return tree_env.compare(observed, tree_env.Requirements.from_cgs(document))

    def _warn_environment_drift(self) -> None:
        try:
            drift = self.check_environment()
        except (ComplexGitSyncError, OSError, RuntimeError, ValueError):
            return
        for mismatch in (*drift.missing, *drift.older):
            warnings.warn(f"environment drift: {mismatch}", stacklevel=3)

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
    # .agent/.local/.localSpec/DevTickets/archive/20260828_Isolation_DevPlanTicket.md) — flagged, not fixed
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
                # Git's own .gitmodules default, not git_branch.DEFAULT_BRANCH
                # — this omits the key only when it would say what Git already
                # assumes, and must not move when our .cgs default moves.
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
        max_depth: int | None = None,
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
            Passed to :meth:`discover_repos`. ``None`` (the default) scans
            with no depth bound.
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
    # .agent/.local/.localSpec/DevTickets/archive/20260828_Isolation_DevPlanTicket.md) — flagged, not fixed
    # under this ticket, since a real refactor of the filesystem-walking
    # discovery flow risks behaviour change under time pressure. New code
    # is enforced at 12.
    def discover_repos(  # noqa: C901
        self,
        root_dir: str | Path | None = None,
        *,
        max_depth: int | None = None,
        output: str | Path | None = None,
    ) -> DiscoverReport:
        """Scan *root_dir* for git repositories and draft a ``.cgs`` from what is there.

        This is the entry point for adopting a project that exists on disk
        but has no ``.cgs`` describing it yet. It is a **pure read** of the
        filesystem and of each repository's git config: nothing is cloned,
        fetched, staged, or modified, and no network call is made.

        The walk descends from *root_dir* with no depth limit by default,
        treating every directory that contains a ``.git`` entry as a
        repository. Pass *max_depth* to bound it. It never descends *into*
        a ``.git`` directory — for a submodule the real git directory
        lives at ``<parent>/.git/modules/<name>`` while the child's own
        ``.git`` is a file, so walking into it would report the same
        repository twice.

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
            Maximum directory depth to descend below *root_dir*. The root
            itself is depth 0. ``None`` (the default) scans with no bound.
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
            # unset here rather than private to "disabled".
            if _is_dot_named_mount(repo.relative_path):
                # A dot-named mount (.agentSpec, .localSpec, .claude) is
                # almost always a config repository shared with other
                # projects, and "private" means exactly that: shared, so
                # tree-wide branch moves must leave it alone. Drafting it
                # private states the convention as a default the author can
                # see and delete, rather than hiding these repositories from
                # the scan — they are still found, still listed, and still
                # written out.
                entry["private"] = True
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
    # .agent/.local/.localSpec/DevTickets/archive/20260828_Isolation_DevPlanTicket.md) — flagged, not fixed
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

        A repo whose HEAD is detached is skipped by the pre-pull: there is
        no branch to fast-forward, and pulling a branch guessed from the
        ``.cgs`` would move the checkout off the commit the caller asked
        for. Its ``.gitignore`` is still written.

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
                    # Detached HEAD: no branch to fast-forward. Guessing one
                    # from the .cgs would pull a branch the caller never asked
                    # for, moving the checkout off the exact commit under test
                    # — and a CI pull-request checkout is always detached, so
                    # this is the common case, not an edge case. The
                    # .gitignore is still written below; only the pull is
                    # skipped, because there is nothing it could safely do.
                    self._log_event(
                        "gitignore_pre_pull_skipped",
                        repo_name=entry.name,
                        absolute_path=str(entry.absolute_path),
                        reason="detached HEAD: no branch to fast-forward",
                    )
                    continue
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
        project_root: Path | None = None,
    ) -> WorkingGitTree:
        """Load a ``.cgs`` file, building the registry from it.

        *project_root* overrides where the tree's repositories are assumed
        to live. Every caller but :meth:`restart` leaves it unset, which
        keeps the long-standing default: the `.cgs` file's own directory —
        right for a spec that sits at the root it describes, which is the
        ordinary case. :meth:`restart` re-syncs a tree that is *already on
        disk*, possibly from a `.cgs` that sits elsewhere in it (a
        developer spec under ``examples/``, say) — for that caller, the
        `.cgs`'s own directory is not the tree's root and must not be
        guessed as one. See
        ``.agent/.local/.localSpec/DevTickets/archive/…_PullOutsideRoot_DevPlanTicket.md``.
        """
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        self.registry = build_registry_from_cgs_document(
            document, source_path, project_root=project_root
        )
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
          already existing and is never recloned; **every dependency below it
          is deleted and cloned again**, which is not the same promise. See
          :meth:`initialise_cgs`.  All ComplexGitSync state is
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
        force_reclone: bool = False,
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

        **Dependencies are re-cloned, not adopted.** Only the root survives a
        second run: every dependency whose destination already holds files is
        deleted and cloned again. Before deleting anything, this checks each
        destination and refuses the whole run -- naming every repository, and
        deleting none -- when one holds work that exists nowhere else:
        uncommitted changes, commits not pushed to its upstream, or a branch
        with no upstream at all. A destination that is not a Git checkout (a
        clone interrupted mid-run) is still cleared without a flag.
        *force_reclone* (``--force-reclone``) skips that check and destroys
        the work.

        All ComplexGitSync state is stored under
        ``CGSHOME/.cgitsync/state(<hash>)_n/``.

        Parameters
        ----------
        config_path:
            Path to the ``.cgs`` authoring spec.
        force_reclone:
            Skip the unpushed-work check described above and clear every
            populated destination, reproducing the pre-guard behaviour.
            Destructive and unrecoverable: the old ``.git`` goes with the
            directory. ``clean_before_clone`` implies it, since ``clean-init``
            purges the workspace itself.
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
            force_reclone=force_reclone,
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
        force_reclone: bool = False,
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
        # clean-init purges the workspace itself, so its destinations are
        # already gone by the time the guard would look: it means
        # --force-reclone and says so in its own name.
        self._force_reclone = force_reclone or clean_before_clone
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
            pending = self._pending_clone_entries(sync_stack)
            self._guard_clone_destinations(pending)
            for entry in pending:
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
        self._warn_environment_drift()
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
        propagate_privacy(registry)
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
        # A snapshot records its paths against the tree, not against a
        # machine, so the reader supplies the tree: the workspace this
        # snapshot was found in. That is what lets a memory be cloned onto
        # another machine and still rebuild the right directories.
        tree_root = _workspace_of_snapshot(resolved_snapshot_path)
        self.registry = build_registry_from_gts_document(document, tree_root=tree_root)
        self.orchestre.git_tree.git.bind_tree(self.registry)
        recorded_source = document.read("project.source_cgs_path")
        self.source_path = (
            _path_from_tree(str(recorded_source), tree_root)
            if recorded_source
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
        force_reclone: bool = False,
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
        self._force_reclone = force_reclone

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
            pending = self._pending_clone_entries(sync_stack)
            self._guard_clone_destinations(pending)
            for entry in pending:
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
        # The tree this re-syncs is already on disk somewhere; find that
        # somewhere by the same walk every other command uses (cwd,
        # $CGSHOME, the default workspace), never by guessing at the .cgs
        # file's own directory. A developer spec that sits under examples/
        # — this project's own — describes a tree rooted well above it.
        try:
            established_root: Path | None = discover_cgshome()
        except FileNotFoundError:
            established_root = None
        restart_cgshome = (
            established_root
            if established_root is not None
            else self.resolve_initialise_cgshome(resolved_path)
        )
        MasterConfig.load(restart_cgshome)
        if git_user_name is not None or git_user_email is not None:
            MasterConfig.persist(restart_cgshome, user_name=git_user_name, user_email=git_user_email)
        registry = self.load_cgs(
            resolved_path, discover_nested=True, project_root=established_root
        )
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
        self._warn_environment_drift()
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
            self._warn_environment_drift()
            return registry
        raise ValueError(
            f"Unsupported source format '{resolved_source.suffix}' for {resolved_source!s}; expected .cgs or .gts."
        )

    def pull_force(
        self,
        source_path: str | Path,
        *,
        force_access_protocol: str | None = None,
        private: bool = False,
    ) -> WorkingGitTree:
        """Destructively resynchronize from a ``.cgs`` spec or ``.gts`` snapshot.

        ``force_access_protocol`` — see :meth:`push`.

        ``private`` limits the resynchronisation to the writable
        configuration repositories. It matters more here than anywhere
        else: this discards local work (``checkout -B FETCH_HEAD``, then
        ``clean -fd``), so a user asking for their configuration
        repositories alone must not get the whole tree.
        """
        resolved_source = Path(source_path).resolve()
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        self._log_event("pull_force_start", source_path=resolved_source)
        if resolved_source.suffix == ".cgs":
            # Same reasoning as restart(): this re-syncs a tree already on
            # disk, so the root is discovered, never guessed from the .cgs
            # file's own directory.
            try:
                established_root: Path | None = discover_cgshome()
            except FileNotFoundError:
                established_root = None
            registry = self.load_cgs(
                resolved_source, discover_nested=True, project_root=established_root
            )
        elif resolved_source.suffix == ".gts":
            registry = self.load_gts(resolved_source)
        else:
            raise ValueError(
                f"Unsupported source format '{resolved_source.suffix}' for {resolved_source!s}; expected .cgs or .gts."
            )
        protocol = AccessProtocol(force_access_protocol) if force_access_protocol else None
        scope = _scope_for(
            registry, private=private, command="pull-force", default=RepoScope.ALL
        )
        try:
            self.orchestre.git_tree.git.pull_force(
                self.git_runner, force_access_protocol=protocol, scope=scope
            )
        except GitSyncError as exc:
            hint = _protocol_switch_hint(str(exc), command="pull-force")
            if hint:
                raise GitSyncError(f"{exc}\n{hint}") from exc
            raise
        if not registry.is_ready():
            if scope is not RepoScope.ALL:
                raise GitSyncError(
                    f"pull-force --private did not produce a READY tree: the "
                    f"repositories outside the {scope.value} scope were not "
                    f"resynchronised, and {resolved_source.name} describes them "
                    f"too. Resynchronise from a .gts snapshot of a tree that is "
                    f"already checked out, or drop --private to do the whole tree."
                )
            raise GitSyncError("pull-force did not produce a READY tree.")
        snapshot_path = self.write_gts_snapshot(command_origin="pull-force")
        self.state_store.record_snapshot(resolved_source, snapshot_path)
        self._log_tree_transition(previous_tree_state, registry.lifecycle_state, reason="pull-force")
        self._log_event("pull_force_end", source_path=resolved_source, output_gts=snapshot_path)
        return registry

    def autofix(
        self,
        *,
        error: str | None = None,
        repo_name: str | None = None,
    ) -> "RepairOutcome":
        """Read "the former error" — or *error*, if given directly — and
        run whichever registered repair in :mod:`ComplexGitSync.autofix`
        matches it.

        With *error* omitted, reads the most recent
        ``.cgitsync/logs/*.log``'s failing command, the same one the
        owner just saw fail — see ``.agent/.local/.localSpec/DevTickets/archive/20260923_Autofix_DevPlanTicket.md``.
        *repo_name* narrows which mounted repository is diagnosed;
        omitted, it is guessed from the error text (a chain-shaped
        repository's name is normally visible in its own remote URL)
        before falling back to every repository in the tree.
        """
        from .autofix import FromCliRepair

        if self.registry is None:
            raise GitSyncError("autofix: no workspace loaded.")
        root_entry = self.registry.get("root")
        logs_dir = root_entry.absolute_path / ".cgitsync" / "logs"
        self._log_event("autofix_start", error=error, repo_name=repo_name)
        outcome = FromCliRepair().run(
            self.registry,
            self.git_runner,
            logs_dir=logs_dir,
            error=error,
            repo_name=repo_name,
        )
        self._log_event("autofix_end", repaired=outcome.repaired, detail=outcome.detail)
        return outcome

    def checkout(
        self,
        branch_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
        private: bool = False,
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
        # Said before the tree moves, because afterwards the build that
        # would say it is gone.
        self._warn_if_build_changes(branch_name)
        self.orchestre.git_tree.git.checkout(
            self.git_runner,
            branch_name,
            ref_kind=ref_kind,
            scope=_scope_for(registry, private=private, command="checkout"),
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
        *,
        private: bool = False,
    ) -> WorkingGitTree:
        """Create *branch_name* across the full tree without checkout."""
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("branch_start", branch_name=branch_name)
        scope = _scope_for(registry, private=private, command="branch")
        self.orchestre.git_tree.git.branch(self.git_runner, branch_name, scope=scope)
        if ROOT_REPO_ID in registry.repos:
            snapshot_path = self.write_gts_snapshot(command_origin="branch")
            if self.source_path is not None:
                self.state_store.record_snapshot(self.source_path, snapshot_path)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="branch")
        self._log_event("branch_end", branch_name=branch_name)
        return registry

    def close_branch(self, branch_name: str, *, private: bool = False) -> WorkingGitTree:
        """Rename *branch_name* to its closed name across the full tree, leaf-first.

        Renames, never deletes
        (`main_1-1_BranchClosing_DevPlanTicket.md` D1) —
        :func:`~ComplexGitSync.git_branch.closed_branch_name` names the
        target, and :func:`~ComplexGitSync.operations.close_branch` performs
        it. Refuses before touching any repository when *branch_name* is
        the project's own default branch, or when any repository in scope
        is currently checked out on it (D5) — see that function's own
        docstring for the full contract. ``--private`` selects the writable
        configuration repositories instead of the project's own, the same
        as ``branch`` (create).
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("close_branch_start", branch_name=branch_name)
        scope = _scope_for(registry, private=private, command="branch close")
        self.last_write_outcomes = self.orchestre.git_tree.git.close_branch(
            self.git_runner, branch_name, scope=scope
        )
        if ROOT_REPO_ID in registry.repos:
            snapshot_path = self.write_gts_snapshot(command_origin="close_branch")
            if self.source_path is not None:
                self.state_store.record_snapshot(self.source_path, snapshot_path)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="close_branch")
        self._log_event(
            "close_branch_end",
            branch_name=branch_name,
            closed=sum(1 for o in self.last_write_outcomes if o.acted),
        )
        return registry

    def _write_scope(
        self, registry: WorkingGitTree, command: str, private: bool, all_writable: bool
    ) -> RepoScope:
        """Which repositories this write command may touch. One rule, six callers."""
        return resolve_command_scope(
            registry, private=private, command=command, all_writable=all_writable
        )

    def commit(
        self,
        message: str,
        *,
        stage_all: bool = True,
        private: bool = False,
        all_writable: bool = False,
    ) -> WorkingGitTree:
        """Commit changes across the full tree, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  Repos with
        no staged changes are silently skipped.  After a successful execution
        the registry remains ``READY``.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        scope = self._write_scope(registry, "commit", private, all_writable)
        self._log_event("commit_start", message=message, stage_all=stage_all, scope=scope.value)
        self.last_write_outcomes = _as_write_outcomes(
            self.orchestre.git_tree.git.commit(
                self.git_runner,
                message,
                stage_all=stage_all,
                scope=scope,
            )
        )
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="commit")
        committed = self._collect_commit_records(registry, scope, message)
        if committed:
            # A commit changes every repository's HEAD, so the tree is in a
            # state nobody has recorded yet. Writing it here is what gives
            # the messages a State to be filed under — and what stops the
            # memory skipping every commit until the next push.
            self.write_gts_snapshot(command_origin="commit", commits=committed)
        self._log_event(
            "commit_end",
            message=message,
            committed=sum(1 for o in self.last_write_outcomes if o.acted),
        )
        return registry

    def _collect_commit_records(
        self,
        registry: WorkingGitTree,
        scope: RepoScope,
        message: str,
    ) -> list[CommitRecord]:
        """What the commit just made, one row per repository that committed.

        The outcomes come back in the order the repositories were visited,
        so they are zipped against the same walk rather than matched by
        name: two repositories may share a name, and a row attributed to the
        wrong one is worse than no row at all.
        """
        records: list[CommitRecord] = []
        visited = list(iter_tree_leaf_first(registry, scope))
        branches = GitTreeBranches(registry, self.git_runner)
        for entry, outcome in zip(visited, self.last_write_outcomes, strict=False):
            if not outcome.acted or not entry.commit_sha:
                continue
            records.append(
                CommitRecord(
                    entry=0,  # replaced with the real seq in write_gts_snapshot
                    repository=entry.name,
                    repo_id=entry.repo_id,
                    scope=SCOPE_PRIVATE if entry.effective_private else SCOPE_PROJECT,
                    branch=branches.observed(entry) or "",
                    sha=entry.commit_sha,
                    message=message,
                    authored_at=self.git_runner.commit_authored_at(
                        entry.absolute_path, entry.commit_sha
                    ),
                )
            )
        return records

    def _collect_publication_records(
        self,
        registry: WorkingGitTree,
        scope: RepoScope,
    ) -> dict[str, list[PublicationRecord]]:
        """What the push just made public, grouped by the State that holds it.

        A push publishes everything a repository has committed since the
        last one, not only the commit at its HEAD, so every remembered
        commit of that repository that carries no publication row yet gets
        one. A commit the memory never saw — made by hand, or before any of
        this existed — gets nothing: the memory speaks for what it watched.

        Repositories are matched by their `.cgs` identifier rather than by
        name, because two repositories in one tree may share a name and a
        publication filed against the wrong one is worse than none.
        """
        root_entry = registry.get("root")
        cgitsync_dir = root_entry.absolute_path / ".cgitsync"
        folded_dir, pending_dir = _memory_dirs(cgitsync_dir)
        if not (folded_dir / COMMIT_LOG_DIR_NAME).is_dir() and not (pending_dir / COMMIT_LOG_DIR_NAME).is_dir():
            return {}
        moment = self.clock.now().isoformat(timespec="seconds")
        branches = GitTreeBranches(registry, self.git_runner)
        published: dict[str, list[PublicationRecord]] = {}
        for entry, outcome in zip(
            iter_tree_leaf_first(registry, scope), self.last_write_outcomes, strict=False
        ):
            if not outcome.acted or not entry.repo_id:
                continue
            branch = branches.observed(entry)
            for state_hash, sha in _memory_unpublished_commits(cgitsync_dir, entry.repo_id):
                published.setdefault(state_hash, []).append(
                    PublicationRecord(
                        entry=0,  # replaced with the real seq in write_gts_snapshot
                        repository=entry.name,
                        sha=sha,
                        remote=repo_identifier(entry),
                        ref=f"refs/heads/{branch}" if branch else "",
                        at=moment,
                    )
                )
        return published

    def merge(
        self,
        project_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
        ff_only: bool = False,
        no_ff: bool = False,
    ) -> tuple[tuple[str, str], ...]:
        """Merge *project_branch* into the tree's current branch, leaf-first.

        *project_branch* is always the **project's** branch name. Each
        repository resolves what that means for itself: a project-owned repo
        merges that branch, and a private/local repo merges the branch
        derived from it (``<base>_<project_branch>``), because that is where
        its settings for that project branch live. ``private=True`` selects
        the writable configuration repositories instead of the project's own.

        Every repository in scope is checked before any is merged, so a
        conflict anywhere leaves the whole tree untouched. Returns one
        ``(repo_name, merged_ref)`` pair per repository a merge moved.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        scope = self._write_scope(registry, "merge", private, all_writable)
        self._log_event("merge_start", project_branch=project_branch, scope=scope.value)
        merged = self.orchestre.git_tree.git.merge(
            self.git_runner,
            project_branch,
            scope=scope,
            ff_only=ff_only,
            no_ff=no_ff,
        )
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="merge")
        self._log_event("merge_end", project_branch=project_branch, merged=len(merged))
        return merged

    def merge_into(
        self,
        source_branch: str,
        target_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
        ff_only: bool = False,
        no_ff: bool = False,
    ) -> tuple[Any, ...]:
        """Check out *target_branch* and merge *source_branch* into it.

        What `merge` does after you have already run `checkout`, except that
        it does both — and doing both in one call is the entire point, not a
        convenience. This project manages a tree containing this project,
        installed editable, so a tree-wide checkout replaces the code that
        runs the next command: `checkout` followed by `merge` makes the
        older branch merge itself. One process cannot be caught that way,
        because its modules are already loaded.

        Both names are the **project's** branches; each repository
        translates them, so a private/local repository merges
        ``<base>_<source>`` into ``<base>``.

        Every repository is checked before any is touched — a conflict or a
        missing target leaves the whole tree on the source branch, with
        nothing checked out and nothing merged.

        A State is written, as `checkout` writes one: the tree is on a
        different branch afterwards and nothing else would record it.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        scope = self._write_scope(registry, "merge", private, all_writable)
        self._log_event(
            "merge_into_start",
            source_branch=source_branch,
            target_branch=target_branch,
            scope=scope.value,
        )
        self._warn_if_build_changes(target_branch, offer_remedy=False)
        outcomes = self.orchestre.git_tree.git.merge_into(
            self.git_runner,
            source_branch,
            target_branch,
            scope=scope,
            ff_only=ff_only,
            no_ff=no_ff,
        )
        self._log_tree_transition(
            previous_state, registry.lifecycle_state, reason="merge_into"
        )
        self.write_gts_snapshot(command_origin="merge-into")
        self._log_event(
            "merge_into_end",
            source_branch=source_branch,
            target_branch=target_branch,
            acted=sum(1 for plan in outcomes if plan.status in MERGE_INTO_ACTS),
        )
        return outcomes

    def merge_into_plan(
        self,
        source_branch: str,
        target_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
    ) -> tuple[Any, ...]:
        """What :meth:`merge_into` would do, in order, without doing it.

        Decided by the same function the merge uses, so a dry run cannot
        promise something the merge then refuses.
        """
        from .operations import merge_into_status

        self._warn_if_build_changes(target_branch, offer_remedy=False)
        registry = self.get_dependency_registry()
        scope = self._write_scope(registry, "merge", private, all_writable)
        project_name = tree_project_name(registry)
        return tuple(
            merge_into_status(
                repo,
                self.git_runner,
                source_branch,
                target_branch,
                project_name=project_name,
            )
            for repo in iter_tree_leaf_first(registry, scope)
        )

    def build_installed_from(self, branch: str) -> str | None:
        """Which ComplexGitSync version *branch* holds, when this tree is one.

        ``None`` when the workspace does not contain the running
        installation, or when the branch does not carry a readable version —
        both mean there is nothing to warn about.

        This exists because a checkout of this tree rewrites the running
        tool. Knowing what the next command will be is the difference
        between a surprise and a sentence.
        """
        registry = self.registry
        if registry is None or ROOT_REPO_ID not in registry.repos:
            return None
        root = registry.get(ROOT_REPO_ID)
        if resolve_use_case(root.absolute_path) is not UseCase.NESTED:
            return None
        manifest = self.git_runner.show_file(root.absolute_path, branch, "pyproject.toml")
        if not manifest:
            return None
        found = re.search(r'^version\s*=\s*"([^"]+)"', manifest, re.MULTILINE)
        return found.group(1) if found else None

    def _warn_if_build_changes(self, branch: str, *, offer_remedy: bool = True) -> None:
        """Warn when moving to *branch* replaces the ComplexGitSync running.

        Warned rather than printed, so a Python caller hears it too — the
        CLI is not the only way this happens. Warned rather than refused,
        because checking out an older branch to read it is legitimate;
        `main_1-4_SnapshotVersionGuard` is what makes the older build fail
        honestly if it is then pointed at a newer workspace.
        """
        installed = self.build_installed_from(branch)
        if installed is None or installed == __version__:
            return
        older = installed < __version__
        # `merge --into` is already the remedy, so it does not offer itself.
        remedy = (
            f" To merge into {branch!r} instead of stranding yourself there, run "
            f"'cgitsync merge <source> --into {branch}', which checks out and "
            f"merges in one command."
            if older and offer_remedy
            else ""
        )
        warnings.warn(
            f"this tree holds the ComplexGitSync you are running: {branch!r} carries "
            f"{installed} and this is {__version__}, so the next command runs "
            f"{'an older' if older else 'a different'} build.{remedy}",
            stacklevel=3,
        )

    def merge_resolve(
        self,
        project_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
        ff_only: bool = False,
        no_ff: bool = False,
    ) -> ResolveOutcome:
        """Merge one repository at a time, stopping at the first conflict.

        Decision recorded: :meth:`merge` stays the default. It merges nothing
        when any repository conflicts, so it can never leave the conflicted
        worktree a merge tool needs. This gives that guarantee up on purpose,
        which is why it is opt-in. Requires a ``READY`` registry.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        scope = self._write_scope(registry, "merge", private, all_writable)
        self._log_event(
            "merge_resolve_start", project_branch=project_branch, scope=scope.value
        )
        outcome = self.orchestre.git_tree.git.merge_one_at_a_time(
            self.git_runner,
            project_branch,
            scope=scope,
            ff_only=ff_only,
            no_ff=no_ff,
        )
        self._log_tree_transition(
            previous_state, registry.lifecycle_state, reason="merge --resolve"
        )
        self._log_event(
            "merge_resolve_end",
            project_branch=project_branch,
            merged=len(outcome.merged),
            stopped_at=outcome.stopped_at,
        )
        return outcome

    def open_merge_tool(self, repo_id: str) -> str | None:
        """Open one repository's conflicts in a merge tool.

        *repo_id* is the registry key (:class:`ResolveOutcome`'s
        ``stopped_at_id``), never the display name (``stopped_at``): two
        repositories in a tree may share a name, and a name is not always
        its own id (`.memory`'s never is) — passing the name here used to
        raise a bare ``KeyError`` instead of finding the repository.

        Returns ``None`` once the tool has run, or the command to run by hand
        when there is no tool to open — a missing editor is a normal outcome
        here, not an error.
        """
        registry = self.get_dependency_registry()
        try:
            repo = registry.get(repo_id)
        except KeyError as exc:
            raise GitSyncError(
                f"{repo_id!r} is not a repository in this tree — expected a repo_id "
                "(ResolveOutcome.stopped_at_id), not a display name."
            ) from exc
        tool, command = self._resolve_merge_tool(repo.absolute_path)
        if tool is None:
            return (
                f"cd {repo.absolute_path} && git mergetool  "
                f"# then: cgitsync add && cgitsync commit"
            )
        self.git_runner.mergetool(
            repo.absolute_path, tool=tool, tool_command=command
        )
        return None

    def _resolve_merge_tool(self, repo_path: Path) -> tuple[str | None, str | None]:
        # The user's own merge.tool always wins; VS Code is only a suggestion
        # when they configured nothing. Argument order is git's, not VS Code's
        # docs': $REMOTE is theirs and $LOCAL ours.
        configured = self.git_runner.configured_merge_tool(repo_path)
        if configured:
            return configured, None
        if shutil.which("code") and os.environ.get("DISPLAY"):
            return "vscode", "code --wait --merge $REMOTE $LOCAL $BASE $MERGED"
        return None, None

    def refresh_private(self) -> tuple[tuple[str, str], ...]:
        """Bring each private/local repository up to date with its base branch.

        What ``pull --private`` runs. A private/local repository records this
        project's settings per project branch; those branches drift while a
        feature branch is open. This fetches and merges the base branch into
        each one, using the same merge primitive :meth:`merge` uses.

        Returns one ``(repo_name, merged_ref)`` pair per repository a merge
        moved. A repository already on its base branch has nothing to take
        and is skipped.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("refresh_private_start")
        refreshed = self.orchestre.git_tree.git.refresh_private(self.git_runner)
        self._log_tree_transition(
            previous_state, registry.lifecycle_state, reason="pull --private"
        )
        self._log_event("refresh_private_end", refreshed=len(refreshed))
        return refreshed

    def merge_plan(
        self,
        project_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
    ) -> tuple[tuple[str, str, str, tuple[Path, ...]], ...]:
        """What :meth:`merge` would do, in order, without doing it.

        One ``(repo_name, source_ref, status, conflicting_paths)`` row per
        in-scope repository, leaf-first. ``source_ref`` is the branch that
        repository would actually merge, which for a private/local repository
        is derived from *project_branch* rather than equal to it — seeing that
        translation before it runs is the point of a merge dry run.

        ``status`` is ``"merge"``, ``"already-on-it"``, ``"no-branch"`` or
        ``"conflicts"``, decided by the same function :meth:`merge` uses, so a
        dry run cannot promise something the merge then refuses.
        """
        from .operations import merge_status

        registry = self.get_dependency_registry()
        scope = self._write_scope(registry, "merge", private, all_writable)
        project_name = tree_project_name(registry)
        return tuple(
            (
                repo.name,
                *merge_status(
                    repo, self.git_runner, project_branch, project_name=project_name
                ),
            )
            for repo in iter_tree_leaf_first(registry, scope)
        )

    def add(
        self,
        paths: Sequence[str | Path] | None = None,
        *,
        private: bool = False,
        all_writable: bool = False,
    ) -> WorkingGitTree:
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
        scope = self._write_scope(registry, "add", private, all_writable)
        self._log_event(
            "add_start",
            paths=[str(p) for p in paths] if paths else None,
            scope=scope.value,
        )
        self.last_write_outcomes = _as_write_outcomes(
            self.orchestre.git_tree.git.add(self.git_runner, paths=paths, scope=scope)
        )
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="add")
        self._log_event("add_end", staged=sum(1 for o in self.last_write_outcomes if o.acted))
        return registry

    def removals_outside_scope(
        self, paths: Sequence[str | Path], *, private: bool = False
    ) -> tuple[str, ...]:
        """Why :meth:`remove` would refuse these paths, without removing any.

        One finished sentence per path whose owning repository falls outside
        the scope ``private`` selects; empty when the removal would go ahead.
        Read-only, so ``rm --dry-run`` can ask the same question the real
        run answers and never print a plan that could not execute.
        """
        registry = self.get_dependency_registry()
        scope = _scope_for(registry, private=private, command="rm", default=RepoScope.ALL)
        return paths_outside_scope(registry, paths, scope=scope)

    def remove(
        self, paths: Sequence[str | Path], *, private: bool = False
    ) -> WorkingGitTree:
        """Remove one or more tracked files, each from the repo that owns it.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise. Each path
        is resolved to its owning repo (see
        :func:`~.git_tree.resolve_repo_for_path`), removed from disk there,
        and the removal staged — a plain ``git rm``, distinct from
        :meth:`GitRunner.rm_cached` (index-only, built for the
        submodule-to-plain-clone conversion; this does not replace it).

        ``private`` narrows the removal to the writable configuration
        repositories, and is a **filter** here rather than a sweep: this
        command is handed its paths instead of finding them, so the scope
        is checked against the repository each path resolves to, and a path
        owned by a repository outside it is refused by name before anything
        is removed. Without it the reach is every repository, which is what
        this command has always done — see
        ``.agent/.local/.localSpec/DevTickets/archive/20260912_DeadScopeFlags_DevPlanTicket.md`` §2.1.

        Each repository actually removed from is reported in
        :attr:`last_write_outcomes`.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        scope = _scope_for(registry, private=private, command="rm", default=RepoScope.ALL)
        self._log_event("rm_start", paths=[str(p) for p in paths], scope=scope.value)
        self.last_write_outcomes = _as_write_outcomes(
            self.orchestre.git_tree.git.rm(self.git_runner, paths, scope=scope)
        )
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="rm")
        self._log_event("rm_end")
        return registry

    def push(
        self,
        *,
        force_access_protocol: str | None = None,
        private: bool = False,
        all_writable: bool = False,
    ) -> WorkingGitTree:
        """Push all repos to their remotes, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  After a
        successful execution the registry remains ``READY`` and refreshes the
        stored commit hashes in the runtime tree state.

        ``force_access_protocol`` (``"ssh"`` or ``"https"``,
        ``--force-protocol``), when given, rewrites each repo's remote to
        that protocol before pushing, persisting the change (``git remote
        set-url``) rather than a one-off override — see
        ``.agent/.local/.localSpec/DevTickets/archive/20260903_ProtocolSwitchOnPush_DevPlanTicket.md``. On a failure
        that looks like an auth problem, the error gains an actionable
        hint naming ``--force-protocol <the other one>``.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        scope = self._write_scope(registry, "push", private, all_writable)
        self._log_event("push_start", scope=scope.value)
        self._fold_memory_before_push()
        protocol = AccessProtocol(force_access_protocol) if force_access_protocol else None
        try:
            self.last_write_outcomes = _as_write_outcomes(
                self.orchestre.git_tree.git.push(
                    self.git_runner, force_access_protocol=protocol, scope=scope
                )
            )
        except GitSyncError as exc:
            hint = _protocol_switch_hint(str(exc), command="push")
            if hint:
                raise GitSyncError(f"{exc}\n{hint}") from exc
            raise
        snapshot_path = self.write_gts_snapshot(
            command_origin="push",
            publications=self._collect_publication_records(registry, scope),
        )
        if self.source_path is not None:
            self.state_store.record_snapshot(self.source_path, snapshot_path)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="push")
        self._log_event("push_end", pushed=sum(1 for o in self.last_write_outcomes if o.acted))
        return registry

    def tag(self, tag_name: str, *, private: bool = False) -> WorkingGitTree:
        """Create and push *tag_name* across the full tree, leaf-first.

        The runtime tree state is refreshed so the recorded tag target remains
        aligned with the synchronized repositories.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("tag_start", tag_name=tag_name)
        self._fold_memory_before_push()
        scope = (
            RepoScope.PRIVATE
            if private
            else _scope_for(registry, private=False, command="tag", default=RepoScope.WRITABLE)
        )
        self.orchestre.git_tree.git.tag(self.git_runner, tag_name, scope=scope)
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
        private: bool = False,
        release: tuple[tuple[str, str], ...] | None = None,
    ) -> WorkingGitTree:
        """Freeze a release by committing, tagging, and pushing leaf-first.

        In lifecycle terms this emits the next persisted ``.gts`` state for the
        synchronized tree.

        *release* is opt-in and ``None`` for every caller except
        :meth:`freeze_release`: :meth:`freeze`/:meth:`freeze_state` share
        this same path for internal, non-release states, which must never
        carry a release row.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        scope = _scope_for(
            registry, private=private, command="freeze", default=RepoScope.WRITABLE
        )
        self._log_event(
            "freeze_release_start",
            tag_name=tag_name,
            output_gts=output_gts,
            stage_all=stage_all,
            scope=scope.value,
        )
        self._fold_memory_before_push()
        self.orchestre.git_tree.git.freeze(
            self.git_runner,
            tag_name,
            message=message,
            stage_all=stage_all,
            scope=scope,
        )
        snapshot_path = self.write_gts_snapshot(
            command_origin="freeze_release",
            output_path=output_gts,
            freeze_name=tag_name,
            release=release,
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
        pushed — since there is nothing to pull.

        The question asked is :meth:`GitRunner.upstream_configured`, not
        :meth:`GitRunner.has_upstream`: ``git pull`` follows
        ``branch.<name>.merge``, so a branch that names an upstream is
        pullable whether or not its remote-tracking ref resolves. Asking the
        stronger question skipped the pull for every branch whose ref was
        missing — and before the fetch refspec was widened, that was every
        branch made after the clone.

        ``force_access_protocol`` — see :meth:`push` — is forwarded to the
        ``pull``/``pull-force`` and ``push`` steps above; the remote
        rewrite it makes persists (``git remote set-url``), so the
        ``freeze`` step's own tag push, further below, picks it up too
        without needing the parameter itself.

        Unlike :meth:`freeze`/:meth:`freeze_state`, this records a
        ``release`` row on the ledger entry it writes: the installed
        package's own SemVer (``__version__``) and build counter
        (``__build__``), plus *release_name* as the tag actually applied.
        See ``.agent/.local/.localSpec/AdditionalSpecs.md``, *Versioning* — *The release
        register*. The orchestrator is expected to pass a SemVer-shaped
        *release_name* (``v<semver>``, matching the tag this workflow
        pushes); that is a convention, not something this method enforces.
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
        if self.git_runner.upstream_configured(root_entry.absolute_path):
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
        release = (
            ("semver", __version__),
            ("git_tag", release_name),
            ("artefact:src", __build__),
        )
        registry = self.freeze(
            release_name,
            output_gts=output_gts,
            message=resolved_message,
            stage_all=stage_all,
            release=release,
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
        private: bool = False,
        release: tuple[tuple[str, str], ...] | None = None,
    ) -> WorkingGitTree:
        """Freeze a tree state and emit the next ``.gts`` snapshot id.

        ``private`` freezes the writable configuration repositories alone.
        Without it every repository this project may write is frozen, which
        is what this command has always done. ``release`` is
        :meth:`freeze_release`'s own parameter, threaded through rather than
        duplicated; every other caller leaves it ``None``.
        """
        return self._freeze_tag(
            name,
            output_gts=output_gts,
            message=message,
            stage_all=stage_all,
            private=private,
            release=release,
        )

    def get_dependency_registry(self) -> WorkingGitTree:
        if self.registry is None:
            raise RuntimeError("No ComplexGitSync registry is loaded.")
        self.orchestre.git_tree.git.bind_tree(self.registry)
        return self.registry

    def repo_create(
        self,
        identifier: str,
        *,
        private: bool = True,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a repository on its provider, using the provider's own tool.

        *identifier* is the ordinary `.cgs` spelling —
        ``github:flipoyo/.memory``, ``gitlab:some/group/project`` — parsed by
        `parse_repo_id` and by nothing else, so the owner or the group comes
        from the same place here as in every spec.

        **No credential is read, stored or sent by this project.** It runs
        `gh`, `glab` or `tea`, which the user has already signed in to. When
        that tool is missing or signed out, this returns the command to run
        rather than pretending it could have done it.

        ``created`` says what happened, in one word:

        - ``created`` — the repository did not exist and now does.
        - ``exists`` — it was already there. That is the normal answer for
          anybody who created it by hand before running this, so it is an
          ordinary success and not a failure.
        - ``unavailable`` — the tool is absent or signed out. The answer
          carries the command and, when it applies, the sign-in command.
        """
        identity = parse_repo_id(identifier)
        plan = creation_plan(identity, private=private, description=description)
        if plan is None:
            # Asked before the URL is built: a provider this project cannot
            # create for may not be one it can spell a remote for either.
            raise GitSyncError(
                f"no repository-creation tool is known for provider "
                f"{identity.get('gitprovider', '?')!r}. Create {identifier} on its "
                "host, then carry on — every other command speaks plain Git."
            )
        remote_url = _remote_url_for_identifier(identifier)
        answer: dict[str, Any] = {
            "repository": identifier,
            "remote_url": remote_url,
            "private": private,
            "command": plan.command,
            "sign_in": plan.sign_in,
        }
        # Asked before running anything: a tool that refuses because the
        # repository is already there says so in prose, and prose is a worse
        # thing to decide on than a ref listing.
        if self.git_runner.remote_reachable(remote_url):
            self._log_event("repo_create", repository=identifier, outcome="exists")
            return {**answer, "created": "exists"}

        run = self.git_runner.run_tool(plan.tool, *plan.argv)
        if not run.ran:
            self._log_event("repo_create", repository=identifier, outcome="no-tool")
            return {**answer, "created": "unavailable", "reason": f"{plan.tool} is not installed"}
        if run.ok:
            self._log_event("repo_create", repository=identifier, outcome="created")
            return {**answer, "created": "created"}
        if looks_like_already_exists(run.message):
            self._log_event("repo_create", repository=identifier, outcome="exists")
            return {**answer, "created": "exists"}
        if looks_like_not_signed_in(run.message):
            self._log_event("repo_create", repository=identifier, outcome="signed-out")
            return {**answer, "created": "unavailable", "reason": f"{plan.tool} is not signed in"}
        raise GitSyncError(f"{plan.command} failed: {run.message}")

    def memory_init(self, cgshome: str | Path, *, owner: str | None = None) -> dict[str, Any]:
        """Propose the `.cgs` entry that mounts this workspace's memory.

        It proposes and stops. **Nothing here creates or changes anything**:
        it returns the entry to add, the branch the memory will live on, and
        the command that creates the repository, and waits.

        The three commands that act on what it proposes are
        :meth:`repo_create`, :meth:`add_memory_repo_cgs` and
        :meth:`memory_adopt`. None of them holds a credential: creating a
        repository runs the provider's own tool, and everything else is
        plain Git.
        """
        workspace = Path(cgshome)
        registry = self.registry
        if registry is None or ROOT_REPO_ID not in registry.repos:
            raise GitSyncError(
                "cgitsync memory init needs a loaded project: run it in a workspace "
                "with a .gts, or pass --gts."
            )
        root = registry.get(ROOT_REPO_ID)
        repository_owner = owner or root.project_owner_name
        if not repository_owner:
            raise GitSyncError(
                "the project's root repository declares no owner, so no memory "
                "repository name can be proposed. Pass one explicitly."
            )
        entry = mount_entry(repository_owner, root.name)
        branches = GitTreeBranches(registry, self.git_runner)
        return {
            "entry": entry,
            "line": format_mount_entry(entry),
            "branch": memory_branch_name(root.name, branches.tree_branch or DEFAULT_BRANCH),
            "mount_path": str(memory_mount_path(workspace)),
            "create_with": creation_command(entry),
            "mounted": memory_mount_path(workspace).joinpath(".git").exists(),
        }

    def add_memory_repo_cgs(
        self,
        cgs_path: str | Path,
        *,
        cgshome: str | Path | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Add this project's memory to a `.cgs` that already exists.

        `create-cgs` writes a whole file from arguments and `configure`
        builds one from scratch; both replace, and neither appends. This
        appends — one entry, in the file's own layout, with every comment
        left where it was. §4 of the MemoryOnboarding ticket says why that
        matters more here than anywhere else.

        The file is parsed and validated before it replaces anything, so a
        `.cgs` is never left in a state that will not load.

        Adding an entry that is already there changes nothing and says so:
        running this twice is what a person does when they are not sure
        whether they ran it once.
        """
        target = Path(cgs_path).resolve()
        if not target.is_file():
            raise GitSyncError(f"{target} is not a file.")
        proposal = self.memory_init(cgshome or target.parent, owner=owner)
        entry = dict(proposal["entry"])
        line = str(proposal["line"])
        original = target.read_text(encoding="utf-8")

        if entry_already_present(original, str(entry["repository"]), str(entry["relative_path"])):
            return {"cgs": str(target), "line": line, "added": False, "entry": entry}

        try:
            updated = insert_repo_entry(original, line)
        except ValueError as exc:
            raise GitSyncError(f"{target} cannot take a repository entry: {exc}.") from exc

        # Validated before it replaces anything: a spec that will not load
        # is worse than one that lacks an entry.
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(updated, encoding="utf-8")
        try:
            CgsDocument.from_toml(temporary)
        except (ConfigValidationError, tomllib.TOMLDecodeError) as exc:
            temporary.unlink(missing_ok=True)
            raise GitSyncError(
                f"adding the memory entry would make {target.name} invalid: {exc}"
            ) from exc
        temporary.replace(target)
        self._log_event("memory_mount", cgs=target, repository=entry["repository"])
        return {"cgs": str(target), "line": line, "added": True, "entry": entry}

    def memory_clone(
        self,
        cgshome: str | Path,
        *,
        owner: str | None = None,
        branch: str | None = None,
        remote: str | None = None,
    ) -> Path:
        """Bring this project's memory onto a machine that does not have it.

        The case this exists for is a machine with **no memory at all**, so
        it asks nothing of a loaded project: give it an owner and a branch
        and it works on a bare clone. When a project *is* loaded it fills
        both in from it, which is the case that needs no arguments.

        It refuses rather than overwrites. A local memory nobody has pushed
        is the only copy of itself, and cloning another one over it would
        destroy exactly the thing this milestone exists to preserve.
        """
        workspace = Path(cgshome)
        destination = memory_mount_path(workspace)
        if (destination / ".git").exists():
            raise GitSyncError(f"{destination} is already a repository; nothing to clone.")
        if destination.is_dir() and any(destination.iterdir()):
            raise GitSyncError(
                f"{destination} already holds a memory. Move it aside before cloning "
                "one over it — this command never overwrites a local memory."
            )

        target_branch, remote_url = self._memory_remote(
            workspace, owner=owner, branch=branch, remote=remote
        )
        if not self.git_runner.remote_branch_exists(remote_url, target_branch):
            raise GitSyncError(
                f"{remote_url} has no branch {target_branch!r}: this project's memory "
                "has never been pushed, so there is nothing to clone."
            )
        self.git_runner.clone(remote_url, destination, branch=target_branch)
        self._log_event("memory_clone", destination=destination, branch=target_branch)
        return destination

    def _memory_remote(
        self,
        workspace: Path,
        *,
        owner: str | None,
        branch: str | None,
        remote: str | None,
    ) -> tuple[str, str]:
        """Which branch of which repository this workspace's memory is.

        Answers from a loaded project when there is one, and from the
        arguments when there is not — which is the fresh-machine case, where
        by definition nothing is loaded yet.
        """
        if branch is None or remote is None:
            proposal = self.memory_init(workspace, owner=owner)
            branch = branch or str(proposal["branch"])
            remote = remote or _remote_url_for_identifier(
                str(proposal["entry"]["repository"])
            )
        return branch, remote

    def memory_adopt(
        self,
        cgshome: str | Path,
        *,
        owner: str | None = None,
        branch: str | None = None,
        remote: str | None = None,
        reboot: bool = False,
    ) -> dict[str, Any]:
        """Make this workspace's memory mount *be* a repository.

        `.cgitsync/.memory` is created fresh, empty — WorkingTransitionState
        moved everything a memory used to adopt "as found" (States, ledger,
        commit logs, logs) one level up, to `.cgitsync` itself, where every
        command already writes it. There is nothing here to leave untracked
        and untouched any more: the first thing this mount ever holds is
        whatever the next `memory push` folds into it.

        Nothing is committed and nothing is pushed: `memory push` does both
        and already knows how. This only ends the state where there is
        nowhere to push *from*.

        *reboot* (`cgitsync memory adopt --reboot`,
        `memory-dev_1-4_MemoryReboot_DevPlanTicket.md` §4) skips the one
        step below that would otherwise carry history forward: starting
        *target_branch* from `fallback_branch`'s tip when that branch
        already exists on the remote. Append — inheriting that history — is
        still the default; `reboot=True` leaves the branch exactly as
        `init_repository` made it, with nothing to inherit from.
        """
        workspace = Path(cgshome)
        mount = memory_mount_path(workspace)
        if (mount / ".git").exists():
            raise GitSyncError(
                f"{mount} is already a repository. 'cgitsync memory push' sends what "
                "it has gained."
            )
        if not memory_pending_path(workspace).is_dir():
            raise GitSyncError(
                f"{memory_pending_path(workspace)} does not exist yet. Run any "
                "cgitsync command in this workspace first."
            )
        mount.mkdir(parents=True, exist_ok=True)

        target_branch, remote_url = self._memory_remote(
            workspace, owner=owner, branch=branch, remote=remote
        )
        if not self.git_runner.remote_reachable(remote_url):
            raise GitSyncError(
                f"{remote_url} is not there, or these credentials cannot see it. "
                f"Create it with 'cgitsync repo create {_identifier_of(remote_url)}'."
            )

        base = self._memory_base_branch(workspace, owner=owner)
        self.git_runner.init_repository(mount, branch=target_branch)
        self.git_runner.configure_remote(mount, "origin", remote_url)
        self.git_runner.fetch(mount)
        started_from = ""
        if not reboot and base and self.git_runner.remote_branch_exists(remote_url, base):
            # Started from the repository's own default branch so the branch
            # shares its history, which is what makes `fallback_branch` in
            # the mount entry mean something.
            self.git_runner.create_branch(mount, target_branch, start_point=f"origin/{base}")
            self.git_runner.checkout(mount, target_branch)
            started_from = base
        self._log_event(
            "memory_adopt", mount=mount, branch=target_branch, started_from=started_from
        )
        return {
            "mount": str(mount),
            "branch": target_branch,
            "remote": remote_url,
            "started_from": started_from,
            "pending": len(uncommitted_memory_paths(self.git_runner.status_porcelain(mount))),
        }

    #: Where a memory mounted before WorkingTransitionState sits: directly
    #: at the workspace's own state area, sharing it with the live-write
    #: content the new layout gives its own place. Migration's own source,
    #: named once so it is never confused with `memory_pending_path` (which
    #: still answers "where is the pending increment", true before and
    #: after a migration — the two concepts collapse onto the same path
    #: only for a workspace that has not migrated yet).
    _OLD_MOUNT_RELATIVE_PATH = ".cgitsync"

    def memory_migrate(self, cgshome: str | Path, cgs_path: str | Path) -> dict[str, Any]:
        """Move a memory mounted before WorkingTransitionState onto its new layout.

        A memory adopted before this milestone is mounted directly at
        `.cgitsync` — sharing it with States, the ledger, commit logs and
        run logs, the exact arrangement WorkingTransitionState exists to
        end (`.agent/.local/.localSpec/DevTickets/openTickets/memory-dev_1-2_WorkingTransitionState_DevPlanTicket.md`).
        This is the one-time move: `.git` and every file `git ls-files`
        names travel down into `.cgitsync/.memory`, untouched — no re-clone,
        no rewritten history — and whatever was never tracked (this
        workspace's own pending States, ledger entries, logs) stays exactly
        where it already was, which is where the new layout wants it
        anyway. The `.cgs` entry that declares the mount is then updated to
        match.

        `memory adopt` never needs this: a fresh adopt already creates the
        mount at the new path. This is only for a `.cgitsync` that is
        *already* a memory's own git repository, at the old path.
        """
        workspace = Path(cgshome)
        old_mount = workspace / self._OLD_MOUNT_RELATIVE_PATH
        new_mount = memory_mount_path(workspace)
        if (new_mount / ".git").exists():
            raise GitSyncError(f"{new_mount} is already a repository; nothing to migrate.")
        if not (old_mount / ".git").is_dir():
            raise GitSyncError(
                f"{old_mount} is not a repository — there is no old-layout memory here "
                "to migrate. 'cgitsync memory adopt' mounts a fresh one at the new layout "
                "directly."
            )

        tracked = self.git_runner.tracked_files(old_mount)
        new_mount.mkdir(parents=True, exist_ok=True)
        (old_mount / ".git").rename(new_mount / ".git")
        moved = 0
        for relative in tracked:
            source = old_mount / relative
            if not source.is_file():
                continue
            destination = new_mount / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            moved += 1

        target = Path(cgs_path).resolve()
        original = target.read_text(encoding="utf-8")
        old_needle = f'relative_path = "{self._OLD_MOUNT_RELATIVE_PATH}"'
        new_value = f'relative_path = "{MOUNT_PATH}"'
        if old_needle not in original:
            raise GitSyncError(
                f"{target} does not declare {old_needle!r} — the mount was moved on disk, "
                "but its .cgs entry needs updating by hand."
            )
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(original.replace(old_needle, new_value, 1), encoding="utf-8")
        try:
            CgsDocument.from_toml(temporary)
        except (ConfigValidationError, tomllib.TOMLDecodeError) as exc:
            temporary.unlink(missing_ok=True)
            raise GitSyncError(
                f"migrating the memory entry would make {target.name} invalid: {exc}"
            ) from exc
        temporary.replace(target)

        self._log_event(
            "memory_migrate", old_mount=old_mount, new_mount=new_mount, files_moved=moved
        )
        return {
            "old_mount": str(old_mount),
            "new_mount": str(new_mount),
            "files_moved": moved,
            "cgs": str(target),
        }

    def memory_branch(
        self,
        cgshome: str | Path,
        project_branch: str,
        *,
        push: bool = True,
    ) -> dict[str, Any]:
        """Create the memory branch another project branch will need.

        A memory born on a feature branch has never had a branch for the
        branch it is about to merge into: merging ``memory-dev`` into
        ``main`` asks for ``<project>`` where only ``<project>_memory-dev``
        has ever existed. `merge` reports that and names this command rather
        than creating the branch itself — a merge that makes its own target
        cannot tell a new project branch from a mistyped one.

        The new branch starts at the memory's current head and is pushed, so
        the merge has something to merge into on both sides.
        """
        workspace = Path(cgshome)
        mount = memory_mount_path(workspace)
        if not (mount / ".git").exists():
            raise GitSyncError(
                f"{mount} is not a repository yet. Run 'cgitsync memory adopt' first."
            )
        registry = self.get_dependency_registry()
        target = memory_branch_name(registry.get(ROOT_REPO_ID).name, project_branch)
        existed = self.git_runner.local_branch_exists(mount, target)
        if not existed:
            self.git_runner.create_branch(mount, target)
        if push:
            self.git_runner.push(mount, ref_name=target)
        self._log_event("memory_branch", mount=mount, branch=target, created=not existed)
        return {
            "mount": str(mount),
            "project_branch": project_branch,
            "branch": target,
            "created": not existed,
            "pushed": push,
        }

    def _memory_base_branch(self, workspace: Path, *, owner: str | None) -> str:
        """The branch a new memory branch starts from — the entry's fallback."""
        proposal = self.memory_init(workspace, owner=owner)
        return str(proposal["entry"].get("fallback_branch") or DEFAULT_BRANCH)

    _FOLD_SUBDIRS = ("lgr", "state", "logs", "env", ".cgs")

    def _fold_memory_pending(self, pending_dir: Path, mount: Path) -> int:
        """Move `.cgitsync`'s pending content into the memory mount.

        The heart of `memory push`, since WorkingTransitionState:
        everything a command wrote since the last fold — `lgr/`, `state/`,
        `logs/`, `.cgs/`, plus the legacy single-file `.lgr` register if
        one is still there — moves one level down, into the mount, so the
        commit this method makes next has something of its own to commit.
        A plain move is safe for all of these: entries and States are
        named uniquely (a seq never repeats; a State's name is its own
        content hash, so a name that does repeat is identical content),
        and `HEAD`/the legacy register's stable copies are meant to be
        overwritten with the newer answer.

        Commit logs are the one exception — a State committed against
        again after a fold would otherwise have its already-folded rows
        silently discarded by a plain overwrite — so those go through
        `append_commits`/`append_publications`, the same merge-on-append
        logic every other write to a commit log already uses.

        Returns how many files moved, across every subdirectory — 0 means
        there was nothing pending to fold.
        """
        moved = 0
        for name in self._FOLD_SUBDIRS:
            source = pending_dir / name
            if not source.is_dir():
                continue
            destination = mount / name
            destination.mkdir(parents=True, exist_ok=True)
            for item in sorted(source.iterdir()):
                item.replace(destination / item.name)
                moved += 1
            source.rmdir()

        commit_logs_source = pending_dir / COMMIT_LOG_DIR_NAME
        if commit_logs_source.is_dir():
            for path in sorted(commit_logs_source.glob("*.toml")):
                state_hash = path.stem
                log = read_commit_log(commit_logs_source, state_hash)
                if log["commit"]:
                    append_commits(
                        mount, state_hash, [CommitRecord(**row) for row in log["commit"]]
                    )
                if log["published"]:
                    append_publications(
                        mount, state_hash, [PublicationRecord(**row) for row in log["published"]]
                    )
                path.unlink()
                moved += 1
            commit_logs_source.rmdir()

        for legacy in pending_dir.glob("*.lgr"):
            legacy.replace(mount / legacy.name)
            moved += 1
        return moved

    def _memory_declared(self, registry: WorkingGitTree) -> bool:
        """Whether *registry* declares a memory mount at all (`MOUNT_PATH`).

        Says nothing about whether `memory adopt`/`memory clone` has run —
        `memory_push` itself answers that, by raising when the mount's
        `.git` is absent. Most trees declare no memory at all, which is why
        this check exists separately: printing anything about a memory that
        does not exist would be noise on every ordinary push.
        """
        return any(entry.relative_path == Path(MOUNT_PATH) for entry in registry.values())

    def memory_declared(self) -> bool:
        """Whether the loaded tree declares a memory mount at all.

        The public, no-argument form of :meth:`_memory_declared` — used by
        the CLI to decide whether a ``--dry-run`` plan should mention the
        fold the real run would attempt
        (`main_1-1_PushFoldsMemory_DevPlanTicket.md` D4). Says nothing
        about adoption; `memory_push` is what answers that.
        """
        return self._memory_declared(self.get_dependency_registry())

    def _fold_memory_before_push(self) -> dict[str, Any] | None:
        """Fold this project's own memory and send it, before publishing anything else.

        Unconditional, once a memory is mounted — `.cgitsync/.memory` is
        this project's own record of itself, not a `--private`-scoped
        configuration repository, so no scope flag decides whether this
        runs (`main_1-1_PushFoldsMemory_DevPlanTicket.md` D1). Called first,
        by `push()`, `tag()`, and `_freeze_tag()` (covering `freeze`/
        `freeze_state`), so `.cgitsync` never carries more than what has
        accumulated since the command that is about to publish something
        else — `freeze_release` folding twice in one run, once via its own
        `push()` call and once via its own `freeze()` call, is a harmless
        consequence of that rather than a special case.

        Returns `None`, without doing anything, when the tree declares no
        memory mount (D2, most trees). When one is declared, returns
        `memory_push`'s own result — or warns and returns `None` when
        `memory_push` raises, whether because `memory adopt`/`memory clone`
        was never run or for any other reason (D2/D3): an otherwise
        successful push, tag, or freeze must never be blocked by the
        memory's own trouble reaching its remote. Warned rather than
        printed, so a Python caller hears it too, the same reasoning
        `_warn_if_build_changes` already follows. Also recorded on
        `self.last_memory_fold`, so the CLI can report what was folded
        (count, branch, whether anything was committed) without asking
        `memory_push` to run a second time.
        """
        registry = self.get_dependency_registry()
        self.last_memory_fold = None
        if not self._memory_declared(registry):
            return None
        try:
            self.last_memory_fold = self.memory_push(self._workspace_root())
        except GitSyncError as exc:
            warnings.warn(
                f"memory not folded: {exc} Run 'cgitsync memory push' by hand "
                "once this is resolved.",
                stacklevel=3,
            )
            return None
        return self.last_memory_fold

    def memory_push(self, cgshome: str | Path, *, message: str | None = None) -> dict[str, Any]:
        """Fold what has accumulated since the last push, commit it, and send it.

        Offline is not a failure mode, it is the normal case: everything a
        memory records is written locally first and pushed when somebody
        asks. So this is a command, never automatic, and a machine with no
        network keeps a complete, valid, verifiable memory without it.

        Folding (:meth:`_fold_memory_pending`) is part of what "send what
        the memory gained" already means, not a step the caller has to
        remember to run first — `.cgitsync`'s pending content only ever
        moves into the mount here, and only here.
        """
        workspace = Path(cgshome)
        mount = memory_mount_path(workspace)
        if not (mount / ".git").exists():
            raise GitSyncError(
                f"{mount} is not a repository yet. Run 'cgitsync memory init' for the "
                "entry that mounts one, then 'cgitsync memory clone'."
            )
        status = self.memory_status(workspace)
        self._fold_memory_pending(memory_pending_path(workspace), mount)
        pending = uncommitted_memory_paths(self.git_runner.status_porcelain(mount))
        committed = False
        if pending:
            self.git_runner.stage_all(mount)
            MasterConfig.load(workspace)
            user_name, user_email = MasterConfig.resolve_identity(mount, self.git_runner)
            self.git_runner.commit(
                mount,
                message
                or commit_message(
                    Path(str(status["cgshome"])).name,
                    int(status["states"]),
                    int(status["entries"]),
                    clock=self.clock,
                ),
                user_name=user_name,
                user_email=user_email,
            )
            committed = True
        branch = self.git_runner.current_branch(mount)
        self.git_runner.push(mount, ref_name=branch, set_upstream=True)
        self._log_event("memory_push", mount=mount, branch=branch, committed=committed)
        return {
            "mount": str(mount),
            "branch": branch,
            "committed": committed,
            "recorded": len(pending),
            "states": status["states"],
            "entries": status["entries"],
        }

    def memory_reboot(self, cgshome: str | Path) -> dict[str, Any]:
        """Close this memory's current chapter and open a fresh one, keeping the old.

        Four steps (`memory-dev_1-4_MemoryReboot_DevPlanTicket.md` §1), in
        this order, touching nothing but the memory itself:

        1. Whatever `.cgitsync` is holding pending is folded in and pushed
           under the branch's current name — `memory_push`'s own fold and
           push, reused rather than duplicated, so nothing recorded since
           the last push is lost to the reboot (§5 D6).
        2. The tree's current shape is exported — `to_cgs()` against the
           loaded `.gts`, never a hand-authored file — to a permanent,
           versioned `.cgitsync/.memory/.cgs/<project>-v<N>.cgs`, committed
           and pushed by the same call as step 1.
        3. The branch is archived: pushed to origin under
           `<branch>.archived-<YYYYMMDD>` *before* the old name is removed
           from origin, never the reverse, so the commits are always
           reachable under some name on the remote — then renamed locally
           to match.
        4. A fresh branch is created under the original name; its States,
           ledger, commit logs and run logs are cleared, so the new
           branch's first commit is a true beginning. `.cgs/`'s versioned
           exports (step 2, and every export before it) are the one thing
           *not* cleared — §2 calls that directory "a permanent, ordered
           record of every shape this project's memory has ever
           described," which a reboot is not exempt from being part of.
           One fresh State is then written and immediately committed —
           *not* pushed; the next `memory push` does that, same as any
           other day — so the branch is a real, live branch the moment
           this method returns, on the project's own current branch name,
           rather than an orphan with no commit that `cgitsync status`
           (and `discover_gts_path()`, and everything built on it) could
           only read as broken. An uncommitted orphan branch was tried
           first and reported back as exactly that: not "a fresh chapter,"
           a dead one.

        Raises `GitSyncError` when the mount is not a repository yet
        (`memory adopt` first), and when today's archived name already
        exists — a second reboot the same day needs the owner to say what
        to call it, rather than silently colliding with the first.
        """
        workspace = Path(cgshome)
        mount = memory_mount_path(workspace)
        if not (mount / ".git").exists():
            raise GitSyncError(
                f"{mount} is not a repository yet. Run 'cgitsync memory adopt' first."
            )

        registry = self.load_gts(discover_gts_path(str(workspace)))
        project_name = registry.get(ROOT_REPO_ID).name

        folded = self._fold_memory_pending(memory_pending_path(workspace), mount)

        cgs_dir = mount / ".cgs"
        cgs_dir.mkdir(parents=True, exist_ok=True)
        next_version = _next_reboot_cgs_version(cgs_dir, project_name)
        exported_path = cgs_dir / f"{project_name}-v{next_version}.cgs"
        _write_file_atomically(exported_path, registry.to_cgs().to_toml)

        pushed = self.memory_push(
            workspace,
            message=f"{project_name} memory reboot: exporting v{next_version} before archiving",
        )
        current_branch = str(pushed["branch"])

        archived_branch = f"{current_branch}.archived-{self.clock.now():%Y%m%d}"
        remote_url = self.git_runner.remote_get_url(mount, "origin") or ""
        if self.git_runner.local_branch_exists(mount, archived_branch) or (
            remote_url and self.git_runner.remote_branch_exists(remote_url, archived_branch)
        ):
            raise GitSyncError(
                f"{archived_branch} already exists — this memory was already rebooted "
                "today. Archive it under another name yourself first, or wait for tomorrow."
            )

        self.git_runner.push_ref_as(mount, current_branch, archived_branch, remote="origin")
        self.git_runner.delete_remote_branch(mount, current_branch, remote="origin")
        self.git_runner.rename_branch(mount, current_branch, archived_branch)

        self.git_runner.create_orphan_branch(mount, current_branch)
        for cleared in (*self._FOLD_SUBDIRS[:-1], COMMIT_LOG_DIR_NAME):
            # Every fold subdirectory except `.cgs` — the one the fold
            # brings forward is exactly the one a reboot must not erase.
            self.git_runner.remove_tracked_path(mount, cleared)

        # Clearing `state/` leaves nothing anywhere `discover_gts_path()`
        # can find — the pending half was already empty (step 1 folded
        # it), so a workspace rebooted this way could not even run
        # `cgitsync status` afterward. `self.registry` is still the tree
        # this method loaded at the top, so writing it now gives the
        # workspace a State to resume from immediately — one State, dated
        # now, not the history just archived.
        self.write_gts_snapshot(command_origin="memory_reboot")

        # An orphan branch with nothing committed has no HEAD to read —
        # `git rev-parse HEAD` fails, and `cgitsync status` reported that
        # as `error`/`error` rather than as the healthy, just-rebooted
        # branch it actually was. Folding and committing the one State
        # just written gives the branch a real HEAD before this method
        # returns; nothing is pushed here, the same way `memory_push`'s
        # own commit step never pushes on its own — the next `memory push`
        # (or the ordinary fold inside `push`/`tag`/`freeze`, once that
        # exists) is what sends it.
        self._fold_memory_pending(memory_pending_path(workspace), mount)
        if uncommitted_memory_paths(self.git_runner.status_porcelain(mount)):
            self.git_runner.stage_all(mount)
            MasterConfig.load(workspace)
            user_name, user_email = MasterConfig.resolve_identity(mount, self.git_runner)
            self.git_runner.commit(
                mount,
                f"{project_name} memory reboot: first State of a fresh chapter",
                user_name=user_name,
                user_email=user_email,
            )

        self._log_event(
            "memory_reboot",
            mount=mount,
            archived=archived_branch,
            exported=exported_path,
            branch=current_branch,
        )
        return {
            "mount": str(mount),
            "folded": folded,
            "exported": str(exported_path),
            "archived_from": current_branch,
            "archived_to": archived_branch,
            "branch": current_branch,
        }

    def memory_status(self, cgshome: str | Path) -> dict[str, Any]:
        """What this workspace remembers, in one answer.

        How many States it holds, how long its chain is, when it was last
        written, which of the four verification answers it is in, and the
        toolchain its first and last entries record — the interesting
        question being whether those two differ.
        """
        workspace = Path(cgshome)
        entries = _read_all_ledger_entries(workspace / ".cgitsync")
        report = self.verify(workspace)
        states = _memory_state_files(workspace / ".cgitsync")
        return {
            "cgshome": str(workspace.resolve()),
            "verification": report.state.name.lower().replace("_", "-"),
            "findings": len(report.findings),
            "states": len(states),
            "entries": len(entries),
            "last_recorded_at": entries[-1].recorded_at if entries else None,
            "genesis_toolchain": dict(entries[0].toolchain) if entries else {},
            "latest_toolchain": dict(entries[-1].toolchain) if entries else {},
        }

    def memory_list(self, cgshome: str | Path) -> list[dict[str, Any]]:
        """Every State this workspace holds, with what the ledger says about it.

        One row per State on disk, newest recording first. ``recorded_at``
        and ``commands`` come from the entries that name it: a State seen
        three times has one row and three commands, because being seen twice
        is two ledger entries pointing at one name.

        A State no entry records still appears, with no timestamp. It is
        there, and saying so is more useful than hiding it — ``verify``
        reports it as an orphan.
        """
        workspace = Path(cgshome)
        cgitsync_dir = workspace / ".cgitsync"
        entries = _read_all_ledger_entries(cgitsync_dir)
        seen: dict[str, list[Any]] = {}
        for entry in entries:
            state_hash = _parse_state_hash(entry.state_id)
            if state_hash is not None:
                seen.setdefault(state_hash, []).append(entry)

        rows: list[dict[str, Any]] = []
        for snapshot in _memory_state_files(cgitsync_dir):
            recorded = seen.pop(snapshot.stem, [])
            rows.append(
                {
                    "state": snapshot.stem,
                    "path": str(snapshot),
                    "recorded_at": recorded[-1].recorded_at if recorded else None,
                    "commands": [entry.command for entry in recorded],
                }
            )
        for state_hash, recorded in seen.items():
            # Recorded, and not on disk. `verify` calls this MISSING_STATE;
            # listing it is how a reader finds out which one.
            rows.append(
                {
                    "state": state_hash,
                    "path": None,
                    "recorded_at": recorded[-1].recorded_at,
                    "commands": [entry.command for entry in recorded],
                }
            )
        rows.sort(key=lambda row: (row["recorded_at"] or "", row["state"]), reverse=True)
        return rows

    def memory_show(self, cgshome: str | Path, state: str) -> dict[str, Any]:
        """One State: what it recorded, every entry that names it, and what
        was committed.

        *state* may be the full content hash or any unambiguous prefix of
        one — a 64-character name is not something anybody retypes.

        The commit messages come back whole. Deciding that a long one should
        be shown as a single line is the printer's business, not this
        method's: a caller reading the memory from Python wants the message
        that was written, not the one that fitted.
        """
        workspace = Path(cgshome)
        cgitsync_dir = workspace / ".cgitsync"
        matches = sorted(
            snapshot
            for snapshot in _memory_state_files(cgitsync_dir)
            if snapshot.stem.startswith(state)
        )
        if not matches:
            raise GitSyncError(
                f"no State under {cgitsync_dir} (folded or pending) begins with {state!r}."
            )
        if len(matches) > 1:
            names = ", ".join(snapshot.stem[:12] for snapshot in matches)
            raise GitSyncError(f"{state!r} matches more than one State: {names}.")

        snapshot = matches[0]
        document = GtsDocument.from_toml(snapshot)
        recorded = [
            entry
            for entry in _read_all_ledger_entries(cgitsync_dir)
            if _parse_state_hash(entry.state_id) == snapshot.stem
        ]
        log = _memory_read_commit_log(cgitsync_dir, snapshot.stem)
        committed: dict[int, list[dict[str, Any]]] = {}
        for row in log["commit"]:
            committed.setdefault(int(row.get("entry", 0)), []).append(row)
        published_shas = {str(row.get("sha", "")) for row in log["published"]}
        environments = environment_store.resolve_environment_references(
            _memory_dirs(cgitsync_dir), (entry.environment for entry in recorded)
        )
        return {
            "state": snapshot.stem,
            "path": str(snapshot),
            "project": document.read("project.name"),
            "lifecycle_state": document.read("tree_state.lifecycle_state"),
            "repos": len(document.repo_states),
            "hash_canonicalisation": document.hash_canonicalisation,
            "environments": environments,
            "entries": [
                {
                    "seq": entry.seq,
                    "recorded_at": entry.recorded_at,
                    "command": entry.command,
                    "outcome": entry.outcome,
                    "toolchain": dict(entry.toolchain),
                    "commits": [
                        {**row, "published": str(row.get("sha", "")) in published_shas}
                        for row in committed.get(entry.seq, [])
                    ],
                }
                for entry in recorded
            ],
            "published": list(log["published"]),
        }

    def memory_explore(
        self,
        cgshome: str | Path,
        *,
        branch: str | None = None,
        timeline: bool = False,
    ) -> dict[str, Any]:
        """A memory a person can actually read, by branch or in ledger order.

        The default view answers *what a colleague pulling this branch
        would see*: one row per commit this memory recorded as published,
        newest push first. ``timeline=True`` answers instead *everything
        that happened, in the order it did*: one row per ledger entry —
        `checkout`, `merge`, `push` included, which the commit-only view
        drops.

        Both read the same two sources every other `memory` command does —
        `.cgitsync/.memory` (folded) and `.cgitsync` itself (pending) — so
        a memory that has never been pushed still explores; nothing here
        needs a mount to exist.

        *branch* names a memory branch other than the one checked out on
        this disk. MemoryExplore §D1 keeps this to local reads only for
        now, so a name that does not match what is actually checked out at
        the mount is refused by name, naming `memory clone --branch` as
        the way to bring that branch here first — silently answering for
        the wrong branch would be worse than saying so.
        """
        workspace = Path(cgshome)
        cgitsync_dir = workspace / ".cgitsync"
        mount = memory_mount_path(workspace)
        current_branch = (
            self.git_runner.current_branch(mount) if (mount / ".git").exists() else None
        )
        if branch is not None and branch != current_branch:
            raise GitSyncError(
                f"branch {branch!r} is not checked out at {mount}. Bring it onto this "
                f"disk first with 'cgitsync memory clone --branch {branch}'."
            )
        resolved_branch = branch or current_branch
        if timeline:
            return {"branch": resolved_branch, "entries": _memory_timeline(cgitsync_dir)}
        return {"branch": resolved_branch, "commits": _memory_published_commits(cgitsync_dir)}

    def verify(self, cgshome: str | Path, *, repair: bool = False) -> VerificationReport:
        """Say which of the four answers this workspace's history deserves.

        ``report.state`` is the answer — **verified**, **no history**,
        **legacy** or **corrupt** — and ``report.findings`` says why when it
        is the last one. The four are fixed by
        ``.agent/.local/.localSpec/AdditionalSpecs.md``, *The hash-chained register*.

        The distinction this method exists to make: an empty
        ``.cgitsync/lgr`` used to be reported as a clean chain, so the
        command answered "yes" for every workspace on earth, a tampered one
        included. Nothing writes that directory yet, which made the answer
        worthless everywhere. Now "I read a chain and it held" and "there was
        no chain to read" are different answers, and a workspace whose only
        history is the single-file ``.lgr`` register is told that its
        history is readable but not verifiable.

        What is checked when there *is* a chain: linkage (``BROKEN_LINK``),
        entry-hash integrity (``BAD_ENTRY_HASH``), sequence gaps and
        duplicates (``SEQ_GAP``/``SEQ_DUPLICATE``), and whether the cached
        ``HEAD`` agrees with the recomputed head (``HEAD_STALE``).

        Store-level checks (``MISSING_STATE``, ``ORPHAN_STATE``,
        ``STATE_DIGEST_MISMATCH`` — cross-referencing entries against the
        state directories on disk) became possible once a State was named by
        its content. ``COMMIT_LOG_MISMATCH`` and ``ORPHAN_COMMIT_LOG`` check
        the commit messages the same way: an entry carries the digest of the
        rows it wrote, so an edited log is caught by arithmetic rather than
        by trust.

        With ``repair=True``, a stale ``HEAD`` cache is corrected in place.
        Entries themselves are never rewritten or deleted — a broken chain
        is reported, not silently healed. A register that can be edited back
        into looking clean is evidence of nothing.
        """
        workspace = Path(cgshome)
        cgitsync_dir = workspace / ".cgitsync"
        entries = _read_all_ledger_entries(cgitsync_dir)
        report = verify_chain(entries)

        if entries:
            # Whichever half currently holds the highest-seq entry is where
            # the HEAD cache that matters lives — `write_entry` always
            # updates it in the same directory it just wrote to, and a
            # fold moves both together, so this is never split across the
            # two halves.
            active_lgr_dir = _current_ledger_dir(cgitsync_dir)
            cached_head = memory_ledger_store.read_head(active_lgr_dir)
            true_head = memory_ledger_store.recompute_head(active_lgr_dir)
            if cached_head != true_head:
                report.findings.append((
                    entries[-1].seq,
                    Finding.HEAD_STALE,
                    f"cached HEAD={cached_head}, recomputed HEAD={true_head}",
                ))
            report.findings.extend(_verify_states_on_disk(workspace, entries))
            report.findings.extend(_verify_commit_logs(workspace, entries))
            # The store checks run after verify_chain, so the verdict is
            # recomputed here rather than left at the chain's own — through
            # `resolve_state`, the same function the chain pass uses, so a
            # finding cannot mean one thing to one pass and something else
            # to the other. Which findings are fatal, which get their own
            # verdict, and which are reported without changing it (an
            # orphan State, for one) is decided there and only there.
            report.state = resolve_state(report.findings)
            if repair:
                memory_ledger_store.verify_and_repair_head(active_lgr_dir)
        elif _legacy_register_exists(workspace):
            report.state = HistoryState.LEGACY

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

    def _collect_status(self) -> _StatusView:
        """Everything both renderings of ``status`` are built from.

        One collection, two renderings: the table a person reads and the
        object a script reads cannot disagree about the tree, because
        neither works the answer out for itself.
        """
        registry = self.get_dependency_registry()
        workspace = self._workspace_root()
        use_case = resolve_use_case(workspace).value
        if ROOT_REPO_ID not in registry.repos:
            # A workspace with no repositories is a valid state, not a
            # failure: it is where every user starts. Answering it here is
            # what keeps `registry.get` below from raising KeyError on the
            # default workspace.
            return _StatusView(
                workspace=workspace,
                use_case=use_case,
                branch_label=TREE_BRANCH_UNKNOWN,
                rows=[],
                counts=_status_summary_counts([]),
                tree_state=build_tree_state(registry),
                incoherent=[],
                is_empty=True,
            )
        root_path = registry.get(ROOT_REPO_ID).absolute_path
        # One instance for the whole command: it reads each repository's
        # branch once and answers both the table and the split-tree warning
        # from that single read.
        branches = GitTreeBranches(registry, self.git_runner)
        entries = list(iter_tree_leaf_first(registry))
        rows = [
            self._repo_status_row(registry, entry, root_path, branches) for entry in entries
        ]
        memory_dirty = any(
            entry.relative_path == Path(MOUNT_PATH) and row[5] != "clean"
            for entry, row in zip(entries, rows, strict=True)
        )
        return _StatusView(
            workspace=workspace,
            use_case=use_case,
            branch_label=_tree_branch_label(
                branches.tree_branch, detached=branches.is_detached
            ),
            rows=rows,
            counts=_status_summary_counts(rows),
            tree_state=build_tree_state(registry),
            incoherent=self._branch_incoherence(registry, branches),
            is_empty=False,
            memory_dirty=memory_dirty,
        )

    def status_json(self) -> str:
        """``status`` as one JSON object — the same answer, for a script.

        The shape lives in ``json_render.py``, not here and not in ``cli/``,
        so every command's machine-readable output is decided in one place.
        """
        view = self._collect_status()
        if view.is_empty:
            payload = empty_status_payload(
                cgshome=str(view.workspace),
                use_case=view.use_case,
                cgitsync_branch=view.branch_label,
                lifecycle_state=view.tree_state.lifecycle_state.value,
            )
        else:
            payload = status_payload(
                cgshome=str(view.workspace),
                use_case=view.use_case,
                cgitsync_branch=view.branch_label,
                lifecycle_state=view.tree_state.lifecycle_state.value,
                is_ready=view.tree_state.is_ready,
                registry_complete=view.tree_state.registry_complete,
                rows=view.rows,
                counts=view.counts,
                warnings=view.incoherent,
            )
        return json_dumps(payload)

    def verify_json(self, cgshome: str | Path, *, repair: bool = False) -> str:
        """``verify`` as one JSON object, from the same report ``verify`` returns.

        The report is kept on :attr:`last_verify_report` so the caller can
        read the verdict — clean or not — without asking for a second
        verification, which under ``--repair`` would be a second repair.
        """
        report = self.verify(cgshome, repair=repair)
        self.last_verify_report = report
        cgitsync_dir = Path(cgshome) / ".cgitsync"
        return json_dumps(
            verify_payload(
                cgshome=str(Path(cgshome).resolve()),
                state=report.state.name.lower().replace("_", "-"),
                entries=len(_read_all_ledger_entries(cgitsync_dir)),
                findings=report.findings,
                repair=repair,
            )
        )

    def status(self) -> str:
        view = self._collect_status()
        if view.is_empty:
            return _render_empty_workspace(view.workspace, view.use_case)
        rows = view.rows
        counts = view.counts
        use_case = view.use_case
        tree_state = view.tree_state
        lines = [
            (
                "summary "
                f"ready={str(tree_state.is_ready).lower()} "
                f"complete={str(tree_state.registry_complete).lower()} "
                f"use_case={use_case} "
                f"cgitsync_branch={view.branch_label} "
                f"repos={len(rows)} "
                f"dirty={counts.dirty} "
                f"staged={counts.staged} "
                f"ahead={counts.ahead} "
                f"behind={counts.behind} "
                f"unmeasured={counts.unmeasured} "
                f"recorded_mismatch={counts.recorded_mismatch} "
                f"errors={counts.errors}"
            )
        ]
        lines.append(_render_status_table(rows))
        incoherent = view.incoherent
        if incoherent:
            lines.append(
                "warning: tree is split across branches — "
                + "; ".join(incoherent)
                + ". Run 'cgitsync checkout <branch>' to put it back."
            )
        if any(row[2] != PROJECT_SCOPE_LABEL for row in rows):
            lines.append(SCOPE_LEGEND)
        if counts.unmeasured:
            lines.append(SYNC_LEGEND)
        if counts.recorded_mismatch:
            lines.append("legend: HEAD ending with * differs from the commit recorded in the loaded .gts")
        if view.memory_dirty:
            lines.append(
                "note: .memory is dirty because it just recorded the command that made this "
                "report — that is expected after any command, not a fault. Run "
                "'cgitsync memory push' to send it; add/commit/push do not touch it."
            )
        return "\n".join(lines)

    def _append_ledger_entry(
        self,
        cgitsync_dir: Path,
        *,
        command_origin: str,
        state_hash: str,
        state_path: Path,
        tree_root: Path,
        commit_log: str = "",
        release: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        """Record in the chain that this State was seen, now, by these tools.

        This writes the chain that makes ``cgitsync verify`` meaningful.

        **Recording must never cost the command its work.** A snapshot that
        stays written if the ledger append fails. The failure is logged and
        the next `verify` reports the gap.

        Always writes into the *pending* half (``cgitsync_dir / "lgr"``,
        never ``.memory/lgr`` — that only ever gains content through
        ``memory push``'s own fold), but chains from whichever entry is
        actually last, folded or pending — `append_entry`'s own
        single-directory read would otherwise treat a workspace that just
        folded as having no history at all, and start a new genesis entry
        over real, already-folded history.
        """
        try:
            existing_entries = _read_all_ledger_entries(cgitsync_dir)
            environment_id = ""
            try:
                observed = self.environment()
                environment_store.write_environment(cgitsync_dir, observed)
                environment_id = environment_store.format_environment_id(observed.digest())
            except (ComplexGitSyncError, OSError, ValueError) as exc:
                self._log_event(
                    "environment_record_failed",
                    level=logging.WARNING,
                    error=str(exc),
                )
            entry = memory_ledger_entry.build_next_entry(
                existing_entries[-1] if existing_entries else None,
                command=command_origin,
                argv=memory_ledger_store.scrub_argv(sys.argv[1:], tree_root=tree_root),
                state_id=_format_state_id(state_hash),
                state_dir=str(state_path.parent.name),
                outcome="ok",
                clock=self.clock,
                toolchain=tuple(sorted(toolchain(self.git_runner).items())),
                commit_log=commit_log,
                environment=environment_id,
                release=release or (),
            )
            memory_ledger_store.write_entry(cgitsync_dir / "lgr", entry)
        except (memory_ledger_store.LedgerStoreError, OSError) as exc:
            self._log_event(
                "ledger_append_failed",
                level=logging.WARNING,
                register_path=cgitsync_dir / "lgr",
                error=str(exc),
            )
            return
        self._log_event(
            "ledger_append",
            register_path=cgitsync_dir / "lgr",
            seq=entry.seq,
            state_id=entry.state_id,
            command=command_origin,
        )

    def _workspace_root(self) -> Path:
        """The workspace this client is answering about.

        The root repository's path when there is one. Otherwise the
        directory the loaded snapshot belongs to, found the same way
        discovery finds a workspace: the nearest ancestor holding a
        ``.cgitsync``. An empty workspace has no root entry to ask, and it
        is exactly the case that has to answer.
        """
        registry = self.registry
        if registry is not None and ROOT_REPO_ID in registry.repos:
            return registry.get(ROOT_REPO_ID).absolute_path
        snapshot = self.loaded_snapshot_path or self.source_path
        if snapshot is None:
            return Path.cwd()
        for candidate in (snapshot.parent, *snapshot.parents):
            if (candidate / ".cgitsync").is_dir():
                return candidate
        return snapshot.parent

    def _branch_incoherence(
        self,
        registry: WorkingGitTree,
        branches: GitTreeBranches | None = None,
    ) -> list[str]:
        """Repositories that are not on the branch the tree says they should be.

        ``status`` is the one command a user runs to ask whether the tree is
        all right, and until this existed it could not see the most basic way
        for it to be wrong: a root checked out with plain ``git`` leaves every
        other repository behind, and the tree still reported ``READY``.

        The rule itself is ``GitTreeBranches``', so a private/distant repo on
        its own branch, and a private/local repo on its derived branch, are
        both coherent rather than findings — the same answer ``checkout``
        would give. A repository Git cannot answer for is skipped: this is a
        report, and one unreadable repository must not cost the reader the
        other six.
        """
        branches = branches or GitTreeBranches(registry, self.git_runner)
        return [
            f"{deviation.repo.name} is on {deviation.observed!r}, "
            f"expected {deviation.expected!r}"
            for deviation in branches.deviations(ignore_unreadable=True)
        ]

    def _repo_status_row(
        self,
        registry: WorkingGitTree,
        entry: WorkingRepo,
        root_path: Path,
        branches: GitTreeBranches,
    ) -> tuple[str, str, str, str, str, str, str, str, str]:
        display_path = _status_display_path(entry, root_path)
        scope_label = _status_scope_label(entry)
        try:
            branch = branches.observed(entry) or TREE_BRANCH_DETACHED
            head = self.git_runner.rev_parse_head(entry.absolute_path)
            status_lines = self._managed_status_lines(registry, entry)
            upstream_ref = self.git_runner.upstream_ref(entry.absolute_path)
            tracking_counts = self.git_runner.branch_tracking_counts(entry.absolute_path)
            tracking_state = self.git_runner.branch_tracking_state(entry.absolute_path)
            # Only asked when there is nothing to measure, since that is the
            # only case where the two answers differ — and it costs a git
            # subprocess per repository to ask.
            upstream_configured = tracking_state is not None or self.git_runner.upstream_configured(
                entry.absolute_path
            )
        except GitSyncError:
            return (
                entry.name,
                display_path,
                scope_label,
                entry.current_ref_name or "-",
                "-",
                "error",
                "error",
                "-",
                _short_sha(entry.commit_sha),
            )

        local_state = _local_status_from_porcelain(status_lines)
        upstream_state = _status_tracking_label(
            tracking_state, tracking_counts, upstream_configured=upstream_configured
        )
        recorded = _short_sha(entry.commit_sha)
        head_short = _short_sha(head)
        if entry.commit_sha and head and entry.commit_sha != head:
            head_short = f"{head_short}*"
        return (
            entry.name,
            display_path,
            scope_label,
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

    def _refresh_memory_mount_state(self, registry: WorkingGitTree) -> None:
        """Read the memory mount's *actual* branch and HEAD, in place.

        Every other repository's recorded `commit_sha` is kept fresh by the
        action that touched it — `checkout`, `commit`, `push` each refresh
        the repos they visited before a State is written. `memory push`
        touches the mount too, but through `git_runner` calls of its own,
        not through `commit_tree`/`push_tree` — so nothing else ever
        refreshes the *registry's* record of it, and a State whose recorded
        commit for the memory never moves would disagree with `status`'s
        own live reading of it the moment a fold first moves it
        (`memory-dev_MemoryRecordedRefresh`). Since
        `memory-dev_WorkingTransitionState`, the mount is an ordinary
        private/local repository everywhere else — `merge`/`checkout`/
        `pull` all reach it normally, and their own per-repo refresh is
        what marks it `READY`; this call only keeps its `commit_sha`/branch
        honest between one `memory push` and the next State write.

        Read-only: `git rev-parse`/`current branch`, the same questions
        `status` already asks. Silently does nothing when the mount does
        not exist yet, is not a repository yet (`memory adopt` not run),
        or — freshly adopted, nothing committed — has no HEAD to read.
        """
        for entry in registry.values():
            if entry.relative_path != Path(MOUNT_PATH):
                continue
            if not (entry.absolute_path / ".git").is_dir():
                continue
            try:
                entry.commit_sha = self.git_runner.rev_parse_head(entry.absolute_path)
            except GitSyncError:
                continue
            branch = self.git_runner.current_branch(entry.absolute_path)
            if branch:
                entry.current_ref_kind = RefKind.BRANCH
                entry.current_ref_name = branch
                entry.resolved_ref_kind = RefKind.BRANCH
                entry.resolved_ref_name = branch

    def write_gts_snapshot(
        self,
        *,
        command_origin: str,
        output_path: str | Path | None = None,
        freeze_name: str | None = None,
        commits: Sequence[Any] = (),
        publications: Mapping[str, Sequence[Any]] | None = None,
        release: tuple[tuple[str, str], ...] | None = None,
    ) -> Path:
        registry = self.get_dependency_registry()
        root_entry = registry.get("root")
        self._refresh_memory_mount_state(registry)
        document = build_gts_document_from_registry(
            registry,
            command_origin=command_origin,
            source_cgs_path=self.source_path,
            freeze_name=freeze_name,
        )
        # The State's name is its content. Two machines holding the same
        # tree write the same file name, which is the whole point of a
        # memory that can travel; and writing the same workspace twice
        # produces one State, not two. The TIME-L0 anchor that used to name
        # this is a clock reading with entropy in it, and belongs to the
        # ledger, where *when* is the subject.
        canonical_state_hash = document.ensure_snapshot_hash()
        cgitsync_dir = root_entry.absolute_path / ".cgitsync"
        cgitsync_dir.mkdir(parents=True, exist_ok=True)
        final_output_path = state_path(cgitsync_dir, canonical_state_hash)
        final_output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_file_atomically(final_output_path, document.to_toml)

        if self.source_path is not None and self.source_path.suffix == ".cgs" and self.source_path.is_file():
            # Beside the State, under its name: the spec it was built from
            # is part of what that State was.
            shutil.copy2(
                self.source_path,
                state_path(cgitsync_dir, canonical_state_hash, ".cgs"),
            )
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

        # One register, at one path. It used to be copied into every state
        # directory before each write, so a workspace held one copy per
        # operation and the parent was picked by modification time. With a
        # flat state area there is nowhere to copy it to, and nothing to
        # gain: the register is a single growing file.
        register_filename = f"{root_entry.name}.lgr"
        final_register_path = cgitsync_dir / register_filename
        if not final_register_path.is_file():
            previous_register_path = _latest_state_artifact(cgitsync_dir, register_filename)
            legacy_register_path = root_entry.absolute_path / register_filename
            if previous_register_path is None and legacy_register_path.is_file():
                previous_register_path = legacy_register_path
            if previous_register_path is not None:
                shutil.copy2(previous_register_path, final_register_path)
        legacy_register_path = root_entry.absolute_path / register_filename

        # One ledger. The single-file register this used to rewrite whole on
        # every operation is still *read* — an existing workspace resolves
        # and replays exactly as it did — but nothing writes it any more.
        # Three records of the same events, one of them tamper-evident, was
        # two too many.
        # The commit log is written before the entry that vouches for it,
        # because the entry carries its digest: an entry can only commit to
        # rows that already exist.
        commit_log_digest = ""
        if commits or publications:
            # The rows name the entry that wrote them and the entry carries
            # their digest, so one of the two has to go first. The rows do,
            # asking the ledger which sequence number is next.
            pending_seq = _next_ledger_seq(cgitsync_dir)
            written: list[Any] = []
            if commits:
                rows = [replace(record, entry=pending_seq) for record in commits]
                append_commits(cgitsync_dir, canonical_state_hash, rows)
                written.extend(rows)
            # Publications go into the logs of the States whose commits they
            # publish, which are older States than this one — a push
            # publishes work that earlier commits recorded. Written in State
            # order so the digest can be recomputed from the files later.
            for state_hash in sorted(publications or {}):
                rows = [
                    replace(record, entry=pending_seq)
                    for record in (publications or {})[state_hash]
                ]
                append_publications(cgitsync_dir, state_hash, rows)
                written.extend(rows)
            commit_log_digest = digest_of(written)
        self._append_ledger_entry(
            cgitsync_dir,
            command_origin=command_origin,
            state_hash=canonical_state_hash,
            state_path=final_output_path,
            tree_root=root_entry.absolute_path,
            commit_log=commit_log_digest,
            release=release,
        )
        # The log is a record of a run, not of a State: two runs that leave
        # the tree identical produce one State and two logs, so it is named
        # for the run and kept out of the state area entirely.
        final_log_path = cgitsync_dir / "logs" / (
            f"{command_origin}-{self.clock.now():%Y%m%dT%H%M%S%fZ}.log"
        )
        final_log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.run_logger is None:
            final_log_path.write_text(
                json.dumps(
                    {
                        "event": "memory_state_finalized",
                        "command_origin": command_origin,
                        "state_id": _format_state_id(canonical_state_hash),
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

        if legacy_register_path.is_file() and final_register_path.is_file():
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

        ref_name = resolve_entry_ref(entry, observed_branch=current_branch).name
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
        landed = BranchResolution.from_landed_ref(
            entry, current_ref, selected_ref_kind, requested_name=selected_ref
        )
        fallback_applied = landed.fallback_applied

        entry.current_ref_kind = selected_ref_kind
        entry.current_ref_name = current_ref if selected_ref_kind == RefKind.BRANCH else selected_ref
        entry.resolved_ref_kind = selected_ref_kind
        entry.resolved_ref_name = current_ref if selected_ref_kind == RefKind.BRANCH else selected_ref
        entry.commit_sha = self.git_runner.rev_parse_head(entry.absolute_path)
        entry.fallback_applied = fallback_applied
        entry.fallback_reason = landed.fallback_detail(entry)
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
        return entry.parent_id is not None and is_populated_destination(entry.absolute_path)

    def _guard_clone_destinations(self, entries: Sequence[WorkingRepo]) -> None:
        """Refuse the whole run when any destination holds unpushed work.

        Runs before the first clone of each batch, so a refusal leaves every
        repository on disk untouched. ``--force-reclone`` skips it.
        """
        if self._force_reclone:
            return
        blocked = blocked_destinations(
            [entry for entry in entries if entry.parent_id is not None],
            self.git_runner,
        )
        if blocked:
            raise GitSyncError(format_block_error(blocked))

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
        if not entry.gitprovider_declared:
            raise GitSyncError(
                f"Cannot determine a remote URL for {entry.name}: it was loaded from a "
                f".gts snapshot written before the provider was recorded there "
                f"(.agent/.local/.localSpec/DevTickets/archive/20260904_GtsProviderLoss_DevPlanTicket.md). "
                f"Regenerate the snapshot from its .cgs — e.g. 'cgitsync initialise "
                f"<the .cgs>' followed by a fresh 'freeze' — rather than cloning "
                f"from a guessed host."
            )
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

"""client — ComplexGitSyncClient, the public facade over the collaborators.

Ring: 3
Contract: hold one client's state (registry, runner, clock, the last results a
    caller reads back) and expose every public method unchanged, each
    delegating to the collaborator that owns it; keep the private helpers that
    several collaborators share, and the ones tests reach through the client.
Imports: auth_hints, autofix, cgs_format, clone_guard, command_run_logger, discovery, discovery_commands, document_loader, environment_commands, errors, git_branch, git_probes, git_repo, git_runner, git_tree, git_tree_branch, gitignore_sync, installer, memory, memory_commands, memory_setup, operations, orchestre, reporting, reports, runtime_state_store, status_render, tree_commands, tree_env, universal_clock
"""

from __future__ import annotations

import logging
import shutil
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import tree_env
from ..cgs_format import CgsDocument
from ..clone_guard import (
    CloneGuard,
)
from ..discovery import (
    ImportSubmodulesReport,
)
from ..errors import (
    ComplexGitSyncError,
    GitSyncError,
)
from ..git_branch import DEFAULT_BRANCH, BranchResolution, resolve_entry_ref

if TYPE_CHECKING:
    from ..autofix import RepairOutcome
from ..git_repo import (
    AccessProtocol,
    DiscoveryState,
    RefKind,
    RepoLifecycleState,
    RepoScope,
    SyncState,
    WorkingRepo,
    repo_remote_url,
)
from ..git_runner import GitRunner
from ..git_tree import (
    ROOT_REPO_ID,
    ProjectTreeState,
    TreeLifecycleState,
    WorkingGitTree,
    iter_tree,
)
from ..git_tree_branch import GitTreeBranches
from ..memory import (
    VerificationReport,
)
from ..memory import self_history as self_history_store
from ..memory.commit_log import (
    COMMIT_LOG_DIR_NAME,
    CommitLog,
    CommitRecord,
    PublicationRecord,
)
from ..operations import (
    BranchTopologyReport,
    RepoOutcome,
    ResolveOutcome,
)
from ..status_render import (
    TREE_BRANCH_DETACHED,
    _status_display_path,
    _status_scope_label,
    _status_tracking_label,
)
from ..universal_clock import ClockProtocol, SystemClock
from .auth_hints import AuthFailureHints
from .command_run_logger import CommandRunLogger
from .discovery_commands import DiscoveryCommands
from .document_loader import DocumentLoader
from .environment_commands import EnvironmentCommands
from .git_probes import GitProbes
from .gitignore_sync import GitignoreSync
from .installer import Installer
from .memory_commands import MemoryCommands
from .memory_setup import MemorySetup
from .orchestre import Orchestre
from .reporting import Reporting
from .reports import (
    DiscoverReport,
    GitignoreSyncEntry,
    InitFromSubmodulesReport,
)
from .runtime_state_store import RuntimeStateStore
from .tree_commands import TreeCommands


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
    #: Set when this run recorded a State in a DEV tree whose ``.cgs`` declares no memory (`MemorySetup`).
    memory_setup_due: bool = False
    run_logger: CommandRunLogger | None = None
    _forced_access_protocol: AccessProtocol | None = field(default=None, init=False, repr=False)
    _force_reclone: bool = field(default=False, init=False, repr=False)
    #: Where a memory mounted before WorkingTransitionState sits: directly
    #: at the workspace's own state area, sharing it with the live-write
    #: content the new layout gives its own place. Migration's own source,
    #: named once so it is never confused with `memory_pending_path` (which
    #: still answers "where is the pending increment", true before and
    #: after a migration — the two concepts collapse onto the same path
    #: only for a workspace that has not migrated yet).
    _OLD_MOUNT_RELATIVE_PATH = ".cgitsync"
    _FOLD_SUBDIRS = ("lgr", "state", "logs", "env")


    def __post_init__(self) -> None:
        # Collaborators hold this client and reach every method and every piece
        # of state through it, so the state lives in one place.
        self._installer = Installer(self)
        self._document_loader = DocumentLoader(self)
        self._tree_commands = TreeCommands(self)
        self._memory_commands = MemoryCommands(self)
        self._discovery_commands = DiscoveryCommands(self)
        self._reporting = Reporting(self)
        self._environment_commands = EnvironmentCommands(self)
        self._gitignore_sync = GitignoreSync(self)
        self._memory_setup = MemorySetup(self)

    def is_loaded(self) -> bool:
        return self._reporting.is_loaded()

    def environment(self) -> tree_env.TreeEnvironment:
        """Observe the machine, tools, credentials, and manifests for the loaded tree."""
        return self._environment_commands.environment()

    def check_environment(self, document: CgsDocument | None = None) -> tree_env.Drift:
        """Compare the observed environment with one ``.cgs`` declaration."""
        return self._environment_commands.check_environment(document)

    def configure(
        self,
        project: str | dict[str, Any],
        repositories: Sequence[str | dict[str, Any]],
        *,
        output_path: str | Path | None = None,
    ) -> CgsDocument:
        """Create a canonical ``.cgs`` document without interactive input."""
        return self._installer.configure(project, repositories, output_path=output_path)

    def import_submodules(
        self,
        repo_root: str | Path,
        *,
        apply: bool = False,
        recursive: bool = False,
    ) -> ImportSubmodulesReport:
        """Report or convert git submodules in *repo_root* to plain nested clones."""
        return self._discovery_commands.import_submodules(repo_root, apply=apply, recursive=recursive)

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
        """Adopt a submodule-based checkout in one call: discover, initialise, convert."""
        return self._discovery_commands.init_from_submodules(repo_root, cgs_path=cgs_path, max_depth=max_depth, dry_run=dry_run, force=force, force_access_protocol=force_access_protocol)

    def discover_repos(  # noqa: C901
        self,
        root_dir: str | Path | None = None,
        *,
        max_depth: int | None = None,
        output: str | Path | None = None,
    ) -> DiscoverReport:
        """Scan *root_dir* for git repositories and draft a ``.cgs`` from what is there."""
        return self._discovery_commands.discover_repos(root_dir, max_depth=max_depth, output=output)

    def load_cgs(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
        project_root: Path | None = None,
    ) -> WorkingGitTree:
        """Load a ``.cgs`` file, building the registry from it."""
        return self._document_loader.load_cgs(config_path, discover_nested=discover_nested, project_root=project_root)

    def initialise(
        self,
        source: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> WorkingGitTree:
        """The nested install (lifecycle step 1): a ``.cgs`` or a ``.gts``."""
        return self._installer.initialise(source, output_path=output_path)

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
        """Initialise a workspace using CGSPATH/CGSHOME semantics."""
        return self._installer.initialise_cgs(config_path, output_path=output_path, clean_before_clone=clean_before_clone, force_reclone=force_reclone, commit_gitignore=commit_gitignore, force_gitignore_sync=force_gitignore_sync, git_user_name=git_user_name, git_user_email=git_user_email, force_access_protocol=force_access_protocol)

    def initialise_gts(
        self,
        snapshot_path: str | Path,
        *,
        output_path: str | Path | None = None,
        force_reclone: bool = False,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Initialise a workspace from a ``.gts`` snapshot, each repository at its recorded commit."""
        return self._installer.initialise_gts(snapshot_path, output_path=output_path, force_reclone=force_reclone, commit_gitignore=commit_gitignore, force_gitignore_sync=force_gitignore_sync, git_user_name=git_user_name, git_user_email=git_user_email, force_access_protocol=force_access_protocol)

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
        """Initialise from an already-normalized, validated ``CgsDocument``."""
        return self._installer.initialise_cgs_document(document, source_path=source_path, output_path=output_path, clean_before_clone=clean_before_clone, force_reclone=force_reclone, commit_gitignore=commit_gitignore, force_gitignore_sync=force_gitignore_sync, git_user_name=git_user_name, git_user_email=git_user_email, force_access_protocol=force_access_protocol)

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
        return self._installer.clean_initialise_cgs(config_path, output_path=output_path, commit_gitignore=commit_gitignore, force_gitignore_sync=force_gitignore_sync, git_user_name=git_user_name, git_user_email=git_user_email, force_access_protocol=force_access_protocol)

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
        return self._installer.clean_init(config_path, output_path=output_path, commit_gitignore=commit_gitignore, force_gitignore_sync=force_gitignore_sync, git_user_name=git_user_name, git_user_email=git_user_email, force_access_protocol=force_access_protocol)

    def purge_cgs(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> tuple[Path, ...]:
        """Remove immediate child repos and project ledgers from CGSHOME."""
        return self._installer.purge_cgs(config_path, output_path=output_path)

    def purge(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> tuple[Path, ...]:
        """Remove generated clone state for a .cgs workspace."""
        return self._installer.purge(config_path, output_path=output_path)

    def resolve_cgshome(
        self,
        document: CgsDocument,
        source_path: Path,
        *,
        output_path: str | Path | None = None,
    ) -> Path:
        """Resolve CGSHOME from CGSPATH, the environment, or CWD."""
        return self._installer.resolve_cgshome(document, source_path, output_path=output_path)

    def resolve_initialise_cgshome(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> Path:
        """Read a .cgs file and resolve the CGSHOME initialise will use."""
        return self._installer.resolve_initialise_cgshome(config_path, output_path=output_path)

    def load(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> WorkingGitTree:
        """Load a ``.cgs`` or ``.gts`` source into the registry."""
        return self._document_loader.load(source_path, discover_nested=discover_nested)

    def expand(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = True,
    ) -> str:
        """Expand the dependency tree (lifecycle step 2: LOADED → PENDING)."""
        return self._document_loader.expand(source_path, discover_nested=discover_nested)

    def fix_circularities(self) -> tuple[str, ...]:
        """Resolve circularities in the loaded dependency tree (step 2.5)."""
        return self._document_loader.fix_circularities()

    def validate(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> ProjectTreeState:
        """Validate the dependency tree state (lifecycle step 3: PENDING → READY)."""
        return self._document_loader.validate(source_path, discover_nested=discover_nested)

    def load_gts(self, snapshot_path: str | Path) -> WorkingGitTree:
        return self._document_loader.load_gts(snapshot_path)

    def load_runtime_or_cgs(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> WorkingGitTree:
        return self._document_loader.load_runtime_or_cgs(config_path, discover_nested=discover_nested)

    def load_source(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
        prefer_runtime_for_cgs: bool = True,
    ) -> WorkingGitTree:
        return self._document_loader.load_source(source_path, discover_nested=discover_nested, prefer_runtime_for_cgs=prefer_runtime_for_cgs)

    def resolve_clone_root(
        self,
        config_path: str | Path,
        *,
        target_dir: str | Path | None = None,
        output_path: str | Path | None = None,
    ) -> Path:
        return self._installer.resolve_clone_root(config_path, target_dir=target_dir, output_path=output_path)

    def clone_cgs(
        self,
        config_path: str | Path,
        *,
        force_reclone: bool = False,
        target_dir: str | Path | None = None,
        output_path: str | Path | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        return self._installer.clone_cgs(config_path, force_reclone=force_reclone, target_dir=target_dir, output_path=output_path, force_access_protocol=force_access_protocol)

    def clone(
        self,
        config_path: str | Path,
        *,
        target_dir: str | Path | None = None,
        output_path: str | Path | None = None,
    ) -> WorkingGitTree:
        """Clone a project tree from a ``.cgs`` source."""
        return self._installer.clone(config_path, target_dir=target_dir, output_path=output_path)

    def resolve_bootstrap_root(
        self,
        project_name: str,
        *,
        cgs_path: str | Path | None = None,
    ) -> Path:
        """Resolve the isolated CGSHOME a :meth:`bootstrap` run will clone into."""
        return self._installer.resolve_bootstrap_root(project_name, cgs_path=cgs_path)

    def bootstrap(
        self,
        config_path: str | Path,
        project_name: str,
        *,
        cgs_path: str | Path | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Bootstrap a brand-new workspace tree from a standalone ComplexGitSync clone."""
        return self._installer.bootstrap(config_path, project_name, cgs_path=cgs_path, force_access_protocol=force_access_protocol)

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
        """Resynchronize an already-cloned tree from a ``.cgs`` file."""
        return self._installer.restart(config_path, commit_gitignore=commit_gitignore, force_gitignore_sync=force_gitignore_sync, git_user_name=git_user_name, git_user_email=git_user_email, force_access_protocol=force_access_protocol)

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
        """Resynchronize from a ``.cgs`` spec or restore from a ``.gts`` snapshot."""
        return self._tree_commands.pull(source_path, commit_gitignore=commit_gitignore, force_gitignore_sync=force_gitignore_sync, git_user_name=git_user_name, git_user_email=git_user_email, force_access_protocol=force_access_protocol)

    def pull_force(
        self,
        source_path: str | Path,
        *,
        force_access_protocol: str | None = None,
        private: bool = False,
    ) -> WorkingGitTree:
        """Destructively resynchronize from a ``.cgs`` spec or ``.gts`` snapshot."""
        return self._tree_commands.pull_force(source_path, force_access_protocol=force_access_protocol, private=private)

    def autofix(
        self,
        *,
        error: str | None = None,
        repo_name: str | None = None,
    ) -> "RepairOutcome":
        """Read "the former error" — or *error*, if given directly — and
        run whichever registered repair in :mod:`ComplexGitSync.autofix`
        matches it."""
        return self._tree_commands.autofix(error=error, repo_name=repo_name)

    def checkout(
        self,
        branch_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
        private: bool = False,
    ) -> WorkingGitTree:
        """Check out *branch_name* across the full tree from a READY ``.gts`` state."""
        return self._tree_commands.checkout(branch_name, ref_kind=ref_kind, private=private)

    def branch(
        self,
        branch_name: str,
        *,
        private: bool = False,
    ) -> WorkingGitTree:
        """Create *branch_name* across the full tree without checkout."""
        return self._tree_commands.branch(branch_name, private=private)

    def close_branch(self, branch_name: str, *, private: bool = False) -> WorkingGitTree:
        """Rename *branch_name* to its closed name across the full tree, leaf-first."""
        return self._tree_commands.close_branch(branch_name, private=private)

    def commit(
        self,
        message: str,
        *,
        stage_all: bool = True,
        private: bool = False,
        all_writable: bool = False,
    ) -> WorkingGitTree:
        """Commit changes across the full tree, leaf-first."""
        return self._tree_commands.commit(message, stage_all=stage_all, private=private, all_writable=all_writable)

    def merge(
        self,
        project_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
        ff_only: bool = False,
        no_ff: bool = False,
    ) -> tuple[tuple[str, str], ...]:
        """Merge *project_branch* into the tree's current branch, leaf-first."""
        return self._tree_commands.merge(project_branch, private=private, all_writable=all_writable, ff_only=ff_only, no_ff=no_ff)

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
        """Check out *target_branch* and merge *source_branch* into it."""
        return self._tree_commands.merge_into(source_branch, target_branch, private=private, all_writable=all_writable, ff_only=ff_only, no_ff=no_ff)

    def merge_into_plan(
        self,
        source_branch: str,
        target_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
    ) -> tuple[Any, ...]:
        """What :meth:`merge_into` would do, in order, without doing it."""
        return self._tree_commands.merge_into_plan(source_branch, target_branch, private=private, all_writable=all_writable)

    def build_installed_from(self, branch: str) -> str | None:
        """Which ComplexGitSync version *branch* holds, when this tree is one."""
        return self._environment_commands.build_installed_from(branch)

    def merge_resolve(
        self,
        project_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
        ff_only: bool = False,
        no_ff: bool = False,
    ) -> ResolveOutcome:
        """Merge one repository at a time, stopping at the first conflict."""
        return self._tree_commands.merge_resolve(project_branch, private=private, all_writable=all_writable, ff_only=ff_only, no_ff=no_ff)

    def merge_resolve_all(
        self,
        project_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
        ff_only: bool = False,
        no_ff: bool = False,
    ) -> ResolveOutcome:
        """Merge all repositories, resolving conflicts one at a time until done."""
        return self._tree_commands.merge_resolve_all(project_branch, private=private, all_writable=all_writable, ff_only=ff_only, no_ff=no_ff)

    def open_merge_tool(self, repo_id: str) -> str | None:
        """Open one repository's conflicts in a merge tool."""
        return self._tree_commands.open_merge_tool(repo_id)

    def refresh_private(self) -> tuple[tuple[str, str], ...]:
        """Bring each private/local repository up to date with its base branch."""
        return self._tree_commands.refresh_private()

    def merge_plan(
        self,
        project_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
    ) -> tuple[tuple[str, str, str, tuple[Path, ...]], ...]:
        """What :meth:`merge` would do, in order, without doing it."""
        return self._tree_commands.merge_plan(project_branch, private=private, all_writable=all_writable)

    def add(
        self,
        paths: Sequence[str | Path] | None = None,
        *,
        private: bool = False,
        all_writable: bool = False,
    ) -> WorkingGitTree:
        """Stage changes across the full tree, leaf-first."""
        return self._tree_commands.add(paths, private=private, all_writable=all_writable)

    def removals_outside_scope(
        self, paths: Sequence[str | Path], *, private: bool = False
    ) -> tuple[str, ...]:
        """Why :meth:`remove` would refuse these paths, without removing any."""
        return self._tree_commands.removals_outside_scope(paths, private=private)

    def remove(
        self, paths: Sequence[str | Path], *, private: bool = False
    ) -> WorkingGitTree:
        """Remove one or more tracked files, each from the repo that owns it."""
        return self._tree_commands.remove(paths, private=private)

    def push(
        self,
        *,
        force_access_protocol: str | None = None,
        private: bool = False,
        all_writable: bool = False,
    ) -> WorkingGitTree:
        """Push all repos to their remotes, leaf-first."""
        return self._tree_commands.push(force_access_protocol=force_access_protocol, private=private, all_writable=all_writable)

    def tag(self, tag_name: str, *, private: bool = False) -> WorkingGitTree:
        """Create and push *tag_name* across the full tree, leaf-first."""
        return self._tree_commands.tag(tag_name, private=private)

    def git(
        self,
        gittree: WorkingGitTree | None,
        command: str,
        *args: str,
    ) -> WorkingGitTree:
        """Dispatch a git command across the full tree (lifecycle step 5)."""
        return self._tree_commands.git(gittree, command, *args)

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
        """Run the minimalist release workflow from a READY tree."""
        return self._memory_commands.freeze_release(release_name, commit_message, output_gts=output_gts, message=message, stage_all=stage_all, force=force, force_access_protocol=force_access_protocol)

    def freeze_state(
        self,
        state_name: str,
        *,
        output_gts: str | Path | None = None,
        message: str | None = None,
        stage_all: bool = True,
    ) -> WorkingGitTree:
        """Freeze an internal development state from a ``READY`` tree."""
        return self._memory_commands.freeze_state(state_name, output_gts=output_gts, message=message, stage_all=stage_all)

    def launch_release(self, release_name: str) -> WorkingGitTree:
        """Check out a frozen release tag across the current READY tree."""
        return self._memory_commands.launch_release(release_name)

    def launch_state(self, snapshot_path: str | Path) -> WorkingGitTree:
        """Restore an internal ``.gts`` state."""
        return self._memory_commands.launch_state(snapshot_path)

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
        """Freeze a tree state and emit the next ``.gts`` snapshot id."""
        return self._memory_commands.freeze(name, output_gts=output_gts, message=message, stage_all=stage_all, private=private, release=release)

    def get_dependency_registry(self) -> WorkingGitTree:
        return self._reporting.get_dependency_registry()

    def repo_create(
        self,
        identifier: str,
        *,
        private: bool = True,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a repository on its provider, using the provider's own tool."""
        return self._discovery_commands.repo_create(identifier, private=private, description=description)

    def memory_init(self, cgshome: str | Path, *, owner: str | None = None) -> dict[str, Any]:
        """Propose the `.cgs` entry that mounts this workspace's memory."""
        return self._memory_commands.memory_init(cgshome, owner=owner)

    def add_memory_repo_cgs(
        self,
        cgs_path: str | Path,
        *,
        cgshome: str | Path | None = None,
        owner: str | None = None,
        entry: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add this project's memory to a `.cgs` that already exists."""
        return self._memory_commands.add_memory_repo_cgs(cgs_path, cgshome=cgshome, owner=owner, entry=entry)

    def memory_setup_proposal(
        self, cgshome: str | Path | None = None, *, provider: str | None = None, owner: str | None = None, name: str | None = None
    ) -> dict[str, Any] | None:
        """What `memory setup` would do for a DEV tree with no declared memory; ``None`` otherwise."""
        return self._memory_setup.proposal(cgshome, provider=provider, owner=owner, name=name)

    def memory_setup(
        self,
        cgshome: str | Path | None = None,
        *,
        provider: str | None = None,
        owner: str | None = None,
        name: str | None = None,
        cgs_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Create, declare and adopt the memory a DEV tree lacks, stopping at the first failure."""
        return self._memory_setup.setup(cgshome, provider=provider, owner=owner, name=name, cgs_path=cgs_path)

    def memory_setup_decline(self, cgshome: str | Path | None = None) -> Path:
        """Remember that the memory setup proposal was declined, so it is asked only once."""
        return self._memory_setup.decline(cgshome)

    def memory_clone(
        self,
        cgshome: str | Path,
        *,
        owner: str | None = None,
        branch: str | None = None,
        remote: str | None = None,
    ) -> Path:
        """Bring this project's memory onto a machine that does not have it."""
        return self._memory_commands.memory_clone(cgshome, owner=owner, branch=branch, remote=remote)

    def memory_adopt(
        self,
        cgshome: str | Path,
        *,
        owner: str | None = None,
        branch: str | None = None,
        remote: str | None = None,
        reboot: bool = False,
    ) -> dict[str, Any]:
        """Make this workspace's memory mount *be* a repository."""
        return self._memory_commands.memory_adopt(cgshome, owner=owner, branch=branch, remote=remote, reboot=reboot)

    def self_history_adopt(
        self, cgshome: str | Path, *, owner: str | None = None, branch: str | None = None
    ) -> dict[str, Any]:
        """Bootstrap self-history for this project, or retrofit it onto a
        `.memory` that was adopted before self-history existed."""
        return self._memory_commands.self_history_adopt(cgshome, owner=owner, branch=branch)

    def memory_migrate(self, cgshome: str | Path, cgs_path: str | Path) -> dict[str, Any]:
        """Move a memory mounted before WorkingTransitionState onto its new layout."""
        return self._memory_commands.memory_migrate(cgshome, cgs_path)

    def memory_branch(
        self,
        cgshome: str | Path,
        project_branch: str,
        *,
        push: bool = True,
    ) -> dict[str, Any]:
        """Create the memory branch another project branch will need."""
        return self._memory_commands.memory_branch(cgshome, project_branch, push=push)

    def memory_declared(self) -> bool:
        """Whether the loaded tree declares a memory mount at all."""
        return self._memory_commands.memory_declared()

    def memory_push(self, cgshome: str | Path, *, message: str | None = None) -> dict[str, Any]:
        """Fold what has accumulated since the last push, commit it, and send it."""
        return self._memory_commands.memory_push(cgshome, message=message)

    def self_history_add(
        self,
        cgshome: str | Path,
        *,
        ticket: str,
        goal: str,
        action: str,
        worker: self_history_store.AgentInfo,
        orchestrator: self_history_store.AgentInfo,
        conformity: self_history_store.ConformityScore,
        state_before: str = "",
        state_after: str = "",
        repos_written: Sequence[tuple[str, str]] = (),
        lint_passed: bool | None = None,
        tests_passed: bool | None = None,
        pushed: bool = False,
        pushed_reason: str = "",
    ) -> Path:
        """Write one self-history record to the pending half (AgentReport WP1)."""
        return self._memory_commands.self_history_add(cgshome, ticket=ticket, goal=goal, action=action, worker=worker, orchestrator=orchestrator, conformity=conformity, state_before=state_before, state_after=state_after, repos_written=repos_written, lint_passed=lint_passed, tests_passed=tests_passed, pushed=pushed, pushed_reason=pushed_reason)

    def memory_reboot(self, cgshome: str | Path) -> dict[str, Any]:
        """Close this memory's current chapter and open a fresh one, keeping the old."""
        return self._memory_commands.memory_reboot(cgshome)

    def memory_status(self, cgshome: str | Path) -> dict[str, Any]:
        """What this workspace remembers, in one answer."""
        return self._memory_commands.memory_status(cgshome)

    def memory_list(self, cgshome: str | Path) -> list[dict[str, Any]]:
        """Every State this workspace holds, with what the ledger says about it."""
        return self._memory_commands.memory_list(cgshome)

    def memory_self_history(self, cgshome: str | Path) -> list[dict[str, Any]]:
        """Every self-history record this workspace holds, oldest first."""
        return self._memory_commands.memory_self_history(cgshome)

    def memory_show(self, cgshome: str | Path, state: str) -> dict[str, Any]:
        """One State: what it recorded, every entry that names it, and what
        was committed."""
        return self._memory_commands.memory_show(cgshome, state)

    def memory_show_environment(self, cgshome: str | Path, env_ref: str) -> dict[str, Any]:
        """One Environment record, in full — ``memory show env=<ref>``."""
        return self._memory_commands.memory_show_environment(cgshome, env_ref)

    def memory_explore(
        self,
        cgshome: str | Path,
        *,
        branch: str | None = None,
        timeline: bool = False,
    ) -> dict[str, Any]:
        """A memory a person can actually read, by branch or in ledger order."""
        return self._memory_commands.memory_explore(cgshome, branch=branch, timeline=timeline)

    def verify(self, cgshome: str | Path, *, repair: bool = False) -> VerificationReport:
        """Say which of the four answers this workspace's history deserves."""
        return self._memory_commands.verify(cgshome, repair=repair)

    def get_tree_state(self) -> ProjectTreeState:
        return self._reporting.get_tree_state()

    def discover_nested_configs(self) -> tuple[str, ...]:
        return self._discovery_commands.discover_nested_configs()

    def format_project_tree(self, *, verbose: bool = True) -> str:
        return self._reporting.format_project_tree(verbose=verbose)

    def format_repo_tree(self) -> str:
        return self._reporting.format_repo_tree()

    def view_tree(
        self,
        *,
        depth: int | None = None,
        collapse: tuple[str, ...] = (),
    ) -> str:
        return self._reporting.view_tree(depth=depth, collapse=collapse)

    def view_operation(self) -> str:
        return self._reporting.view_operation()

    def status_json(self) -> str:
        """``status`` as one JSON object — the same answer, for a script."""
        return self._reporting.status_json()

    def verify_json(self, cgshome: str | Path, *, repair: bool = False) -> str:
        """``verify`` as one JSON object, from the same report ``verify`` returns."""
        return self._reporting.verify_json(cgshome, repair=repair)

    def status(self) -> str:
        return self._reporting.status()

    def describe_cgs(self) -> str:
        return self._document_loader.describe_cgs()

    def print(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
        prefer_runtime_for_cgs: bool = True,
    ) -> str:
        """Return a printable JSON summary for ``.cgs`` or ``.gts`` sources."""
        return self._reporting.print(source_path, discover_nested=discover_nested, prefer_runtime_for_cgs=prefer_runtime_for_cgs)

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
        return self._document_loader.write_gts_snapshot(command_origin=command_origin, output_path=output_path, freeze_name=freeze_name, commits=commits, publications=publications, release=release)

    def get_ledger_history(self, register_path: str | Path) -> list[dict[str, Any]]:
        """Return all ledger events for *register_path* in topological DAG order."""
        return self._memory_commands.get_ledger_history(register_path)

    def replay_ledger(self, register_path: str | Path) -> list[dict[str, Any]]:
        """Return ledger events in topological order for deterministic replay."""
        return self._memory_commands.replay_ledger(register_path)

    def validate_branch_topology(self) -> BranchTopologyReport:
        """Inspect and validate the workspace branch topology."""
        return self._environment_commands.validate_branch_topology()

    def validate_topology(self) -> BranchTopologyReport:
        """Inspect and validate the workspace branch topology."""
        return self._environment_commands.validate_topology()

    def _assert_adoptable(
        self,
        root: Path,
        report: DiscoverReport,
        *,
        project_name: str,
        reuse_existing: bool,
        force: bool,
    ) -> None:
        """Refuse an adoption that cannot work, before anything is written."""
        return self._discovery_commands._assert_adoptable(root, report, project_name=project_name, reuse_existing=reuse_existing, force=force)

    def _resolve_merge_tool(self, repo_path: Path) -> tuple[str | None, str | None]:
        # The user's own merge.tool always wins; VS Code is only a suggestion
        # when they configured nothing. Argument order is git's, not VS Code's
        # docs': $REMOTE is theirs and $LOCAL ours.
        return self._tree_commands._resolve_merge_tool(repo_path)

    def _adopt_self_history_if_declared(
        self, workspace: Path, *, branch: str
    ) -> dict[str, Any] | None:
        """Follow what `.memory`'s own already-fetched content already
        decided, if anything — never bootstraps, never probes a remote
        that has no reason to exist."""
        return self._memory_commands._adopt_self_history_if_declared(workspace, branch=branch)

    def _warn_environment_drift(self) -> None:
        try:
            drift = self.check_environment()
        except (ComplexGitSyncError, OSError, RuntimeError, ValueError):
            return
        for mismatch in (*drift.missing, *drift.older):
            warnings.warn(f"environment drift: {mismatch}", stacklevel=4)

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

    def _write_scope(
        self, registry: WorkingGitTree, command: str, private: bool, all_writable: bool
    ) -> RepoScope:
        """Which repositories this write command may touch. One rule, six callers."""
        return GitProbes.resolve_command_scope(
            registry, private=private, command=command, all_writable=all_writable
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
        scope = GitProbes.scope_for(
            registry, private=private, command="freeze", default=RepoScope.WRITABLE
        )
        self._log_event(
            "freeze_release_start",
            tag_name=tag_name,
            output_gts=output_gts,
            stage_all=stage_all,
            scope=scope.value,
        )
        self._memory_commands._fold_memory_before_push()
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
                log = CommitLog(commit_logs_source).read(state_hash)
                if log["commit"]:
                    CommitLog(mount).append_commits(state_hash, [CommitRecord(**row) for row in log["commit"]])
                if log["published"]:
                    CommitLog(mount).append_publications(state_hash, [PublicationRecord(**row) for row in log["published"]])
                path.unlink()
                moved += 1
            commit_logs_source.rmdir()

        for legacy in pending_dir.glob("*.lgr"):
            legacy.replace(mount / legacy.name)
            moved += 1
        return moved

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
            status_lines = self._reporting._managed_status_lines(registry, entry)
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
                GitProbes.short_sha(entry.commit_sha),
            )

        local_state = GitProbes.local_status_from_porcelain(status_lines)
        upstream_state = _status_tracking_label(
            tracking_state, tracking_counts, upstream_configured=upstream_configured
        )
        recorded = GitProbes.short_sha(entry.commit_sha)
        head_short = GitProbes.short_sha(head)
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
            if effective_protocol == AccessProtocol.SSH and AuthFailureHints.looks_like_ssh_auth_failure(str(exc)):
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
        return entry.parent_id is not None and CloneGuard.is_populated(entry.absolute_path)

    def _guard_clone_destinations(self, entries: Sequence[WorkingRepo]) -> None:
        """Refuse the whole run when any destination holds unpushed work.

        Runs before the first clone of each batch, so a refusal leaves every
        repository on disk untouched. ``--force-reclone`` skips it.
        """
        if self._force_reclone:
            return
        blocked = CloneGuard.blocked([entry for entry in entries if entry.parent_id is not None], self.git_runner)
        if blocked:
            raise GitSyncError(CloneGuard.format_error(blocked))

    def _select_clone_ref(self, entry: WorkingRepo, remote_url: str) -> tuple[str, RefKind]:
        if entry.target_ref_kind == RefKind.TAG and entry.target_ref_name:
            if self.git_runner.remote_tag_exists(remote_url, entry.target_ref_name):
                return (entry.target_ref_name, RefKind.TAG)
            raise GitSyncError(
                f"No cloneable tag found for {entry.name}: expected '{entry.target_ref_name}' on {remote_url}"
            )

        if entry.effective_private and entry.effective_writable:
            return self._select_private_local_ref(entry, remote_url)

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

    def _select_private_local_ref(self, entry: WorkingRepo, remote_url: str) -> tuple[str, RefKind]:
        """The branch to clone a private/local repository on, three rungs deep.

        The first clone must decide what every later branch move already
        decides — :class:`~ComplexGitSync.git_tree_branch.GitTreeBranches`, and
        through it ``git_branch.resolve_propagated_ref`` — from the branch the
        tree's root is on, so that both agree on the name however the ``.cgs``
        was written. That name is created lazily by a private commit, so on a
        first install it usually does not exist yet; the shared repository's own
        branch is then the honest answer, not an error.

        In order: the computed private/local branch, the entry's declared
        ``fallback_branch``, the branch the remote's own ``HEAD`` points at.
        """
        computed = GitTreeBranches(self.get_dependency_registry()).target(
            entry, self._tree_branch_for_clone()
        ).name
        entry.target_ref_name = computed
        entry.target_ref_kind = RefKind.BRANCH
        for branch in (computed, entry.fallback_branch):
            if branch and self.git_runner.remote_branch_exists(remote_url, branch):
                return (branch, RefKind.BRANCH)
        active = self.git_runner.remote_head_branch(remote_url)
        if active:
            return (active, RefKind.BRANCH)
        expected = [branch for branch in (computed, entry.fallback_branch) if branch]
        raise GitSyncError(
            f"No cloneable branch found for {entry.name}: expected one of {expected} on "
            f"{remote_url}, and its remote names no active branch to fall back to."
        )

    def _tree_branch_for_clone(self) -> str:
        """The branch the tree is on while it is still being cloned.

        The root's, which a nested install has already attached from its own
        checkout and a standalone install has just cloned (the root is always
        the shallowest pending entry). Before either, the root's declared
        target, then the ordinary default.
        """
        root = self.get_dependency_registry().repos.get(ROOT_REPO_ID)
        if root is None:
            return DEFAULT_BRANCH
        return (
            root.resolved_ref_name or root.current_ref_name or root.target_ref_name or DEFAULT_BRANCH
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


__all__ = ["ComplexGitSyncClient"]

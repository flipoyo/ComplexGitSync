"""Tier 2 synchronization operations for ComplexGitSync.

Each function operates on a :class:`~ComplexGitSync.git_tree.DependencyTreeRegistry`
and a :class:`~ComplexGitSync.orchestre.GitRunner`.  Mutation operations require a
``READY`` registry and raise :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.

Free functions exported here (Tier 2 — Actions):
    propagate_global_branch   Set a shared branch target across every registry entry
    create_global_branch      Create the branch locally if it does not exist yet
    restart_tree              Resync the tree using the root repo's current branch
    checkout_tree             propagate → create → git checkout, parent-first
    branch_tree               propagate → create branch refs, no checkout
    add_tree                  Stage changes across the tree, leaf-first
    commit_tree               Stage and commit changes across the tree, leaf-first
    push_tree                 Push all repos to their remotes, leaf-first
    tag_tree                  Create and push a shared tag, leaf-first
    freeze_release_tree       Commit, tag, and push a shared release tag, leaf-first
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pathlib import Path

from .errors import GitSyncError, TreeNotReadyError
from .git_repo import RefKind, RepoLifecycleState, RepoRegistryEntry, SyncState
from .git_tree import DependencyTreeRegistry, GitTree, iter_tree, iter_tree_leaf_first

if TYPE_CHECKING:
    from .orchestre import GitRunner


class PreflightSeverity(StrEnum):
    """Severity level emitted by the workspace preflight validation engine."""

    WARNING = "warning"
    BLOCKING_ERROR = "blocking error"


@dataclass(slots=True)
class PreflightDiagnostic:
    """Single workspace preflight diagnostic for one repository."""

    severity: PreflightSeverity
    repo_name: str
    message: str


# ---------------------------------------------------------------------------
# propagate_global_branch — Tier 2 helper
# ---------------------------------------------------------------------------


def propagate_global_branch(
    registry: DependencyTreeRegistry,
    branch_name: str,
    *,
    ref_kind: RefKind = RefKind.BRANCH,
) -> None:
    """Set *branch_name* as the target ref on every entry in *registry*.

    This is a pure in-memory operation: no git commands are issued.  It
    prepares the registry so that subsequent operations (create, checkout)
    all target the same branch.
    """
    for entry in registry.values():
        entry.target_ref_name = branch_name
        entry.target_ref_kind = ref_kind


# ---------------------------------------------------------------------------
# create_global_branch — Tier 2 helper
# ---------------------------------------------------------------------------


def create_global_branch(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
    branch_name: str,
) -> None:
    """Create *branch_name* in every repo where it does not already exist locally.

    Iterates the tree parent-first so that parent repositories always have the
    branch before their children are processed.  Requires each entry to have
    a valid ``absolute_path`` on disk.
    """
    for entry in iter_tree(registry):
        if not git_runner.local_branch_exists(entry.absolute_path, branch_name):
            git_runner.create_branch(entry.absolute_path, branch_name)


# ---------------------------------------------------------------------------
# restart_tree — Tier 2 action
# ---------------------------------------------------------------------------


def restart_tree(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
) -> None:
    """Resynchronize the full tree using the root repository's current branch.

    Reads the current branch from the root repository, propagates it across
    all entries, then synchronizes parent-first:

    * root repository: ``git pull --ff-only``
    * child repositories: submodule sync/update from their parent repository

    Does not require a ``READY`` registry; intended for use after loading a
    ``.cgs`` file (``DECLARED`` state).  Produces a ``READY`` registry or
    raises if any repository checkout fails.
    """
    root_entry = registry.get("root")
    current_branch = git_runner.current_branch(root_entry.absolute_path)
    if current_branch is None:
        current_branch = (
            root_entry.resolved_ref_name
            or root_entry.target_ref_name
            or "main"
        )

    propagate_global_branch(registry, current_branch)

    for entry in iter_tree(registry):
        if entry.parent_id is None:
            git_runner.pull(entry.absolute_path, ref_name=current_branch)
        else:
            parent = registry.get(entry.parent_id)
            try:
                relative_path = entry.absolute_path.relative_to(parent.absolute_path)
            except ValueError as exc:
                raise GitSyncError(
                    f"pull preflight failed: {entry.name} is outside parent path {parent.absolute_path}."
                ) from exc
            if relative_path == Path("."):
                raise GitSyncError(
                    "pull preflight failed: child repository cannot share the exact parent path "
                    f"({parent.name}->{entry.name})."
                )
            if not git_runner.is_submodule(parent.absolute_path, relative_path):
                raise GitSyncError(
                    "pull preflight failed: child repositories must be linked as submodules "
                    f"({parent.name}->{entry.name}:{relative_path.as_posix()})."
                )
            git_runner.update_submodule(parent.absolute_path, relative_path)

        # Branch refresh uses entry-specific behavior:
        # - children keep the propagated root branch contract because submodule
        #   updates may leave them detached.
        # - root reads its actual current branch (with fallback).
        resolved_branch = (
            current_branch
            if entry.parent_id is not None
            else (git_runner.current_branch(entry.absolute_path) or current_branch)
        )
        _refresh_entry_after_checkout(entry, resolved_branch, RefKind.BRANCH, git_runner)

    registry.recompute_tree_state()


# ---------------------------------------------------------------------------
# checkout_tree — Tier 2 action
# ---------------------------------------------------------------------------


def checkout_tree(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
    branch_name: str,
    *,
    ref_kind: RefKind = RefKind.BRANCH,
) -> None:
    """Check out *branch_name* across the whole tree.

    Requires a ``READY`` registry; raises :exc:`~.errors.TreeNotReadyError`
    otherwise.  After a successful execution the registry remains ``READY``.

    Steps performed in order:

    1. :func:`propagate_global_branch` — set the target ref on every entry.
    2. :func:`create_global_branch`    — create the branch locally where missing.
    3. ``git checkout`` on every repo, parent-first; registry entries are
       updated to reflect the new current ref, resolved ref, commit SHA, and
       lifecycle / sync states.
    """
    _assert_ready(registry)

    # Step 1: propagate target ref across the whole tree
    propagate_global_branch(registry, branch_name, ref_kind=ref_kind)

    # Step 2: create the branch in each repo where it does not exist yet
    create_global_branch(registry, git_runner, branch_name)

    # Step 3: checkout and refresh each entry (parent-first)
    for entry in iter_tree(registry):
        git_runner.checkout(entry.absolute_path, branch_name)
        _refresh_entry_after_checkout(entry, branch_name, ref_kind, git_runner)

    registry.recompute_tree_state()


# ---------------------------------------------------------------------------
# branch_tree — Tier 2 action
# ---------------------------------------------------------------------------


def branch_tree(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
    branch_name: str,
) -> None:
    """Create *branch_name* across the whole tree without checkout.

    Requires a ``READY`` registry; raises :exc:`~.errors.TreeNotReadyError`
    otherwise. After a successful execution the registry remains ``READY``.
    """
    _assert_ready(registry)
    propagate_global_branch(registry, branch_name, ref_kind=RefKind.BRANCH)
    create_global_branch(registry, git_runner, branch_name)
    registry.recompute_tree_state()


# ---------------------------------------------------------------------------
# add_tree — Tier 2 action
# ---------------------------------------------------------------------------


def add_tree(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
) -> None:
    """Stage all changes across the tree, leaf-first.

    Requires a ``READY`` registry; raises :exc:`~.errors.TreeNotReadyError`
    otherwise.  After a successful execution the registry remains ``READY``.
    """
    _assert_ready(registry)

    for entry in iter_tree_leaf_first(registry):
        git_runner.stage_all(entry.absolute_path)

    registry.recompute_tree_state()


# ---------------------------------------------------------------------------
# commit_tree — Tier 2 action
# ---------------------------------------------------------------------------


def commit_tree(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
    message: str,
    *,
    stage_all: bool = True,
) -> None:
    """Commit changes across the tree, leaf-first.

    Requires a ``READY`` registry; raises :exc:`~.errors.TreeNotReadyError`
    otherwise.  After a successful execution the registry remains ``READY``.

    Each repo is processed from deepest leaf to root:

    * When *stage_all* is ``True`` (the default), ``git add --all`` is run
      before committing.
    * Repos with no staged changes after (optional) staging are silently skipped.
    * The ``commit_sha`` of each entry is refreshed after committing.
    """
    _assert_ready(registry)
    _run_preflight_checks(
        registry,
        git_runner,
        require_clean=False,
        operation_name="commit",
    )

    for entry in iter_tree_leaf_first(registry):
        if stage_all:
            git_runner.stage_all(entry.absolute_path)
        if not git_runner.has_staged_changes(entry.absolute_path):
            continue
        git_runner.commit(entry.absolute_path, message)
        entry.commit_sha = git_runner.rev_parse_head(entry.absolute_path)

    registry.recompute_tree_state()


# ---------------------------------------------------------------------------
# push_tree — Tier 2 action
# ---------------------------------------------------------------------------


def push_tree(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
) -> None:
    """Push all repos to their remotes, leaf-first.

    Requires a ``READY`` registry; raises :exc:`~.errors.TreeNotReadyError`
    otherwise.  After a successful execution the registry remains ``READY``.

    The remote and branch used for each push are taken from
    ``entry.remote_name`` (defaulting to ``"origin"``) and
    ``entry.resolved_ref_name``.
    """
    _assert_ready(registry)
    _run_preflight_checks(
        registry,
        git_runner,
        require_clean=False,
        operation_name="push",
    )

    for entry in iter_tree_leaf_first(registry):
        remote = entry.remote_name or "origin"
        git_runner.push(
            entry.absolute_path,
            remote=remote,
            ref_name=entry.resolved_ref_name,
        )

    registry.recompute_tree_state()


def tag_tree(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
    tag_name: str,
) -> None:
    """Create and push *tag_name* across the tree, leaf-first."""
    _assert_ready(registry)
    _run_preflight_checks(
        registry,
        git_runner,
        tag_name=tag_name,
        require_clean=True,
        operation_name="tag",
    )
    GitTree().propagate_tag(registry, tag_name)

    for entry in iter_tree_leaf_first(registry):
        git_runner.create_tag(entry.absolute_path, tag_name)
        remote = entry.remote_name or "origin"
        git_runner.push(entry.absolute_path, remote=remote, ref_name=tag_name)
        entry.current_ref_kind = RefKind.TAG
        entry.current_ref_name = tag_name
        entry.resolved_ref_kind = RefKind.TAG
        entry.resolved_ref_name = tag_name
        entry.commit_sha = git_runner.rev_parse_head(entry.absolute_path)
        entry.repo_lifecycle_state = RepoLifecycleState.READY
        entry.sync_state = SyncState.ALIGNED
        entry.fallback_applied = False
        entry.fallback_reason = None

    registry.recompute_tree_state()


def freeze_release_tree(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
    tag_name: str,
    *,
    message: str | None = None,
    stage_all: bool = True,
) -> None:
    """Freeze a release by committing, tagging, and pushing leaf-first."""
    _assert_ready(registry)
    _run_preflight_checks(
        registry,
        git_runner,
        tag_name=tag_name,
        require_clean=False,
        operation_name="freeze_release",
    )
    GitTree().propagate_tag(registry, tag_name)
    commit_message = message or f"freeze release {tag_name}"

    for entry in iter_tree_leaf_first(registry):
        if stage_all:
            git_runner.stage_all(entry.absolute_path)
        if git_runner.has_staged_changes(entry.absolute_path):
            git_runner.commit(entry.absolute_path, commit_message)
        git_runner.create_tag(entry.absolute_path, tag_name)
        remote = entry.remote_name or "origin"
        git_runner.push(entry.absolute_path, remote=remote, ref_name=tag_name)
        entry.current_ref_kind = RefKind.TAG
        entry.current_ref_name = tag_name
        entry.resolved_ref_kind = RefKind.TAG
        entry.resolved_ref_name = tag_name
        entry.commit_sha = git_runner.rev_parse_head(entry.absolute_path)
        entry.repo_lifecycle_state = RepoLifecycleState.READY
        entry.sync_state = SyncState.ALIGNED
        entry.fallback_applied = False
        entry.fallback_reason = None

    registry.recompute_tree_state()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _assert_ready(registry: DependencyTreeRegistry) -> None:
    """Raise :exc:`~.errors.TreeNotReadyError` when *registry* is not READY."""
    if not registry.is_ready():
        raise TreeNotReadyError(
            f"Operation requires a READY tree; current state: {registry.lifecycle_state.value}"
        )


def _run_preflight_checks(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
    *,
    tag_name: str | None = None,
    require_clean: bool,
    operation_name: str,
) -> None:
    diagnostics = _collect_preflight_diagnostics(
        registry,
        git_runner,
        operation_name=operation_name,
        tag_name=tag_name,
        require_clean=require_clean,
    )
    warnings_only = [item for item in diagnostics if item.severity == PreflightSeverity.WARNING]
    blocking = [
        item for item in diagnostics if item.severity == PreflightSeverity.BLOCKING_ERROR
    ]
    if warnings_only:
        warnings.warn(_format_preflight_warning(operation_name, warnings_only), stacklevel=2)
    if blocking:
        raise GitSyncError(_format_preflight_error(operation_name, blocking, warnings_only))


def _collect_preflight_diagnostics(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
    *,
    operation_name: str,
    tag_name: str | None,
    require_clean: bool,
) -> list[PreflightDiagnostic]:
    diagnostics: list[PreflightDiagnostic] = []
    diagnostics.extend(_collect_remote_diagnostics(registry, git_runner))
    if tag_name is not None:
        diagnostics.extend(_collect_tag_conflict_diagnostics(registry, git_runner, tag_name=tag_name))
    diagnostics.extend(_collect_detached_head_diagnostics(registry, git_runner))
    diagnostics.extend(_collect_merge_diagnostics(registry, git_runner))
    diagnostics.extend(_collect_branch_alignment_diagnostics(registry, git_runner))
    diagnostics.extend(_collect_tracking_diagnostics(registry, git_runner))
    diagnostics.extend(
        _collect_submodule_diagnostics(
            registry,
            git_runner,
            blocking=operation_name != "commit",
        )
    )
    diagnostics.extend(
        _collect_commit_sha_diagnostics(
            registry,
            git_runner,
            blocking=False,
        )
    )
    diagnostics.extend(
        _collect_worktree_diagnostics(registry, git_runner, require_clean=require_clean)
    )
    return diagnostics


def _collect_remote_diagnostics(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
) -> list[PreflightDiagnostic]:
    missing: list[PreflightDiagnostic] = []
    for entry in iter_tree_leaf_first(registry):
        remote = entry.remote_name or "origin"
        if not git_runner.remote_exists(entry.absolute_path, remote):
            missing.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    entry.name,
                    f"missing remote {remote!r}.",
                )
            )
    return missing


def _collect_tag_conflict_diagnostics(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
    *,
    tag_name: str,
) -> list[PreflightDiagnostic]:
    duplicates: list[PreflightDiagnostic] = []
    for entry in iter_tree_leaf_first(registry):
        if git_runner.tag_exists(entry.absolute_path, tag_name):
            duplicates.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    entry.name,
                    f"tag {tag_name!r} already exists.",
                )
            )
    return duplicates


def _collect_detached_head_diagnostics(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
) -> list[PreflightDiagnostic]:
    detached: list[PreflightDiagnostic] = []
    for entry in iter_tree_leaf_first(registry):
        if git_runner.current_branch(entry.absolute_path) is None:
            detached.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    entry.name,
                    "repository is in detached HEAD state.",
                )
            )
    return detached


def _collect_merge_diagnostics(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
) -> list[PreflightDiagnostic]:
    merges: list[PreflightDiagnostic] = []
    for entry in iter_tree_leaf_first(registry):
        if git_runner.has_unresolved_merge(entry.absolute_path):
            merges.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    entry.name,
                    "repository has an unresolved merge in progress.",
                )
            )
    return merges


def _collect_branch_alignment_diagnostics(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
) -> list[PreflightDiagnostic]:
    if "root" not in registry.entries:
        return [
            PreflightDiagnostic(
                PreflightSeverity.BLOCKING_ERROR,
                "registry",
                "registry has no root repository entry.",
            )
        ]
    root = registry.get("root")
    expected_branch = git_runner.current_branch(root.absolute_path)
    if expected_branch is None:
        return []
    mismatched: list[PreflightDiagnostic] = []
    for entry in iter_tree_leaf_first(registry):
        current = git_runner.current_branch(entry.absolute_path)
        if current is not None and current != expected_branch:
            mismatched.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    entry.name,
                    f"branch misalignment: expected {expected_branch!r}, found {current!r}.",
                )
            )
    return mismatched


def _collect_tracking_diagnostics(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
) -> list[PreflightDiagnostic]:
    diagnostics: list[PreflightDiagnostic] = []
    for entry in iter_tree_leaf_first(registry):
        tracking_state = git_runner.branch_tracking_state(entry.absolute_path)
        if tracking_state in (None, SyncState.ALIGNED):
            continue
        if tracking_state == SyncState.AHEAD:
            diagnostics.append(
                PreflightDiagnostic(
                    PreflightSeverity.WARNING,
                    entry.name,
                    "local branch is ahead of its upstream.",
                )
            )
        elif tracking_state == SyncState.BEHIND:
            diagnostics.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    entry.name,
                    "local branch is behind its upstream.",
                )
            )
        elif tracking_state == SyncState.DIVERGED:
            diagnostics.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    entry.name,
                    "local branch diverged from its upstream.",
                )
            )
        else:
            diagnostics.append(
                PreflightDiagnostic(
                    PreflightSeverity.WARNING,
                    entry.name,
                    f"repository reports tracking state {tracking_state.value!r}.",
                )
            )
    return diagnostics


def _collect_submodule_diagnostics(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
    *,
    blocking: bool,
) -> list[PreflightDiagnostic]:
    missing: list[PreflightDiagnostic] = []
    severity = PreflightSeverity.BLOCKING_ERROR if blocking else PreflightSeverity.WARNING
    for entry in iter_tree_leaf_first(registry):
        if entry.parent_id is None:
            continue
        if entry.parent_id not in registry.entries:
            missing.append(
                PreflightDiagnostic(
                    severity,
                    entry.name,
                    f"parent repository {entry.parent_id!r} is missing from the registry.",
                )
            )
            continue
        parent = registry.get(entry.parent_id)
        try:
            relative_path = entry.absolute_path.relative_to(parent.absolute_path)
        except ValueError:
            missing.append(
                PreflightDiagnostic(
                    severity,
                    entry.name,
                    f"repository path {entry.absolute_path} is outside parent path {parent.absolute_path}.",
                )
            )
            continue
        if relative_path == Path("."):
            continue
        if not entry.absolute_path.exists():
            missing.append(
                PreflightDiagnostic(
                    severity,
                    entry.name,
                    f"submodule path {relative_path.as_posix()!r} is missing on disk.",
                )
            )
            continue
        if not git_runner.is_submodule(parent.absolute_path, relative_path):
            missing.append(
                PreflightDiagnostic(
                    severity,
                    entry.name,
                    (
                        f"repository is not linked as submodule {relative_path.as_posix()!r} "
                        f"from parent {parent.name!r}."
                    ),
                )
            )
    return missing


def _collect_commit_sha_diagnostics(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
    *,
    blocking: bool,
) -> list[PreflightDiagnostic]:
    inconsistent: list[PreflightDiagnostic] = []
    severity = PreflightSeverity.BLOCKING_ERROR if blocking else PreflightSeverity.WARNING
    for entry in iter_tree_leaf_first(registry):
        if not entry.commit_sha:
            continue
        head_sha = git_runner.rev_parse_head(entry.absolute_path)
        if head_sha != entry.commit_sha:
            inconsistent.append(
                PreflightDiagnostic(
                    severity,
                    entry.name,
                    f"recorded commit_sha {entry.commit_sha!r} does not match HEAD {head_sha!r}.",
                )
            )
    return inconsistent


def _collect_worktree_diagnostics(
    registry: DependencyTreeRegistry,
    git_runner: GitRunner,
    *,
    require_clean: bool,
) -> list[PreflightDiagnostic]:
    dirty: list[PreflightDiagnostic] = []
    severity = (
        PreflightSeverity.BLOCKING_ERROR if require_clean else PreflightSeverity.WARNING
    )
    for entry in iter_tree_leaf_first(registry):
        is_dirty = git_runner.has_uncommitted_changes(entry.absolute_path)
        entry.worktree_state = "DIRTY" if is_dirty else "CLEAN"
        if is_dirty:
            dirty.append(
                PreflightDiagnostic(
                    severity,
                    entry.name,
                    "worktree has uncommitted changes.",
                )
            )
    return dirty


def _format_preflight_warning(
    operation_name: str,
    diagnostics: list[PreflightDiagnostic],
) -> str:
    details = "; ".join(f"{item.repo_name}: {item.message}" for item in diagnostics)
    return f"{operation_name} preflight warning: {details}"


def _format_preflight_error(
    operation_name: str,
    blocking: list[PreflightDiagnostic],
    warnings_only: list[PreflightDiagnostic],
) -> str:
    blocking_details = "; ".join(f"{item.repo_name}: {item.message}" for item in blocking)
    if not warnings_only:
        return f"{operation_name} preflight failed: {blocking_details}"
    warning_details = "; ".join(f"{item.repo_name}: {item.message}" for item in warnings_only)
    return (
        f"{operation_name} preflight failed: {blocking_details} "
        f"(warnings: {warning_details})"
    )


def _refresh_entry_after_checkout(
    entry: RepoRegistryEntry,
    branch_name: str,
    ref_kind: RefKind,
    git_runner: GitRunner,
) -> None:
    """Update *entry* in-place to reflect a completed ``git checkout``."""
    entry.current_ref_kind = ref_kind
    entry.current_ref_name = branch_name
    entry.resolved_ref_kind = ref_kind
    entry.resolved_ref_name = branch_name
    entry.commit_sha = git_runner.rev_parse_head(entry.absolute_path)
    entry.fallback_applied = False
    entry.fallback_reason = None
    entry.repo_lifecycle_state = RepoLifecycleState.READY
    entry.sync_state = SyncState.ALIGNED

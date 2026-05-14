"""Tier 2 synchronization operations for ComplexGitSync.

Each function operates on a :class:`~ComplexGitSync.git_tree.DependencyTreeRegistry`
and a :class:`~ComplexGitSync.orchestre.GitRunner`.  Mutation operations require a
``READY`` registry and raise :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.

Free functions exported here (Tier 2 — Actions):
    propagate_global_branch   Set a shared branch target across every registry entry
    create_global_branch      Create the branch locally if it does not exist yet
    checkout_tree             propagate → create → git checkout, parent-first
    commit_tree               Stage and commit changes across the tree, leaf-first
    push_tree                 Push all repos to their remotes, leaf-first
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

from .errors import TreeNotReadyError
from .git_repo import RefKind, RepoLifecycleState, RepoRegistryEntry, SyncState
from .git_tree import DependencyTreeRegistry, _iter_tree

if TYPE_CHECKING:
    from .orchestre import GitRunner


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
    for entry in _iter_tree(registry):
        if not git_runner.local_branch_exists(entry.absolute_path, branch_name):
            git_runner.create_branch(entry.absolute_path, branch_name)


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
    for entry in _iter_tree(registry):
        git_runner.checkout(entry.absolute_path, branch_name)
        entry.current_ref_kind = ref_kind
        entry.current_ref_name = branch_name
        entry.resolved_ref_kind = ref_kind
        entry.resolved_ref_name = branch_name
        entry.commit_sha = git_runner.rev_parse_head(entry.absolute_path)
        entry.fallback_applied = False
        entry.fallback_reason = None
        entry.repo_lifecycle_state = RepoLifecycleState.READY
        entry.sync_state = SyncState.ALIGNED

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

    for entry in _iter_leaf_first(registry):
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

    for entry in _iter_leaf_first(registry):
        remote = entry.remote_name or "origin"
        git_runner.push(
            entry.absolute_path,
            remote=remote,
            branch=entry.resolved_ref_name,
        )

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


def _iter_leaf_first(registry: DependencyTreeRegistry) -> Iterator[RepoRegistryEntry]:
    """Iterate registry entries leaf-first (deepest leaves → root)."""
    return reversed(list(_iter_tree(registry)))

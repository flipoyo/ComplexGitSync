"""operations — Tier 2 synchronization operations for ComplexGitSync.

Ring: 2 (no direct subprocess import; drives Git only through an injected
    GitRunner-shaped object, same ring as git_runner.py per
    .localSpec/AdditionalSpecs.md's ring table)
Contract: leaf/parent-first Git operations over a WorkingGitTree + GitRunner;
    requires a READY tree for mutations, raises TreeNotReadyError otherwise.
Imports: errors, git_branch, git_repo, git_tree

Each function operates on a :class:`~ComplexGitSync.git_tree.WorkingGitTree`
and a :class:`~ComplexGitSync.orchestre.GitRunner`.  Mutation operations require a
``READY`` tree and raise :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.

Free functions exported here (Tier 2 — Actions):
    propagate_global_branch   Set a shared branch target across every tree repo
    create_global_branch      Create the branch locally if it does not exist yet
    restart_tree              Resync the tree using the root repo's current branch
    restart_tree_force        Destructively resync the tree, discarding local changes
    checkout_tree             propagate → create → git checkout, parent-first
    branch_tree               propagate → create branch refs, no checkout
    add_tree                  Stage changes across the tree, leaf-first
    remove_paths              Remove one or more tracked files, each from its owning repo
    commit_tree               Stage and commit changes across the tree, leaf-first
    push_tree                 Push all repos to their remotes, leaf-first
    tag_tree                  Create and push a shared tag, leaf-first
    freeze_release_tree       Commit, tag, and push a shared release tag, leaf-first
    validate_branch_topology  Inspect branch topology and return a topology report

Data classes exported here (Tier 2 — Actions):
    RepoOutcome               What one tree-wide write did to one repository
    BranchTopologyConflict    A single branch alignment conflict in the workspace
    BranchTopologyReport      Full workspace branch topology inspection report
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .errors import GitSyncError, TreeNotReadyError
from .git_branch import DEFAULT_BRANCH, resolve_entry_ref, resolve_propagated_ref
from .git_repo import (
    AccessProtocol,
    RefKind,
    RepoLifecycleState,
    RepoScope,
    SyncState,
    WorkingRepo,
    convert_remote_url_protocol,
)
from .git_tree import (
    ROOT_REPO_ID,
    WorkingGitTree,
    cgitsync_managed_state_paths,
    iter_tree,
    iter_tree_leaf_first,
    resolve_repo_for_path,
)
from .git_tree_branch import GitTreeBranches, tree_project_name

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
    tree: WorkingGitTree,
    branch_name: str,
    *,
    ref_kind: RefKind = RefKind.BRANCH,
    git_runner: GitRunner | None = None,
) -> None:
    """Set *branch_name* as the target ref on every repo in *tree*.

    This is a pure in-memory operation: no git commands are issued.  It
    prepares the tree so that subsequent operations (create, checkout)
    all target the same branch — except a repo declared ``private`` in the
    ``.cgs``, which keeps its own ``default_branch`` because it is shared
    with other projects. Privacy governs *branch* propagation only, so a
    tag still reaches every repo and a frozen release stays reproducible.

    The privacy rule itself lives in
    :func:`~ComplexGitSync.git_branch.resolve_propagated_ref`, so that the
    reason each repo ended up on the branch it did is decided in one place
    and recorded on the entry rather than re-derived by each reader.

    A **private/local** repo does not target *branch_name* itself but the
    branch named after the project for it — ``<project>`` on ``main``,
    ``<project>_<branch_name>`` elsewhere. The rule is deterministic, so no
    repository is left to guess: whatever it names, ``create_global_branch``
    makes and ``checkout`` moves to.

    *git_runner* is accepted for call-site symmetry and is unused; nothing
    here needs to look at a repository on disk.
    """
    branches = GitTreeBranches(tree)
    for repo in tree.values():
        branches.target(repo, branch_name, ref_kind=ref_kind).apply_to(repo)


# ---------------------------------------------------------------------------
# create_global_branch — Tier 2 helper
# ---------------------------------------------------------------------------


def create_global_branch(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    branch_name: str,
    *,
    scope: RepoScope = RepoScope.ALL,
) -> None:
    """Create *branch_name* in every repo where it does not already exist locally.

    Iterates the tree parent-first so that parent repositories always have the
    branch before their children are processed.  Requires each repo to have
    a valid ``absolute_path`` on disk.

    A private/**distant** repository is never given a branch: this project
    cannot write to it at all.

    A private/**local** repository is given the branch the project's own
    rule names for it — ``<project>`` on ``main``,
    ``<project>_<branch_name>`` elsewhere. Both ``branch`` and ``checkout``
    do this, because a user asking for a branch should not have to know that
    their configuration repository spells it differently; the point of the
    rule is that they never have to think about it.

    A branch this clone already has a remote-tracking ref for is created
    *from* that ref, with tracking set. Offline either way: it reads refs
    already on disk and never contacts the remote, so what a fresh clone can
    join is what the last fetch brought.
    """
    branches = GitTreeBranches(tree)
    for repo in iter_tree(tree, scope):
        if repo.effective_private and not repo.effective_writable:
            continue
        target = branches.target(repo, branch_name).name
        if git_runner.local_branch_exists(repo.absolute_path, target):
            continue
        # A branch this clone already knows from the remote is that branch,
        # not a new one that happens to share its name. Starting it at HEAD
        # instead forked a second history under a name the user believed they
        # were joining, and `checkout` then reported success on the wrong
        # commits — a colleague's work simply was not there
        # (.localSpec/DevTickets/archive/20260911_UpstreamBranchDisplay_DevPlanTicket.md §3).
        remote = repo.remote_name or "origin"
        if git_runner.remote_tracking_branch_exists(repo.absolute_path, target, remote=remote):
            git_runner.create_branch(
                repo.absolute_path, target, start_point=f"{remote}/{target}"
            )
            continue
        git_runner.create_branch(repo.absolute_path, target)


# ---------------------------------------------------------------------------
# restart_tree — Tier 2 action
# ---------------------------------------------------------------------------


def _rewrite_remote_if_forced(
    git_runner: GitRunner,
    repo: WorkingRepo,
    remote: str,
    force_access_protocol: AccessProtocol | None,
) -> None:
    """Persist a ``--force-protocol`` override onto *repo*'s remote, once.

    ``git remote set-url`` (via :meth:`GitRunner.configure_remote`, a
    no-op when the URL already matches) — not a per-invocation override —
    so the switch sticks for every command after this one too, the same
    way a repo's protocol at clone time sticks for everything downstream
    of it. A no-op when *force_access_protocol* is ``None`` (the default,
    unchanged behavior).

    Reads *repo*'s current remote URL and only swaps its scheme
    (:func:`~ComplexGitSync.git_repo.convert_remote_url_protocol`), rather
    than rebuilding a URL from *repo*'s stored identity fields. Those
    fields can be missing or stale for a repo loaded from an older
    ``.gts`` snapshot (gitprovider was not always recorded there — see
    ``.localSpec/DevTickets/archive/20260904_GtsProviderLoss_DevPlanTicket.md``), and
    rebuilding from a wrong or absent provider silently aims the push at
    the wrong host. The URL actually configured on disk is never wrong in
    that way, so converting it in place is what stays correct regardless
    of the snapshot's age.
    """
    if force_access_protocol is None:
        return
    current_url = git_runner.remote_get_url(repo.absolute_path, remote)
    if current_url is None:
        raise GitSyncError(
            f"--force-protocol: {repo.name} has no '{remote}' remote configured to "
            f"convert the protocol of."
        )
    try:
        forced_url = convert_remote_url_protocol(current_url, force_access_protocol)
    except ValueError as exc:
        raise GitSyncError(f"--force-protocol: {repo.name}'s '{remote}' remote: {exc}") from exc
    git_runner.configure_remote(repo.absolute_path, remote, forced_url)


def _repair_fetch_refspec(git_runner: GitRunner, repo: WorkingRepo, remote: str) -> None:
    """Widen *repo*'s fetch refspec if a ``--single-branch`` clone narrowed it.

    Every workspace cloned before that narrowing was fixed
    (``.localSpec/DevTickets/archive/20260911_UpstreamBranchDisplay_DevPlanTicket.md``)
    carries a refspec mapping one branch only, and no re-clone should be
    needed to recover from it. Modelled on :func:`_rewrite_remote_if_forced`,
    beside which it is called: a config fix persisted once, idempotently, by
    the commands that are already writing to the repository.

    Deliberately *not* called from ``status``. A read-only command that
    silently rewrites ``.git/config`` is a worse surprise than a column that
    says ``unknown`` for one more invocation, and ``push`` — the command the
    missing refspec actually breaks — repairs it before it matters.

    Failure is not fatal: a repository with no such remote, or one whose
    config is not writable, still has a pull and a push to attempt.
    """
    try:
        git_runner.ensure_fetch_refspec(repo.absolute_path, remote=remote)
    except GitSyncError:
        return


def _fetch_all_refs(git_runner: GitRunner, repo: WorkingRepo, remote: str) -> None:
    """Bring every branch of *remote* into ``refs/remotes/`` before pulling.

    ``git pull --ff-only origin <branch>`` fetches that one branch, so a
    workspace could pull for months and still hold no ref for any branch but
    its own. ``checkout`` reads those refs and never contacts the network —
    deliberately, so it keeps working offline — which left it unable to join
    a branch a colleague had pushed, no matter how often the user pulled.
    Pull is the command that means "bring this workspace up to date with the
    remote", and a branch that exists is part of what is up to date.

    Costs one extra round trip per repository on a command that is already
    talking to the same remote. Best-effort: the refs are a convenience and
    the pull is the command, so a fetch that fails must not take the pull
    down with it — if the remote is genuinely unreachable, the pull says so
    a moment later, in its own words.
    """
    try:
        git_runner.fetch(repo.absolute_path, remote=remote)
    except GitSyncError:
        return


def _restart_tree(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    force: bool,
    force_access_protocol: AccessProtocol | None,
    scope: RepoScope = RepoScope.ALL,
) -> None:
    """Shared body of :func:`restart_tree` and :func:`restart_tree_force`.

    *force* selects the destructive pull. Everything else — reading the
    root's branch, propagating it, the parent/child path preflight, the
    per-repo remote rewrite, and the refresh — is identical either way.
    """
    label = "pull-force" if force else "pull"
    root_entry = tree.get(ROOT_REPO_ID)
    observed = GitTreeBranches(tree, git_runner).observed(root_entry)
    current_branch = resolve_entry_ref(root_entry, observed_branch=observed).name
    # The runner matters here for the same reason it does in checkout_tree:
    # the loop below pulls whatever this decides, and a private/local repo's
    # derived branch has to be one that exists.
    propagate_global_branch(tree, current_branch, git_runner=git_runner)

    for repo in iter_write_scope(tree, scope, leaf_first=False):
        if repo.parent_id is not None:
            parent = tree.get(repo.parent_id)
            try:
                relative_path = repo.absolute_path.relative_to(parent.absolute_path)
            except ValueError as exc:
                raise GitSyncError(
                    f"{label} preflight failed: {repo.name} is outside parent path "
                    f"{parent.absolute_path}."
                ) from exc
            if relative_path == Path("."):
                raise GitSyncError(
                    f"{label} preflight failed: child repository cannot share the exact "
                    f"parent path ({parent.name}->{repo.name})."
                )
        remote = repo.remote_name or "origin"
        _rewrite_remote_if_forced(git_runner, repo, remote, force_access_protocol)
        _repair_fetch_refspec(git_runner, repo, remote)
        _fetch_all_refs(git_runner, repo, remote)
        pull = git_runner.force_pull if force else git_runner.pull
        pull(repo.absolute_path, remote=remote, ref_name=repo.target_ref_name or current_branch)

        resolved_branch = git_runner.current_branch(repo.absolute_path) or current_branch
        _refresh_repo_after_checkout(repo, resolved_branch, RefKind.BRANCH, git_runner)

    tree.recompute_tree_state()


def restart_tree(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    force_access_protocol: AccessProtocol | None = None,
    scope: RepoScope = RepoScope.ALL,
) -> None:
    """Resynchronize the full tree using the root repository's current branch.

    Reads the current branch from the root repository, propagates it across
    all repos except those declared ``private``, then pulls every repository
    but the workspace's own memory (``iter_write_scope``, parent-first)
    with ``git pull --ff-only`` on the branch that repo actually targets.

    The memory is skipped for the same reason ``add``/``commit``/``push``
    skip it: this call records itself into the memory when it finishes, so
    pulling the memory in the same sweep could never leave it clean, and a
    fast-forward attempted against a memory that structurally always has
    something uncommitted has no reason to behave any better. Its own
    sync-from-a-colleague's-push is `memory push`'s and `memory clone`'s
    job, not this one's.

    Does not require a ``READY`` tree; intended for use after loading a
    ``.cgs`` file (``DECLARED`` state).  Produces a ``READY`` tree or
    raises if any repository checkout fails.

    *force_access_protocol*, when given, rewrites each repo's remote to
    that protocol before pulling (``--force-protocol`` on ``pull``).
    """
    _restart_tree(
        tree, git_runner, force=False, force_access_protocol=force_access_protocol, scope=scope
    )


def restart_tree_force(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    force_access_protocol: AccessProtocol | None = None,
    scope: RepoScope = RepoScope.ALL,
) -> None:
    """Force-resynchronize the full tree using the root repository's branch.

    This is the destructive counterpart of :func:`restart_tree`: local
    uncommitted changes and untracked files can be discarded by the underlying
    git commands. It exists as an explicit recovery command for worktrees that
    block a fast-forward pull. The workspace's own memory is excluded from
    that, same as from the ordinary pull it destructively repeats — a
    discard-and-reclone is exactly the operation the memory must never be
    exposed to from a command that is not one of its own.

    *force_access_protocol*, when given, rewrites each repo's remote to
    that protocol before force-pulling (``--force-protocol`` on
    ``pull-force``).
    """
    _restart_tree(
        tree, git_runner, force=True, force_access_protocol=force_access_protocol, scope=scope
    )


# ---------------------------------------------------------------------------
# checkout_tree — Tier 2 action
# ---------------------------------------------------------------------------


def checkout_tree(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    branch_name: str,
    *,
    ref_kind: RefKind = RefKind.BRANCH,
    scope: RepoScope = RepoScope.ALL,
) -> None:
    """Check out *branch_name* across the whole tree.

    Requires a ``READY`` tree; raises :exc:`~.errors.TreeNotReadyError`
    otherwise.  After a successful execution the tree remains ``READY``.

    Steps performed in order:

    1. :func:`propagate_global_branch` — set the target ref on every repo.
    2. :func:`create_global_branch`    — create the branch locally where missing.
    3. ``git checkout`` on every repo, parent-first; tree repos are
       updated to reflect the new current ref, resolved ref, commit SHA, and
       lifecycle / sync states.
    """
    _assert_ready(tree)

    # Step 1: propagate target ref across the whole tree. The runner is
    # passed because step 3 is about to `git checkout` what this decides:
    # a private/local repo's derived branch has to be one that exists.
    propagate_global_branch(tree, branch_name, ref_kind=ref_kind, git_runner=git_runner)

    # Step 2: create the branch in each repo where it does not exist yet --
    # including the private/local name, so `checkout <B>` alone puts the whole
    # tree where it belongs and the user never has to spell the derived branch.
    create_global_branch(tree, git_runner, branch_name, scope=scope)

    # Step 3: checkout and refresh each repo (parent-first)
    for repo in iter_tree(tree, scope):
        ref = repo.target_ref_name or branch_name
        git_runner.checkout(repo.absolute_path, ref)
        _refresh_repo_after_checkout(repo, ref, repo.target_ref_kind or ref_kind, git_runner)

    tree.recompute_tree_state()


# ---------------------------------------------------------------------------
# branch_tree — Tier 2 action
# ---------------------------------------------------------------------------


def branch_tree(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    branch_name: str,
    *,
    scope: RepoScope = RepoScope.ALL,
) -> None:
    """Create *branch_name* across the whole tree without checkout.

    Requires a ``READY`` tree; raises :exc:`~.errors.TreeNotReadyError`
    otherwise. After a successful execution the tree remains ``READY``.
    """
    _assert_ready(tree)
    propagate_global_branch(tree, branch_name, ref_kind=RefKind.BRANCH)
    create_global_branch(tree, git_runner, branch_name, scope=scope)
    tree.recompute_tree_state()


# ---------------------------------------------------------------------------
# add_tree — Tier 2 action
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoOutcome:
    """What one tree-wide write command did to one repository.

    A sweep that writes nowhere and a sweep that writes everywhere used to
    print the same thing, which is what makes "nothing happened" so hard to
    diagnose: the user cannot tell a command that found no work from one
    that never looked at their repository at all. Every repository the
    command visited gets one of these, in the order it was visited.

    ``acted`` answers "did anything change here?". ``detail`` says what
    changed (a new commit's sha, the ref pushed, how many paths were
    staged) or, when ``acted`` is ``False``, why nothing did.
    """

    name: str
    acted: bool
    detail: str


def iter_write_scope(
    tree: WorkingGitTree, scope: RepoScope, *, leaf_first: bool = True
) -> Iterator[WorkingRepo]:
    """*scope*'s repositories, minus the workspace's own memory.

    For ``add``/``commit``/``push`` (leaf-first, the default) and
    ``pull``/``pull-force`` (``leaf_first=False``, matching
    :func:`_restart_tree`'s own parent-first order) only — see
    :meth:`~ComplexGitSync.git_repo.RepoScope.includes`'s docstring for why
    those, and no other scoped command, need this.

    Every one of them records itself into the memory *after* it runs, so a
    sweep that also committed, pushed, or pulled the memory can never leave
    it clean — the record of that very sweep is always still pending, and
    for ``pull`` specifically a fast-forward attempted against a memory
    that (structurally) always has *something* uncommitted is a fresh way
    for the same problem to surface, not a different one. Excluded here,
    not from `RepoScope` itself, so `merge`, `tag` and `freeze-release` go
    on reconciling the memory across project branches exactly as they
    already reconcile `.localSpec`/`.claude` — and `memory push`/
    `memory adopt` lose nothing either way, since neither ever went
    through scope at all.
    """
    walk = iter_tree_leaf_first if leaf_first else iter_tree
    return (repo for repo in walk(tree, scope) if not repo.is_memory_mount)


def add_tree(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    paths: Sequence[str | Path] | None = None,
    scope: RepoScope = RepoScope.PROJECT,
) -> tuple[RepoOutcome, ...]:
    """Stage changes across the tree, leaf-first.

    Requires a ``READY`` tree; raises :exc:`~.errors.TreeNotReadyError`
    otherwise.  After a successful execution the tree remains ``READY``.

    With *paths* omitted (the default), every repo is staged in full
    (``git add --all``) — today's exact behaviour. With *paths* given, each
    one is resolved via :func:`~.git_tree.resolve_repo_for_path` to its
    owning repo and staged there individually (``git add -- <path>``),
    leaving every other repo untouched; a path outside every repo in the
    tree raises :exc:`~.errors.GitSyncError` immediately, before anything
    is staged.

    Returns one :class:`RepoOutcome` per repository staged or visited, so a
    caller can report which repositories had nothing to stage instead of
    leaving the user to guess.
    """
    _assert_ready(tree)

    outcomes: list[RepoOutcome] = []
    if paths is None:
        for repo in iter_write_scope(tree, scope):
            pending = len(git_runner.status_porcelain(repo.absolute_path))
            git_runner.stage_all(repo.absolute_path)
            outcomes.append(
                RepoOutcome(
                    name=repo.name,
                    acted=pending > 0,
                    detail=(
                        f"staged {pending} change(s)" if pending else "nothing to stage"
                    ),
                )
            )
    else:
        resolved = [resolve_repo_for_path(tree, path) for path in paths]
        staged_by_repo: dict[str, list[str]] = {}
        for repo, relative_path in resolved:
            git_runner.stage_path(repo.absolute_path, relative_path)
            staged_by_repo.setdefault(repo.name, []).append(relative_path)
        outcomes.extend(
            RepoOutcome(name=name, acted=True, detail=f"staged {' '.join(staged)}")
            for name, staged in staged_by_repo.items()
        )

    tree.recompute_tree_state()
    return tuple(outcomes)


def paths_outside_scope(
    tree: WorkingGitTree,
    paths: Sequence[str | Path],
    *,
    scope: RepoScope,
) -> tuple[str, ...]:
    """Which of *paths* belong to a repository *scope* does not cover.

    A read-only question, worktree-free and Git-free, so a dry run can ask
    it about every path before anything is removed — the same reason
    ``clone_guard`` and ``merge_status`` are questions rather than actions.
    Returns one finished refusal sentence per offending path, in the order
    given; an empty tuple means every path is in scope.

    A path outside every repository in the tree is not this function's
    business: :func:`~.git_tree.resolve_repo_for_path` reports that one in
    full, and reporting it twice in two voices helps nobody.
    """
    refusals: list[str] = []
    for path in paths:
        try:
            repo, relative_path = resolve_repo_for_path(tree, path)
        except GitSyncError:
            continue
        if not scope.includes(repo):
            refusals.append(
                f"{repo.absolute_path / relative_path} is inside '{repo.name}', "
                f"which is outside this command's scope ({scope.value}). "
                f"A configuration repository is reached with --private, and a "
                f"repository this project owns by leaving --private off."
            )
    return tuple(refusals)


def remove_paths(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    paths: Sequence[str | Path],
    *,
    scope: RepoScope = RepoScope.ALL,
) -> tuple[RepoOutcome, ...]:
    """Remove one or more tracked files, each from the repo that owns it.

    Requires a ``READY`` tree; raises :exc:`~.errors.TreeNotReadyError`
    otherwise. Each path is resolved via
    :func:`~.git_tree.resolve_repo_for_path`; a path outside every repo in
    the tree raises :exc:`~.errors.GitSyncError` immediately, before
    anything is removed.

    *scope* is the set of repositories this call may remove from, and it is
    checked against the repository each path resolves to — a filter, not a
    sweep, because ``rm`` is given its paths rather than finding them. A
    path owned by a repository outside *scope* raises
    :exc:`~.errors.GitSyncError` naming that repository, and nothing is
    removed anywhere: the check runs over every path before the first
    removal. The default reaches every repository, which is what the bare
    command has always done.

    A plain tracked file only (``git rm -- <path>``, removing it from disk
    and staging the removal) — a path that resolves to a directory, or that
    does not exist, also raises :exc:`~.errors.GitSyncError` rather than
    failing silently or partially. Distinct from and unrelated to
    ``rm_cached`` (index-only, built for the submodule-to-plain-clone
    conversion): this does not replace it.

    Returns one :class:`RepoOutcome` per repository removed from, so the
    caller can say which repositories a removal actually reached instead of
    leaving the user to infer it.
    """
    _assert_ready(tree)

    resolved = [resolve_repo_for_path(tree, path) for path in paths]
    refusals = paths_outside_scope(tree, paths, scope=scope)
    if refusals:
        raise GitSyncError(refusals[0])
    for repo, relative_path in resolved:
        target = repo.absolute_path / relative_path
        if target.is_dir():
            raise GitSyncError(
                f"{target} is a directory; rm only removes a single tracked file today (no -r yet)."
            )
        if not target.exists():
            raise GitSyncError(f"{target} does not exist.")

    removed_by_repo: dict[str, list[str]] = {}
    for repo, relative_path in resolved:
        git_runner.remove(repo.absolute_path, relative_path)
        removed_by_repo.setdefault(repo.name, []).append(relative_path)

    tree.recompute_tree_state()
    return tuple(
        RepoOutcome(name=name, acted=True, detail=f"removed {' '.join(removed)}")
        for name, removed in removed_by_repo.items()
    )


# ---------------------------------------------------------------------------
# commit_tree — Tier 2 action
# ---------------------------------------------------------------------------


def commit_tree(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    message: str,
    *,
    stage_all: bool = True,
    scope: RepoScope = RepoScope.PROJECT,
) -> tuple[RepoOutcome, ...]:
    """Commit changes across the tree, leaf-first.

    Requires a ``READY`` tree; raises :exc:`~.errors.TreeNotReadyError`
    otherwise.  After a successful execution the tree remains ``READY``.

    Each repo is processed from deepest leaf to root:

    * When *stage_all* is ``True`` (the default), ``git add --all`` is run
      before committing.
    * Repos with no staged changes after (optional) staging are skipped —
      and reported as skipped, rather than passed over in silence.
    * The ``commit_sha`` of each repo is refreshed after committing.

    Returns one :class:`RepoOutcome` per repository in scope, in the order
    they were visited.
    """
    _assert_ready(tree)
    _run_preflight_checks(
        tree,
        git_runner,
        require_clean=False,
        operation_name="commit",
        scope=scope,
    )

    outcomes: list[RepoOutcome] = []
    for repo in iter_write_scope(tree, scope):
        if stage_all:
            git_runner.stage_all(repo.absolute_path)
        if not git_runner.has_staged_changes(repo.absolute_path):
            outcomes.append(
                RepoOutcome(
                    name=repo.name,
                    acted=False,
                    detail=(
                        "nothing staged"
                        if stage_all
                        else "nothing staged (--no-stage: stage with 'cgitsync add')"
                    ),
                )
            )
            continue
        git_runner.commit(repo.absolute_path, message)
        repo.commit_sha = git_runner.rev_parse_head(repo.absolute_path)
        outcomes.append(
            RepoOutcome(name=repo.name, acted=True, detail=repo.commit_sha or "committed")
        )

    tree.recompute_tree_state()
    return tuple(outcomes)


# ---------------------------------------------------------------------------
# merge_tree — Tier 2 action
# ---------------------------------------------------------------------------


def merge_source_ref(
    repo: WorkingRepo, project_branch: str, *, project_name: str | None = None
) -> str:
    """The branch *repo* should merge when the project merges *project_branch*.

    The argument a user types is always the **project's** branch. Each
    repository then resolves its own source through the one rule that owns
    branch propagation, so ``merge --private multi-branch`` merges
    ``<project>_multi-branch`` into a private/local repository rather than
    ``multi-branch``, which does not exist there.

    This is the whole reason the private case needs no code of its own: it
    is the same command with a different scope and this one translation.
    """
    return resolve_propagated_ref(
        repo, project_branch, project_name=project_name
    ).name


def merge_status(
    repo: WorkingRepo,
    git_runner: GitRunner,
    project_branch: str,
    *,
    project_name: str | None = None,
) -> tuple[str, str, tuple[Path, ...]]:
    """What ``merge`` would do to *repo*, as ``(source_ref, status, paths)``.

    The one place a repository's fate is decided, so the dry run and the
    merge itself cannot disagree. ``status`` is ``"merge"``,
    ``"already-on-it"`` (nothing to merge into), ``"no-branch"`` (nothing to
    merge from) or ``"conflicts"``. ``paths`` is empty for every status but
    the last, and empty for that one too when git blamed no file.
    """
    source = merge_source_ref(repo, project_branch, project_name=project_name)
    if git_runner.current_branch(repo.absolute_path) == source:
        return source, "already-on-it", ()
    if not git_runner.branch_known(
        repo.absolute_path, source, remote=repo.remote_name or "origin"
    ):
        return source, "no-branch", ()
    check = git_runner.can_merge_cleanly(repo.absolute_path, source)
    if not check.is_clean:
        return source, "conflicts", tuple(check.conflicting_paths)
    return source, "merge", ()


def _warn_branch_missing(repo: WorkingRepo, source: str, project_branch: str) -> None:
    """Say that a repository was skipped because its branch does not exist.

    A merge used to skip these in silence. For most repositories that is
    harmless — there is nothing of that project branch in them. For a
    private/local repository it is the opposite: its branch is *derived*
    from the project's, so a missing one means the half of the change that
    configures the project was quietly left behind. A memory born on a
    feature branch is the first repository where that happens on the very
    first merge, because the branch it merges *into* has never existed.
    """
    remedy = (
        f" For this project's memory, 'cgitsync memory branch --project-branch "
        f"{project_branch}' creates it."
        if repo.effective_private
        else ""
    )
    warnings.warn(
        f"merge skipped {repo.name}: it has no branch {source!r}, here or on its "
        f"remote, so nothing was merged into it.{remedy}",
        stacklevel=3,
    )


def _describe_merge_conflict(
    repo_name: str, source: str, paths: Sequence[Path]
) -> str:
    # Falls back to the branch when git named no file: an unmergeable
    # repository conflicts without blaming one.
    if paths:
        return f"{repo_name}: {', '.join(str(path) for path in paths)}"
    return f"{repo_name}: merging {source!r} conflicts"


def _iter_merge_scope_project_first(
    tree: WorkingGitTree, scope: RepoScope
) -> Iterator[WorkingRepo]:
    """Leaf-first, but every repository *scope* reaches through ``PROJECT``
    before any it reaches through ``PRIVATE``.

    ``--all`` (``RepoScope.WRITABLE``) is the one merge scope that mixes the
    two, and a plain leaf-first walk over the union interleaves them by
    physical mount position, not by which one matters more: a private
    configuration repository (`.memory` included) sits wherever it happens
    to be mounted, so a conflict in one can leave this project's own root
    repository unreached — merged nowhere, private repos ahead of it
    already merged. That is backwards. This project's own history is
    reconciled first, completely, before any shared configuration
    repository is touched at all — so a conflict in the private half can
    never again leave the project half only partly done
    (`.localSpec/DevTickets/archive/20260918_MergeProjectBeforePrivate_DevPlanTicket.md`).

    ``PROJECT`` and ``PRIVATE`` never overlap (a repository is either not
    private, or private *and* writable), so this never yields one twice.
    """
    for repo in iter_tree_leaf_first(tree, RepoScope.PROJECT):
        if scope.includes(repo):
            yield repo
    for repo in iter_tree_leaf_first(tree, RepoScope.PRIVATE):
        if scope.includes(repo):
            yield repo


def merge_tree(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    project_branch: str,
    *,
    scope: RepoScope = RepoScope.PROJECT,
    ff_only: bool = False,
    no_ff: bool = False,
) -> tuple[tuple[str, str], ...]:
    """Merge *project_branch* into each in-scope repository, leaf-first.

    Returns one ``(repo_name, merged_ref)`` pair per repository that a merge
    actually moved; a repository already containing the branch is skipped and
    not reported.

    **Every repository is checked before any repository is merged.** A
    tree-wide merge that stopped halfway would leave the workspace in a state
    no ``.gts`` describes and no command undoes — which is the failure this
    command exists to prevent, not one it may cause. So the whole scope is
    asked first, with :meth:`GitRunner.can_merge_cleanly`, which touches
    neither worktree nor index; only if all of them can does the first merge
    run.

    That is a guarantee about *conflicts*, not a transaction: a merge can
    still fail for a reason no check anticipated, and the error then names
    what had already landed.
    """
    _assert_ready(tree)
    _run_preflight_checks(
        tree,
        git_runner,
        require_clean=True,
        operation_name="merge",
        scope=scope,
    )

    planned: list[tuple[WorkingRepo, str]] = []
    blocked: list[str] = []
    on_source: list[str] = []
    project_name = tree_project_name(tree)
    for repo in _iter_merge_scope_project_first(tree, scope):
        source, status, conflicts = merge_status(
            repo, git_runner, project_branch, project_name=project_name
        )
        if status == "already-on-it":
            # Merging a branch into itself does nothing and reports success,
            # which reads as "it worked" when the tree is simply still on the
            # branch the user meant to merge *from*.
            on_source.append(repo.name)
            continue
        if status == "no-branch":
            _warn_branch_missing(repo, source, project_branch)
            continue
        if status == "conflicts":
            blocked.append(_describe_merge_conflict(repo.name, source, conflicts))
            continue
        planned.append((repo, source))

    if blocked:
        raise GitSyncError(
            "merge refused; no repository was merged: " + "; ".join(blocked)
        )
    if on_source and not planned:
        raise GitSyncError(
            f"merge {project_branch}: the tree is already on {project_branch!r} "
            f"({', '.join(on_source)}), so there is nothing to merge it into. "
            f"Check out the branch you want to merge *into* first — "
            f"'cgitsync checkout <target>' — then run this again."
        )

    merged: list[tuple[str, str]] = []
    for repo, source in planned:
        before = git_runner.rev_parse_head(repo.absolute_path)
        git_runner.merge(repo.absolute_path, source, ff_only=ff_only, no_ff=no_ff)
        after = git_runner.rev_parse_head(repo.absolute_path)
        repo.commit_sha = after
        if before != after:
            merged.append((repo.name, source))

    tree.recompute_tree_state()
    return tuple(merged)


@dataclass(frozen=True)
class MergeIntoPlan:
    """What ``merge --into`` would do, or did, to one repository.

    The same shape answers both questions, as ``merge_status`` does for the
    ordinary merge: ``status`` is a prediction before the run and a verdict
    after it, and a dry run cannot promise something the merge then refuses
    because both come from :func:`merge_into_status`.
    """

    name: str
    source: str
    target: str
    status: str
    conflicting_paths: tuple[Path, ...] = ()


#: What a repository's fate can be. ``fast-forward`` and ``merge`` both act;
#: the rest do not. They are told apart because a fast-forward makes no
#: commit and explains why a repository looks untouched afterwards.
MERGE_INTO_ACTS = ("fast-forward", "merge")


def merge_into_status(
    repo: WorkingRepo,
    git_runner: GitRunner,
    source_branch: str,
    target_branch: str,
    *,
    project_name: str | None = None,
) -> MergeIntoPlan:
    """What merging *source_branch* into *target_branch* would do to *repo*.

    Both names are the **project's** branches, and each is translated for
    this repository by the one rule that owns branch propagation — so a
    private/local repository merges ``<base>_<source>`` into ``<base>``
    while the project's own repositories take both names literally.

    Five answers:

    - ``no-source`` / ``no-target`` — that branch is not here and not on the
      remote. Neither is invented: a branch that is missing is as likely to
      be a typing mistake as a new branch.
    - ``already-merged`` — the target already contains the source. Nothing
      to do, and not a failure.
    - ``fast-forward`` — the target is an ancestor of the source, so it only
      has to move.
    - ``merge`` — a real merge that applies cleanly.
    - ``conflicts`` — with the paths git blamed, empty when it blamed none.
    """
    source = merge_source_ref(repo, source_branch, project_name=project_name)
    target = merge_source_ref(repo, target_branch, project_name=project_name)
    remote = repo.remote_name or "origin"

    def plan(status: str, paths: tuple[Path, ...] = ()) -> MergeIntoPlan:
        return MergeIntoPlan(repo.name, source, target, status, paths)

    if not git_runner.branch_known(repo.absolute_path, source, remote=remote):
        return plan("no-source")
    if not git_runner.branch_known(repo.absolute_path, target, remote=remote):
        return plan("no-target")
    if git_runner.is_ancestor(repo.absolute_path, source, target):
        return plan("already-merged")
    if git_runner.is_ancestor(repo.absolute_path, target, source):
        return plan("fast-forward")
    check = git_runner.can_merge_cleanly(repo.absolute_path, source, into=target)
    if not check.is_clean:
        return plan("conflicts", tuple(check.conflicting_paths))
    return plan("merge")


def merge_into_tree(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    source_branch: str,
    target_branch: str,
    *,
    scope: RepoScope = RepoScope.PROJECT,
    ff_only: bool = False,
    no_ff: bool = False,
) -> tuple[MergeIntoPlan, ...]:
    """Check out *target_branch* and merge *source_branch* into it, tree-wide.

    **One operation, deliberately, and it must stay one.** This project
    manages a tree that contains this project, installed editable, so a
    tree-wide checkout rewrites the code that runs the *next* command.
    Asking a user to run ``checkout`` and then ``merge`` therefore makes the
    branch being merged *into* perform its own merge — and when that branch
    is older, an older build reads a workspace a newer one wrote. That is
    the incident of 2026-09-16.

    A single process is immune to it: Python has already imported its
    modules, so the build that started this call finishes it whatever
    happens to the files underneath. **Nothing may be inserted between the
    checkout and the merge below that starts another process**, and the two
    must never be split into separate commands again. See
    ``.localSpec/DevTickets/…_SelfHostedMerge_DevPlanTicket.md`` §2.

    Every repository in scope is checked before any is touched, so a refusal
    leaves the whole tree exactly where it was — still on the source branch,
    nothing checked out and nothing merged. That is the promise
    :func:`merge_tree` already makes, extended to cover the checkout.

    **The shared preflight's branch-alignment check is left out on
    purpose.** That check exists for commands that only ever act on
    whatever branch a repository is already on — for them, "not on the
    branch the tree expects" means `checkout` was skipped or failed. This
    command's entire job is taking a repository *from* wherever it
    currently sits *to* the branch named by *target_branch*, resolved
    directly through :func:`merge_source_ref` rather than read off what is
    checked out — so "not yet on the target" is this call's input, not a
    sign anything is wrong. Without this, a `merge --into` scoped to part
    of the tree could never be finished by a second scoped call: the second
    call's own preflight would refuse the very repositories it exists to
    move, on the grounds that they have not moved yet. `merge_into_status`
    below is the accurate read of whether a repository can be acted on;
    a check built for a different family of commands is not.
    """
    _assert_ready(tree)
    _run_preflight_checks(
        tree,
        git_runner,
        require_clean=True,
        operation_name="merge",
        scope=scope,
        check_branch_alignment=False,
    )

    project_name = tree_project_name(tree)
    plans = [
        merge_into_status(
            repo, git_runner, source_branch, target_branch, project_name=project_name
        )
        for repo in _iter_merge_scope_project_first(tree, scope)
    ]
    by_name = {plan.name: plan for plan in plans}

    blocked = [
        _describe_merge_conflict(plan.name, plan.source, plan.conflicting_paths)
        for plan in plans
        if plan.status == "conflicts"
    ]
    if blocked:
        raise GitSyncError(
            "merge refused; nothing was checked out and nothing was merged: "
            + "; ".join(blocked)
        )

    missing = [plan for plan in plans if plan.status == "no-target"]
    if missing:
        named = "; ".join(f"{plan.name}: no branch {plan.target!r}" for plan in missing)
        raise GitSyncError(
            f"merge --into {target_branch}: refused, and nothing was changed. {named}. "
            "Create the branch where the work is, then run this again — this command "
            "never creates its own target, because a branch that is not there is as "
            "likely to be a typing mistake as a new branch."
        )

    for plan in plans:
        if plan.status == "no-source":
            _warn_branch_missing(
                next(
                    r
                    for r in _iter_merge_scope_project_first(tree, scope)
                    if r.name == plan.name
                ),
                plan.source,
                source_branch,
            )

    outcomes: list[MergeIntoPlan] = []
    for repo in _iter_merge_scope_project_first(tree, scope):
        plan = by_name[repo.name]
        if plan.status not in MERGE_INTO_ACTS and plan.status != "already-merged":
            outcomes.append(plan)
            continue
        # Checkout and merge, in that order, in this process. See the
        # docstring: splitting these is the bug this function exists to fix.
        git_runner.checkout(repo.absolute_path, plan.target)
        if plan.status in MERGE_INTO_ACTS:
            git_runner.merge(repo.absolute_path, plan.source, ff_only=ff_only, no_ff=no_ff)
        _refresh_repo_after_checkout(repo, plan.target, RefKind.BRANCH, git_runner)
        outcomes.append(plan)

    tree.recompute_tree_state()
    return tuple(outcomes)


@dataclass
class ResolveOutcome:
    """Where ``merge --resolve`` got to: merged, then stopped, then untouched.

    A resolve run can leave the tree partly merged, so the caller has to be
    able to say exactly where it stopped.

    ``stopped_at`` is the repository's display **name**, for printing — two
    repositories in a tree may share one, so it is never a lookup key.
    ``stopped_at_id`` is its ``repo_id``, the one thing
    :meth:`~ComplexGitSync.orchestre.ComplexGitSyncClient.open_merge_tool`
    can actually find in the registry with (a bare name lookup there raised
    ``KeyError`` for any repo, `.memory` included, whose id is not simply
    its own name — see
    `.localSpec/DevTickets/archive/20260918_ResolveMergeToolCrash_DevPlanTicket.md`).
    """

    merged: tuple[tuple[str, str], ...]
    stopped_at: str | None
    stopped_at_id: str | None
    stopped_paths: tuple[Path, ...]
    not_reached: tuple[str, ...]


def merge_tree_one_at_a_time(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    project_branch: str,
    *,
    scope: RepoScope = RepoScope.PROJECT,
    ff_only: bool = False,
    no_ff: bool = False,
) -> ResolveOutcome:
    """Merge leaf-first, stopping at the first repository that conflicts.

    The opposite trade from :func:`merge_tree`: repositories ahead of the
    conflict stay merged, and the conflict is left in the worktree for a
    merge tool to open. Project repositories are still ordered ahead of
    private ones (:func:`_iter_merge_scope_project_first`) even here, where
    it matters most: this is the mode that can genuinely stop partway, and
    stopping on a private repository with this project's own root already
    merged is what that ordering exists to guarantee.
    """
    _assert_ready(tree)

    project_name = tree_project_name(tree)
    repos = list(_iter_merge_scope_project_first(tree, scope))
    merged: list[tuple[str, str]] = []

    for position, repo in enumerate(repos):
        source, status, conflicts = merge_status(
            repo, git_runner, project_branch, project_name=project_name
        )
        if status in ("already-on-it", "no-branch"):
            if status == "no-branch":
                _warn_branch_missing(repo, source, project_branch)
            continue
        if status == "conflicts":
            # Let the merge run and fail: that is what writes the conflict
            # markers a merge tool needs. The error is swallowed on purpose —
            # the caller is told where the run stopped instead, because it
            # also has to be told what was merged before that.
            try:
                git_runner.merge(
                    repo.absolute_path, source, ff_only=ff_only, no_ff=no_ff
                )
            except GitSyncError:
                pass
            tree.recompute_tree_state()
            return ResolveOutcome(
                merged=tuple(merged),
                stopped_at=repo.name,
                stopped_at_id=repo.repo_id,
                stopped_paths=conflicts,
                not_reached=tuple(r.name for r in repos[position + 1 :]),
            )
        before = git_runner.rev_parse_head(repo.absolute_path)
        git_runner.merge(repo.absolute_path, source, ff_only=ff_only, no_ff=no_ff)
        after = git_runner.rev_parse_head(repo.absolute_path)
        repo.commit_sha = after
        if before != after:
            merged.append((repo.name, source))

    tree.recompute_tree_state()
    return ResolveOutcome(
        merged=tuple(merged),
        stopped_at=None,
        stopped_at_id=None,
        stopped_paths=(),
        not_reached=(),
    )


def refresh_private_tree(
    tree: WorkingGitTree,
    git_runner: GitRunner,
) -> tuple[tuple[str, str], ...]:
    """Bring every private/local repository up to date with its base branch.

    A private/local repository records this project's settings per project
    branch, on ``<base>_<branch>``. Those branches drift: work lands on the
    base while a feature branch is open, and the feature branch does not see
    it. This pulls each one from its own upstream, then merges its base
    branch in — the same :meth:`GitRunner.merge` primitive ``merge`` uses,
    not a second mechanism.

    A repository already sitting on its base branch has nothing to merge and
    is left alone. Returns one ``(repo_name, base_branch)`` pair per
    repository a merge actually moved.
    """
    _run_preflight_checks(
        tree,
        git_runner,
        require_clean=True,
        operation_name="pull --private",
        scope=RepoScope.PRIVATE,
    )

    planned: list[tuple[WorkingRepo, str]] = []
    blocked: list[str] = []
    branches = GitTreeBranches(tree, git_runner)
    for repo in iter_tree_leaf_first(tree, RepoScope.PRIVATE):
        # The base is the project's main-line settings branch -- the same rule
        # applied to "main", which by definition takes no suffix.
        base = branches.target(repo, DEFAULT_BRANCH).name
        current = git_runner.current_branch(repo.absolute_path)
        if current is None or current == base:
            continue
        remote = repo.remote_name or "origin"
        # Fetch, then merge the remote-tracking ref. Not `git pull <base>`:
        # that would fast-forward the *current* branch onto the base and
        # fail the moment the two have diverged, which is the normal state
        # of a feature branch and the only case worth handling.
        git_runner.fetch(repo.absolute_path, remote=remote, ref_name=base)
        source = f"{remote}/{base}"
        if not git_runner.branch_known(repo.absolute_path, base, remote=remote):
            continue
        merge_check = git_runner.can_merge_cleanly(repo.absolute_path, source)
        if not merge_check.is_clean:
            blocked.append(
                _describe_merge_conflict(repo.name, source, merge_check.conflicting_paths)
            )
            continue
        planned.append((repo, source))

    if blocked:
        raise GitSyncError(
            "pull --private refused; no repository was merged: " + "; ".join(blocked)
        )

    refreshed: list[tuple[str, str]] = []
    for repo, source in planned:
        before = git_runner.rev_parse_head(repo.absolute_path)
        git_runner.merge(repo.absolute_path, source)
        after = git_runner.rev_parse_head(repo.absolute_path)
        repo.commit_sha = after
        if before != after:
            refreshed.append((repo.name, source))

    tree.recompute_tree_state()
    return tuple(refreshed)


# ---------------------------------------------------------------------------
# push_tree — Tier 2 action
# ---------------------------------------------------------------------------


def push_tree(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    force_access_protocol: AccessProtocol | None = None,
    scope: RepoScope = RepoScope.PROJECT,
) -> tuple[RepoOutcome, ...]:
    """Push all repos to their remotes, leaf-first.

    Requires a ``READY`` tree; raises :exc:`~.errors.TreeNotReadyError`
    otherwise.  After a successful execution the tree remains ``READY``.

    The remote and branch used for each push are taken from
    ``repo.remote_name`` (defaulting to ``"origin"``) and
    ``repo.resolved_ref_name``.

    *force_access_protocol*, when given, rewrites each repo's remote to
    that protocol before pushing (``--force-protocol`` on ``push``).

    Returns one :class:`RepoOutcome` per repository pushed. ``acted`` is
    ``False`` for a repository that had nothing new to send — its branch
    was already level with its upstream — which is the common reason a push
    across a whole tree appears to do nothing.
    """
    _assert_ready(tree)
    _run_preflight_checks(
        tree,
        git_runner,
        require_clean=False,
        operation_name="push",
        scope=scope,
    )

    outcomes: list[RepoOutcome] = []
    for repo in iter_write_scope(tree, scope):
        remote = repo.remote_name or "origin"
        _rewrite_remote_if_forced(git_runner, repo, remote, force_access_protocol)
        # Before the push, not after: ``push -u`` can only write the
        # remote-tracking ref the upstream resolves through if the refspec
        # already maps the branch being pushed.
        _repair_fetch_refspec(git_runner, repo, remote)
        current_branch = git_runner.current_branch(repo.absolute_path)
        ref_name = repo.resolved_ref_name or current_branch
        set_upstream = False
        if ref_name is not None and current_branch == ref_name:
            set_upstream = not git_runner.has_upstream(repo.absolute_path)
        # Read before pushing: afterwards the branch is level with its
        # upstream either way, so the count that says whether this push
        # carried anything only exists now.
        ahead = _commits_ahead_of_upstream(git_runner, repo)
        git_runner.push(
            repo.absolute_path,
            remote=remote,
            ref_name=ref_name,
            set_upstream=set_upstream,
        )
        repo.commit_sha = git_runner.rev_parse_head(repo.absolute_path)
        target = f"{remote}/{ref_name}" if ref_name else remote
        if set_upstream:
            outcomes.append(
                RepoOutcome(name=repo.name, acted=True, detail=f"{target} (upstream set)")
            )
        elif ahead is None:
            outcomes.append(RepoOutcome(name=repo.name, acted=True, detail=target))
        elif ahead == 0:
            outcomes.append(
                RepoOutcome(
                    name=repo.name, acted=False, detail=f"{target} already up to date"
                )
            )
        else:
            outcomes.append(
                RepoOutcome(name=repo.name, acted=True, detail=f"{target} (+{ahead})")
            )

    tree.recompute_tree_state()
    return tuple(outcomes)


def _commits_ahead_of_upstream(git_runner: GitRunner, repo: WorkingRepo) -> int | None:
    """How many commits *repo* has that its upstream does not, or ``None``.

    ``None`` means the question does not apply — no upstream is configured,
    or the runner cannot answer — in which case a caller should not claim
    the push carried nothing.
    """
    try:
        counts = git_runner.branch_tracking_counts(repo.absolute_path)
    except (AttributeError, GitSyncError, ValueError):
        return None
    return counts[0] if counts is not None else None


def tag_tree(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    tag_name: str,
    *,
    scope: RepoScope = RepoScope.WRITABLE,
) -> None:
    """Create and push *tag_name* across the tree, leaf-first."""
    _assert_ready(tree)
    _run_preflight_checks(
        tree,
        git_runner,
        tag_name=tag_name,
        require_clean=True,
        operation_name="tag",
        # The same scope the loop below uses: a read-only configuration repo
        # is not tagged, so its state cannot block this.
        scope=scope,
    )
    _propagate_tag(tree, tag_name)

    # WRITABLE, not ALL: a tag is created *and pushed* in the same step, and
    # a read-only configuration repo is one this project may not push to.
    # Reproducibility does not suffer -- the .gts snapshot records every
    # repo's exact commit_sha, read-only ones included, so the tree is
    # rebuilt from the snapshot rather than from tags.
    for repo in iter_tree_leaf_first(tree, scope):
        git_runner.create_tag(repo.absolute_path, tag_name)
        remote = repo.remote_name or "origin"
        git_runner.push(repo.absolute_path, remote=remote, ref_name=tag_name)
        repo.current_ref_kind = RefKind.TAG
        repo.current_ref_name = tag_name
        repo.resolved_ref_kind = RefKind.TAG
        repo.resolved_ref_name = tag_name
        repo.commit_sha = git_runner.rev_parse_head(repo.absolute_path)
        repo.repo_lifecycle_state = RepoLifecycleState.READY
        repo.sync_state = SyncState.ALIGNED
        repo.fallback_applied = False
        repo.fallback_reason = None

    tree.recompute_tree_state()


def freeze_release_tree(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    tag_name: str,
    *,
    message: str | None = None,
    stage_all: bool = True,
    scope: RepoScope = RepoScope.WRITABLE,
) -> None:
    """Freeze a release by committing, tagging, and pushing leaf-first.

    *scope* defaults to every repository this project may write, which is
    what the bare command has always frozen. ``--private`` narrows it to
    the writable configuration repositories, so a settings branch can be
    frozen on its own without freezing the project with it.
    """
    _assert_ready(tree)
    _run_preflight_checks(
        tree,
        git_runner,
        tag_name=tag_name,
        require_clean=False,
        operation_name="freeze_release",
        scope=scope,
    )
    _propagate_tag(tree, tag_name)
    commit_message = message or f"freeze release {tag_name}"

    # The default is WRITABLE for the same reason as tag_tree: this commits,
    # tags *and* pushes, none of which this project may do to a read-only
    # configuration repo. Their exact SHAs are still recorded in the
    # snapshot this freeze writes.
    for repo in iter_tree_leaf_first(tree, scope):
        if stage_all:
            git_runner.stage_all(repo.absolute_path)
        if git_runner.has_staged_changes(repo.absolute_path):
            git_runner.commit(repo.absolute_path, commit_message)
        git_runner.create_tag(repo.absolute_path, tag_name)
        remote = repo.remote_name or "origin"
        git_runner.push(repo.absolute_path, remote=remote, ref_name=tag_name)
        repo.current_ref_kind = RefKind.TAG
        repo.current_ref_name = tag_name
        repo.resolved_ref_kind = RefKind.TAG
        repo.resolved_ref_name = tag_name
        repo.commit_sha = git_runner.rev_parse_head(repo.absolute_path)
        repo.repo_lifecycle_state = RepoLifecycleState.READY
        repo.sync_state = SyncState.ALIGNED
        repo.fallback_applied = False
        repo.fallback_reason = None

    tree.recompute_tree_state()


# ---------------------------------------------------------------------------
# validate_branch_topology — Tier 2 inspection function
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BranchTopologyConflict:
    """A single branch alignment conflict in the workspace topology.

    Produced by :func:`validate_branch_topology` for each repository that
    deviates from the expected branch topology.
    """

    repo_name: str
    """Name of the repository with the conflict."""

    expected_branch: str | None
    """The reference branch (root's active branch), or ``None`` if unknown."""

    actual_branch: str | None
    """The repository's current branch, or ``None`` when detached HEAD."""

    conflict_kind: Literal[
        "misaligned_branch", "detached_head", "tag_divergence", "missing_root"
    ]
    """Conflict classification.

    One of:

    ``"misaligned_branch"``
        The repository is on a different branch than the root.
    ``"detached_head"``
        The repository is in detached HEAD state without a tag reference.
    ``"tag_divergence"``
        The repository is on a tag (allowed divergence — frozen state).
    ``"missing_root"``
        The tree has no root repo; topology cannot be determined.
    """


@dataclass
class BranchTopologyReport:
    """Workspace branch topology inspection report.

    Produced by :func:`validate_branch_topology`.  The report is deterministic
    for a given workspace state: the same tree in the same branch configuration
    always produces an identical report.

    Branch Topology Propagation Rules (T35)
    ----------------------------------------
    1. **Reference branch**: The root repository's current branch is the
       canonical reference.  All other repositories must match it.
    2. **Leaf-to-root inheritance**: Branch targeting flows root-first via
       :func:`propagate_global_branch` and :func:`create_global_branch`.
       This function verifies that the resulting on-disk state is coherent.
    3. **Allowed divergence**: Repositories whose ``resolved_ref_kind`` is
       ``TAG`` are flagged as ``tag_divergence`` but do not make the topology
       incoherent — they represent frozen (released) state.
    4. **Incoherent states**:
       - ``misaligned_branch``: repo is on a different branch than root.
       - ``detached_head``: repo is in detached HEAD state without a known
         tag reference.
    """

    reference_branch: str | None
    """The root repository's active branch; the expected branch for all repos."""

    is_coherent: bool
    """``True`` when all repositories are on the reference branch or in an allowed tag state.

    Tag-divergent repos are considered allowed divergence and do not make the
    topology incoherent.  A topology is incoherent when at least one repository
    is on a different branch from the root, or is in an unexpected detached HEAD
    state.
    """

    conflicts: list[BranchTopologyConflict]
    """All detected branch alignment conflicts, one repo per repository."""

    repo_branches: dict[str, str | None]
    """Per-repository current branch snapshot: ``{repo_name: current_branch}``.

    ``None`` values indicate a detached HEAD state.  The dict is ordered in
    parent-first traversal order (root first, then direct children, then their
    descendants) for deterministic output.
    """

    def format(self) -> str:
        """Return a deterministic human-readable summary of the topology report."""
        lines: list[str] = []
        ref = self.reference_branch if self.reference_branch is not None else "(none)"
        status = "coherent" if self.is_coherent else "incoherent"
        lines.append(f"branch topology: {status} (reference={ref!r})")
        for repo_name, branch in self.repo_branches.items():
            branch_str = branch if branch is not None else "(detached)"
            lines.append(f"  {repo_name}: {branch_str!r}")
        if self.conflicts:
            lines.append("conflicts:")
            for c in self.conflicts:
                actual = c.actual_branch if c.actual_branch is not None else "(detached)"
                expected = c.expected_branch if c.expected_branch is not None else "(none)"
                lines.append(
                    f"  [{c.conflict_kind}] {c.repo_name}: "
                    f"expected={expected!r} actual={actual!r}"
                )
        return "\n".join(lines)


def validate_branch_topology(
    tree: WorkingGitTree,
    git_runner: GitRunner,
) -> BranchTopologyReport:
    """Inspect and validate the workspace branch topology.

    Walks the dependency tree and reports whether every repository is on the
    same branch as the root (or in an expected tag/frozen state).  The result
    is deterministic for the same workspace state: this function does not
    mutate the tree or issue any git write commands.

    Branch Topology Propagation Rules (T35)
    ----------------------------------------
    1. **Reference branch**: The root repository's current branch is the
       canonical reference.  All other repositories must match it.
    2. **Leaf-to-root inheritance**: Branch targeting flows root-first via
       :func:`propagate_global_branch` and :func:`create_global_branch`.
       This function verifies that the resulting on-disk state is coherent.
    3. **Allowed divergence**: Repositories whose ``resolved_ref_kind`` is
       ``TAG`` are flagged as ``tag_divergence`` but do not make the topology
       incoherent — they represent frozen (released) state.
    4. **Incoherent states** (blocking conflicts):
       - ``misaligned_branch``: repo is on a different branch than root.
       - ``detached_head``: repo is in detached HEAD state without a known
         tag reference.

    Parameters
    ----------
    tree:
        The runtime dependency tree.
    git_runner:
        The git subprocess wrapper used to read live branch state.

    Returns
    -------
    BranchTopologyReport
        A deterministic, inspectable snapshot of the workspace branch topology.
    """
    if ROOT_REPO_ID not in tree.repos:
        return BranchTopologyReport(
            reference_branch=None,
            is_coherent=False,
            conflicts=[
                BranchTopologyConflict(
                    repo_name="tree",
                    expected_branch=None,
                    actual_branch=None,
                    conflict_kind="missing_root",
                )
            ],
            repo_branches={},
        )

    # This report measures every repository against the root's branch
    # itself, privacy included — a private repository sitting on its own
    # branch is reported here as a divergence and is *not* one to the
    # preflight below, which measures against `GitTreeBranches.expected`.
    # The two questions differ on purpose, so only the reading is shared.
    branches = GitTreeBranches(tree, git_runner)
    reference_branch = branches.tree_branch

    conflicts: list[BranchTopologyConflict] = []
    repo_branches: dict[str, str | None] = {}

    for repo in iter_tree(tree):
        current = branches.observed(repo)
        repo_branches[repo.name] = current

        if current is None:
            # Detached HEAD: allowed only when the repo carries a tag reference
            kind = (
                "tag_divergence"
                if repo.resolved_ref_kind == RefKind.TAG
                else "detached_head"
            )
            conflicts.append(
                BranchTopologyConflict(
                    repo_name=repo.name,
                    expected_branch=reference_branch,
                    actual_branch=None,
                    conflict_kind=kind,
                )
            )
        elif reference_branch is not None and current != reference_branch:
            kind = (
                "tag_divergence"
                if repo.resolved_ref_kind == RefKind.TAG
                else "misaligned_branch"
            )
            conflicts.append(
                BranchTopologyConflict(
                    repo_name=repo.name,
                    expected_branch=reference_branch,
                    actual_branch=current,
                    conflict_kind=kind,
                )
            )

    _blocking_kinds = {"misaligned_branch", "detached_head", "missing_root"}
    is_coherent = not any(c.conflict_kind in _blocking_kinds for c in conflicts)

    return BranchTopologyReport(
        reference_branch=reference_branch,
        is_coherent=is_coherent,
        conflicts=conflicts,
        repo_branches=repo_branches,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _assert_ready(tree: WorkingGitTree) -> None:
    """Raise :exc:`~.errors.TreeNotReadyError` when *tree* is not READY."""
    if not tree.is_ready():
        raise TreeNotReadyError(
            f"Operation requires a READY tree; current state: {tree.lifecycle_state.value}"
        )


def _run_preflight_checks(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    tag_name: str | None = None,
    require_clean: bool,
    operation_name: str,
    scope: RepoScope = RepoScope.ALL,
    check_branch_alignment: bool = True,
) -> None:
    """Check the repositories *scope* selects, and only those.

    An operation must not be blocked by the state of a repository it is
    never going to touch. ``commit`` writes this project's own repos, so a
    read-only configuration repo sitting on its own branch, or behind its
    upstream, is none of its business.

    ``check_branch_alignment=False`` is for :func:`merge_into_tree` alone
    (see its own docstring). Every other caller leaves it at the default:
    "this repository is on the branch the tree expects" is a real
    precondition for a command that only ever acts on whatever is already
    checked out, and stays enforced for all of them.
    """
    diagnostics = _collect_preflight_diagnostics(
        tree,
        git_runner,
        operation_name=operation_name,
        tag_name=tag_name,
        require_clean=require_clean,
        scope=scope,
        check_branch_alignment=check_branch_alignment,
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
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    operation_name: str,
    tag_name: str | None,
    require_clean: bool,
    scope: RepoScope = RepoScope.ALL,
    check_branch_alignment: bool = True,
) -> list[PreflightDiagnostic]:
    diagnostics: list[PreflightDiagnostic] = []
    diagnostics.extend(_collect_remote_diagnostics(tree, git_runner, scope=scope))
    if tag_name is not None:
        diagnostics.extend(
            _collect_tag_conflict_diagnostics(tree, git_runner, tag_name=tag_name, scope=scope)
        )
    diagnostics.extend(_collect_detached_head_diagnostics(tree, git_runner, scope=scope))
    diagnostics.extend(_collect_merge_diagnostics(tree, git_runner, scope=scope))
    if check_branch_alignment:
        diagnostics.extend(_collect_branch_alignment_diagnostics(tree, git_runner, scope=scope))
    diagnostics.extend(_collect_tracking_diagnostics(tree, git_runner, scope=scope))
    diagnostics.extend(
        _collect_commit_sha_diagnostics(
            tree,
            git_runner,
            blocking=False,
            scope=scope,
        )
    )
    diagnostics.extend(
        _collect_worktree_diagnostics(tree, git_runner, require_clean=require_clean, scope=scope)
    )
    return diagnostics


def _collect_remote_diagnostics(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    scope: RepoScope = RepoScope.ALL,
) -> list[PreflightDiagnostic]:
    missing: list[PreflightDiagnostic] = []
    for repo in iter_tree_leaf_first(tree, scope):
        remote = repo.remote_name or "origin"
        if not git_runner.remote_exists(repo.absolute_path, remote):
            missing.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    repo.name,
                    f"missing remote {remote!r}.",
                )
            )
    return missing


def _collect_tag_conflict_diagnostics(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    tag_name: str,
    scope: RepoScope = RepoScope.ALL,
) -> list[PreflightDiagnostic]:
    duplicates: list[PreflightDiagnostic] = []
    for repo in iter_tree_leaf_first(tree, scope):
        if git_runner.tag_exists(repo.absolute_path, tag_name):
            duplicates.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    repo.name,
                    f"ERROR tag already taken: {tag_name!r}.",
                )
            )
    return duplicates


def _collect_detached_head_diagnostics(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    scope: RepoScope = RepoScope.ALL,
) -> list[PreflightDiagnostic]:
    detached: list[PreflightDiagnostic] = []
    for repo in iter_tree_leaf_first(tree, scope):
        if git_runner.current_branch(repo.absolute_path) is None:
            detached.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    repo.name,
                    "repository is in detached HEAD state.",
                )
            )
    return detached


def _collect_merge_diagnostics(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    scope: RepoScope = RepoScope.ALL,
) -> list[PreflightDiagnostic]:
    merges: list[PreflightDiagnostic] = []
    for repo in iter_tree_leaf_first(tree, scope):
        if git_runner.has_unresolved_merge(repo.absolute_path):
            merges.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    repo.name,
                    "repository has an unresolved merge in progress.",
                )
            )
    return merges


def _collect_branch_alignment_diagnostics(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    scope: RepoScope = RepoScope.ALL,
) -> list[PreflightDiagnostic]:
    if ROOT_REPO_ID not in tree.repos:
        return [
            PreflightDiagnostic(
                PreflightSeverity.BLOCKING_ERROR,
                "tree",
                "tree has no root repository.",
            )
        ]
    # A private repository is shared with other projects and stays on a
    # branch of its own, so the root's branch is not what it should be on.
    # GitTreeBranches asks git_branch.resolve_propagated_ref for each
    # repository's own answer, which is where that rule lives.
    return [
        PreflightDiagnostic(
            PreflightSeverity.BLOCKING_ERROR,
            deviation.repo.name,
            f"branch misalignment: expected {deviation.expected!r}"
            f"{' (private to its own branch)' if deviation.repo.effective_private else ''}, "
            f"found {deviation.observed!r}.",
        )
        for deviation in GitTreeBranches(tree, git_runner).deviations(scope=scope)
    ]


def _collect_tracking_diagnostics(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    scope: RepoScope = RepoScope.ALL,
) -> list[PreflightDiagnostic]:
    # The memory mount's relationship to its own origin is managed only by
    # ``memory push``/``memory adopt``/``memory clone`` — on a cadence
    # entirely decoupled from whatever tree-wide operation is asking here —
    # so being behind or diverged from that origin is not this operation's
    # business, the same reasoning that keeps it out of add/commit/push's
    # own action scope (``iter_write_scope``).
    diagnostics: list[PreflightDiagnostic] = []
    for repo in iter_tree_leaf_first(tree, scope):
        if repo.is_memory_mount:
            continue
        tracking_state = git_runner.branch_tracking_state(repo.absolute_path)
        if tracking_state in (None, SyncState.ALIGNED):
            continue
        if tracking_state == SyncState.AHEAD:
            diagnostics.append(
                PreflightDiagnostic(
                    PreflightSeverity.WARNING,
                    repo.name,
                    "local branch is ahead of its upstream.",
                )
            )
        elif tracking_state == SyncState.BEHIND:
            diagnostics.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    repo.name,
                    "local branch is behind its upstream.",
                )
            )
        elif tracking_state == SyncState.DIVERGED:
            diagnostics.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    repo.name,
                    "local branch diverged from its upstream.",
                )
            )
        else:
            diagnostics.append(
                PreflightDiagnostic(
                    PreflightSeverity.WARNING,
                    repo.name,
                    f"repository reports tracking state {tracking_state.value!r}.",
                )
            )
    return diagnostics


def _collect_commit_sha_diagnostics(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    blocking: bool,
    scope: RepoScope = RepoScope.ALL,
) -> list[PreflightDiagnostic]:
    inconsistent: list[PreflightDiagnostic] = []
    severity = PreflightSeverity.BLOCKING_ERROR if blocking else PreflightSeverity.WARNING
    for repo in iter_tree_leaf_first(tree, scope):
        if not repo.commit_sha:
            continue
        head_sha = git_runner.rev_parse_head(repo.absolute_path)
        if head_sha != repo.commit_sha:
            inconsistent.append(
                PreflightDiagnostic(
                    severity,
                    repo.name,
                    f"recorded commit_sha {repo.commit_sha!r} does not match HEAD {head_sha!r}.",
                )
            )
    return inconsistent


def _collect_worktree_diagnostics(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    require_clean: bool,
    scope: RepoScope = RepoScope.ALL,
) -> list[PreflightDiagnostic]:
    dirty: list[PreflightDiagnostic] = []
    severity = (
        PreflightSeverity.BLOCKING_ERROR if require_clean else PreflightSeverity.WARNING
    )
    # Walks the whole tree even when the scope is narrower: worktree_state
    # is written into the .gts snapshot for every repository, so it must
    # stay fresh. Only the diagnostics are scoped.
    #
    # The memory mount is excluded from the diagnostic (not from the
    # worktree_state refresh above it): it records the very command that
    # is running, so it is expected to read dirty at the moment a preflight
    # asks, the same fact `status` already reports as a note rather than a
    # fault. Blocking `merge --into` on it would make merging a branch that
    # touches `.memory` impossible by construction.
    for repo in iter_tree_leaf_first(tree):
        is_dirty = _has_managed_uncommitted_changes(tree, git_runner, repo)
        repo.worktree_state = "DIRTY" if is_dirty else "CLEAN"
        if is_dirty and scope.includes(repo) and not repo.is_memory_mount:
            dirty.append(
                PreflightDiagnostic(
                    severity,
                    repo.name,
                    "worktree has uncommitted changes.",
                )
            )
    return dirty


def _has_managed_uncommitted_changes(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    repo: WorkingRepo,
) -> bool:
    try:
        status_lines = git_runner.status_porcelain(repo.absolute_path)
        unmanaged_gitlinks = _unmanaged_gitlink_paths(tree, git_runner, repo)
    except (AttributeError, GitSyncError):
        return git_runner.has_uncommitted_changes(repo.absolute_path)
    ignored_paths = unmanaged_gitlinks | cgitsync_managed_state_paths(repo)
    if not ignored_paths:
        return bool(status_lines)
    return any(
        not _status_line_targets_any(line, ignored_paths)
        for line in status_lines
    )


def _unmanaged_gitlink_paths(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    repo: WorkingRepo,
) -> set[Path]:
    try:
        gitlinks = git_runner.tracked_gitlink_paths(repo.absolute_path)
    except (AttributeError, GitSyncError):
        return set()

    managed_children: set[Path] = set()
    for child in tree.children_of(repo.repo_id):
        try:
            managed_children.add(child.absolute_path.relative_to(repo.absolute_path))
        except ValueError:
            continue
    return {path for path in gitlinks if path not in managed_children}


def _status_line_targets_any(status_line: str, paths: set[Path]) -> bool:
    status_path = _status_line_path(status_line)
    if status_path is None:
        return False
    return any(status_path == path or _path_is_relative_to(status_path, path) for path in paths)


def _status_line_path(status_line: str) -> Path | None:
    if len(status_line) < 4:
        return None
    raw_path = status_line[3:]
    if " -> " in raw_path:
        raw_path = raw_path.rsplit(" -> ", 1)[1]
    raw_path = raw_path.strip().strip('"')
    return Path(raw_path) if raw_path else None


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


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


def _propagate_tag(tree: WorkingGitTree, tag_name: str) -> None:
    """Propagate *tag_name* across *tree* from parent to leaves."""
    for repo in iter_tree(tree):
        repo.target_ref_kind = RefKind.TAG
        repo.target_ref_name = tag_name


def _refresh_repo_after_checkout(
    repo: WorkingRepo,
    branch_name: str,
    ref_kind: RefKind,
    git_runner: GitRunner,
) -> None:
    """Update *repo* in-place to reflect a completed ``git checkout``."""
    repo.current_ref_kind = ref_kind
    repo.current_ref_name = branch_name
    repo.resolved_ref_kind = ref_kind
    repo.resolved_ref_name = branch_name
    repo.commit_sha = git_runner.rev_parse_head(repo.absolute_path)
    repo.fallback_applied = False
    repo.fallback_reason = None
    repo.repo_lifecycle_state = RepoLifecycleState.READY
    repo.sync_state = SyncState.ALIGNED

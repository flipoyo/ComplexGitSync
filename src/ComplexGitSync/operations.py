"""operations — Tier 2 synchronization operations for ComplexGitSync.

Ring: 2 (no direct subprocess import; drives Git only through an injected
    GitRunner-shaped object, same ring as git_runner.py per IsolationPlan.md §1)
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
from collections.abc import Sequence
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
    _as_optional_str,
    cgitsync_managed_state_paths,
    iter_tree,
    iter_tree_leaf_first,
    resolve_repo_for_path,
)

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


def tree_project_name(tree: WorkingGitTree) -> str | None:
    """The project's name, which is what a private/local branch is named after.

    Read from the root entry, the one place a tree records what project it
    is. Returns ``None`` for a tree with no root, where the private/local
    rule cannot apply anyway.
    """
    root = tree.repos.get(ROOT_REPO_ID)
    if root is None:
        return None
    return _as_optional_str(root.project_name) or _as_optional_str(root.name)


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
    with other projects. Pinning governs *branch* propagation only, so a
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
    project_name = tree_project_name(tree)
    for repo in tree.values():
        resolve_propagated_ref(
            repo, branch_name, ref_kind=ref_kind, project_name=project_name
        ).apply_to(repo)


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
    """
    project_name = tree_project_name(tree)
    for repo in iter_tree(tree, scope):
        if repo.effective_private and not repo.effective_writable:
            continue
        target = resolve_propagated_ref(
            repo, branch_name, project_name=project_name
        ).name
        if git_runner.local_branch_exists(repo.absolute_path, target):
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
    ``AgentSpec/archive/20260904_GtsProviderLoss_DevPlanTicket.md``), and
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
    root_entry = tree.get("root")
    observed = git_runner.current_branch(root_entry.absolute_path)
    current_branch = resolve_entry_ref(root_entry, observed_branch=observed).name
    # The runner matters here for the same reason it does in checkout_tree:
    # the loop below pulls whatever this decides, and a private/local repo's
    # derived branch has to be one that exists.
    propagate_global_branch(tree, current_branch, git_runner=git_runner)

    for repo in iter_tree(tree, scope):
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
    (parent-first) with ``git pull --ff-only`` on the branch that repo
    actually targets.

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
    block a fast-forward pull.

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
        for repo in iter_tree_leaf_first(tree, scope):
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


def remove_paths(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    paths: Sequence[str | Path],
) -> None:
    """Remove one or more tracked files, each from the repo that owns it.

    Requires a ``READY`` tree; raises :exc:`~.errors.TreeNotReadyError`
    otherwise. Each path is resolved via
    :func:`~.git_tree.resolve_repo_for_path`; a path outside every repo in
    the tree raises :exc:`~.errors.GitSyncError` immediately, before
    anything is removed.

    A plain tracked file only (``git rm -- <path>``, removing it from disk
    and staging the removal) — a path that resolves to a directory, or that
    does not exist, also raises :exc:`~.errors.GitSyncError` rather than
    failing silently or partially. Distinct from and unrelated to
    ``rm_cached`` (index-only, built for the submodule-to-plain-clone
    conversion): this does not replace it.
    """
    _assert_ready(tree)

    resolved = [resolve_repo_for_path(tree, path) for path in paths]
    for repo, relative_path in resolved:
        target = repo.absolute_path / relative_path
        if target.is_dir():
            raise GitSyncError(
                f"{target} is a directory; rm only removes a single tracked file today (no -r yet)."
            )
        if not target.exists():
            raise GitSyncError(f"{target} does not exist.")

    for repo, relative_path in resolved:
        git_runner.remove(repo.absolute_path, relative_path)

    tree.recompute_tree_state()


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
    for repo in iter_tree_leaf_first(tree, scope):
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
) -> tuple[str, str]:
    """What ``merge`` would do to *repo*, as ``(source_ref, status)``.

    The one place a repository's fate is decided, so the dry run and the
    merge itself cannot disagree. ``status`` is ``"merge"``,
    ``"already-on-it"`` (the repository is sitting on the branch it would
    merge, so there is nothing to merge it into) or ``"no-branch"`` (nothing
    to merge from — normal for a private/local repository with no branch for
    this project branch yet).
    """
    source = merge_source_ref(repo, project_branch, project_name=project_name)
    if git_runner.current_branch(repo.absolute_path) == source:
        return source, "already-on-it"
    if not git_runner.branch_known(
        repo.absolute_path, source, remote=repo.remote_name or "origin"
    ):
        return source, "no-branch"
    return source, "merge"


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
    for repo in iter_tree_leaf_first(tree, scope):
        source, status = merge_status(
            repo, git_runner, project_branch, project_name=project_name
        )
        if status == "already-on-it":
            # Merging a branch into itself does nothing and reports success,
            # which reads as "it worked" when the tree is simply still on the
            # branch the user meant to merge *from*.
            on_source.append(repo.name)
            continue
        if status == "no-branch":
            continue
        if not git_runner.can_merge_cleanly(repo.absolute_path, source):
            blocked.append(f"{repo.name}: merging {source!r} conflicts")
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
    project_name = tree_project_name(tree)
    for repo in iter_tree_leaf_first(tree, RepoScope.PRIVATE):
        # The base is the project's main-line settings branch -- the same rule
        # applied to "main", which by definition takes no suffix.
        base = resolve_propagated_ref(
            repo, DEFAULT_BRANCH, project_name=project_name
        ).name
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
        if not git_runner.can_merge_cleanly(repo.absolute_path, source):
            blocked.append(f"{repo.name}: merging {source!r} conflicts")
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
    for repo in iter_tree_leaf_first(tree, scope):
        remote = repo.remote_name or "origin"
        _rewrite_remote_if_forced(git_runner, repo, remote, force_access_protocol)
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
) -> None:
    """Freeze a release by committing, tagging, and pushing leaf-first."""
    _assert_ready(tree)
    _run_preflight_checks(
        tree,
        git_runner,
        tag_name=tag_name,
        require_clean=False,
        operation_name="freeze_release",
        scope=RepoScope.WRITABLE,
    )
    _propagate_tag(tree, tag_name)
    commit_message = message or f"freeze release {tag_name}"

    # WRITABLE for the same reason as tag_tree: this commits, tags *and*
    # pushes, none of which this project may do to a read-only
    # configuration repo. Their exact SHAs are still recorded in the
    # snapshot this freeze writes.
    for repo in iter_tree_leaf_first(tree, RepoScope.WRITABLE):
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
    if "root" not in tree.repos:
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

    root = tree.get("root")
    reference_branch = git_runner.current_branch(root.absolute_path)

    conflicts: list[BranchTopologyConflict] = []
    repo_branches: dict[str, str | None] = {}

    for repo in iter_tree(tree):
        current = git_runner.current_branch(repo.absolute_path)
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
) -> None:
    """Check the repositories *scope* selects, and only those.

    An operation must not be blocked by the state of a repository it is
    never going to touch. ``commit`` writes this project's own repos, so a
    read-only configuration repo sitting on its own branch, or behind its
    upstream, is none of its business.
    """
    diagnostics = _collect_preflight_diagnostics(
        tree,
        git_runner,
        operation_name=operation_name,
        tag_name=tag_name,
        require_clean=require_clean,
        scope=scope,
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
) -> list[PreflightDiagnostic]:
    diagnostics: list[PreflightDiagnostic] = []
    diagnostics.extend(_collect_remote_diagnostics(tree, git_runner, scope=scope))
    if tag_name is not None:
        diagnostics.extend(
            _collect_tag_conflict_diagnostics(tree, git_runner, tag_name=tag_name, scope=scope)
        )
    diagnostics.extend(_collect_detached_head_diagnostics(tree, git_runner, scope=scope))
    diagnostics.extend(_collect_merge_diagnostics(tree, git_runner, scope=scope))
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
    if "root" not in tree.repos:
        return [
            PreflightDiagnostic(
                PreflightSeverity.BLOCKING_ERROR,
                "tree",
                "tree has no root repository.",
            )
        ]
    root = tree.get("root")
    root_branch = git_runner.current_branch(root.absolute_path)
    if root_branch is None:
        return []
    mismatched: list[PreflightDiagnostic] = []
    project_name = tree_project_name(tree)
    for repo in iter_tree_leaf_first(tree, scope):
        # A private repository is shared with other projects and stays on a
        # branch of its own, so the root's branch is not what it should be
        # on. resolve_propagated_ref is the one place that rule lives, and
        # resolve_existing_propagated_ref then applies the same fallback
        # checkout would: a private/local repo whose derived branch has not
        # been created is measured against where it actually belongs today,
        # not against a branch nobody has made yet.
        expected_branch = resolve_propagated_ref(
            repo, root_branch, project_name=project_name
        ).name
        current = git_runner.current_branch(repo.absolute_path)
        if current is not None and current != expected_branch:
            detail = " (private to its own branch)" if repo.effective_private else ""
            mismatched.append(
                PreflightDiagnostic(
                    PreflightSeverity.BLOCKING_ERROR,
                    repo.name,
                    f"branch misalignment: expected {expected_branch!r}{detail}, "
                    f"found {current!r}.",
                )
            )
    return mismatched


def _collect_tracking_diagnostics(
    tree: WorkingGitTree,
    git_runner: GitRunner,
    *,
    scope: RepoScope = RepoScope.ALL,
) -> list[PreflightDiagnostic]:
    diagnostics: list[PreflightDiagnostic] = []
    for repo in iter_tree_leaf_first(tree, scope):
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
    for repo in iter_tree_leaf_first(tree):
        is_dirty = _has_managed_uncommitted_changes(tree, git_runner, repo)
        repo.worktree_state = "DIRTY" if is_dirty else "CLEAN"
        if is_dirty and scope.includes(repo):
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

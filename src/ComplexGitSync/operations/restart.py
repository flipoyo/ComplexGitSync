"""restart — Resync the tree from its remotes, gently or destructively.

Ring: 2
Contract: Resync the tree from its remotes, gently or destructively.
Imports: branch, errors, git_branch, git_repo, git_tree, git_tree_branch, merge, orchestre, preflight
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import GitSyncError
from ..git_branch import (
    DEFAULT_BRANCH,
    resolve_entry_ref,
)
from ..git_repo import (
    AccessProtocol,
    RefKind,
    RepoScope,
    WorkingRepo,
    convert_remote_url_protocol,
)
from ..git_tree import (
    ROOT_REPO_ID,
    WorkingGitTree,
    iter_tree,
    iter_tree_leaf_first,
)
from ..git_tree_branch import GitTreeBranches

if TYPE_CHECKING:
    from ..orchestre import GitRunner

from .branch import BranchOperation
from .merge import MergeOperation
from .preflight import Preflight


class RestartOperation:
    """Resync the tree from its remotes, gently or destructively."""

    @staticmethod
    def rewrite_remote_if_forced(
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
        ``.agent/.local/.localSpec/DevTickets/archive/20260904_GtsProviderLoss_DevPlanTicket.md``), and
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

    @staticmethod
    def repair_fetch_refspec(git_runner: GitRunner, repo: WorkingRepo, remote: str) -> None:
        """Widen *repo*'s fetch refspec if a ``--single-branch`` clone narrowed it.

        Every workspace cloned before that narrowing was fixed
        (``.agent/.local/.localSpec/DevTickets/archive/20260911_UpstreamBranchDisplay_DevPlanTicket.md``)
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

    @staticmethod
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

    @staticmethod
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
        BranchOperation.propagate_global_branch(tree, current_branch, git_runner=git_runner)

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
            RestartOperation.rewrite_remote_if_forced(git_runner, repo, remote, force_access_protocol)
            RestartOperation.repair_fetch_refspec(git_runner, repo, remote)
            RestartOperation._fetch_all_refs(git_runner, repo, remote)
            target_branch = repo.target_ref_name or current_branch
            # A branch that has never been pushed has nothing to pull from —
            # `git pull --ff-only origin <branch>` fails outright when origin
            # has no such ref, which used to abort this whole tree-wide loop
            # over one repository with real, current, merely unpublished work
            # (a memory freshly rebooted or freshly adopted, most often). That
            # is not a pull failure, it is nothing to do — the same distinction
            # `freeze_release` already draws before its own pull
            # (`MemoryCommands.freeze_release`), generalised here to every
            # repository this loop visits, not only the root.
            if git_runner.remote_tracking_branch_exists(repo.absolute_path, target_branch, remote=remote):
                pull = git_runner.force_pull if force else git_runner.pull
                pull(repo.absolute_path, remote=remote, ref_name=target_branch)

            resolved_branch = git_runner.current_branch(repo.absolute_path) or current_branch
            BranchOperation.refresh_repo_after_checkout(repo, resolved_branch, RefKind.BRANCH, git_runner)

        tree.recompute_tree_state()

    @staticmethod
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
        in *scope*, parent-first, with ``git pull --ff-only`` on the branch that
        repo actually targets — the workspace's own memory mount included: its
        worktree is an ordinary private/local repository now that nothing but
        `memory push`'s own fold writes into it
        (`memory-dev_WorkingTransitionState`).

        Does not require a ``READY`` tree; intended for use after loading a
        ``.cgs`` file (``DECLARED`` state).  Produces a ``READY`` tree or
        raises if any repository checkout fails.

        *force_access_protocol*, when given, rewrites each repo's remote to
        that protocol before pulling (``--force-protocol`` on ``pull``).
        """
        RestartOperation._restart_tree(
            tree, git_runner, force=False, force_access_protocol=force_access_protocol, scope=scope
        )

    @staticmethod
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
        block a fast-forward pull — the memory mount included, in scope the same
        way :func:`restart_tree` includes it.

        *force_access_protocol*, when given, rewrites each repo's remote to
        that protocol before force-pulling (``--force-protocol`` on
        ``pull-force``).
        """
        RestartOperation._restart_tree(
            tree, git_runner, force=True, force_access_protocol=force_access_protocol, scope=scope
        )

    @staticmethod
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
        Preflight.run_preflight_checks(
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
                    MergeOperation.describe_merge_conflict(repo.name, source, merge_check.conflicting_paths)
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


__all__ = [
    "RestartOperation",
]

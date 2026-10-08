"""merge — Merge a branch across the tree, with a preflight over every repository first.

Ring: 2
Contract: Merge a branch across the tree, with a preflight over every repository first.
Imports: branch, errors, git_branch, git_repo, git_tree, git_tree_branch, memory_merge, orchestre, preflight
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import GitSyncError
from ..git_branch import (
    resolve_propagated_ref,
)
from ..git_repo import (
    RefKind,
    RepoScope,
    WorkingRepo,
)
from ..git_tree import (
    WorkingGitTree,
    iter_tree_leaf_first,
)
from ..git_tree_branch import tree_project_name

if TYPE_CHECKING:
    from ..orchestre import GitRunner

from .branch import BranchOperation
from .memory_merge import MemoryMergeOperation
from .preflight import Preflight


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

#: What a repository's fate can be. ``fast-forward``, ``merge`` and ``kept``
#: act; the rest do not. They are told apart because a fast-forward makes no
#: commit and explains why a repository looks untouched afterwards, and
#: ``kept`` is the memory: its own side is kept whole and the other recorded
#: as history, never merged file by file (UnrelatedHistoryMerge).
MERGE_INTO_ACTS = ("fast-forward", "merge", "kept")

#: Hint shown when a merge is refused due to conflicts.
MERGE_RESOLVE_HINT = (
    " Run 'cgitsync merge --resolve <branch>' to merge one repository at a "
    "time — it will stop at the first conflict and open a merge tool "
    "automatically if one is configured."
)

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
    `.agent/.local/.dev/DevTickets/archive/20260918_ResolveMergeToolCrash_DevPlanTicket.md`).
    """

    merged: tuple[tuple[str, str], ...]
    stopped_at: str | None
    stopped_at_id: str | None
    stopped_paths: tuple[Path, ...]
    not_reached: tuple[str, ...]


class MergeOperation:
    """Merge a branch across the tree, with a preflight over every repository first."""

    @staticmethod
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

    @staticmethod
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
        merge from), ``"conflicts"``, or one of the three that
        :meth:`MemoryMergeOperation.tree_status` decides: ``"kept"`` and ``"up-to-date"`` (the
        memory) and ``"unrelated"`` (no commit in common). ``paths`` is empty
        for every status but ``"conflicts"``, and empty for that one too when
        git blamed no file.
        """
        source = MergeOperation.merge_source_ref(repo, project_branch, project_name=project_name)
        if git_runner.current_branch(repo.absolute_path) == source:
            return source, "already-on-it", ()
        if not git_runner.branch_known(
            repo.absolute_path, source, remote=repo.remote_name or "origin"
        ):
            return source, "no-branch", ()
        history = MemoryMergeOperation.tree_status(repo, git_runner, source)
        if history is not None:
            return source, history, ()
        check = git_runner.can_merge_cleanly(repo.absolute_path, source)
        if not check.is_clean:
            return source, "conflicts", tuple(check.conflicting_paths)
        return source, "merge", ()

    @staticmethod
    def describe_unrelated(repo_name: str, source: str, target: str | None) -> str:
        """One repository's entry in a refusal for histories with nothing in common."""
        return f"{repo_name}: {source!r} and {target or 'HEAD'!r} share no commit; Git will not merge unrelated histories"

    @staticmethod
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

    @staticmethod
    def describe_merge_conflict(
        repo_name: str, source: str, paths: Sequence[Path]
    ) -> str:
        """One repository's entry in a refusal: which branch, and which files.

        The branch is named whether or not git blamed a file. It used to be
        named only in the no-file case, which read fine in a terminal the
        moment you had typed the branch yourself and badly everywhere else: a
        refusal listing four repositories by path alone leaves a reader — and
        `autofix.repair_merge_conflict`, which re-checks the conflict against
        Git before reporting it — with no way to tell what was being merged.
        """
        where = f" in {', '.join(str(path) for path in paths)}" if paths else ""
        return f"{repo_name}: merging {source!r} conflicts{where}"

    @staticmethod
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
        (`.agent/.local/.dev/DevTickets/archive/20260918_MergeProjectBeforePrivate_DevPlanTicket.md`).

        ``PROJECT`` and ``PRIVATE`` never overlap (a repository is either not
        private, or private *and* writable), so this never yields one twice.
        """
        for repo in iter_tree_leaf_first(tree, RepoScope.PROJECT):
            if scope.includes(repo):
                yield repo
        for repo in iter_tree_leaf_first(tree, RepoScope.PRIVATE):
            if scope.includes(repo):
                yield repo

    @staticmethod
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
        Preflight.assert_ready(tree)
        Preflight.run_preflight_checks(
            tree,
            git_runner,
            require_clean=True,
            operation_name="merge",
            scope=scope,
        )

        planned: list[tuple[WorkingRepo, str, bool]] = []
        blocked: list[str] = []
        unrelated: list[str] = []
        on_source: list[str] = []
        project_name = tree_project_name(tree)
        for repo in MergeOperation._iter_merge_scope_project_first(tree, scope):
            source, status, conflicts = MergeOperation.merge_status(
                repo, git_runner, project_branch, project_name=project_name
            )
            if status == "up-to-date":
                continue
            if status == "unrelated":
                unrelated.append(MergeOperation.describe_unrelated(repo.name, source, git_runner.current_branch(repo.absolute_path)))
                continue
            if status == "already-on-it":
                # Merging a branch into itself does nothing and reports success,
                # which reads as "it worked" when the tree is simply still on the
                # branch the user meant to merge *from*.
                on_source.append(repo.name)
                continue
            if status == "no-branch":
                MergeOperation._warn_branch_missing(repo, source, project_branch)
                continue
            if status == "conflicts":
                blocked.append(MergeOperation.describe_merge_conflict(repo.name, source, conflicts))
                continue
            planned.append((repo, source, status == "kept"))

        if blocked or unrelated:
            raise GitSyncError(
                "merge refused; no repository was merged: " + "; ".join(blocked + unrelated)
                + (MERGE_RESOLVE_HINT if blocked else "")
            )
        if on_source and not planned:
            raise GitSyncError(
                f"merge {project_branch}: the tree is already on {project_branch!r} "
                f"({', '.join(on_source)}), so there is nothing to merge it into. "
                f"Check out the branch you want to merge *into* first — "
                f"'cgitsync checkout <target>' — then run this again."
            )

        merged: list[tuple[str, str]] = []
        for repo, source, kept in planned:
            before = git_runner.rev_parse_head(repo.absolute_path)
            if kept:
                MemoryMergeOperation.keep_in_tree(repo, git_runner, source)
            else:
                git_runner.merge(repo.absolute_path, source, ff_only=ff_only, no_ff=no_ff)
            after = git_runner.rev_parse_head(repo.absolute_path)
            repo.commit_sha = after
            if before != after:
                merged.append((repo.name, source))

        tree.recompute_tree_state()
        return tuple(merged)

    @staticmethod
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

        Seven answers:

        - ``no-source`` / ``no-target`` — that branch is not here and not on the
          remote. Neither is invented: a branch that is missing is as likely to
          be a typing mistake as a new branch.
        - ``already-merged`` — the target already contains the source. Nothing
          to do, and not a failure.
        - ``fast-forward`` — the target is an ancestor of the source, so it only
          has to move.
        - ``merge`` — a real merge that applies cleanly.
        - ``conflicts`` — with the paths git blamed, empty when it blamed none.
        - ``kept`` — the memory: its own side stays whole and the source is
          recorded as history (:meth:`MemoryMergeOperation.tree_status`); never a file-by-file
          merge, so never a conflict.
        - ``unrelated`` — the two branches share no commit.
        """
        source = MergeOperation.merge_source_ref(repo, source_branch, project_name=project_name)
        target = MergeOperation.merge_source_ref(repo, target_branch, project_name=project_name)
        remote = repo.remote_name or "origin"

        def plan(status: str, paths: tuple[Path, ...] = ()) -> MergeIntoPlan:
            return MergeIntoPlan(repo.name, source, target, status, paths)

        if not git_runner.branch_known(repo.absolute_path, source, remote=remote):
            return plan("no-source")
        if not git_runner.branch_known(repo.absolute_path, target, remote=remote):
            return plan("no-target")
        history = MemoryMergeOperation.tree_status(repo, git_runner, source, target)
        if history in ("kept", "unrelated"):
            return plan(history)
        if history == "up-to-date" or git_runner.is_ancestor(repo.absolute_path, source, target):
            return plan("already-merged")
        if git_runner.is_ancestor(repo.absolute_path, target, source):
            return plan("fast-forward")
        check = git_runner.can_merge_cleanly(repo.absolute_path, source, into=target)
        if not check.is_clean:
            return plan("conflicts", tuple(check.conflicting_paths))
        return plan("merge")

    @staticmethod
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
        ``.agent/.local/.dev/DevTickets/…_SelfHostedMerge_DevPlanTicket.md`` §2.

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
        Preflight.assert_ready(tree)
        Preflight.run_preflight_checks(
            tree,
            git_runner,
            require_clean=True,
            operation_name="merge",
            scope=scope,
            check_branch_alignment=False,
        )

        project_name = tree_project_name(tree)
        plans = [
            MergeOperation.merge_into_status(
                repo, git_runner, source_branch, target_branch, project_name=project_name
            )
            for repo in MergeOperation._iter_merge_scope_project_first(tree, scope)
        ]
        by_name = {plan.name: plan for plan in plans}

        blocked = [
            MergeOperation.describe_merge_conflict(plan.name, plan.source, plan.conflicting_paths)
            for plan in plans
            if plan.status == "conflicts"
        ]
        unrelated = [
            MergeOperation.describe_unrelated(plan.name, plan.source, plan.target) for plan in plans if plan.status == "unrelated"
        ]
        if blocked or unrelated:
            raise GitSyncError(
                "merge refused; nothing was checked out and nothing was merged: "
                + "; ".join(blocked + unrelated) + (MERGE_RESOLVE_HINT if blocked else "")
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
                MergeOperation._warn_branch_missing(
                    next(
                        r
                        for r in MergeOperation._iter_merge_scope_project_first(tree, scope)
                        if r.name == plan.name
                    ),
                    plan.source,
                    source_branch,
                )

        outcomes: list[MergeIntoPlan] = []
        for repo in MergeOperation._iter_merge_scope_project_first(tree, scope):
            plan = by_name[repo.name]
            if plan.status not in MERGE_INTO_ACTS and plan.status != "already-merged":
                outcomes.append(plan)
                continue
            # Checkout and merge, in that order, in this process. See the
            # docstring: splitting these is the bug this function exists to fix.
            git_runner.checkout(repo.absolute_path, plan.target)
            if plan.status == "kept":
                MemoryMergeOperation.keep_in_tree(repo, git_runner, plan.source, plan.target)
            elif plan.status in MERGE_INTO_ACTS:
                git_runner.merge(repo.absolute_path, plan.source, ff_only=ff_only, no_ff=no_ff)
            BranchOperation.refresh_repo_after_checkout(repo, plan.target, RefKind.BRANCH, git_runner)
            outcomes.append(plan)

        tree.recompute_tree_state()
        return tuple(outcomes)

    @staticmethod
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
        Preflight.assert_ready(tree)

        project_name = tree_project_name(tree)
        repos = list(MergeOperation._iter_merge_scope_project_first(tree, scope))
        merged: list[tuple[str, str]] = []

        for position, repo in enumerate(repos):
            source, status, conflicts = MergeOperation.merge_status(
                repo, git_runner, project_branch, project_name=project_name
            )
            if status in ("already-on-it", "no-branch", "up-to-date"):
                if status == "no-branch":
                    MergeOperation._warn_branch_missing(repo, source, project_branch)
                continue
            if status in ("conflicts", "unrelated"):
                # ("unrelated" stops here as it did when it was reported as a
                # conflict; what a stop there does next is MergeErgonomics.)
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
            if status == "kept":
                MemoryMergeOperation.keep_in_tree(repo, git_runner, source)
            else:
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


__all__ = [
    "MERGE_INTO_ACTS",
    "MERGE_RESOLVE_HINT",
    "MergeIntoPlan",
    "MergeOperation",
    "ResolveOutcome",
]

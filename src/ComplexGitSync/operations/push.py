"""push — Push, tag and release across the tree, leaf-first.

Ring: 2
Contract: Push, tag and release across the tree, leaf-first.
Imports: errors, git_repo, git_tree, orchestre, outcome, preflight, restart
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..errors import GitSyncError
from ..git_repo import (
    AccessProtocol,
    RefKind,
    RepoScope,
    WorkingRepo,
)
from ..git_tree import (
    WorkingGitTree,
    iter_tree_leaf_first,
)

if TYPE_CHECKING:
    from ..orchestre import GitRunner

from .outcome import RepoOutcome
from .preflight import Preflight
from .restart import RestartOperation


class PushOperation:
    """Push, tag and release across the tree, leaf-first."""

    @staticmethod
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
        ``repo.resolved_ref_name`` — unless that names a tag, which is never
        pushed as a branch: the branch Git has checked out is pushed instead,
        and a repository detached on a tag is refused before anything is
        pushed (ReleaseTags WP4).

        *force_access_protocol*, when given, rewrites each repo's remote to
        that protocol before pushing (``--force-protocol`` on ``push``).

        Returns one :class:`RepoOutcome` per repository pushed. ``acted`` is
        ``False`` for a repository that had nothing new to send — its branch
        was already level with its upstream — which is the common reason a push
        across a whole tree appears to do nothing.
        """
        Preflight.assert_ready(tree)
        Preflight.run_preflight_checks(
            tree,
            git_runner,
            require_clean=False,
            operation_name="push",
            scope=scope,
        )

        # Every repository's branch is decided before any is pushed, so a
        # refusal leaves the whole tree unpushed.
        repos = list(iter_tree_leaf_first(tree, scope))
        refs = {repo.repo_id: PushOperation._branch_to_push(repo, git_runner) for repo in repos}

        outcomes: list[RepoOutcome] = []
        for repo in repos:
            remote = repo.remote_name or "origin"
            RestartOperation.rewrite_remote_if_forced(git_runner, repo, remote, force_access_protocol)
            # Before the push, not after: ``push -u`` can only write the
            # remote-tracking ref the upstream resolves through if the refspec
            # already maps the branch being pushed.
            RestartOperation.repair_fetch_refspec(git_runner, repo, remote)
            current_branch, ref_name = refs[repo.repo_id]
            set_upstream = False
            if ref_name is not None and current_branch == ref_name:
                set_upstream = not git_runner.has_upstream(repo.absolute_path)
            # Read before pushing: afterwards the branch is level with its
            # upstream either way, so the count that says whether this push
            # carried anything only exists now.
            ahead = Preflight.commits_ahead_of_upstream(git_runner, repo)
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

    @staticmethod
    def _branch_to_push(repo: WorkingRepo, git_runner: GitRunner) -> tuple[str | None, str | None]:
        """``(current branch, ref to push)`` for *repo*; never a tag.

        The recorded ref wins when it is a branch — ``push`` has always pushed
        what the tree recorded. A recorded *tag* is what the old
        ``freeze-release`` wrote into every repository it released
        (ReleaseTags R1): pushing it sent nothing and reported success. The
        branch Git has checked out is pushed instead; with none, there is no
        branch to push and the repository is refused by name.
        """
        current_branch = git_runner.current_branch(repo.absolute_path)
        recorded = repo.resolved_ref_name
        recorded_is_tag = repo.resolved_ref_kind is RefKind.TAG or (
            recorded is not None
            and recorded != current_branch
            and not git_runner.local_branch_exists(repo.absolute_path, recorded)
            and git_runner.tag_exists(repo.absolute_path, recorded)
        )
        if recorded and not recorded_is_tag:
            return current_branch, recorded
        if current_branch is None:
            raise GitSyncError(
                f"push refused; no repository was pushed: {repo.name} is detached at "
                f"{recorded or 'a commit'}, so there is no branch to push. "
                "Check out a branch first (cgitsync checkout <branch>)."
            )
        return current_branch, current_branch

    @staticmethod
    def tag_tree(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        tag_name: str,
        *,
        scope: RepoScope = RepoScope.WRITABLE,
    ) -> None:
        """Create and push *tag_name* across the tree, leaf-first.

        Tagging moves no ``HEAD``, so it changes no repository's recorded ref:
        every repository stays on the branch it was on, and only its commit is
        re-read (ReleaseTags D1). Writing the tag in as the current ref made
        the next ``push`` push the tag instead of the branch.
        """
        Preflight.assert_ready(tree)
        Preflight.run_preflight_checks(
            tree,
            git_runner,
            tag_name=tag_name,
            require_clean=True,
            operation_name="tag",
            # The same scope the loop below uses: a read-only configuration repo
            # is not tagged, so its state cannot block this.
            scope=scope,
        )
        # WRITABLE, not ALL: a tag is created *and pushed* in the same step, and
        # a read-only configuration repo is one this project may not push to.
        # Reproducibility does not suffer -- the .gts snapshot records every
        # repo's exact commit_sha, read-only ones included, so the tree is
        # rebuilt from the snapshot rather than from tags.
        for repo in iter_tree_leaf_first(tree, scope):
            git_runner.create_tag(repo.absolute_path, tag_name)
            remote = repo.remote_name or "origin"
            git_runner.push(repo.absolute_path, remote=remote, ref_name=tag_name)
            repo.commit_sha = git_runner.rev_parse_head(repo.absolute_path)

        tree.recompute_tree_state()

    @staticmethod
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

        Like :meth:`tag_tree`, it changes no repository's recorded ref: the
        tree stays on its branches, and the release is named by the tag and
        by the ledger's ``release`` row (ReleaseTags D1). When this step's own
        commit made a new commit, the branch is pushed with the tag, so the
        remote branch is never left behind the release it carries (R5).
        """
        Preflight.assert_ready(tree)
        Preflight.run_preflight_checks(
            tree,
            git_runner,
            tag_name=tag_name,
            require_clean=False,
            operation_name="freeze_release",
            scope=scope,
        )
        commit_message = message or f"freeze release {tag_name}"

        # The default is WRITABLE for the same reason as tag_tree: this commits,
        # tags *and* pushes, none of which this project may do to a read-only
        # configuration repo. Their exact SHAs are still recorded in the
        # snapshot this freeze writes.
        for repo in iter_tree_leaf_first(tree, scope):
            if stage_all:
                git_runner.stage_all(repo.absolute_path)
            committed = git_runner.has_staged_changes(repo.absolute_path)
            if committed:
                git_runner.commit(repo.absolute_path, commit_message)
            git_runner.create_tag(repo.absolute_path, tag_name)
            remote = repo.remote_name or "origin"
            if committed:
                branch = git_runner.current_branch(repo.absolute_path)
                if branch is not None:
                    RestartOperation.repair_fetch_refspec(git_runner, repo, remote)
                    git_runner.push(
                        repo.absolute_path,
                        remote=remote,
                        ref_name=branch,
                        set_upstream=not git_runner.has_upstream(repo.absolute_path),
                    )
            git_runner.push(repo.absolute_path, remote=remote, ref_name=tag_name)
            repo.commit_sha = git_runner.rev_parse_head(repo.absolute_path)

        tree.recompute_tree_state()


__all__ = [
    "PushOperation",
]

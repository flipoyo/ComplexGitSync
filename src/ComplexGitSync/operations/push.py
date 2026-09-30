"""push — Push, tag and release across the tree, leaf-first.

Ring: 2
Contract: Push, tag and release across the tree, leaf-first.
Imports: git_repo, git_tree, orchestre, outcome, preflight, restart
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..git_repo import (
    AccessProtocol,
    RefKind,
    RepoLifecycleState,
    RepoScope,
    SyncState,
)
from ..git_tree import (
    WorkingGitTree,
    iter_tree,
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
        ``repo.resolved_ref_name``.

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

        outcomes: list[RepoOutcome] = []
        for repo in iter_tree_leaf_first(tree, scope):
            remote = repo.remote_name or "origin"
            RestartOperation.rewrite_remote_if_forced(git_runner, repo, remote, force_access_protocol)
            # Before the push, not after: ``push -u`` can only write the
            # remote-tracking ref the upstream resolves through if the refspec
            # already maps the branch being pushed.
            RestartOperation.repair_fetch_refspec(git_runner, repo, remote)
            current_branch = git_runner.current_branch(repo.absolute_path)
            ref_name = repo.resolved_ref_name or current_branch
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
    def tag_tree(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        tag_name: str,
        *,
        scope: RepoScope = RepoScope.WRITABLE,
    ) -> None:
        """Create and push *tag_name* across the tree, leaf-first."""
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
        PushOperation._propagate_tag(tree, tag_name)

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
        PushOperation._propagate_tag(tree, tag_name)
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

    @staticmethod
    def _propagate_tag(tree: WorkingGitTree, tag_name: str) -> None:
        """Propagate *tag_name* across *tree* from parent to leaves."""
        for repo in iter_tree(tree):
            repo.target_ref_kind = RefKind.TAG
            repo.target_ref_name = tag_name


__all__ = [
    "PushOperation",
]

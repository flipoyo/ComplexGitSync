"""preflight — Ask every repository in scope whether an operation may run before running it on any.

Ring: 2
Contract: Ask every repository in scope whether an operation may run before running it on any.
Imports: errors, git_repo, git_tree, git_tree_branch, orchestre
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import GitSyncError, TreeNotReadyError
from ..git_repo import (
    RepoScope,
    SyncState,
    WorkingRepo,
)
from ..git_tree import (
    ROOT_REPO_ID,
    WorkingGitTree,
    cgitsync_managed_state_paths,
    iter_tree_leaf_first,
)
from ..git_tree_branch import GitTreeBranches

if TYPE_CHECKING:
    from ..orchestre import GitRunner


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


class Preflight:
    """Ask every repository in scope whether an operation may run before running it on any."""

    @staticmethod
    def commits_ahead_of_upstream(git_runner: GitRunner, repo: WorkingRepo) -> int | None:
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

    @staticmethod
    def assert_ready(tree: WorkingGitTree) -> None:
        """Raise :exc:`~.errors.TreeNotReadyError` when *tree* is not READY."""
        if not tree.is_ready():
            raise TreeNotReadyError(
                f"Operation requires a READY tree; current state: {tree.lifecycle_state.value}"
            )

    @staticmethod
    def run_preflight_checks(
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
        diagnostics = Preflight._collect_preflight_diagnostics(
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
            warnings.warn(Preflight._format_preflight_warning(operation_name, warnings_only), stacklevel=2)
        if blocking:
            raise GitSyncError(Preflight._format_preflight_error(operation_name, blocking, warnings_only))

    @staticmethod
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
        diagnostics.extend(Preflight._collect_remote_diagnostics(tree, git_runner, scope=scope))
        if tag_name is not None:
            diagnostics.extend(
                Preflight._collect_tag_conflict_diagnostics(tree, git_runner, tag_name=tag_name, scope=scope)
            )
        diagnostics.extend(Preflight._collect_detached_head_diagnostics(tree, git_runner, scope=scope))
        diagnostics.extend(Preflight._collect_merge_diagnostics(tree, git_runner, scope=scope))
        if check_branch_alignment:
            diagnostics.extend(Preflight._collect_branch_alignment_diagnostics(tree, git_runner, scope=scope))
        diagnostics.extend(Preflight._collect_tracking_diagnostics(tree, git_runner, scope=scope))
        diagnostics.extend(
            Preflight._collect_commit_sha_diagnostics(
                tree,
                git_runner,
                blocking=False,
                scope=scope,
            )
        )
        diagnostics.extend(
            Preflight._collect_worktree_diagnostics(tree, git_runner, require_clean=require_clean, scope=scope)
        )
        return diagnostics

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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

    @staticmethod
    def _collect_merge_diagnostics(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        *,
        scope: RepoScope = RepoScope.ALL,
    ) -> list[PreflightDiagnostic]:
        merges: list[PreflightDiagnostic] = []
        for repo in iter_tree_leaf_first(tree, scope):
            if git_runner.has_unresolved_merge(repo.absolute_path):
                # Merge is in progress. Check if there are still unmerged paths.
                # If not, all conflicts have been resolved and staged, and the
                # commit can go through to finish the merge.
                if git_runner.has_unmerged_paths(repo.absolute_path):
                    merges.append(
                        PreflightDiagnostic(
                            PreflightSeverity.BLOCKING_ERROR,
                            repo.name,
                            "repository has an unresolved merge in progress.",
                        )
                    )
        return merges

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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

    @staticmethod
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
            is_dirty = Preflight._has_managed_uncommitted_changes(tree, git_runner, repo)
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

    @staticmethod
    def _has_managed_uncommitted_changes(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        repo: WorkingRepo,
    ) -> bool:
        try:
            status_lines = git_runner.status_porcelain(repo.absolute_path)
            unmanaged_gitlinks = Preflight._unmanaged_gitlink_paths(tree, git_runner, repo)
        except (AttributeError, GitSyncError):
            return git_runner.has_uncommitted_changes(repo.absolute_path)
        ignored_paths = unmanaged_gitlinks | cgitsync_managed_state_paths(repo)
        if not ignored_paths:
            return bool(status_lines)
        return any(
            not Preflight._status_line_targets_any(line, ignored_paths)
            for line in status_lines
        )

    @staticmethod
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

    @staticmethod
    def _status_line_targets_any(status_line: str, paths: set[Path]) -> bool:
        status_path = Preflight._status_line_path(status_line)
        if status_path is None:
            return False
        return any(status_path == path or Preflight._path_is_relative_to(status_path, path) for path in paths)

    @staticmethod
    def _status_line_path(status_line: str) -> Path | None:
        if len(status_line) < 4:
            return None
        raw_path = status_line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.rsplit(" -> ", 1)[1]
        raw_path = raw_path.strip().strip('"')
        return Path(raw_path) if raw_path else None

    @staticmethod
    def _path_is_relative_to(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
        except ValueError:
            return False
        return True

    @staticmethod
    def _format_preflight_warning(
        operation_name: str,
        diagnostics: list[PreflightDiagnostic],
    ) -> str:
        details = "; ".join(f"{item.repo_name}: {item.message}" for item in diagnostics)
        return f"{operation_name} preflight warning: {details}"

    @staticmethod
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


__all__ = [
    "Preflight",
    "PreflightDiagnostic",
    "PreflightSeverity",
]

from __future__ import annotations

from pathlib import Path

from .errors import TreeNotReadyError
from .git_runner import GitRunner
from .models import LoadedSession, OperationResult, RepoOutcome, WorktreeState
from .registry import leaf_first_entries, parent_first_entries


def require_ready(session: LoadedSession, command_name: str) -> None:
    session.refresh_tree_state()
    if not session.is_ready:
        raise TreeNotReadyError(f"{command_name} requires a READY tree")


def commit_ready_tree(
    session: LoadedSession,
    message: str,
    *,
    stage_all: bool = True,
    git_runner: GitRunner | None = None,
) -> OperationResult:
    require_ready(session, "commit")
    runner = git_runner or GitRunner()
    outcomes = []
    pre = session.tree_state
    for entry in leaf_first_entries(session.registry):
        if not runner.is_git_repo(entry.absolute_path):
            continue
        runner.commit(entry.absolute_path, message, stage_all=stage_all)
        outcomes.append(RepoOutcome(entry.repo_id, entry.name, "committed"))
    post = session.refresh_tree_state()
    return OperationResult(pre_tree_state=pre, post_tree_state=post, per_repo_outcomes=tuple(outcomes))


def push_ready_tree(session: LoadedSession, *, git_runner: GitRunner | None = None) -> OperationResult:
    require_ready(session, "push")
    runner = git_runner or GitRunner()
    outcomes = []
    pre = session.tree_state
    for entry in leaf_first_entries(session.registry):
        if not runner.is_git_repo(entry.absolute_path):
            continue
        runner.push(entry.absolute_path, entry.remote_name or "origin")
        outcomes.append(RepoOutcome(entry.repo_id, entry.name, "pushed"))
    post = session.refresh_tree_state()
    return OperationResult(pre_tree_state=pre, post_tree_state=post, per_repo_outcomes=tuple(outcomes))


def tag_ready_tree(
    session: LoadedSession,
    tag_name: str,
    *,
    annotated: bool = True,
    git_runner: GitRunner | None = None,
) -> OperationResult:
    require_ready(session, "tag")
    runner = git_runner or GitRunner()
    _require_clean_worktrees(session)
    outcomes = []
    pre = session.tree_state
    for entry in parent_first_entries(session.registry):
        if not runner.is_git_repo(entry.absolute_path):
            continue
        runner.tag(entry.absolute_path, tag_name, annotated=annotated)
        outcomes.append(RepoOutcome(entry.repo_id, entry.name, "tagged"))
    post = session.refresh_tree_state()
    return OperationResult(pre_tree_state=pre, post_tree_state=post, per_repo_outcomes=tuple(outcomes))


def snapshot_path(root_path: Path, project_name: str) -> Path:
    return (root_path / ".cgitsync" / "state" / f"{project_name}.gts").resolve()


def _require_clean_worktrees(session: LoadedSession) -> None:
    for entry in parent_first_entries(session.registry):
        if entry.worktree_state == WorktreeState.DIRTY:
            raise TreeNotReadyError(f"tag requires clean worktrees: {entry.name}")

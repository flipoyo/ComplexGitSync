"""Unit tests for ``git_tree_branch`` — the tree's branch and who follows it.

The four call sites this module replaced each asked git for every
repository's branch and then compared it to the root's. These tests pin the
answers they used to compute separately: which branch the tree is on, what
a private/local repository targets under it, which repositories deviate,
and what happens when the root is detached, missing, or unreadable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_repo import (
    AccessProtocol,
    DiscoveryState,
    GitProvider,
    NodeType,
    RefKind,
    RepoLifecycleState,
    RepoScope,
    SyncState,
    WorkingRepo,
)
from ComplexGitSync.git_tree import WorkingGitTree, propagate_privacy
from ComplexGitSync.git_tree_branch import GitTreeBranches, tree_project_name

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _BranchReadingRunner:
    """The one method this module needs, plus a count of how often it ran."""

    def __init__(self, branches: dict[Path, str | None], unreadable: set[Path] | None = None):
        self._branches = branches
        self._unreadable = unreadable or set()
        self.reads: list[Path] = []

    def current_branch(self, repo_path: Path | str) -> str | None:
        path = Path(repo_path)
        self.reads.append(path)
        if path in self._unreadable:
            raise GitSyncError(f"cannot read branch of {path}")
        return self._branches.get(path)


def _entry(
    repo_id: str,
    name: str,
    node_type: NodeType,
    parent_id: str | None,
    absolute_path: Path,
    *,
    private: bool = False,
    writable: bool = False,
) -> WorkingRepo:
    return WorkingRepo(
        repo_id=repo_id,
        name=name,
        node_type=node_type,
        parent_id=parent_id,
        absolute_path=absolute_path,
        relative_path=Path(".") if parent_id is None else Path(name),
        current_ref_kind=RefKind.BRANCH,
        current_ref_name="main",
        target_ref_kind=RefKind.BRANCH,
        target_ref_name="main",
        resolved_ref_kind=RefKind.BRANCH,
        resolved_ref_name="main",
        commit_sha="abc123",
        repo_lifecycle_state=RepoLifecycleState.READY,
        sync_state=SyncState.ALIGNED,
        discovery_state=DiscoveryState.RESOLVED,
        is_reachable=True,
        gitprovider=GitProvider.GITHUB,
        project_owner_name="owner",
        project_name=name,
        access_protocol=AccessProtocol.SSH,
        default_branch="main",
        remote_name="origin",
        private=private,
        writable=writable,
    )


@pytest.fixture
def tree(tmp_path: Path) -> WorkingGitTree:
    """root (project "Demo") + a plain leaf + private/local + private/distant."""
    registry = WorkingGitTree()
    root = _entry("root", "Demo", NodeType.ROOT, None, tmp_path)
    root.project_name = "Demo"
    registry.add(root)
    registry.add(_entry("root:docs", "docs", NodeType.LEAF, "root", tmp_path / "docs"))
    registry.add(
        _entry(
            "root:.localSpec",
            ".localSpec",
            NodeType.LEAF,
            "root",
            tmp_path / ".localSpec",
            private=True,
            writable=True,
        )
    )
    registry.add(
        _entry(
            "root:.agentSpec",
            ".agentSpec",
            NodeType.LEAF,
            "root",
            tmp_path / ".agentSpec",
            private=True,
        )
    )
    propagate_privacy(registry)
    registry.recompute_tree_state()
    return registry


def _aligned_runner(tree: WorkingGitTree, project_branch: str) -> _BranchReadingRunner:
    """Every repository sitting exactly where *project_branch* says it should."""
    branches = GitTreeBranches(tree)
    return _BranchReadingRunner(
        {
            repo.absolute_path: branches.target(repo, project_branch).name
            for repo in tree.values()
        }
    )


# ---------------------------------------------------------------------------
# tree_project_name
# ---------------------------------------------------------------------------


def test_tree_project_name_reads_the_root(tree):
    assert tree_project_name(tree) == "Demo"


def test_tree_project_name_is_none_without_a_root():
    assert tree_project_name(WorkingGitTree()) is None


# ---------------------------------------------------------------------------
# tree_branch — the value `cgitsync_branch` prints
# ---------------------------------------------------------------------------


def test_tree_branch_is_the_roots_branch(tree, tmp_path):
    runner = _aligned_runner(tree, "apoub")

    branches = GitTreeBranches(tree, runner)

    assert branches.tree_branch == "apoub"
    assert branches.is_detached is False


def test_tree_branch_is_none_and_detached_for_a_parked_root(tree, tmp_path):
    runner = _BranchReadingRunner({tmp_path: None})

    branches = GitTreeBranches(tree, runner)

    assert branches.tree_branch is None
    assert branches.is_detached is True


def test_tree_branch_is_none_and_not_detached_without_a_root():
    branches = GitTreeBranches(WorkingGitTree(), _BranchReadingRunner({}))

    assert branches.tree_branch is None
    assert branches.is_detached is False


def test_tree_branch_is_none_and_not_detached_when_git_cannot_answer(tree, tmp_path):
    runner = _BranchReadingRunner({}, unreadable={tmp_path})

    branches = GitTreeBranches(tree, runner)

    # Unreadable is not detached: one is "parked on a commit", the other is
    # "nobody could tell", and `cgitsync_branch` prints them differently.
    assert branches.tree_branch is None
    assert branches.is_detached is False


# ---------------------------------------------------------------------------
# target / expected — the rule, asked once
# ---------------------------------------------------------------------------


def test_target_applies_the_private_local_naming_rule(tree):
    branches = GitTreeBranches(tree)
    local = tree.get("root:.localSpec")
    distant = tree.get("root:.agentSpec")
    docs = tree.get("root:docs")

    assert branches.target(docs, "apoub").name == "apoub"
    assert branches.target(local, "apoub").name == "Demo_apoub"
    assert branches.target(local, "main").name == "Demo"
    assert branches.target(distant, "apoub").name == "main"


def test_expected_measures_against_the_trees_own_branch(tree):
    runner = _aligned_runner(tree, "apoub")

    branches = GitTreeBranches(tree, runner)

    assert branches.expected(tree.get("root:docs")) == "apoub"
    assert branches.expected(tree.get("root:.localSpec")) == "Demo_apoub"


def test_expected_is_none_when_there_is_nothing_to_measure_against(tree, tmp_path):
    branches = GitTreeBranches(tree, _BranchReadingRunner({tmp_path: None}))

    assert branches.expected(tree.get("root:docs")) is None


def test_target_needs_no_runner(tree):
    """`propagate_global_branch` builds an instance from the tree alone."""
    branches = GitTreeBranches(tree)

    assert branches.target(tree.get("root:.localSpec"), "apoub").name == "Demo_apoub"
    with pytest.raises(ValueError, match="GitRunner"):
        branches.observed(tree.get("root:docs"))


# ---------------------------------------------------------------------------
# deviations
# ---------------------------------------------------------------------------


def test_an_aligned_tree_has_no_deviations(tree):
    branches = GitTreeBranches(tree, _aligned_runner(tree, "apoub"))

    assert branches.deviations() == ()


def test_a_private_local_repo_on_its_derived_branch_is_not_a_deviation(tree, tmp_path):
    # The whole point of the derived name: the tree is on 'apoub' and the
    # configuration repository is on 'Demo_apoub', and that is correct.
    runner = _BranchReadingRunner(
        {
            tmp_path: "apoub",
            tmp_path / "docs": "apoub",
            tmp_path / ".localSpec": "Demo_apoub",
            tmp_path / ".agentSpec": "main",
        }
    )

    assert GitTreeBranches(tree, runner).deviations() == ()


def test_a_repo_left_behind_is_reported_with_both_branches(tree, tmp_path):
    runner = _BranchReadingRunner(
        {
            tmp_path: "apoub",
            tmp_path / "docs": "main",
            tmp_path / ".localSpec": "Demo_apoub",
            tmp_path / ".agentSpec": "main",
        }
    )

    deviations = GitTreeBranches(tree, runner).deviations()

    assert len(deviations) == 1
    assert deviations[0].repo.name == "docs"
    assert deviations[0].expected == "apoub"
    assert deviations[0].observed == "main"


def test_a_detached_repo_is_not_a_deviation(tree, tmp_path):
    runner = _BranchReadingRunner(
        {
            tmp_path: "apoub",
            tmp_path / "docs": None,
            tmp_path / ".localSpec": "Demo_apoub",
            tmp_path / ".agentSpec": "main",
        }
    )

    assert GitTreeBranches(tree, runner).deviations() == ()


def test_nothing_deviates_when_the_root_is_detached(tree, tmp_path):
    runner = _BranchReadingRunner({tmp_path: None, tmp_path / "docs": "somewhere-else"})

    assert GitTreeBranches(tree, runner).deviations() == ()


def test_deviations_honour_the_scope(tree, tmp_path):
    runner = _BranchReadingRunner(
        {
            tmp_path: "apoub",
            tmp_path / "docs": "main",
            tmp_path / ".localSpec": "Demo",
            tmp_path / ".agentSpec": "main",
        }
    )

    branches = GitTreeBranches(tree, runner)

    assert [d.repo.name for d in branches.deviations(scope=RepoScope.PROJECT)] == ["docs"]
    assert [d.repo.name for d in branches.deviations(scope=RepoScope.PRIVATE)] == [
        ".localSpec"
    ]


def test_an_unreadable_repo_stops_a_gate_and_is_skipped_by_a_report(tree, tmp_path):
    runner = _BranchReadingRunner(
        {tmp_path: "apoub", tmp_path / ".localSpec": "Demo_apoub", tmp_path / ".agentSpec": "main"},
        unreadable={tmp_path / "docs"},
    )

    branches = GitTreeBranches(tree, runner)

    with pytest.raises(GitSyncError):
        branches.deviations()
    assert branches.deviations(ignore_unreadable=True) == ()


# ---------------------------------------------------------------------------
# The cache — one git call per repository per pass
# ---------------------------------------------------------------------------


def test_each_repository_is_read_once_per_instance(tree, tmp_path):
    runner = _aligned_runner(tree, "main")
    branches = GitTreeBranches(tree, runner)

    branches.tree_branch
    branches.deviations()
    for repo in tree.values():
        branches.observed(repo)

    assert sorted(runner.reads) == sorted(repo.absolute_path for repo in tree.values())


def test_refresh_asks_git_again(tree, tmp_path):
    runner = _aligned_runner(tree, "main")
    branches = GitTreeBranches(tree, runner)

    assert branches.tree_branch == "main"
    branches.refresh()
    assert branches.tree_branch == "main"

    assert runner.reads.count(tmp_path) == 2

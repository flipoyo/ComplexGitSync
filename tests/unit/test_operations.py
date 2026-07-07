"""Unit tests for Tier 2 operations: checkout_tree, commit_tree, push_tree,
propagate_global_branch, create_global_branch, restart_tree, and the
ComplexGitSyncClient façade methods checkout / commit / push.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError, TreeNotReadyError
from ComplexGitSync.git_repo import (
    NodeType,
    RefKind,
    RepoLifecycleState,
    SyncState,
)
from ComplexGitSync.git_tree import WorkingGitTree, GitTree, TreeLifecycleState
from ComplexGitSync.operations import (
    BranchTopologyConflict,
    BranchTopologyReport,
    add_tree,
    branch_tree,
    checkout_tree,
    commit_tree,
    create_global_branch,
    freeze_release_tree,
    propagate_global_branch,
    push_tree,
    restart_tree,
    tag_tree,
    validate_branch_topology,
)
from ComplexGitSync.orchestre import ComplexGitSyncClient, GitRunner


# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------


def _make_ready_registry(tmp_path: Path) -> WorkingGitTree:
    """Build a minimal 3-entry READY registry backed by real directories."""
    from ComplexGitSync.git_repo import (
        AccessProtocol,
        DiscoveryState,
        GitProvider,
        WorkingRepo,
    )

    root_path = tmp_path / "project"
    leaf_path = tmp_path / "project" / "deps" / "leaf"
    root_path.mkdir(parents=True)
    leaf_path.mkdir(parents=True)

    def _create_ready_entry(repo_id, name, node_type, parent_id, absolute_path):
        relative_path = (
            Path(".")
            if parent_id is None
            else absolute_path.relative_to(root_path)
        )
        return WorkingRepo(
            repo_id=repo_id,
            name=name,
            node_type=node_type,
            parent_id=parent_id,
            absolute_path=absolute_path,
            relative_path=relative_path,
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
        )

    registry = WorkingGitTree()
    registry.add(
        _create_ready_entry("root", "project", NodeType.ROOT, None, root_path)
    )
    registry.add(
        _create_ready_entry("root:deps/leaf", "leaf", NodeType.LEAF, "root", leaf_path)
    )
    registry.recompute_tree_state()
    assert registry.is_ready(), "Fixture must produce a READY registry"
    return registry


def _make_deep_ready_registry(tmp_path: Path) -> WorkingGitTree:
    """Build a 3-level READY registry: root → middle → sub-leaf."""
    from ComplexGitSync.git_repo import (
        AccessProtocol,
        DiscoveryState,
        GitProvider,
        WorkingRepo,
    )

    root_path = tmp_path / "deep"
    middle_path = tmp_path / "deep" / "middle"
    sub_path = tmp_path / "deep" / "middle" / "sub"
    for p in (root_path, middle_path, sub_path):
        p.mkdir(parents=True)

    def _entry(repo_id, name, node_type, parent_id, absolute_path, parent_root):
        return WorkingRepo(
            repo_id=repo_id,
            name=name,
            node_type=node_type,
            parent_id=parent_id,
            absolute_path=absolute_path,
            relative_path=(
                Path(".")
                if parent_id is None
                else absolute_path.relative_to(parent_root)
            ),
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
        )

    registry = WorkingGitTree()
    registry.add(_entry("root", "deep", NodeType.ROOT, None, root_path, root_path))
    registry.add(_entry("root:middle", "middle", NodeType.PARENT, "root", middle_path, root_path))
    registry.add(_entry("root:middle:sub", "sub", NodeType.LEAF, "root:middle", sub_path, middle_path))
    registry.recompute_tree_state()
    assert registry.is_ready(), "Deep fixture must produce a READY registry"
    return registry


def _mark_all_children_as_submodules(
    registry: WorkingGitTree,
    runner: "_FakeGitRunnerForOperations",
) -> None:
    for entry in registry.values():
        if entry.parent_id is None:
            continue
        parent = registry.get(entry.parent_id)
        relative_path = entry.absolute_path.relative_to(parent.absolute_path)
        if relative_path != Path("."):
            runner.add_submodule_link(parent.absolute_path, relative_path)


class _FakeGitRunnerForOperations:
    """Minimal fake GitRunner for operation unit tests.

    Tracks calls and simulates branch existence.
    """

    def __init__(self, *, existing_local_branches: dict[Path, set[str]] | None = None):
        # {path: set of branch names that exist locally}
        self._local_branches: dict[Path, set[str]] = existing_local_branches or {}
        self.created: list[tuple[Path, str]] = []
        self.checked_out: list[tuple[Path, str]] = []
        self.staged: list[Path] = []
        self.committed: list[tuple[Path, str]] = []
        self.pushed: list[tuple[Path, str, str | None]] = []
        self.pushed_with_upstream: list[tuple[Path, str, str | None]] = []
        self.pulled: list[tuple[Path, str, str | None]] = []
        self.tagged: list[tuple[Path, str]] = []
        self.cloned: list[tuple[str, Path, str]] = []
        self.updated_submodules: list[tuple[Path, Path]] = []
        self.command_order: list[tuple[str, Path]] = []
        self._staged_changes: dict[Path, bool] = {}
        self._unstaged_changes: dict[Path, bool] = {}
        self._extra_status_lines: dict[Path, list[str]] = {}
        self._shas: dict[Path, str] = {}
        self._current_branches: dict[Path, str | None] = {}
        self._existing_remotes: dict[Path, set[str]] = {}
        self._existing_tags: dict[Path, set[str]] = {}
        self._submodule_links: set[tuple[Path, Path]] = set()
        self._gitlinks: dict[Path, set[Path]] = {}
        self._tracking_states: dict[Path, SyncState | None] = {}
        self._has_upstream: dict[Path, bool] = {}
        self._merge_in_progress: dict[Path, bool] = {}

    # --- branch / checkout ---
    def current_branch(self, repo_path: Path | str) -> str | None:
        return self._current_branches.get(Path(repo_path), "main")
    def local_branch_exists(self, repo_path: Path | str, branch: str) -> bool:
        return branch in self._local_branches.get(Path(repo_path), set())

    def create_branch(self, repo_path: Path | str, branch: str) -> None:
        path = Path(repo_path)
        self._local_branches.setdefault(path, set()).add(branch)
        self.created.append((path, branch))

    def checkout(self, repo_path: Path | str, branch: str) -> None:
        self.checked_out.append((Path(repo_path), branch))

    def clone(self, remote_url: str, destination: Path | str, *, branch: str) -> None:
        destination_path = Path(destination)
        destination_path.mkdir(parents=True, exist_ok=True)
        self.cloned.append((remote_url, destination_path, branch))

    def rev_parse_head(self, repo_path: Path | str) -> str:
        return self._shas.get(Path(repo_path), "abc123")

    # --- commit ---
    def stage_all(self, repo_path: Path | str) -> None:
        path = Path(repo_path)
        self.staged.append(path)
        # Simulate: staging always marks the repo as having staged changes
        self._staged_changes[path] = True

    def has_staged_changes(self, repo_path: Path | str) -> bool:
        return self._staged_changes.get(Path(repo_path), False)

    def has_uncommitted_changes(self, repo_path: Path | str) -> bool:
        path = Path(repo_path)
        return self._staged_changes.get(path, False) or self._unstaged_changes.get(path, False)

    def status_porcelain(self, repo_path: Path | str) -> list[str]:
        path = Path(repo_path)
        lines: list[str] = []
        if self._staged_changes.get(path, False):
            lines.append("A  staged.txt")
        if self._unstaged_changes.get(path, False):
            lines.append(" M dirty.txt")
        lines.extend(self._extra_status_lines.get(path, []))
        return lines

    def commit(self, repo_path: Path | str, message: str) -> None:
        path = Path(repo_path)
        self.committed.append((path, message))
        # After commit, no more staged changes
        self._staged_changes[path] = False

    # --- push ---
    def push(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
        set_upstream: bool = False,
    ) -> None:
        path = Path(repo_path)
        self.pushed.append((path, remote, ref_name))
        if set_upstream:
            self.pushed_with_upstream.append((path, remote, ref_name))

    def has_upstream(self, repo_path: Path | str) -> bool:
        return self._has_upstream.get(Path(repo_path), True)

    def pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None:
        path = Path(repo_path)
        self.pulled.append((path, remote, ref_name))
        self.command_order.append(("pull", path))

    def update_submodule(self, repo_path: Path | str, relative_path: Path | str) -> None:
        parent_path = Path(repo_path)
        rel_path = Path(relative_path)
        self.updated_submodules.append((parent_path, rel_path))
        self.command_order.append(("submodule_update", parent_path / rel_path))

    def set_staged(self, repo_path: Path | str, value: bool) -> None:
        """Helper: manually set whether a repo has staged changes."""
        path = Path(repo_path)
        self._staged_changes[path] = value
        if value:
            self._unstaged_changes[path] = False

    def set_unstaged(self, repo_path: Path | str, value: bool) -> None:
        """Helper: manually set whether a repo has unstaged changes."""
        self._unstaged_changes[Path(repo_path)] = value

    def create_tag(self, repo_path: Path | str, tag_name: str) -> None:
        path = Path(repo_path)
        self.tagged.append((path, tag_name))
        self._existing_tags.setdefault(path, set()).add(tag_name)

    def remote_exists(self, repo_path: Path | str, remote: str = "origin") -> bool:
        path = Path(repo_path)
        remotes = self._existing_remotes.get(path)
        if remotes is None:
            return remote == "origin"
        return remote in remotes

    def tag_exists(self, repo_path: Path | str, tag_name: str) -> bool:
        return tag_name in self._existing_tags.get(Path(repo_path), set())

    def has_unresolved_merge(self, repo_path: Path | str) -> bool:
        return self._merge_in_progress.get(Path(repo_path), False)

    def branch_tracking_state(self, repo_path: Path | str) -> SyncState | None:
        return self._tracking_states.get(Path(repo_path), SyncState.ALIGNED)

    def is_submodule(self, repo_path: Path | str, relative_path: Path | str) -> bool:
        return (Path(repo_path), Path(relative_path)) in self._submodule_links

    def set_remote_exists(self, repo_path: Path | str, remote: str, exists: bool) -> None:
        path = Path(repo_path)
        remotes = self._existing_remotes.setdefault(path, set())
        if exists:
            remotes.add(remote)
        else:
            remotes.discard(remote)

    def add_existing_tag(self, repo_path: Path | str, tag_name: str) -> None:
        self._existing_tags.setdefault(Path(repo_path), set()).add(tag_name)

    def add_submodule_link(self, repo_path: Path | str, relative_path: Path | str) -> None:
        parent = Path(repo_path)
        relative = Path(relative_path)
        self._submodule_links.add((parent, relative))
        self._gitlinks.setdefault(parent, set()).add(relative)

    def add_gitlink(self, repo_path: Path | str, relative_path: Path | str) -> None:
        self._gitlinks.setdefault(Path(repo_path), set()).add(Path(relative_path))

    def add_status_line(self, repo_path: Path | str, line: str) -> None:
        self._extra_status_lines.setdefault(Path(repo_path), []).append(line)

    def tracked_gitlink_paths(self, repo_path: Path | str) -> set[Path]:
        return set(self._gitlinks.get(Path(repo_path), set()))

    def set_tracking_state(self, repo_path: Path | str, state: SyncState | None) -> None:
        self._tracking_states[Path(repo_path)] = state

    def set_unresolved_merge(self, repo_path: Path | str, value: bool) -> None:
        self._merge_in_progress[Path(repo_path)] = value


# ---------------------------------------------------------------------------
# add_tree
# ---------------------------------------------------------------------------


def test_add_tree_requires_ready_registry(tmp_path):
    registry = _make_ready_registry(tmp_path)
    for entry in registry.values():
        entry.commit_sha = None
    registry.recompute_tree_state()
    runner = _FakeGitRunnerForOperations()

    with pytest.raises(TreeNotReadyError):
        add_tree(registry, runner)


def test_add_tree_stages_all_repos_leaf_first(tmp_path):
    registry = _make_deep_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

    add_tree(registry, runner)

    expected_order = [
        tmp_path / "deep" / "middle" / "sub",
        tmp_path / "deep" / "middle",
        tmp_path / "deep",
    ]
    assert runner.staged == expected_order
    assert registry.recompute_tree_state() == TreeLifecycleState.READY


# ---------------------------------------------------------------------------
# restart_tree
# ---------------------------------------------------------------------------


def test_restart_tree_pulls_from_root_and_updates_children_as_submodules(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    root_path = tmp_path / "project"
    runner._current_branches[root_path] = "feature-restart"

    restart_tree(registry, runner)

    assert runner.pulled == [(root_path, "origin", "feature-restart")]
    assert runner.updated_submodules == [(root_path, Path("deps/leaf"))]
    assert registry.is_ready()


def test_restart_tree_propagates_branch_to_all_entries(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    root_path = tmp_path / "project"
    runner._current_branches[root_path] = "sync-branch"

    restart_tree(registry, runner)

    for entry in registry.values():
        assert entry.target_ref_name == "sync-branch"
        assert entry.current_ref_name == "sync-branch"


def test_restart_tree_runs_pull_and_submodule_updates_parent_first(tmp_path):
    registry = _make_deep_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    root_path = tmp_path / "deep"
    runner._current_branches[root_path] = "main"

    restart_tree(registry, runner)

    executed_paths = [path for _, path in runner.command_order]
    root_idx = executed_paths.index(tmp_path / "deep")
    middle_idx = executed_paths.index(tmp_path / "deep" / "middle")
    sub_idx = executed_paths.index(tmp_path / "deep" / "middle" / "sub")
    assert root_idx < middle_idx < sub_idx


def test_restart_tree_falls_back_to_resolved_ref_when_no_current_branch(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    root_path = tmp_path / "project"
    runner._current_branches[root_path] = None
    # Set a resolved ref name on the root entry as fallback
    registry.get("root").resolved_ref_name = "fallback-branch"

    restart_tree(registry, runner)

    assert runner.pulled == [(root_path, "origin", "fallback-branch")]


def test_restart_tree_fails_when_child_is_not_tracked_as_submodule(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

    with pytest.raises(GitSyncError, match="linked as submodules"):
        restart_tree(registry, runner)


def test_restart_tree_fails_when_child_path_is_outside_parent(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    rogue_path = tmp_path / "rogue-leaf"
    rogue_path.mkdir(parents=True)
    registry.get("root:deps/leaf").absolute_path = rogue_path

    with pytest.raises(GitSyncError, match="outside parent path"):
        restart_tree(registry, runner)


def test_restart_tree_fails_when_child_path_matches_parent(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = registry.get("root").absolute_path
    registry.get("root:deps/leaf").absolute_path = root_path

    with pytest.raises(GitSyncError, match="cannot share the exact parent path"):
        restart_tree(registry, runner)


# ---------------------------------------------------------------------------
# propagate_global_branch
# ---------------------------------------------------------------------------


def test_propagate_global_branch_updates_all_entries(tmp_path):
    registry = _make_ready_registry(tmp_path)

    propagate_global_branch(registry, "feature-x")

    for entry in registry.values():
        assert entry.target_ref_name == "feature-x"
        assert entry.target_ref_kind == RefKind.BRANCH


def test_propagate_global_branch_accepts_custom_ref_kind(tmp_path):
    registry = _make_ready_registry(tmp_path)

    propagate_global_branch(registry, "v1.2.3", ref_kind=RefKind.TAG)

    for entry in registry.values():
        assert entry.target_ref_name == "v1.2.3"
        assert entry.target_ref_kind == RefKind.TAG


def test_propagate_global_branch_does_not_require_ready_tree(tmp_path):
    """propagate_global_branch is a pure data update — no state gate required."""
    registry = _make_ready_registry(tmp_path)
    # Corrupt the state to simulate non-READY
    for entry in registry.values():
        entry.commit_sha = None
    registry.recompute_tree_state()
    assert not registry.is_ready()

    # Should NOT raise
    propagate_global_branch(registry, "any-branch")

    for entry in registry.values():
        assert entry.target_ref_name == "any-branch"


# ---------------------------------------------------------------------------
# GitTree.propagate_tag
# ---------------------------------------------------------------------------


def test_git_tree_propagate_tag_updates_all_entries(tmp_path):
    registry = _make_ready_registry(tmp_path)

    GitTree().propagate_tag(registry, "v1.2.3")

    for entry in registry.values():
        assert entry.target_ref_kind == RefKind.TAG
        assert entry.target_ref_name == "v1.2.3"


# ---------------------------------------------------------------------------
# create_global_branch
# ---------------------------------------------------------------------------


def test_create_global_branch_creates_only_missing_branches(tmp_path):
    registry = _make_ready_registry(tmp_path)
    root_path = registry.get("root").absolute_path
    leaf_path = registry.get("root:deps/leaf").absolute_path

    runner = _FakeGitRunnerForOperations(
        existing_local_branches={root_path: {"feature-x"}}  # root already has it
    )

    create_global_branch(registry, runner, "feature-x")

    # Only leaf should have had branch created
    assert (leaf_path, "feature-x") in runner.created
    assert (root_path, "feature-x") not in runner.created


def test_create_global_branch_creates_in_all_repos_when_none_exist(tmp_path):
    registry = _make_ready_registry(tmp_path)

    runner = _FakeGitRunnerForOperations()
    create_global_branch(registry, runner, "new-branch")

    created_paths = {path for path, _ in runner.created}
    for entry in registry.values():
        assert entry.absolute_path in created_paths


# ---------------------------------------------------------------------------
# branch_tree
# ---------------------------------------------------------------------------


def test_branch_tree_raises_when_not_ready(tmp_path):
    registry = _make_ready_registry(tmp_path)
    for entry in registry.values():
        entry.commit_sha = None
    registry.recompute_tree_state()

    runner = _FakeGitRunnerForOperations()
    with pytest.raises(TreeNotReadyError):
        branch_tree(registry, runner, "feature-x")


def test_branch_tree_creates_branch_without_checkout(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

    branch_tree(registry, runner, "feature-x")

    created_paths = {path for path, _ in runner.created}
    for entry in registry.values():
        assert entry.absolute_path in created_paths
        assert entry.target_ref_name == "feature-x"
        assert entry.target_ref_kind == RefKind.BRANCH
    assert runner.checked_out == []
    assert registry.recompute_tree_state() == TreeLifecycleState.READY


# ---------------------------------------------------------------------------
# checkout_tree
# ---------------------------------------------------------------------------


def test_checkout_tree_raises_when_not_ready(tmp_path):
    registry = _make_ready_registry(tmp_path)
    for entry in registry.values():
        entry.commit_sha = None
    registry.recompute_tree_state()

    runner = _FakeGitRunnerForOperations()
    with pytest.raises(TreeNotReadyError):
        checkout_tree(registry, runner, "feature-x")


def test_checkout_tree_propagates_creates_and_checks_out(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

    checkout_tree(registry, runner, "feature-x")

    # All repos must have been checked out
    checked_out_paths = {path for path, _ in runner.checked_out}
    for entry in registry.values():
        assert entry.absolute_path in checked_out_paths

    # Registry entries must reflect the new branch
    for entry in registry.values():
        assert entry.current_ref_name == "feature-x"
        assert entry.resolved_ref_name == "feature-x"
        assert entry.repo_lifecycle_state == RepoLifecycleState.READY
        assert entry.sync_state == SyncState.ALIGNED
        assert entry.fallback_applied is False

    # Tree must remain READY
    assert registry.recompute_tree_state() == TreeLifecycleState.READY


def test_checkout_tree_parent_first_ordering(tmp_path):
    """Root must be checked out before its leaf child."""
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

    checkout_tree(registry, runner, "feature-x")

    paths = [path for path, _ in runner.checked_out]
    root_idx = paths.index(registry.get("root").absolute_path)
    leaf_idx = paths.index(registry.get("root:deps/leaf").absolute_path)
    assert root_idx < leaf_idx


def test_checkout_tree_does_not_recreate_existing_branch(tmp_path):
    registry = _make_ready_registry(tmp_path)
    root_path = registry.get("root").absolute_path
    leaf_path = registry.get("root:deps/leaf").absolute_path

    runner = _FakeGitRunnerForOperations(
        existing_local_branches={root_path: {"feature-x"}, leaf_path: {"feature-x"}}
    )

    checkout_tree(registry, runner, "feature-x")

    assert runner.created == []


def test_checkout_tree_deep_hierarchy_parent_first(tmp_path):
    """Ordering must be root → middle → sub for a 3-level tree."""
    registry = _make_deep_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

    checkout_tree(registry, runner, "feature-x")

    paths = [path for path, _ in runner.checked_out]
    root_idx = paths.index(registry.get("root").absolute_path)
    mid_idx = paths.index(registry.get("root:middle").absolute_path)
    sub_idx = paths.index(registry.get("root:middle:sub").absolute_path)
    assert root_idx < mid_idx < sub_idx


def test_gittree_git_checkout_allows_direct_tree_manipulation(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    git_tree = GitTree()
    git_tree.git.bind_tree(registry)

    git_tree.git.checkout(runner, "feature-direct")

    for entry in registry.values():
        assert entry.current_ref_name == "feature-direct"
    assert registry.recompute_tree_state() == TreeLifecycleState.READY


def test_branch_tree_via_gittree_git_facade_creates_branch_without_checkout(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    git_tree = GitTree()
    git_tree.git.bind_tree(registry)

    git_tree.git.branch(runner, "feature-branch")

    assert runner.checked_out == []
    assert len(runner.created) == len(registry.values())
    for entry in registry.values():
        assert entry.target_ref_name == "feature-branch"
        assert entry.target_ref_kind == RefKind.BRANCH


# ---------------------------------------------------------------------------
# commit_tree
# ---------------------------------------------------------------------------


def test_commit_tree_raises_when_not_ready(tmp_path):
    registry = _make_ready_registry(tmp_path)
    for entry in registry.values():
        entry.commit_sha = None
    registry.recompute_tree_state()

    runner = _FakeGitRunnerForOperations()
    with pytest.raises(TreeNotReadyError):
        commit_tree(registry, runner, "wip")


def test_commit_tree_commits_leaf_before_root(tmp_path):
    """Leaf repos must be committed before their parents."""
    registry = _make_ready_registry(tmp_path)
    root_path = registry.get("root").absolute_path
    leaf_path = registry.get("root:deps/leaf").absolute_path

    runner = _FakeGitRunnerForOperations()
    runner.set_staged(root_path, True)
    runner.set_staged(leaf_path, True)

    commit_tree(registry, runner, "my commit", stage_all=False)

    committed_paths = [path for path, _ in runner.committed]
    assert committed_paths.index(leaf_path) < committed_paths.index(root_path)


def test_commit_tree_deep_hierarchy_leaf_first(tmp_path):
    """Ordering must be sub → middle → root for a 3-level tree."""
    registry = _make_deep_ready_registry(tmp_path)
    root_path = registry.get("root").absolute_path
    mid_path = registry.get("root:middle").absolute_path
    sub_path = registry.get("root:middle:sub").absolute_path

    runner = _FakeGitRunnerForOperations()
    for p in (root_path, mid_path, sub_path):
        runner.set_staged(p, True)

    commit_tree(registry, runner, "deep commit", stage_all=False)

    committed_paths = [path for path, _ in runner.committed]
    assert committed_paths.index(sub_path) < committed_paths.index(mid_path)
    assert committed_paths.index(mid_path) < committed_paths.index(root_path)


def test_commit_tree_skips_repos_with_no_staged_changes(tmp_path):
    registry = _make_ready_registry(tmp_path)
    leaf_path = registry.get("root:deps/leaf").absolute_path

    runner = _FakeGitRunnerForOperations()
    # Only leaf has staged changes
    runner.set_staged(leaf_path, True)

    commit_tree(registry, runner, "partial commit", stage_all=False)

    committed_paths = {path for path, _ in runner.committed}
    assert leaf_path in committed_paths
    assert registry.get("root").absolute_path not in committed_paths


def test_commit_tree_stages_all_when_stage_all_true(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

    commit_tree(registry, runner, "stage all commit", stage_all=True)

    staged_paths = set(runner.staged)
    for entry in registry.values():
        assert entry.absolute_path in staged_paths


def test_commit_tree_updates_commit_sha(tmp_path):
    registry = _make_ready_registry(tmp_path)
    leaf_path = registry.get("root:deps/leaf").absolute_path

    runner = _FakeGitRunnerForOperations()
    runner.set_staged(leaf_path, True)
    runner._shas[leaf_path] = "new-sha-leaf"

    commit_tree(registry, runner, "update sha", stage_all=False)

    assert registry.get("root:deps/leaf").commit_sha == "new-sha-leaf"


def test_commit_tree_tree_remains_ready(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

    commit_tree(registry, runner, "empty commit")

    assert registry.recompute_tree_state() == TreeLifecycleState.READY


# ---------------------------------------------------------------------------
# push_tree
# ---------------------------------------------------------------------------


def test_push_tree_raises_when_not_ready(tmp_path):
    registry = _make_ready_registry(tmp_path)
    for entry in registry.values():
        entry.commit_sha = None
    registry.recompute_tree_state()

    runner = _FakeGitRunnerForOperations()
    with pytest.raises(TreeNotReadyError):
        push_tree(registry, runner)


def test_push_tree_pushes_leaf_before_root(tmp_path):
    """Leaves must be pushed before their parents."""
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)

    push_tree(registry, runner)

    pushed_paths = [path for path, _, _ in runner.pushed]
    root_path = registry.get("root").absolute_path
    leaf_path = registry.get("root:deps/leaf").absolute_path
    assert pushed_paths.index(leaf_path) < pushed_paths.index(root_path)


def test_push_tree_deep_hierarchy_leaf_first(tmp_path):
    """Ordering must be sub → middle → root for a 3-level tree."""
    registry = _make_deep_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)

    push_tree(registry, runner)

    pushed_paths = [path for path, _, _ in runner.pushed]
    root_path = registry.get("root").absolute_path
    mid_path = registry.get("root:middle").absolute_path
    sub_path = registry.get("root:middle:sub").absolute_path
    assert pushed_paths.index(sub_path) < pushed_paths.index(mid_path)
    assert pushed_paths.index(mid_path) < pushed_paths.index(root_path)


def test_push_tree_updates_commit_sha(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)

    root_path = registry.get("root").absolute_path
    leaf_path = registry.get("root:deps/leaf").absolute_path
    runner._shas[root_path] = "pushed-root-sha"
    runner._shas[leaf_path] = "pushed-leaf-sha"

    push_tree(registry, runner)

    assert registry.get("root").commit_sha == "pushed-root-sha"
    assert registry.get("root:deps/leaf").commit_sha == "pushed-leaf-sha"


def test_push_tree_uses_remote_name_and_resolved_ref(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)

    push_tree(registry, runner)

    for path, remote, branch in runner.pushed:
        entry = next(e for e in registry.values() if e.absolute_path == path)
        assert remote == (entry.remote_name or "origin")
        assert branch == entry.resolved_ref_name


def test_push_tree_defaults_remote_to_origin_when_not_set(tmp_path):
    registry = _make_ready_registry(tmp_path)
    for entry in registry.values():
        entry.remote_name = None

    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    push_tree(registry, runner)

    for _, remote, _ in runner.pushed:
        assert remote == "origin"


def test_push_tree_sets_upstream_when_current_branch_is_unpublished(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)

    for entry in registry.values():
        entry.resolved_ref_name = "btest0"
        runner._current_branches[entry.absolute_path] = "btest0"
        runner._has_upstream[entry.absolute_path] = False

    push_tree(registry, runner)

    assert runner.pushed_with_upstream == runner.pushed
    for path, remote, branch in runner.pushed_with_upstream:
        entry = next(e for e in registry.values() if e.absolute_path == path)
        assert remote == (entry.remote_name or "origin")
        assert branch == "btest0"


def test_push_tree_does_not_set_upstream_when_upstream_exists(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)

    for entry in registry.values():
        runner._has_upstream[entry.absolute_path] = True

    push_tree(registry, runner)

    assert runner.pushed_with_upstream == []


def test_push_tree_tree_remains_ready(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)

    push_tree(registry, runner)

    assert registry.recompute_tree_state() == TreeLifecycleState.READY


# ---------------------------------------------------------------------------
# tag_tree / freeze_release_tree
# ---------------------------------------------------------------------------


def test_tag_tree_requires_ready_tree(tmp_path):
    registry = _make_ready_registry(tmp_path)
    for entry in registry.values():
        entry.commit_sha = None
    registry.recompute_tree_state()

    runner = _FakeGitRunnerForOperations()
    with pytest.raises(TreeNotReadyError):
        tag_tree(registry, runner, "v1.0.0")


def test_tag_tree_tags_and_pushes_leaf_first(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)

    tag_tree(registry, runner, "v1.0.0")

    root_path = registry.get("root").absolute_path
    leaf_path = registry.get("root:deps/leaf").absolute_path
    tagged_paths = [path for path, _ in runner.tagged]
    pushed_paths = [path for path, _, _ in runner.pushed]
    assert tagged_paths.index(leaf_path) < tagged_paths.index(root_path)
    assert pushed_paths.index(leaf_path) < pushed_paths.index(root_path)
    for entry in registry.values():
        assert entry.current_ref_kind == RefKind.TAG
        assert entry.current_ref_name == "v1.0.0"


def test_freeze_release_tree_commits_tags_and_pushes_leaf_first(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)

    freeze_release_tree(registry, runner, "release-1")

    root_path = registry.get("root").absolute_path
    leaf_path = registry.get("root:deps/leaf").absolute_path
    committed_paths = [path for path, _ in runner.committed]
    tagged_paths = [path for path, _ in runner.tagged]
    pushed_paths = [path for path, _, _ in runner.pushed]
    assert committed_paths.index(leaf_path) < committed_paths.index(root_path)
    assert tagged_paths.index(leaf_path) < tagged_paths.index(root_path)
    assert pushed_paths.index(leaf_path) < pushed_paths.index(root_path)
    assert registry.recompute_tree_state() == TreeLifecycleState.READY


def test_tag_tree_preflight_fails_when_tag_exists(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    root_path = registry.get("root").absolute_path
    runner.add_existing_tag(root_path, "v1.0.0")

    with pytest.raises(GitSyncError, match="tag 'v1.0.0' already exists"):
        tag_tree(registry, runner, "v1.0.0")


def test_tag_tree_preflight_fails_when_tree_is_dirty(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    leaf_path = registry.get("root:deps/leaf").absolute_path
    runner.set_staged(leaf_path, True)

    with pytest.raises(GitSyncError, match="worktree has uncommitted changes"):
        tag_tree(registry, runner, "v1.0.0")


def test_tag_tree_preflight_ignores_unmanaged_gitlink_dirty_state(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    root_path = registry.get("root").absolute_path
    runner.add_gitlink(root_path, "ComplexGitSync")
    runner.add_status_line(root_path, " M ComplexGitSync")

    tag_tree(registry, runner, "v1.0.0")

    assert registry.get("root").worktree_state == "CLEAN"


def test_tag_tree_preflight_ignores_managed_state_files(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    root_path = registry.get("root").absolute_path
    runner.add_status_line(root_path, " M .cgitsync/state/project.gts")
    runner.add_status_line(root_path, "?? project.lgr")

    tag_tree(registry, runner, "v1.0.0")

    assert registry.get("root").worktree_state == "CLEAN"


def test_commit_tree_preflight_warns_when_tree_is_dirty(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    leaf_path = registry.get("root:deps/leaf").absolute_path
    runner.set_unstaged(leaf_path, True)

    with pytest.warns(UserWarning, match="commit preflight warning: leaf: worktree has uncommitted changes"):
        commit_tree(registry, runner, "commit dirty tree")

    assert registry.get("root:deps/leaf").worktree_state == "DIRTY"


def test_push_tree_preflight_warns_when_branch_is_ahead(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    root_path = registry.get("root").absolute_path
    runner.set_tracking_state(root_path, SyncState.AHEAD)

    with pytest.warns(UserWarning, match="push preflight warning: project: local branch is ahead of its upstream"):
        push_tree(registry, runner)


def test_tag_tree_preflight_fails_when_child_is_not_submodule(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

    with pytest.raises(GitSyncError, match="not linked as submodule"):
        tag_tree(registry, runner, "v1.0.0")


def test_push_tree_preflight_fails_when_merge_is_unresolved(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    root_path = registry.get("root").absolute_path
    runner.set_unresolved_merge(root_path, True)

    with pytest.raises(GitSyncError, match="unresolved merge in progress"):
        push_tree(registry, runner)


def test_tag_tree_preflight_warns_when_commit_sha_does_not_match_head(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    root_path = registry.get("root").absolute_path
    runner._shas[root_path] = "actual-sha"
    registry.get("root").commit_sha = "recorded-sha"

    with pytest.warns(UserWarning, match="recorded commit_sha 'recorded-sha' does not match HEAD 'actual-sha'"):
        tag_tree(registry, runner, "v1.0.0")


def test_freeze_release_preflight_fails_when_branches_misalign(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    root_path = registry.get("root").absolute_path
    leaf_path = registry.get("root:deps/leaf").absolute_path
    runner._current_branches[root_path] = "main"
    runner._current_branches[leaf_path] = "feature-x"

    with pytest.raises(GitSyncError, match="branch misalignment"):
        freeze_release_tree(registry, runner, "release-1")


def test_tag_tree_preflight_fails_when_repo_is_detached(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    _mark_all_children_as_submodules(registry, runner)
    leaf_path = registry.get("root:deps/leaf").absolute_path
    runner._current_branches[leaf_path] = None

    with pytest.raises(GitSyncError, match="detached HEAD state"):
        tag_tree(registry, runner, "v1.0.0")


def test_git_runner_create_tag_default_does_not_force(monkeypatch):
    runner = GitRunner()
    captured: dict[str, object] = {}

    def _spy_run(_self, *args: str, cwd: Path | str | None = None):
        captured["args"] = args
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(GitRunner, "_run", _spy_run)

    runner.create_tag("/tmp/repo", "v1.2.3")

    assert captured["args"] == ("tag", "v1.2.3")


def test_git_runner_push_can_set_upstream(monkeypatch):
    runner = GitRunner()
    captured: dict[str, object] = {}

    def _spy_run(_self, *args: str, cwd: Path | str | None = None):
        captured["args"] = args
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(GitRunner, "_run", _spy_run)

    runner.push("/tmp/repo", remote="origin", ref_name="btest0", set_upstream=True)

    assert captured["args"] == ("push", "-u", "origin", "btest0")
    assert captured["cwd"] == "/tmp/repo"


def test_git_runner_file_transport_detection_handles_windows_paths():
    assert GitRunner._uses_file_transport("file:///tmp/remote.git") is True
    assert GitRunner._uses_file_transport("/tmp/remote.git") is True
    assert GitRunner._uses_file_transport(r"C:\tmp\remote.git") is True
    assert GitRunner._uses_file_transport("https://example.com/repo.git") is False
    assert GitRunner._uses_file_transport("git@github.com:owner/repo.git") is False


def test_git_runner_add_submodule_restores_tracked_gitmodules(monkeypatch, tmp_path):
    runner = GitRunner()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    calls: list[tuple[str, ...]] = []

    def _spy_run(_self, *args: str, cwd: Path | str | None = None):
        calls.append(args)
        return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout="", stderr="")

    def _fake_subprocess_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=".gitmodules", stderr="")

    monkeypatch.setattr(GitRunner, "_run", _spy_run)
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)

    runner.add_submodule(
        repo_path,
        "git@github.com:owner/child.git",
        Path("deps") / "child",
        branch="main",
    )

    assert calls[0] == ("checkout", "--", ".gitmodules")
    assert calls[1] == (
        "submodule",
        "add",
        "-b",
        "main",
        "git@github.com:owner/child.git",
        "deps/child",
    )


def test_git_runner_add_submodule_creates_gitmodules_when_missing(monkeypatch, tmp_path):
    runner = GitRunner()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    calls: list[tuple[str, ...]] = []

    def _spy_run(_self, *args: str, cwd: Path | str | None = None):
        calls.append(args)
        return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout="", stderr="")

    def _fake_subprocess_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")

    monkeypatch.setattr(GitRunner, "_run", _spy_run)
    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)

    runner.add_submodule(
        repo_path,
        "git@github.com:owner/child.git",
        Path("deps") / "child",
        branch="main",
    )

    assert (repo_path / ".gitmodules").is_file()
    assert (repo_path / ".gitmodules").read_text(encoding="utf-8") == ""
    assert calls == [
        (
            "submodule",
            "add",
            "-b",
            "main",
            "git@github.com:owner/child.git",
            "deps/child",
        )
    ]


# ---------------------------------------------------------------------------
# ComplexGitSyncClient.checkout / commit / push / tag / freeze_release / launch_release
# ---------------------------------------------------------------------------


def _make_client_with_ready_registry(tmp_path):
    """Build a ComplexGitSyncClient whose registry is already READY."""
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    client = ComplexGitSyncClient(git_runner=runner)
    client.registry = registry
    return client, runner


def test_client_checkout_requires_ready_tree(tmp_path):
    client, runner = _make_client_with_ready_registry(tmp_path)
    for entry in client.registry.values():
        entry.commit_sha = None
    client.registry.recompute_tree_state()

    with pytest.raises(TreeNotReadyError):
        client.checkout("feature-x")


def test_client_checkout_updates_registry_and_writes_gts(tmp_path):
    client, runner = _make_client_with_ready_registry(tmp_path)
    # Point root absolute_path somewhere writable for the .gts snapshot
    root_path = client.registry.get("root").absolute_path

    result = client.checkout("feature-x")

    # Registry updated
    for entry in result.values():
        assert entry.current_ref_name == "feature-x"
    assert result.recompute_tree_state() == TreeLifecycleState.READY

    # snapshot written inside root
    snapshot_dir = root_path / ".cgitsync" / "state"
    assert snapshot_dir.exists()
    gts_files = list(snapshot_dir.glob("*.gts"))
    assert gts_files, "Expected at least one .gts snapshot"


def test_client_checkout_delegates_to_gittree_git_checkout(tmp_path, monkeypatch):
    client, runner = _make_client_with_ready_registry(tmp_path)
    captured_call: dict[str, object] = {}

    def _spy_checkout(self, git_runner, branch_name, *, ref_kind=RefKind.BRANCH, tree=None):
        captured_call["tree"] = tree
        captured_call["git_runner"] = git_runner
        captured_call["branch_name"] = branch_name
        captured_call["ref_kind"] = ref_kind

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "checkout", _spy_checkout)

    result = client.checkout("feature-x")

    assert result is client.registry
    assert captured_call["tree"] is None
    assert captured_call["git_runner"] is runner
    assert captured_call["branch_name"] == "feature-x"
    assert captured_call["ref_kind"] == RefKind.BRANCH


def test_client_branch_delegates_to_gittree_git_branch(tmp_path, monkeypatch):
    client, runner = _make_client_with_ready_registry(tmp_path)
    captured_call: dict[str, object] = {}

    def _spy_branch(self, git_runner, branch_name, *, tree=None):
        captured_call["tree"] = tree
        captured_call["git_runner"] = git_runner
        captured_call["branch_name"] = branch_name

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "branch", _spy_branch)

    result = client.branch("feature-x")

    assert result is client.registry
    assert captured_call["tree"] is None
    assert captured_call["git_runner"] is runner
    assert captured_call["branch_name"] == "feature-x"


def test_client_commit_delegates_to_gittree_git_commit(tmp_path, monkeypatch):
    client, runner = _make_client_with_ready_registry(tmp_path)
    captured_call: dict[str, object] = {}

    def _spy_commit(self, git_runner, message, *, stage_all=True, tree=None):
        captured_call["git_runner"] = git_runner
        captured_call["message"] = message
        captured_call["stage_all"] = stage_all
        captured_call["tree"] = tree

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "commit", _spy_commit)

    result = client.commit("my-message", stage_all=False)

    assert result is client.registry
    assert captured_call == {
        "git_runner": runner,
        "message": "my-message",
        "stage_all": False,
        "tree": None,
    }


def test_client_add_delegates_to_gittree_git_add(tmp_path, monkeypatch):
    client, runner = _make_client_with_ready_registry(tmp_path)
    captured_call: dict[str, object] = {}

    def _spy_add(self, git_runner, *, tree=None):
        captured_call["git_runner"] = git_runner
        captured_call["tree"] = tree

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "add", _spy_add)

    result = client.add()

    assert result is client.registry
    assert captured_call == {"git_runner": runner, "tree": None}


def test_client_push_delegates_to_gittree_git_push(tmp_path, monkeypatch):
    client, runner = _make_client_with_ready_registry(tmp_path)
    captured_call: dict[str, object] = {}

    def _spy_push(self, git_runner, *, tree=None):
        captured_call["git_runner"] = git_runner
        captured_call["tree"] = tree

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "push", _spy_push)

    result = client.push()

    assert result is client.registry
    assert captured_call == {"git_runner": runner, "tree": None}


def test_client_tag_delegates_to_gittree_git_tag(tmp_path, monkeypatch):
    client, runner = _make_client_with_ready_registry(tmp_path)
    captured_call: dict[str, object] = {}

    def _spy_tag(self, git_runner, tag_name, *, tree=None):
        captured_call["git_runner"] = git_runner
        captured_call["tag_name"] = tag_name
        captured_call["tree"] = tree

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "tag", _spy_tag)

    result = client.tag("v1.2.3")

    assert result is client.registry
    assert captured_call == {
        "git_runner": runner,
        "tag_name": "v1.2.3",
        "tree": None,
    }


def test_client_freeze_release_delegates_to_gittree_git_freeze(tmp_path, monkeypatch):
    client, runner = _make_client_with_ready_registry(tmp_path)
    captured_call: dict[str, object] = {}

    def _spy_freeze(self, git_runner, tag_name, *, message=None, stage_all=True, tree=None):
        captured_call["git_runner"] = git_runner
        captured_call["tag_name"] = tag_name
        captured_call["message"] = message
        captured_call["stage_all"] = stage_all
        captured_call["tree"] = tree

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "freeze", _spy_freeze)

    result = client.freeze_release("release-1", message="msg", stage_all=False)

    assert result is client.registry
    assert captured_call == {
        "git_runner": runner,
        "tag_name": "release-1",
        "message": "msg",
        "stage_all": False,
        "tree": None,
    }


def test_client_commit_requires_ready_tree(tmp_path):
    client, runner = _make_client_with_ready_registry(tmp_path)
    for entry in client.registry.values():
        entry.commit_sha = None
    client.registry.recompute_tree_state()

    with pytest.raises(TreeNotReadyError):
        client.commit("wip")


def test_client_commit_delegates_to_commit_tree(tmp_path):
    client, runner = _make_client_with_ready_registry(tmp_path)
    leaf_path = client.registry.get("root:deps/leaf").absolute_path
    runner.set_staged(leaf_path, True)

    result = client.commit("test commit", stage_all=False)

    committed_paths = {path for path, _ in runner.committed}
    assert leaf_path in committed_paths
    assert result.recompute_tree_state() == TreeLifecycleState.READY


def test_client_push_requires_ready_tree(tmp_path):
    client, runner = _make_client_with_ready_registry(tmp_path)
    for entry in client.registry.values():
        entry.commit_sha = None
    client.registry.recompute_tree_state()

    with pytest.raises(TreeNotReadyError):
        client.push()


def test_client_push_delegates_to_push_tree(tmp_path):
    client, runner = _make_client_with_ready_registry(tmp_path)
    _mark_all_children_as_submodules(client.registry, runner)

    result = client.push()

    assert runner.pushed, "Expected at least one push call"
    assert result.recompute_tree_state() == TreeLifecycleState.READY


def test_client_tag_requires_ready_tree(tmp_path):
    client, runner = _make_client_with_ready_registry(tmp_path)
    for entry in client.registry.values():
        entry.commit_sha = None
    client.registry.recompute_tree_state()

    with pytest.raises(TreeNotReadyError):
        client.tag("v1.0.0")


def test_client_tag_delegates_to_tag_tree(tmp_path):
    client, runner = _make_client_with_ready_registry(tmp_path)
    _mark_all_children_as_submodules(client.registry, runner)

    result = client.tag("v1.0.0")

    assert runner.tagged, "Expected at least one tag call"
    assert result.recompute_tree_state() == TreeLifecycleState.READY


def test_client_freeze_release_delegates_and_writes_named_gts(tmp_path):
    import tomllib

    client, runner = _make_client_with_ready_registry(tmp_path)
    _mark_all_children_as_submodules(client.registry, runner)
    output_gts = tmp_path / "release.gts"

    result = client.freeze_release("release-1", output_gts=output_gts)

    assert runner.committed, "Expected commit calls during freeze_release"
    assert runner.tagged, "Expected tag calls during freeze_release"
    assert output_gts.exists()
    snapshot_data = tomllib.loads(output_gts.read_text(encoding="utf-8"))
    assert snapshot_data["freeze_manifest"]["schema_version"] == "1.0"
    assert snapshot_data["freeze_manifest"]["synchronized_ref_kind"] == "tag"
    assert snapshot_data["freeze_manifest"]["synchronized_ref_name"] == "release-1"
    assert snapshot_data["freeze_manifest"]["restore_operation"] == "launch_state"
    assert snapshot_data["freeze_manifest"]["immutable_snapshot"] is True
    assert snapshot_data["freeze_manifest"]["workspace_validated"] is True
    assert snapshot_data["freeze_manifest"]["ledger_checkpoint"] is True
    assert result.recompute_tree_state() == TreeLifecycleState.READY


def test_client_launch_release_loads_gts_clones_and_checks_out(tmp_path):
    source_client, _ = _make_client_with_ready_registry(tmp_path)
    root_path = tmp_path / "released-project"
    leaf_path = root_path / "deps" / "leaf"
    source_client.registry.get("root").absolute_path = root_path
    source_client.registry.get("root:deps/leaf").absolute_path = leaf_path
    snapshot_path = source_client.write_gts_snapshot(
        command_origin="test",
        output_path=tmp_path / "snapshot.gts",
    )

    runner = _FakeGitRunnerForOperations()
    client = ComplexGitSyncClient(git_runner=runner)
    result = client.launch_release(snapshot_path)

    assert len(runner.cloned) == len(result.values())
    assert len(runner.checked_out) == len(result.values())
    assert result.recompute_tree_state() == TreeLifecycleState.READY


def test_client_checkout_raises_when_no_registry_loaded():
    client = ComplexGitSyncClient()
    with pytest.raises(RuntimeError, match="No ComplexGitSync registry is loaded"):
        client.checkout("feature-x")


def test_client_commit_raises_when_no_registry_loaded():
    client = ComplexGitSyncClient()
    with pytest.raises(RuntimeError, match="No ComplexGitSync registry is loaded"):
        client.commit("message")


def test_client_push_raises_when_no_registry_loaded():
    client = ComplexGitSyncClient()
    with pytest.raises(RuntimeError, match="No ComplexGitSync registry is loaded"):
        client.push()


def test_client_tag_raises_when_no_registry_loaded():
    client = ComplexGitSyncClient()
    with pytest.raises(RuntimeError, match="No ComplexGitSync registry is loaded"):
        client.tag("v1.0.0")


def test_client_freeze_release_raises_when_no_registry_loaded():
    client = ComplexGitSyncClient()
    with pytest.raises(RuntimeError, match="No ComplexGitSync registry is loaded"):
        client.freeze_release("release-1")


def test_client_add_delegates_to_stage_tree(tmp_path):
    client, runner = _make_client_with_ready_registry(tmp_path)

    result = client.add()

    assert runner.staged, "Expected stage calls during add"
    assert result.recompute_tree_state() == TreeLifecycleState.READY


def test_client_add_raises_when_no_registry_loaded():
    client = ComplexGitSyncClient()
    with pytest.raises(RuntimeError, match="No ComplexGitSync registry is loaded"):
        client.add()


def test_client_freeze_state_delegates_and_writes_named_gts(tmp_path):
    client, runner = _make_client_with_ready_registry(tmp_path)
    _mark_all_children_as_submodules(client.registry, runner)
    output_gts = tmp_path / "internal-state.gts"

    result = client.freeze_state("state-1", output_gts=output_gts)

    assert runner.committed, "Expected commit calls during freeze_state"
    assert runner.tagged, "Expected tag calls during freeze_state"
    assert output_gts.exists()
    assert result.recompute_tree_state() == TreeLifecycleState.READY


def test_client_launch_state_loads_gts_clones_and_checks_out(tmp_path):
    source_client, _ = _make_client_with_ready_registry(tmp_path)
    root_path = tmp_path / "state-project"
    leaf_path = root_path / "deps" / "leaf"
    source_client.registry.get("root").absolute_path = root_path
    source_client.registry.get("root:deps/leaf").absolute_path = leaf_path
    snapshot_path = source_client.write_gts_snapshot(
        command_origin="test",
        output_path=tmp_path / "state-snapshot.gts",
    )

    runner = _FakeGitRunnerForOperations()
    client = ComplexGitSyncClient(git_runner=runner)
    result = client.launch_state(snapshot_path)

    assert len(runner.cloned) == len(result.values())
    assert len(runner.checked_out) == len(result.values())
    assert result.recompute_tree_state() == TreeLifecycleState.READY


# ---------------------------------------------------------------------------
# validate_branch_topology
# ---------------------------------------------------------------------------


def test_validate_branch_topology_coherent_tree(tmp_path):
    """All repos on the same branch → coherent, no conflicts."""
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    # Default: current_branch returns "main" for all repos

    report = validate_branch_topology(registry, runner)

    assert report.is_coherent is True
    assert report.reference_branch == "main"
    assert report.conflicts == []
    assert set(report.repo_branches.keys()) == {"project", "leaf"}
    assert all(b == "main" for b in report.repo_branches.values())


def test_validate_branch_topology_misaligned_branch(tmp_path):
    """A repo on a different branch than root → misaligned_branch conflict."""
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    leaf_path = registry.get("root:deps/leaf").absolute_path
    runner._current_branches[leaf_path] = "feature-x"

    report = validate_branch_topology(registry, runner)

    assert report.is_coherent is False
    assert report.reference_branch == "main"
    assert len(report.conflicts) == 1
    conflict = report.conflicts[0]
    assert conflict.repo_name == "leaf"
    assert conflict.expected_branch == "main"
    assert conflict.actual_branch == "feature-x"
    assert conflict.conflict_kind == "misaligned_branch"


def test_validate_branch_topology_detached_head_is_blocking(tmp_path):
    """A repo in detached HEAD state without a tag reference → detached_head conflict."""
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    leaf_path = registry.get("root:deps/leaf").absolute_path
    runner._current_branches[leaf_path] = None  # detached

    report = validate_branch_topology(registry, runner)

    assert report.is_coherent is False
    assert len(report.conflicts) == 1
    conflict = report.conflicts[0]
    assert conflict.repo_name == "leaf"
    assert conflict.actual_branch is None
    assert conflict.conflict_kind == "detached_head"


def test_validate_branch_topology_tag_divergence_is_allowed(tmp_path):
    """A repo on a tag (resolved_ref_kind=TAG) → tag_divergence, topology still coherent."""
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    leaf_entry = registry.get("root:deps/leaf")
    leaf_entry.resolved_ref_kind = RefKind.TAG
    leaf_path = leaf_entry.absolute_path
    runner._current_branches[leaf_path] = None  # detached (on tag)

    report = validate_branch_topology(registry, runner)

    assert report.is_coherent is True  # tag divergence is allowed
    assert len(report.conflicts) == 1
    conflict = report.conflicts[0]
    assert conflict.conflict_kind == "tag_divergence"
    assert conflict.repo_name == "leaf"


def test_validate_branch_topology_tag_divergence_on_different_branch(tmp_path):
    """A tag-state repo on a named branch still produces tag_divergence (non-blocking)."""
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    leaf_entry = registry.get("root:deps/leaf")
    leaf_entry.resolved_ref_kind = RefKind.TAG
    leaf_path = leaf_entry.absolute_path
    runner._current_branches[leaf_path] = "v1.0.0"

    report = validate_branch_topology(registry, runner)

    assert report.is_coherent is True
    assert report.conflicts[0].conflict_kind == "tag_divergence"


def test_validate_branch_topology_repo_branches_map(tmp_path):
    """repo_branches maps repo names to their current branches."""
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = registry.get("root").absolute_path
    leaf_path = registry.get("root:deps/leaf").absolute_path
    runner._current_branches[root_path] = "develop"
    runner._current_branches[leaf_path] = "develop"

    report = validate_branch_topology(registry, runner)

    assert report.repo_branches == {"project": "develop", "leaf": "develop"}
    assert report.is_coherent is True


def test_validate_branch_topology_format_coherent(tmp_path):
    """format() on a coherent tree produces expected text."""
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

    report = validate_branch_topology(registry, runner)
    output = report.format()

    assert "branch topology: coherent" in output
    assert "reference='main'" in output
    assert "project" in output
    assert "leaf" in output
    assert "conflicts:" not in output
    assert len(report.conflicts) == 0


def test_validate_branch_topology_format_incoherent(tmp_path):
    """format() on an incoherent tree shows conflicts section."""
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    leaf_path = registry.get("root:deps/leaf").absolute_path
    runner._current_branches[leaf_path] = "hotfix"

    report = validate_branch_topology(registry, runner)
    output = report.format()

    assert "branch topology: incoherent" in output
    assert "conflicts:" in output
    assert "[misaligned_branch]" in output
    assert "leaf" in output


def test_validate_branch_topology_missing_root(tmp_path):
    """Registry with no root → missing_root conflict, incoherent."""
    registry = WorkingGitTree()
    runner = _FakeGitRunnerForOperations()

    report = validate_branch_topology(registry, runner)

    assert report.is_coherent is False
    assert report.reference_branch is None
    assert len(report.conflicts) == 1
    assert report.conflicts[0].conflict_kind == "missing_root"
    assert report.repo_branches == {}


def test_validate_branch_topology_deep_hierarchy(tmp_path):
    """All three repos aligned → coherent on a 3-level tree."""
    registry = _make_deep_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    # Default current_branch returns "main" for all

    report = validate_branch_topology(registry, runner)

    assert report.is_coherent is True
    assert len(report.conflicts) == 0
    assert set(report.repo_branches.keys()) == {"deep", "middle", "sub"}


def test_client_validate_branch_topology_returns_report(tmp_path):
    """ComplexGitSyncClient.validate_branch_topology() delegates to the operation."""
    client, runner = _make_client_with_ready_registry(tmp_path)

    report = client.validate_branch_topology()

    assert isinstance(report, BranchTopologyReport)
    assert report.is_coherent is True
    assert report.reference_branch == "main"


def test_client_validate_branch_topology_detects_misalignment(tmp_path):
    """Client method surfaces conflicts when a repo is misaligned."""
    client, runner = _make_client_with_ready_registry(tmp_path)
    leaf_path = client.registry.get("root:deps/leaf").absolute_path
    runner._current_branches[leaf_path] = "different-branch"

    report = client.validate_branch_topology()

    assert report.is_coherent is False
    assert any(c.conflict_kind == "misaligned_branch" for c in report.conflicts)

"""Unit tests for Tier 2 operations: checkout_tree, commit_tree, push_tree,
propagate_global_branch, create_global_branch, restart_tree, and the
ComplexGitSyncClient façade methods checkout / commit / push.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ComplexGitSync.errors import TreeNotReadyError
from ComplexGitSync.git_repo import (
    NodeType,
    RefKind,
    RepoLifecycleState,
    SyncState,
)
from ComplexGitSync.git_tree import DependencyTreeRegistry, GitTree, TreeLifecycleState
from ComplexGitSync.operations import (
    add_tree,
    checkout_tree,
    commit_tree,
    create_global_branch,
    freeze_release_tree,
    propagate_global_branch,
    push_tree,
    restart_tree,
    tag_tree,
)
from ComplexGitSync.orchestre import ComplexGitSyncClient


# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------


def _make_ready_registry(tmp_path: Path) -> DependencyTreeRegistry:
    """Build a minimal 3-entry READY registry backed by real directories."""
    from ComplexGitSync.git_repo import (
        AccessProtocol,
        DiscoveryState,
        GitProvider,
        RepoRegistryEntry,
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
        return RepoRegistryEntry(
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

    registry = DependencyTreeRegistry()
    registry.add(
        _create_ready_entry("root", "project", NodeType.ROOT, None, root_path)
    )
    registry.add(
        _create_ready_entry("root:deps/leaf", "leaf", NodeType.LEAF, "root", leaf_path)
    )
    registry.recompute_tree_state()
    assert registry.is_ready(), "Fixture must produce a READY registry"
    return registry


def _make_deep_ready_registry(tmp_path: Path) -> DependencyTreeRegistry:
    """Build a 3-level READY registry: root → middle → sub-leaf."""
    from ComplexGitSync.git_repo import (
        AccessProtocol,
        DiscoveryState,
        GitProvider,
        RepoRegistryEntry,
    )

    root_path = tmp_path / "deep"
    middle_path = tmp_path / "deep" / "middle"
    sub_path = tmp_path / "deep" / "middle" / "sub"
    for p in (root_path, middle_path, sub_path):
        p.mkdir(parents=True)

    def _entry(repo_id, name, node_type, parent_id, absolute_path, parent_root):
        return RepoRegistryEntry(
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

    registry = DependencyTreeRegistry()
    registry.add(_entry("root", "deep", NodeType.ROOT, None, root_path, root_path))
    registry.add(_entry("root:middle", "middle", NodeType.PARENT, "root", middle_path, root_path))
    registry.add(_entry("root:middle:sub", "sub", NodeType.LEAF, "root:middle", sub_path, middle_path))
    registry.recompute_tree_state()
    assert registry.is_ready(), "Deep fixture must produce a READY registry"
    return registry


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
        self.tagged: list[tuple[Path, str]] = []
        self.cloned: list[tuple[str, Path, str]] = []
        self._staged_changes: dict[Path, bool] = {}
        self._shas: dict[Path, str] = {}
        self._current_branches: dict[Path, str | None] = {}

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
        return self._shas.get(Path(repo_path), f"sha-{Path(repo_path).name}")

    # --- commit ---
    def stage_all(self, repo_path: Path | str) -> None:
        path = Path(repo_path)
        self.staged.append(path)
        # Simulate: staging always marks the repo as having staged changes
        self._staged_changes[path] = True

    def has_staged_changes(self, repo_path: Path | str) -> bool:
        return self._staged_changes.get(Path(repo_path), False)

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
    ) -> None:
        self.pushed.append((Path(repo_path), remote, ref_name))

    def set_staged(self, repo_path: Path | str, value: bool) -> None:
        """Helper: manually set whether a repo has staged changes."""
        self._staged_changes[Path(repo_path)] = value

    def create_tag(self, repo_path: Path | str, tag_name: str) -> None:
        self.tagged.append((Path(repo_path), tag_name))


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


def test_restart_tree_checks_out_root_branch_across_all_repos(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = tmp_path / "project"
    runner._current_branches[root_path] = "feature-restart"

    restart_tree(registry, runner)

    checked_out_branches = {path: branch for path, branch in runner.checked_out}
    assert all(b == "feature-restart" for b in checked_out_branches.values())
    assert registry.is_ready()


def test_restart_tree_propagates_branch_to_all_entries(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = tmp_path / "project"
    runner._current_branches[root_path] = "sync-branch"

    restart_tree(registry, runner)

    for entry in registry.values():
        assert entry.target_ref_name == "sync-branch"
        assert entry.current_ref_name == "sync-branch"


def test_restart_tree_checkouts_parent_first(tmp_path):
    registry = _make_deep_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = tmp_path / "deep"
    runner._current_branches[root_path] = "main"

    restart_tree(registry, runner)

    checked_paths = [p for p, _ in runner.checked_out]
    root_idx = checked_paths.index(tmp_path / "deep")
    middle_idx = checked_paths.index(tmp_path / "deep" / "middle")
    sub_idx = checked_paths.index(tmp_path / "deep" / "middle" / "sub")
    assert root_idx < middle_idx < sub_idx


def test_restart_tree_falls_back_to_resolved_ref_when_no_current_branch(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = tmp_path / "project"
    runner._current_branches[root_path] = None
    # Set a resolved ref name on the root entry as fallback
    registry.get("root").resolved_ref_name = "fallback-branch"

    restart_tree(registry, runner)

    checked_branches = {branch for _, branch in runner.checked_out}
    assert "fallback-branch" in checked_branches


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

    push_tree(registry, runner)

    pushed_paths = [path for path, _, _ in runner.pushed]
    root_path = registry.get("root").absolute_path
    leaf_path = registry.get("root:deps/leaf").absolute_path
    assert pushed_paths.index(leaf_path) < pushed_paths.index(root_path)


def test_push_tree_deep_hierarchy_leaf_first(tmp_path):
    """Ordering must be sub → middle → root for a 3-level tree."""
    registry = _make_deep_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

    push_tree(registry, runner)

    pushed_paths = [path for path, _, _ in runner.pushed]
    root_path = registry.get("root").absolute_path
    mid_path = registry.get("root:middle").absolute_path
    sub_path = registry.get("root:middle:sub").absolute_path
    assert pushed_paths.index(sub_path) < pushed_paths.index(mid_path)
    assert pushed_paths.index(mid_path) < pushed_paths.index(root_path)


def test_push_tree_uses_remote_name_and_resolved_ref(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

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
    push_tree(registry, runner)

    for _, remote, _ in runner.pushed:
        assert remote == "origin"


def test_push_tree_tree_remains_ready(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

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

    result = client.tag("v1.0.0")

    assert runner.tagged, "Expected at least one tag call"
    assert result.recompute_tree_state() == TreeLifecycleState.READY


def test_client_freeze_release_delegates_and_writes_named_gts(tmp_path):
    client, runner = _make_client_with_ready_registry(tmp_path)
    output_gts = tmp_path / "release.gts"

    result = client.freeze_release("release-1", output_gts=output_gts)

    assert runner.committed, "Expected commit calls during freeze_release"
    assert runner.tagged, "Expected tag calls during freeze_release"
    assert output_gts.exists()
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

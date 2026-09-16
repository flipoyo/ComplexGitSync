"""Unit tests for Tier 2 operations: checkout_tree, commit_tree, push_tree,
propagate_global_branch, create_global_branch, restart_tree, and the
ComplexGitSyncClient façade methods checkout / commit / push.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError, TreeNotReadyError
from ComplexGitSync.git_branch import resolve_propagated_ref
from ComplexGitSync.git_repo import (
    NodeType,
    RefKind,
    RepoLifecycleState,
    RepoScope,
    SyncState,
    WorkingRepo,
)
from ComplexGitSync.git_runner import MergeCheckResult
from ComplexGitSync.git_tree import (
    GitTree,
    TreeLifecycleState,
    WorkingGitTree,
    iter_tree,
    iter_tree_leaf_first,
    propagate_privacy,
)
from ComplexGitSync.operations import (
    BranchTopologyReport,
    add_tree,
    branch_tree,
    checkout_tree,
    commit_tree,
    create_global_branch,
    freeze_release_tree,
    merge_source_ref,
    merge_status,
    merge_tree,
    merge_tree_one_at_a_time,
    paths_outside_scope,
    propagate_global_branch,
    push_tree,
    refresh_private_tree,
    remove_paths,
    restart_tree,
    restart_tree_force,
    tag_tree,
    validate_branch_topology,
)
from ComplexGitSync.orchestre import (
    ComplexGitSyncClient,
    GitRunner,
    RuntimeStateStore,
)


def _is_state_file(path: Path) -> bool:
    """A State is ``.cgitsync/state/<content hash>.gts``.

    Named by what it contains, so the same tree yields the same name on any
    machine — which is why there is no ``_n`` occurrence counter any more.
    """
    return path.parent.name == "state" and re.fullmatch(r"[0-9a-f]{64}\.gts", path.name) is not None


# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------


def _current_state_path(root_path: Path) -> Path:
    """The State the newest ledger entry names.

    The chain at ``.cgitsync/lgr/`` is the root_path's own record of what it
    last wrote. It replaced the single-file register these tests used to
    read, which nothing writes any more.
    """
    from ComplexGitSync.memory.ledger_store import read_all_entries
    from ComplexGitSync.memory.states import _parse_state_hash, state_path

    entries = read_all_entries(root_path / ".cgitsync" / "lgr")
    assert entries, "no ledger entry was written"
    return state_path(root_path / ".cgitsync", _parse_state_hash(entries[-1].state_id)).resolve()


def _ledger_entries(root_path: Path):
    """Every entry in the root_path's chain, oldest first."""
    from ComplexGitSync.memory.ledger_store import read_all_entries

    return read_all_entries(root_path / ".cgitsync" / "lgr")


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


class _FakeGitRunnerForOperations:
    """Minimal fake GitRunner for operation unit tests.

    Tracks calls and simulates branch existence.
    """

    def __init__(self, *, existing_local_branches: dict[Path, set[str]] | None = None):
        # {path: set of branch names that exist locally}
        self._local_branches: dict[Path, set[str]] = existing_local_branches or {}
        # {path: set of branches this clone knows as refs/remotes/<remote>/…}
        self._remote_tracking_branches: dict[Path, set[str]] = {}
        self.created: list[tuple[Path, str]] = []
        self.created_from: list[tuple[Path, str, str | None]] = []
        self.checked_out: list[tuple[Path, str]] = []
        self.staged: list[Path] = []
        self.staged_paths: list[tuple[Path, str]] = []
        self.removed_paths: list[tuple[Path, str]] = []
        self.committed: list[tuple[Path, str]] = []
        self.pushed: list[tuple[Path, str, str | None]] = []
        self.pushed_with_upstream: list[tuple[Path, str, str | None]] = []
        self.pulled: list[tuple[Path, str, str | None]] = []
        self.force_pulled: list[tuple[Path, str, str | None]] = []
        self.tagged: list[tuple[Path, str]] = []
        self.cloned: list[tuple[str, Path, str]] = []
        self.reset_hard_paths: list[Path] = []
        self.cleaned_paths: list[Path] = []
        self.command_order: list[tuple[str, Path]] = []
        self._staged_changes: dict[Path, bool] = {}
        self._unstaged_changes: dict[Path, bool] = {}
        self._extra_status_lines: dict[Path, list[str]] = {}
        self._shas: dict[Path, str] = {}
        self._current_branches: dict[Path, str | None] = {}
        self._existing_remotes: dict[Path, set[str]] = {}
        self._existing_tags: dict[Path, set[str]] = {}
        self._gitlinks: dict[Path, set[Path]] = {}
        self._tracking_states: dict[Path, SyncState | None] = {}
        self._has_upstream: dict[Path, bool] = {}
        self._merge_in_progress: dict[Path, bool] = {}
        self._unmergeable: dict[Path, set[str]] = {}
        self._conflicting_paths: dict[Path, list[Path]] = {}
        self.mergetool_opened: list[Path] = []
        self.merged: list[tuple[Path, str]] = []
        self.merge_aborted: list[Path] = []
        self.fetched: list[tuple[Path, str, str | None]] = []
        self.refspecs_ensured: list[tuple[Path, str]] = []
        # Ordered log of the two calls whose *relative* order matters: a
        # refspec must be widened before the push that depends on it.
        self.write_order: list[tuple[str, Path]] = []

    def tool_version(self, executable: str) -> str | None:
        """Versions the ledger records; a fake reports a fixed one."""
        return f"{executable} 0.0-test"

    # --- branch / checkout ---
    def current_branch(self, repo_path: Path | str) -> str | None:
        return self._current_branches.get(Path(repo_path), "main")
    def local_branch_exists(self, repo_path: Path | str, branch: str) -> bool:
        return branch in self._local_branches.get(Path(repo_path), set())

    def branch_known(
        self, repo_path: Path | str, branch: str, *, remote: str = "origin"
    ) -> bool:
        return self.local_branch_exists(repo_path, branch) or (
            self.remote_tracking_branch_exists(repo_path, branch, remote=remote)
        )

    def remote_tracking_branch_exists(
        self, repo_path: Path | str, branch: str, *, remote: str = "origin"
    ) -> bool:
        return branch in self._remote_tracking_branches.get(Path(repo_path), set())

    def merge(
        self,
        repo_path: Path | str,
        ref_name: str,
        *,
        ff_only: bool = False,
        no_ff: bool = False,
        message: str | None = None,
    ) -> None:
        path = Path(repo_path)
        if ref_name in self._unmergeable.get(path, set()):
            raise GitSyncError(f"Git command failed (git merge {ref_name}): conflict")
        self.merged.append((path, ref_name))
        self.command_order.append(("merge", path))

    def can_merge_cleanly(self, repo_path: Path | str, ref_name: str) -> MergeCheckResult:
        path = Path(repo_path)
        if ref_name in self._unmergeable.get(path, set()):
            return MergeCheckResult(
                is_clean=False,
                conflicting_paths=list(self._conflicting_paths.get(path, ())),
            )
        return MergeCheckResult(is_clean=True, conflicting_paths=[])

    def merge_abort(self, repo_path: Path | str) -> None:
        self.merge_aborted.append(Path(repo_path))

    def configured_merge_tool(self, repo_path: Path | str) -> str | None:
        return None

    def mergetool(
        self,
        repo_path: Path | str,
        *,
        tool: str | None = None,
        tool_command: str | None = None,
    ) -> None:
        self.mergetool_opened.append(Path(repo_path))

    def fetch(
        self, repo_path: Path | str, *, remote: str = "origin", ref_name: str | None = None
    ) -> None:
        self.fetched.append((Path(repo_path), remote, ref_name))

    def create_branch(
        self, repo_path: Path | str, branch: str, *, start_point: str | None = None
    ) -> None:
        path = Path(repo_path)
        self._local_branches.setdefault(path, set()).add(branch)
        self.created.append((path, branch))
        self.created_from.append((path, branch, start_point))

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

    def stage_path(self, repo_path: Path | str, relative_path: str) -> None:
        path = Path(repo_path)
        self.staged_paths.append((path, relative_path))
        self._staged_changes[path] = True

    def remove(self, repo_path: Path | str, relative_path: str) -> None:
        path = Path(repo_path)
        self.removed_paths.append((path, relative_path))
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
        self.write_order.append(("push", path))

    def has_upstream(self, repo_path: Path | str) -> bool:
        return self._has_upstream.get(Path(repo_path), True)

    def upstream_configured(self, repo_path: Path | str) -> bool:
        return self._has_upstream.get(Path(repo_path), True)

    def ensure_fetch_refspec(self, repo_path: Path | str, *, remote: str = "origin") -> bool:
        self.refspecs_ensured.append((Path(repo_path), remote))
        self.write_order.append(("ensure_fetch_refspec", Path(repo_path)))
        return False

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
        if ref_name is not None:
            self._current_branches[path] = ref_name

    def force_pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None:
        path = Path(repo_path)
        self.force_pulled.append((path, remote, ref_name))
        self.command_order.append(("force_pull", path))
        if ref_name is not None:
            self._current_branches[path] = ref_name

    def reset_hard(self, repo_path: Path | str, ref_name: str = "HEAD") -> None:
        path = Path(repo_path)
        self.reset_hard_paths.append(path)
        self.command_order.append(("reset_hard", path))

    def clean_untracked(self, repo_path: Path | str) -> None:
        path = Path(repo_path)
        self.cleaned_paths.append(path)
        self.command_order.append(("clean_untracked", path))

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

    def set_remote_exists(self, repo_path: Path | str, remote: str, exists: bool) -> None:
        path = Path(repo_path)
        remotes = self._existing_remotes.setdefault(path, set())
        if exists:
            remotes.add(remote)
        else:
            remotes.discard(remote)

    def add_existing_tag(self, repo_path: Path | str, tag_name: str) -> None:
        self._existing_tags.setdefault(Path(repo_path), set()).add(tag_name)

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


def test_add_tree_with_paths_stages_only_the_owning_repo(tmp_path):
    registry = _make_deep_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    target = tmp_path / "deep" / "middle" / "sub" / "file.txt"

    add_tree(registry, runner, paths=[target])

    assert runner.staged == []
    assert runner.staged_paths == [(tmp_path / "deep" / "middle" / "sub", "file.txt")]


def test_add_tree_with_paths_resolves_each_independently(tmp_path):
    registry = _make_deep_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_file = tmp_path / "deep" / "root-file.txt"
    sub_file = tmp_path / "deep" / "middle" / "sub" / "file.txt"

    add_tree(registry, runner, paths=[root_file, sub_file])

    assert runner.staged == []
    assert set(runner.staged_paths) == {
        (tmp_path / "deep", "root-file.txt"),
        (tmp_path / "deep" / "middle" / "sub", "file.txt"),
    }


def test_add_tree_with_paths_rejects_a_path_outside_every_repo(tmp_path):
    registry = _make_deep_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    outside = tmp_path / "elsewhere" / "file.txt"

    with pytest.raises(GitSyncError, match="not inside any repository"):
        add_tree(registry, runner, paths=[outside])

    # Nothing staged anywhere -- resolution (and its failure) happens
    # before any git_runner call, not partway through.
    assert runner.staged == []
    assert runner.staged_paths == []


def test_tree_iterators_include_root_parent_and_leaf_in_expected_directions(tmp_path):
    registry = _make_deep_ready_registry(tmp_path)

    assert [entry.node_type for entry in iter_tree(registry)] == [
        NodeType.ROOT,
        NodeType.PARENT,
        NodeType.LEAF,
    ]
    assert [entry.node_type for entry in iter_tree_leaf_first(registry)] == [
        NodeType.LEAF,
        NodeType.PARENT,
        NodeType.ROOT,
    ]


def test_git_runner_stage_all_respects_local_gitignore(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    runner = GitRunner()

    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True, text=True)
    (repo_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo_path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (repo_path / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    runner.stage_all(repo_path)

    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert ".gitignore" in staged
    assert "tracked.txt" in staged
    assert "ignored.txt" not in staged


def test_git_runner_force_pull_fetches_resets_fetch_head_and_cleans(monkeypatch, tmp_path):
    runner = GitRunner()
    calls: list[tuple[tuple[str, ...], Path | None]] = []

    def _fake_run(self, *args, cwd=None):
        calls.append((tuple(args), Path(cwd) if cwd is not None else None))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(GitRunner, "_run", _fake_run)
    repo_path = tmp_path / "repo"

    runner.force_pull(repo_path, remote="origin", ref_name="main")

    assert calls == [
        (("fetch", "origin", "main"), repo_path),
        (("checkout", "-B", "main", "FETCH_HEAD"), repo_path),
        (("clean", "-fd"), repo_path),
    ]


# ---------------------------------------------------------------------------
# restart_tree
# ---------------------------------------------------------------------------


def test_restart_tree_pulls_root_and_children(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = tmp_path / "project"
    runner._current_branches[root_path] = "feature-restart"

    restart_tree(registry, runner)

    assert runner.pulled == [
        (root_path, "origin", "feature-restart"),
        (root_path / "deps" / "leaf", "origin", "feature-restart"),
    ]
    assert registry.is_ready()


def test_client_pull_gts_pulls_root_then_updates_parents_and_leaves(tmp_path):
    registry = _make_deep_ready_registry(tmp_path)
    snapshot_path = tmp_path / "deep.gts"
    registry.to_gts(command_origin="snapshot").to_toml(snapshot_path)

    runner = _FakeGitRunnerForOperations()
    runner._current_branches[tmp_path / "deep"] = "main"
    client = ComplexGitSyncClient(
        git_runner=runner,
        state_store=RuntimeStateStore(tmp_path / "state-store"),
    )

    result = client.pull(snapshot_path)

    assert result.is_ready()
    assert runner.pulled == [
        (tmp_path / "deep", "origin", "main"),
        (tmp_path / "deep" / "middle", "origin", "main"),
        (tmp_path / "deep" / "middle" / "sub", "origin", "main"),
    ]
    assert runner.command_order == [
        ("pull", tmp_path / "deep"),
        ("pull", tmp_path / "deep" / "middle"),
        ("pull", tmp_path / "deep" / "middle" / "sub"),
    ]


def test_restart_tree_propagates_branch_to_all_entries(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = tmp_path / "project"
    runner._current_branches[root_path] = "sync-branch"

    restart_tree(registry, runner)

    for entry in registry.values():
        assert entry.target_ref_name == "sync-branch"
        assert entry.current_ref_name == "sync-branch"


def test_restart_tree_runs_pull_parent_first(tmp_path):
    registry = _make_deep_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = tmp_path / "deep"
    runner._current_branches[root_path] = "main"

    restart_tree(registry, runner)

    executed_paths = [path for _, path in runner.command_order]
    root_idx = executed_paths.index(tmp_path / "deep")
    middle_idx = executed_paths.index(tmp_path / "deep" / "middle")
    sub_idx = executed_paths.index(tmp_path / "deep" / "middle" / "sub")
    assert root_idx < middle_idx < sub_idx


class TestABranchSomebodyElsePushedIsThatBranch:
    """Creating a branch at HEAD when the remote already has one forks a name.

    ``checkout`` then reported ``READY``/``ALIGNED`` on commits that shared
    nothing with the colleague's branch but its name — worse than failing to
    find it (``.localSpec/DevTickets/archive/20260911_UpstreamBranchDisplay_DevPlanTicket.md`` §3).
    """

    def test_a_known_remote_branch_is_the_start_point(self, tmp_path):
        registry = _make_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()
        for repo in registry.values():
            runner._remote_tracking_branches[repo.absolute_path] = {"colleague"}

        create_global_branch(registry, runner, "colleague")

        for _, branch, start_point in runner.created_from:
            assert (branch, start_point) == ("colleague", "origin/colleague")

    def test_an_unknown_branch_still_starts_where_we_stand(self, tmp_path):
        registry = _make_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()

        create_global_branch(registry, runner, "mine-alone")

        assert [start_point for _, _, start_point in runner.created_from] == [None, None]

    def test_the_start_point_names_the_repository_s_own_remote(self, tmp_path):
        registry = _make_ready_registry(tmp_path)
        root = registry.get("root")
        root.remote_name = "upstream"
        runner = _FakeGitRunnerForOperations()
        runner._remote_tracking_branches[root.absolute_path] = {"shared"}

        create_global_branch(registry, runner, "shared")

        assert (root.absolute_path, "shared", "upstream/shared") in runner.created_from

    def test_a_branch_that_already_exists_locally_is_left_alone(self, tmp_path):
        registry = _make_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()
        for repo in registry.values():
            runner._local_branches[repo.absolute_path] = {"colleague"}
            runner._remote_tracking_branches[repo.absolute_path] = {"colleague"}

        create_global_branch(registry, runner, "colleague")

        assert runner.created_from == []


class TestPullBringsEveryBranchSRef:
    """``git pull origin <branch>`` fetches one branch; ``checkout`` reads all of them."""

    def test_pull_fetches_the_whole_remote_before_pulling_one_branch(self, tmp_path):
        registry = _make_deep_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()
        runner._current_branches[tmp_path / "deep"] = "main"

        restart_tree(registry, runner)

        for repo in registry.values():
            assert (repo.absolute_path, "origin", None) in runner.fetched

    def test_a_failed_fetch_does_not_stop_the_pull(self, tmp_path):
        """The refs are a convenience; the pull is the command."""

        class _RefusingFetch(_FakeGitRunnerForOperations):
            def fetch(self, repo_path, *, remote="origin", ref_name=None):
                raise GitSyncError("network is down")

        registry = _make_ready_registry(tmp_path)
        runner = _RefusingFetch()
        runner._current_branches[registry.get("root").absolute_path] = "main"

        restart_tree(registry, runner)

        assert [path for path, _, _ in runner.pulled]


class TestEveryWorkspaceRepairsItsOwnFetchRefspec:
    """A workspace cloned before the refspec fix must not need a re-clone.

    ``git clone --single-branch`` narrowed ``remote.origin.fetch`` to one
    branch and left it there, so ``push -u`` could never write the
    remote-tracking ref that ``@{upstream}`` resolves through. Pull and push
    are the commands that write to a repository anyway, so they are where the
    config is repaired — once, idempotently
    (``.localSpec/DevTickets/archive/20260911_UpstreamBranchDisplay_DevPlanTicket.md``).
    """

    def test_pull_widens_the_refspec_of_every_repository(self, tmp_path):
        registry = _make_deep_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()
        runner._current_branches[tmp_path / "deep"] = "main"

        restart_tree(registry, runner)

        repaired = [path for path, _ in runner.refspecs_ensured]
        assert repaired == [repo.absolute_path for repo in registry.values()]

    def test_push_widens_the_refspec_before_pushing_that_repository(self, tmp_path):
        """Order matters: ``push -u`` can only write the tracking ref if the
        refspec already maps the branch being pushed."""
        registry = _make_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()

        push_tree(registry, runner)

        for path, _, _ in runner.pushed:
            assert runner.write_order.index(("ensure_fetch_refspec", path)) < (
                runner.write_order.index(("push", path))
            )

    def test_the_repair_uses_the_repository_s_own_remote_name(self, tmp_path):
        registry = _make_ready_registry(tmp_path)
        root = registry.get("root")
        root.remote_name = "upstream"
        runner = _FakeGitRunnerForOperations()
        runner._existing_remotes[root.absolute_path] = {"upstream"}

        push_tree(registry, runner)

        root_path = registry.get("root").absolute_path
        assert (root_path, "upstream") in runner.refspecs_ensured

    def test_a_repository_that_cannot_be_repaired_is_still_pushed(self, tmp_path):
        """The refspec is a convenience; refusing to push over it would turn a
        display bug into a lost command."""

        class _RefusingRunner(_FakeGitRunnerForOperations):
            def ensure_fetch_refspec(self, repo_path, *, remote="origin"):
                raise GitSyncError("config is read-only")

        registry = _make_ready_registry(tmp_path)
        runner = _RefusingRunner()

        push_tree(registry, runner)

        assert [path for path, _, _ in runner.pushed]


def test_restart_tree_force_pulls_parent_first(tmp_path):
    registry = _make_deep_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = tmp_path / "deep"
    runner._current_branches[root_path] = "main"

    restart_tree_force(registry, runner)

    assert runner.force_pulled == [
        (root_path, "origin", "main"),
        (root_path / "middle", "origin", "main"),
        (root_path / "middle" / "sub", "origin", "main"),
    ]
    executed_force_paths = [
        path for action, path in runner.command_order if action == "force_pull"
    ]
    assert executed_force_paths == [
        root_path,
        root_path / "middle",
        root_path / "middle" / "sub",
    ]


def test_restart_tree_falls_back_to_resolved_ref_when_no_current_branch(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = tmp_path / "project"
    runner._current_branches[root_path] = None
    # Set a resolved ref name on the root entry as fallback
    registry.get("root").resolved_ref_name = "fallback-branch"

    restart_tree(registry, runner)

    assert runner.pulled == [
        (root_path, "origin", "fallback-branch"),
        (root_path / "deps" / "leaf", "origin", "fallback-branch"),
    ]


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


def test_propagate_global_branch_leaves_a_private_repo_on_its_own_branch(tmp_path):
    """A private mount is shared with other projects: the tree must not move it."""
    registry = _make_ready_registry(tmp_path)
    private = registry.get("root:deps/leaf")
    private.private = True
    private.default_branch = "ComplexGitSync"

    propagate_global_branch(registry, "feature-x")

    assert private.target_ref_name == "ComplexGitSync"
    assert registry.get("root").target_ref_name == "feature-x"


def test_propagate_global_branch_still_moves_a_private_repo_to_a_tag(tmp_path):
    """Privacy governs branch propagation only, so a frozen release stays whole."""
    registry = _make_ready_registry(tmp_path)
    private = registry.get("root:deps/leaf")
    private.private = True
    private.default_branch = "ComplexGitSync"

    propagate_global_branch(registry, "v1.2.3", ref_kind=RefKind.TAG)

    assert private.target_ref_name == "v1.2.3"
    assert private.target_ref_kind == RefKind.TAG


def test_create_global_branch_never_creates_inside_a_private_repo(tmp_path):
    """The incident of 2026-09-05: a branch appeared inside shared repositories."""
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    private = registry.get("root:deps/leaf")
    private.private = True

    create_global_branch(registry, runner, "feature-x")

    created = [path for path, branch in runner.created if branch == "feature-x"]
    assert private.absolute_path not in created
    assert registry.get("root").absolute_path in created


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


def test_push_tree_updates_commit_sha(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

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


def test_push_tree_sets_upstream_when_current_branch_is_unpublished(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

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

    for entry in registry.values():
        runner._has_upstream[entry.absolute_path] = True

    push_tree(registry, runner)

    assert runner.pushed_with_upstream == []


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


def test_tag_tree_preflight_fails_when_tag_exists(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = registry.get("root").absolute_path
    runner.add_existing_tag(root_path, "v1.0.0")

    with pytest.raises(GitSyncError, match="ERROR tag already taken: 'v1.0.0'"):
        tag_tree(registry, runner, "v1.0.0")


def test_tag_tree_preflight_fails_when_tree_is_dirty(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    leaf_path = registry.get("root:deps/leaf").absolute_path
    runner.set_staged(leaf_path, True)

    with pytest.raises(GitSyncError, match="worktree has uncommitted changes"):
        tag_tree(registry, runner, "v1.0.0")


def test_tag_tree_preflight_ignores_unmanaged_gitlink_dirty_state(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = registry.get("root").absolute_path
    runner.add_gitlink(root_path, "ComplexGitSync")
    runner.add_status_line(root_path, " M ComplexGitSync")

    tag_tree(registry, runner, "v1.0.0")

    assert registry.get("root").worktree_state == "CLEAN"


def test_tag_tree_preflight_ignores_managed_state_files(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = registry.get("root").absolute_path
    runner.add_status_line(root_path, " M .cgitsync/state/project.gts")
    runner.add_status_line(root_path, "?? project.lgr")

    tag_tree(registry, runner, "v1.0.0")

    assert registry.get("root").worktree_state == "CLEAN"


def test_commit_tree_preflight_warns_when_tree_is_dirty(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    leaf_path = registry.get("root:deps/leaf").absolute_path
    runner.set_unstaged(leaf_path, True)

    with pytest.warns(UserWarning, match="commit preflight warning: leaf: worktree has uncommitted changes"):
        commit_tree(registry, runner, "commit dirty tree")

    assert registry.get("root:deps/leaf").worktree_state == "DIRTY"


def test_push_tree_preflight_warns_when_branch_is_ahead(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = registry.get("root").absolute_path
    runner.set_tracking_state(root_path, SyncState.AHEAD)

    with pytest.warns(UserWarning, match="push preflight warning: project: local branch is ahead of its upstream"):
        push_tree(registry, runner)


def test_push_tree_preflight_fails_when_merge_is_unresolved(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = registry.get("root").absolute_path
    runner.set_unresolved_merge(root_path, True)

    with pytest.raises(GitSyncError, match="unresolved merge in progress"):
        push_tree(registry, runner)


def test_tag_tree_preflight_warns_when_commit_sha_does_not_match_head(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = registry.get("root").absolute_path
    runner._shas[root_path] = "actual-sha"
    registry.get("root").commit_sha = "recorded-sha"

    with pytest.warns(UserWarning, match="recorded commit_sha 'recorded-sha' does not match HEAD 'actual-sha'"):
        tag_tree(registry, runner, "v1.0.0")


def test_freeze_release_preflight_fails_when_branches_misalign(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    root_path = registry.get("root").absolute_path
    leaf_path = registry.get("root:deps/leaf").absolute_path
    runner._current_branches[root_path] = "main"
    runner._current_branches[leaf_path] = "feature-x"

    with pytest.raises(GitSyncError, match="branch misalignment"):
        freeze_release_tree(registry, runner, "release-1")


def _make_registry_with_config_repo(tmp_path: Path) -> WorkingGitTree:
    """A READY registry whose leaf is a writable configuration repository.

    Shaped like a real tree: the project's own root on a feature branch, and
    a private mount sitting on a branch named after the project.
    """
    from ComplexGitSync.git_repo import WorkingRepo

    registry = _make_ready_registry(tmp_path)
    # The root of _make_ready_registry is named "project", so that is what a
    # private/local branch here is named after: "project" on main,
    # "project_<branch>" elsewhere.
    leaf = registry.get("root:deps/leaf")
    leaf.private = True
    leaf.writable = True
    leaf.default_branch = "project"
    leaf.target_ref_name = "project"
    leaf.resolved_ref_name = "project"
    assert isinstance(leaf, WorkingRepo)
    propagate_privacy(registry)
    return registry


class TestPreflightOnlyChecksWhatTheOperationTouches:
    """A commit must not be blocked by a repository it will never write to.

    The scope work made ``commit``/``push`` skip configuration repos, but
    their preflight still swept the whole tree. A private mount sitting on
    its own branch — the entire point of privacy — then read as a branch
    misalignment and blocked every commit in the tree.
    """

    @staticmethod
    def _runner(registry: WorkingGitTree) -> _FakeGitRunnerForOperations:
        runner = _FakeGitRunnerForOperations()
        runner._current_branches[registry.get("root").absolute_path] = "multi-branch"
        runner._current_branches[registry.get("root:deps/leaf").absolute_path] = (
            "project_multi-branch"
        )
        return runner

    def test_a_private_mount_on_its_own_branch_does_not_block_a_commit(self, tmp_path):
        """The bug reported from a live workspace."""
        registry = _make_registry_with_config_repo(tmp_path)
        runner = self._runner(registry)

        commit_tree(registry, runner, "project work")

        assert [path for path, _ in runner.committed] == [registry.get("root").absolute_path]

    def test_a_private_mount_is_measured_against_its_own_branch_not_the_roots(self, tmp_path):
        """Under --private the private mount *is* in scope, and still passes."""
        registry = _make_registry_with_config_repo(tmp_path)
        runner = self._runner(registry)

        commit_tree(registry, runner, "config work", scope=RepoScope.PRIVATE)

        assert [path for path, _ in runner.committed] == [
            registry.get("root:deps/leaf").absolute_path
        ]

    def test_a_private_mount_off_its_declared_branch_still_blocks(self, tmp_path):
        """Scoping must not turn the check off, only point it at the right branch."""
        registry = _make_registry_with_config_repo(tmp_path)
        runner = self._runner(registry)
        runner._current_branches[registry.get("root:deps/leaf").absolute_path] = "somewhere-else"

        with pytest.raises(GitSyncError, match="expected 'project_multi-branch'"):
            commit_tree(registry, runner, "config work", scope=RepoScope.PRIVATE)

    def test_an_owned_repo_off_the_roots_branch_still_blocks(self, tmp_path):
        registry = _make_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()
        runner._current_branches[registry.get("root").absolute_path] = "main"
        runner._current_branches[registry.get("root:deps/leaf").absolute_path] = "feature-x"

        with pytest.raises(GitSyncError, match="branch misalignment"):
            commit_tree(registry, runner, "project work")

    def test_a_config_repo_behind_its_upstream_does_not_block_a_project_commit(self, tmp_path):
        registry = _make_registry_with_config_repo(tmp_path)
        runner = self._runner(registry)
        runner._tracking_states[registry.get("root:deps/leaf").absolute_path] = SyncState.BEHIND

        commit_tree(registry, runner, "project work")

        assert [path for path, _ in runner.committed] == [registry.get("root").absolute_path]

    def test_worktree_state_is_refreshed_for_every_repo_whatever_the_scope(self, tmp_path):
        """It is written into the .gts for every repo, so it must stay fresh."""
        registry = _make_registry_with_config_repo(tmp_path)
        runner = self._runner(registry)
        leaf = registry.get("root:deps/leaf")
        leaf.worktree_state = None

        commit_tree(registry, runner, "project work")

        assert leaf.worktree_state is not None


class TestMergeTree:
    """`merge` lands a project branch across the tree, or lands nothing.

    The argument is always the *project's* branch. Each repository resolves
    what that means for itself, which is why the private case needs no code
    of its own — it is the same command with a different scope.
    """

    @staticmethod
    def _tree(tmp_path: Path) -> WorkingGitTree:
        registry = _make_registry_with_config_repo(tmp_path)
        return registry

    @staticmethod
    def _runner(registry: WorkingGitTree) -> _FakeGitRunnerForOperations:
        runner = _FakeGitRunnerForOperations()
        root = registry.get("root").absolute_path
        leaf = registry.get("root:deps/leaf").absolute_path
        runner._current_branches[root] = "main"
        runner._current_branches[leaf] = "project"
        # Every branch either side might merge exists.
        runner._local_branches[root] = {"main", "multi-branch"}
        runner._local_branches[leaf] = {"MyProject", "project_multi-branch"}
        return runner

    def test_a_project_repo_merges_the_branch_it_was_given(self, tmp_path):
        registry = self._tree(tmp_path)
        runner = self._runner(registry)

        merge_tree(registry, runner, "multi-branch")

        assert runner.merged == [(registry.get("root").absolute_path, "multi-branch")]

    def test_a_private_local_repo_merges_the_derived_branch_instead(self, tmp_path):
        """The whole design in one assertion: the name is translated."""
        registry = self._tree(tmp_path)
        runner = self._runner(registry)

        merge_tree(registry, runner, "multi-branch", scope=RepoScope.PRIVATE)

        assert runner.merged == [
            (registry.get("root:deps/leaf").absolute_path, "project_multi-branch")
        ]

    def test_a_conflict_anywhere_leaves_nothing_merged(self, tmp_path):
        """The guarantee that makes a tree-wide merge safe to run at all."""
        registry = _make_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()
        root = registry.get("root").absolute_path
        leaf = registry.get("root:deps/leaf").absolute_path
        for path in (root, leaf):
            runner._current_branches[path] = "main"
            runner._local_branches[path] = {"main", "multi-branch"}
        # The root conflicts; the leaf is merged first, leaf-first order.
        runner._unmergeable[root] = {"multi-branch"}

        with pytest.raises(GitSyncError, match="no repository was merged"):
            merge_tree(registry, runner, "multi-branch")

        assert runner.merged == [], "the clean repo must not have been merged"

    def test_the_error_names_every_blocked_repository(self, tmp_path):
        registry = _make_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()
        for repo in registry.values():
            runner._current_branches[repo.absolute_path] = "main"
            runner._local_branches[repo.absolute_path] = {"main", "multi-branch"}
            runner._unmergeable[repo.absolute_path] = {"multi-branch"}

        with pytest.raises(GitSyncError) as excinfo:
            merge_tree(registry, runner, "multi-branch")

        message = str(excinfo.value)
        assert "leaf" in message and "project" in message

    def test_the_error_names_every_conflicting_file_under_its_repository(self, tmp_path):
        """The reporting case: 'ComplexGitSync: tests/unit/test_documents.py'."""
        registry = _make_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()
        root = registry.get("root").absolute_path
        for repo in registry.values():
            runner._current_branches[repo.absolute_path] = "main"
            runner._local_branches[repo.absolute_path] = {"main", "multi-branch"}
        runner._unmergeable[root] = {"multi-branch"}
        runner._conflicting_paths[root] = [
            Path("tests/unit/test_documents.py"),
            Path("docs/MASTER.pdf"),
        ]

        with pytest.raises(GitSyncError) as excinfo:
            merge_tree(registry, runner, "multi-branch")

        message = str(excinfo.value)
        assert "project: tests/unit/test_documents.py, docs/MASTER.pdf" in message

    def test_resolve_keeps_what_it_merged_and_names_where_it_stopped(self, tmp_path):
        """The trade --resolve makes: partial progress, reported exactly."""
        registry = _make_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()
        root = registry.get("root").absolute_path
        for repo in registry.values():
            runner._current_branches[repo.absolute_path] = "main"
            runner._local_branches[repo.absolute_path] = {"main", "multi-branch"}
        # Leaf-first order, so the leaf merges before the root conflicts.
        runner._unmergeable[root] = {"multi-branch"}
        runner._conflicting_paths[root] = [Path("a.txt")]

        outcome = merge_tree_one_at_a_time(registry, runner, "multi-branch")

        leaf = registry.get("root:deps/leaf").absolute_path
        assert (leaf, "multi-branch") in runner.merged, "the clean leaf must merge"
        assert outcome.stopped_at == "project"
        assert outcome.stopped_paths == (Path("a.txt"),)

    def test_resolve_leaves_the_conflicted_repo_mid_merge(self, tmp_path):
        """A merge tool needs the conflict written to the worktree."""
        registry = _make_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()
        root = registry.get("root").absolute_path
        for repo in registry.values():
            runner._current_branches[repo.absolute_path] = "main"
            runner._local_branches[repo.absolute_path] = {"main", "multi-branch"}
        runner._unmergeable[root] = {"multi-branch"}

        outcome = merge_tree_one_at_a_time(registry, runner, "multi-branch")

        assert outcome.stopped_at == "project"
        assert runner.merge_aborted == [], "the conflict must be left to resolve"

    def test_plain_merge_still_writes_nothing_when_resolve_would_progress(self, tmp_path):
        """--resolve must not weaken the default."""
        registry = _make_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()
        root = registry.get("root").absolute_path
        for repo in registry.values():
            runner._current_branches[repo.absolute_path] = "main"
            runner._local_branches[repo.absolute_path] = {"main", "multi-branch"}
        runner._unmergeable[root] = {"multi-branch"}

        with pytest.raises(GitSyncError):
            merge_tree(registry, runner, "multi-branch")

        assert runner.merged == [], "the clean leaf must not have been merged"

    def test_a_conflicting_repo_is_reported_by_the_dry_run_too(self, tmp_path):
        """The dry run must not promise a merge that merge_tree then refuses."""
        registry = _make_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()
        root = registry.get("root").absolute_path
        for repo in registry.values():
            runner._current_branches[repo.absolute_path] = "main"
            runner._local_branches[repo.absolute_path] = {"main", "multi-branch"}
        runner._unmergeable[root] = {"multi-branch"}
        runner._conflicting_paths[root] = [Path("a.txt")]

        root_repo = registry.get("root")
        source, status, paths = merge_status(root_repo, runner, "multi-branch")

        assert status == "conflicts"
        assert paths == (Path("a.txt"),)
        assert runner.merged == [], "asking must not merge anything"

    def test_a_repo_with_no_such_branch_is_skipped_not_failed(self, tmp_path):
        """A private/local repo may simply have no branch for this one yet."""
        registry = self._tree(tmp_path)
        runner = self._runner(registry)
        leaf = registry.get("root:deps/leaf").absolute_path
        runner._local_branches[leaf] = {"project"}

        merged = merge_tree(registry, runner, "multi-branch", scope=RepoScope.PRIVATE)

        assert merged == ()
        assert runner.merged == []

    def test_a_read_only_config_repo_is_never_merged(self, tmp_path):
        registry = _make_registry_with_config_repo(tmp_path)
        leaf = registry.get("root:deps/leaf")
        leaf.writable = False
        propagate_privacy(registry)
        runner = self._runner(registry)

        merge_tree(registry, runner, "multi-branch", scope=RepoScope.WRITABLE)

        merged_paths = [path for path, _ in runner.merged]
        assert leaf.absolute_path not in merged_paths

    def test_one_writable_pass_merges_both_halves(self, tmp_path):
        """What ``merge --all`` runs: one pass, both halves, names translated."""
        registry = self._tree(tmp_path)
        runner = self._runner(registry)

        merge_tree(registry, runner, "multi-branch", scope=RepoScope.WRITABLE)

        assert runner.merged == [
            (registry.get("root:deps/leaf").absolute_path, "project_multi-branch"),
            (registry.get("root").absolute_path, "multi-branch"),
        ]

    def test_a_conflict_in_the_private_half_leaves_the_project_half_unmerged(self, tmp_path):
        """Why ``--all`` must be one pass and never two sequential ones.

        Two passes means two preflights: the project half would merge, the
        private half would then refuse, and the tree would be left exactly
        half-merged — the state the whole-scope preflight exists to prevent.
        One ``WRITABLE`` pass checks both halves before touching either.
        """
        registry = self._tree(tmp_path)
        runner = self._runner(registry)
        leaf = registry.get("root:deps/leaf").absolute_path
        runner._unmergeable[leaf] = {"project_multi-branch"}

        with pytest.raises(GitSyncError, match="no repository was merged"):
            merge_tree(registry, runner, "multi-branch", scope=RepoScope.WRITABLE)

        assert runner.merged == [], "the project repository must not have been merged"

    def test_a_conflict_in_the_project_half_leaves_the_private_half_unmerged(self, tmp_path):
        """The same guarantee in the other direction."""
        registry = self._tree(tmp_path)
        runner = self._runner(registry)
        runner._unmergeable[registry.get("root").absolute_path] = {"multi-branch"}

        with pytest.raises(GitSyncError, match="no repository was merged"):
            merge_tree(registry, runner, "multi-branch", scope=RepoScope.WRITABLE)

        assert runner.merged == []

    def test_merge_source_ref_translates_only_for_private_local(self, tmp_path):
        registry = self._tree(tmp_path)

        assert (
            merge_source_ref(registry.get("root"), "multi-branch", project_name="project")
            == "multi-branch"
        )
        assert (
            merge_source_ref(
                registry.get("root:deps/leaf"), "multi-branch", project_name="project"
            )
            == "project_multi-branch"
        )


class TestCheckoutAndBranchBothUseTheProjectRule:
    """`branch` creates a private/local repo's derived branch; `checkout` never does.

    Both halves matter. Without the first the feature is unreachable — no
    command could ever bring `<base>_<branch>` into existence, so resolution
    would fall back forever. Without the second, moving the tree would
    silently create a branch in a repository shared with other projects.
    """

    @staticmethod
    def _tree_and_runner(tmp_path: Path):
        registry = _make_registry_with_config_repo(tmp_path)
        runner = _FakeGitRunnerForOperations()
        for repo in registry.values():
            runner._current_branches[repo.absolute_path] = "main"
            runner._local_branches[repo.absolute_path] = {"main"}
        leaf = registry.get("root:deps/leaf").absolute_path
        runner._current_branches[leaf] = "project"
        runner._local_branches[leaf] = {"project"}
        return registry, runner

    def test_branch_creates_the_derived_branch_for_a_private_local_repo(self, tmp_path):
        registry, runner = self._tree_and_runner(tmp_path)
        leaf = registry.get("root:deps/leaf").absolute_path

        branch_tree(registry, runner, "multi-branch")

        assert (leaf, "project_multi-branch") in runner.created

    def test_branch_still_creates_the_plain_branch_for_a_project_repo(self, tmp_path):
        registry, runner = self._tree_and_runner(tmp_path)
        root = registry.get("root").absolute_path

        branch_tree(registry, runner, "multi-branch")

        assert (root, "multi-branch") in runner.created

    def test_branch_never_touches_a_private_distant_repo(self, tmp_path):
        registry, runner = self._tree_and_runner(tmp_path)
        leaf = registry.get("root:deps/leaf")
        leaf.writable = False
        propagate_privacy(registry)

        branch_tree(registry, runner, "multi-branch")

        assert leaf.absolute_path not in [path for path, _ in runner.created]

    def test_checkout_creates_and_moves_in_one_command(self, tmp_path):
        """The user asks for a branch, not for two spellings of one.

        `checkout <B>` puts the project's repos on `B` and its settings repo
        on the branch named after the project for `B`, creating it when it is
        not there. Nobody should have to know the second name.
        """
        registry, runner = self._tree_and_runner(tmp_path)
        leaf = registry.get("root:deps/leaf").absolute_path

        checkout_tree(registry, runner, "multi-branch")

        assert (leaf, "project_multi-branch") in runner.created
        assert (leaf, "project_multi-branch") in runner.checked_out
        assert (registry.get("root").absolute_path, "multi-branch") in runner.checked_out

    def test_checkout_main_puts_it_back_on_the_bare_project_name(self, tmp_path):
        registry, runner = self._tree_and_runner(tmp_path)
        leaf = registry.get("root:deps/leaf").absolute_path

        checkout_tree(registry, runner, "main")

        assert (leaf, "project") in runner.checked_out


class TestTheProjectNamesThePrivateLocalBranch:
    """`X` for the project's repos; `<project>` or `<project>_X` for its settings.

    `main` takes no suffix, because the project's main line's settings branch
    is simply the project's name — which is what every existing tree already
    has, so nothing has to migrate.
    """

    @staticmethod
    def _entry() -> WorkingRepo:
        return WorkingRepo(
            repo_id="c",
            name=".claude",
            private=True,
            writable=True,
            default_branch="MyProject",
            resolved_ref_name="MyProject_multi-branch",
            target_ref_name="MyProject_multi-branch",
        )

    def test_a_feature_branch_gets_the_suffix(self):
        assert (
            resolve_propagated_ref(
                self._entry(), "multi-branch", project_name="MyProject"
            ).name
            == "MyProject_multi-branch"
        )

    def test_main_gets_the_bare_project_name(self):
        """Going back to main lands on the project's own settings branch."""
        assert (
            resolve_propagated_ref(self._entry(), "main", project_name="MyProject").name
            == "MyProject"
        )

    def test_it_does_not_stay_on_the_branch_it_came_from(self):
        landed = resolve_propagated_ref(
            self._entry(), "main", project_name="MyProject"
        ).name

        assert landed != "MyProject_multi-branch"


class TestMergeRefusesToMergeABranchIntoItself:
    """Merging a branch into itself succeeds and does nothing, which reads as
    "it worked" when the tree is simply still on the branch you meant to
    merge *from*. That silence is the bug."""

    @staticmethod
    def _tree_and_runner(tmp_path: Path):
        registry = _make_ready_registry(tmp_path)
        runner = _FakeGitRunnerForOperations()
        for repo in registry.values():
            runner._current_branches[repo.absolute_path] = "multi-branch"
            runner._local_branches[repo.absolute_path] = {"main", "multi-branch"}
        return registry, runner

    def test_it_refuses_and_says_what_to_do(self, tmp_path):
        registry, runner = self._tree_and_runner(tmp_path)

        with pytest.raises(GitSyncError) as excinfo:
            merge_tree(registry, runner, "multi-branch")

        message = str(excinfo.value)
        assert "already on 'multi-branch'" in message
        assert "cgitsync checkout" in message, "the message must say how to fix it"
        assert runner.merged == []

    def test_merging_a_different_branch_still_works(self, tmp_path):
        registry, runner = self._tree_and_runner(tmp_path)

        merge_tree(registry, runner, "main")

        assert [ref for _, ref in runner.merged] == ["main", "main"]

    def test_the_plan_and_the_merge_agree(self, tmp_path):
        """One function decides both, so a dry run cannot promise a refusal."""
        registry, runner = self._tree_and_runner(tmp_path)
        root = registry.get("root")

        assert merge_status(root, runner, "multi-branch")[1] == "already-on-it"
        assert merge_status(root, runner, "main")[1] == "merge"


class TestRefreshPrivateTree:
    """`pull --private` keeps a settings branch current with its base.

    A private/local repository records this project's settings per project
    branch. While a feature branch is open, work lands on the base branch and
    the derived one does not see it. This is the command that closes that gap,
    and it uses the same merge primitive `merge` does.
    """

    @staticmethod
    def _tree_and_runner(tmp_path: Path, *, on_derived: bool = True):
        # A coherent tree: the project is on multi-branch, so its settings
        # repository is on the branch derived from it.
        registry = _make_registry_with_config_repo(tmp_path)
        runner = _FakeGitRunnerForOperations()
        root = registry.get("root").absolute_path
        leaf = registry.get("root:deps/leaf").absolute_path
        runner._current_branches[root] = "multi-branch"
        runner._current_branches[leaf] = (
            "project_multi-branch" if on_derived else "project"
        )
        runner._local_branches[root] = {"multi-branch"}
        runner._local_branches[leaf] = (
            {"project", "project_multi-branch"} if on_derived else {"project"}
        )
        return registry, runner

    def test_it_fetches_then_merges_the_base_branch(self, tmp_path):
        registry, runner = self._tree_and_runner(tmp_path)
        leaf = registry.get("root:deps/leaf").absolute_path

        refresh_private_tree(registry, runner)

        assert runner.fetched == [(leaf, "origin", "project")]
        assert runner.merged == [(leaf, "origin/project")]

    def test_a_repo_already_on_its_base_has_nothing_to_take(self, tmp_path):
        registry, runner = self._tree_and_runner(tmp_path, on_derived=False)
        # Coherent for that case means the project is on main, where the
        # settings branch is the bare project name.
        runner._current_branches[registry.get("root").absolute_path] = "main"

        assert refresh_private_tree(registry, runner) == ()
        assert runner.merged == []

    def test_a_project_owned_repo_is_never_touched(self, tmp_path):
        registry, runner = self._tree_and_runner(tmp_path)
        root = registry.get("root").absolute_path

        refresh_private_tree(registry, runner)

        assert root not in [path for path, _ in runner.merged]

    def test_a_conflict_leaves_nothing_merged(self, tmp_path):
        registry, runner = self._tree_and_runner(tmp_path)
        leaf = registry.get("root:deps/leaf").absolute_path
        runner._unmergeable[leaf] = {"origin/project"}

        with pytest.raises(GitSyncError, match="no repository was merged"):
            refresh_private_tree(registry, runner)

        assert runner.merged == []


def test_tag_tree_preflight_fails_when_repo_is_detached(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
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


@pytest.mark.parametrize(
    ("method_name", "selector", "ref_name"),
    [
        ("remote_branch_exists", "--heads", "main"),
        ("remote_tag_exists", "--tags", "v1.0.0"),
    ],
)
def test_git_runner_remote_ref_resolution_is_explicit_runtime_work(
    monkeypatch, method_name, selector, ref_name
):
    runner = GitRunner()
    captured: dict[str, object] = {}

    def _spy_run(_self, *args: str, cwd: Path | str | None = None):
        captured["args"] = args
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=0, stdout="deadbeef\tref\n", stderr=""
        )

    monkeypatch.setattr(GitRunner, "_run", _spy_run)

    exists = getattr(runner, method_name)("git@github.com:owner/repository.git", ref_name)

    assert exists is True
    assert captured["args"] == (
        "ls-remote",
        selector,
        "git@github.com:owner/repository.git",
        ref_name,
    )
    assert captured["cwd"] is None


def test_git_runner_file_transport_detection_handles_windows_paths():
    assert GitRunner._uses_file_transport("file:///tmp/remote.git") is True
    assert GitRunner._uses_file_transport("/tmp/remote.git") is True
    assert GitRunner._uses_file_transport(r"C:\tmp\remote.git") is True
    assert GitRunner._uses_file_transport("https://example.com/repo.git") is False
    assert GitRunner._uses_file_transport("git@github.com:owner/repo.git") is False


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

    snapshot_path = _current_state_path(root_path)
    assert snapshot_path.exists()
    assert _is_state_file(snapshot_path)


def test_client_checkout_delegates_to_gittree_git_checkout(tmp_path, monkeypatch):
    client, runner = _make_client_with_ready_registry(tmp_path)
    captured_call: dict[str, object] = {}

    def _spy_checkout(
        self, git_runner, branch_name, *, ref_kind=RefKind.BRANCH, tree=None, scope=None
    ):
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

    def _spy_branch(self, git_runner, branch_name, *, tree=None, scope=None):
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

    def _spy_commit(self, git_runner, message, *, stage_all=True, tree=None, scope=None):
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

    def _spy_add(self, git_runner, *, tree=None, paths=None, scope=None):
        captured_call["git_runner"] = git_runner
        captured_call["tree"] = tree

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "add", _spy_add)

    result = client.add()

    assert result is client.registry
    assert captured_call == {"git_runner": runner, "tree": None}


def test_client_add_forwards_paths_to_gittree_git_add(tmp_path, monkeypatch):
    client, runner = _make_client_with_ready_registry(tmp_path)
    captured_call: dict[str, object] = {}

    def _spy_add(self, git_runner, *, tree=None, paths=None, scope=None):
        captured_call["paths"] = paths

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "add", _spy_add)

    client.add(paths=["a.txt", "sub/b.txt"])

    assert captured_call == {"paths": ["a.txt", "sub/b.txt"]}


def test_client_push_delegates_to_gittree_git_push(tmp_path, monkeypatch):
    client, runner = _make_client_with_ready_registry(tmp_path)
    captured_call: dict[str, object] = {}

    def _spy_push(self, git_runner, *, tree=None, force_access_protocol=None, scope=None):
        captured_call["git_runner"] = git_runner
        captured_call["tree"] = tree

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "push", _spy_push)

    result = client.push()

    assert result is client.registry
    assert captured_call == {"git_runner": runner, "tree": None}


def test_client_tag_delegates_to_gittree_git_tag(tmp_path, monkeypatch):
    client, runner = _make_client_with_ready_registry(tmp_path)
    captured_call: dict[str, object] = {}

    def _spy_tag(self, git_runner, tag_name, *, tree=None, scope=None):
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

    def _spy_freeze(
        self, git_runner, tag_name, *, message=None, stage_all=True, tree=None, scope=None
    ):
        captured_call["git_runner"] = git_runner
        captured_call["tag_name"] = tag_name
        captured_call["message"] = message
        captured_call["stage_all"] = stage_all
        captured_call["tree"] = tree
        captured_call["scope"] = scope

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "freeze", _spy_freeze)

    result = client.freeze("release-1", message="msg", stage_all=False)

    assert result is client.registry
    assert captured_call == {
        "git_runner": runner,
        "tag_name": "release-1",
        "message": "msg",
        "stage_all": False,
        "tree": None,
        # Bare freeze still reaches every repository this project may write.
        "scope": RepoScope.WRITABLE,
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

    result = client.freeze("release-1", output_gts=output_gts)

    assert runner.committed, "Expected commit calls during freeze_release"
    assert runner.tagged, "Expected tag calls during freeze_release"
    assert not output_gts.exists()
    snapshot_path = _current_state_path(client.registry.get("root").absolute_path)
    snapshot_data = tomllib.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot_data["freeze_manifest"]["schema_version"] == "1.0"
    assert snapshot_data["freeze_manifest"]["synchronized_ref_kind"] == "tag"
    assert snapshot_data["freeze_manifest"]["synchronized_ref_name"] == "release-1"
    assert snapshot_data["freeze_manifest"]["release-name"] == "release-1"
    assert snapshot_data["freeze_manifest"]["restore_operation"] == "launch_state"
    assert snapshot_data["freeze_manifest"]["immutable_snapshot"] is True
    assert snapshot_data["freeze_manifest"]["workspace_validated"] is True
    assert snapshot_data["freeze_manifest"]["ledger_checkpoint"] is True
    assert result.recompute_tree_state() == TreeLifecycleState.READY


def test_client_freeze_release_writes_release_name_and_named_immutable_gts(tmp_path):

    client, runner = _make_client_with_ready_registry(tmp_path)
    root_path = client.registry.get("root").absolute_path

    result = client.freeze("release-1")

    immutable_snapshot = _current_state_path(root_path)
    assert immutable_snapshot.exists()
    assert _is_state_file(immutable_snapshot)
    snapshot_data = tomllib.loads(immutable_snapshot.read_text(encoding="utf-8"))
    assert snapshot_data["freeze_manifest"]["release-name"] == "release-1"
    [entry] = _ledger_entries(root_path)
    assert entry.state_id == f"state({immutable_snapshot.stem})"
    assert re.fullmatch(r"state\([0-9a-f]{64}\)", entry.state_id)
    assert result.recompute_tree_state() == TreeLifecycleState.READY


def test_write_freeze_snapshot_uses_explicit_freeze_name_over_stale_registry_tag(tmp_path):

    client, _runner = _make_client_with_ready_registry(tmp_path)
    root = client.registry.get("root")
    root.current_ref_kind = RefKind.TAG
    root.current_ref_name = "20260708-v2"
    root.resolved_ref_kind = RefKind.TAG
    root.resolved_ref_name = "20260708-v2"
    root.target_ref_kind = RefKind.TAG
    root.target_ref_name = "20260708-v2"

    result = client.write_gts_snapshot(
        command_origin="freeze_release",
        freeze_name="20260708-v3",
    )

    assert result.exists()
    assert _is_state_file(result)
    snapshot_data = tomllib.loads(result.read_text(encoding="utf-8"))
    assert snapshot_data["freeze_manifest"]["release-name"] == "20260708-v3"
    assert snapshot_data["freeze_manifest"]["synchronized_ref_name"] == "20260708-v3"


def test_freeze_snapshot_loaded_from_gts_creates_new_named_immutable_gts(tmp_path):

    client, runner = _make_client_with_ready_registry(tmp_path)
    root = client.registry.get("root")
    source_snapshot = root.absolute_path / ".cgitsync" / "state" / "gts-000005.gts"
    source_snapshot.parent.mkdir(parents=True, exist_ok=True)
    source_snapshot.write_text("# existing runtime snapshot\n", encoding="utf-8")
    original_source_content = source_snapshot.read_text(encoding="utf-8")
    client.source_path = source_snapshot
    client.state_store = RuntimeStateStore(tmp_path / "runtime-state")

    result = client.freeze("20260708-v4")

    immutable_snapshot = _current_state_path(root.absolute_path)
    assert result.recompute_tree_state() == TreeLifecycleState.READY
    assert source_snapshot.read_text(encoding="utf-8") == original_source_content
    assert immutable_snapshot.exists()
    assert _is_state_file(immutable_snapshot)

    assert _ledger_entries(root.absolute_path)[-1].state_id == (
        f"state({immutable_snapshot.stem})"
    )
    snapshot_data = tomllib.loads(immutable_snapshot.read_text(encoding="utf-8"))
    assert snapshot_data["freeze_manifest"]["release-name"] == "20260708-v4"


def test_client_launch_release_checkouts_release_tag_and_writes_gts(tmp_path, monkeypatch):
    client, runner = _make_client_with_ready_registry(tmp_path)
    captured_call: dict[str, object] = {}

    def _spy_checkout(
        self, git_runner, branch_name, *, ref_kind=RefKind.BRANCH, tree=None, scope=None
    ):
        captured_call["git_runner"] = git_runner
        captured_call["branch_name"] = branch_name
        captured_call["ref_kind"] = ref_kind
        captured_call["tree"] = tree

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "checkout", _spy_checkout)

    result = client.launch_release("v1.0.0")

    assert result is client.registry
    assert captured_call == {
        "git_runner": runner,
        "branch_name": "v1.0.0",
        "ref_kind": RefKind.TAG,
        "tree": None,
    }
    snapshot_path = _current_state_path(client.registry.get("root").absolute_path)
    assert snapshot_path.exists()
    assert _is_state_file(snapshot_path)
    assert result.recompute_tree_state() == TreeLifecycleState.READY


def test_client_launch_release_raises_when_no_registry_loaded():
    client = ComplexGitSyncClient()
    with pytest.raises(RuntimeError, match="No ComplexGitSync registry is loaded"):
        client.launch_release("v1.0.0")


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
        client.freeze("release-1")


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
    assert not output_gts.exists()
    assert _current_state_path(client.registry.get("root").absolute_path).exists()
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


# ---------------------------------------------------------------------------
# RepoOutcome — a tree-wide write says what it did to each repository, so a
# sweep that wrote nowhere is distinguishable from one that wrote everywhere.
# ---------------------------------------------------------------------------


def test_add_tree_reports_which_repos_had_nothing_to_stage(tmp_path):
    registry = _make_ready_registry(tmp_path)
    leaf_path = registry.get("root:deps/leaf").absolute_path

    runner = _FakeGitRunnerForOperations()
    runner.set_unstaged(leaf_path, True)

    outcomes = add_tree(registry, runner, scope=RepoScope.ALL)

    by_name = {outcome.name: outcome for outcome in outcomes}
    assert by_name["leaf"].acted is True
    assert by_name["leaf"].detail == "staged 1 change(s)"
    assert by_name["project"].acted is False
    assert by_name["project"].detail == "nothing to stage"


def test_add_tree_with_paths_reports_the_paths_it_staged(tmp_path):
    registry = _make_ready_registry(tmp_path)
    root_path = registry.get("root").absolute_path
    target = root_path / "a.txt"
    target.write_text("x", encoding="utf-8")

    outcomes = add_tree(registry, _FakeGitRunnerForOperations(), paths=[target])

    assert [(o.name, o.acted, o.detail) for o in outcomes] == [
        ("project", True, "staged a.txt")
    ]


def test_commit_tree_reports_committed_and_skipped_repos(tmp_path):
    registry = _make_ready_registry(tmp_path)
    leaf_path = registry.get("root:deps/leaf").absolute_path

    runner = _FakeGitRunnerForOperations()
    runner.set_staged(leaf_path, True)

    outcomes = commit_tree(registry, runner, "partial commit", stage_all=False)

    by_name = {outcome.name: outcome for outcome in outcomes}
    assert by_name["leaf"].acted is True
    assert by_name["leaf"].detail == registry.get("root:deps/leaf").commit_sha
    assert by_name["project"].acted is False
    assert "--no-stage" in by_name["project"].detail


def test_commit_tree_skip_reason_omits_the_no_stage_hint_when_staging(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()
    # A clean tree: staging finds nothing to stage, so nothing is committed.
    runner.stage_all = lambda repo_path: None

    outcomes = commit_tree(registry, runner, "nothing here", stage_all=True)

    assert outcomes
    assert all(outcome.acted is False for outcome in outcomes)
    assert all(outcome.detail == "nothing staged" for outcome in outcomes)


def test_push_tree_reports_a_repo_already_level_with_its_upstream(tmp_path):
    registry = _make_ready_registry(tmp_path)
    leaf_path = registry.get("root:deps/leaf").absolute_path
    root_path = registry.get("root").absolute_path

    runner = _FakeGitRunnerForOperations()
    ahead = {leaf_path: (3, 0), root_path: (0, 0)}
    runner.branch_tracking_counts = lambda repo_path: ahead[Path(repo_path)]

    outcomes = push_tree(registry, runner, scope=RepoScope.ALL)

    by_name = {outcome.name: outcome for outcome in outcomes}
    assert by_name["leaf"].acted is True
    assert by_name["leaf"].detail.endswith("(+3)")
    assert by_name["project"].acted is False
    assert by_name["project"].detail.endswith("already up to date")


def test_push_tree_does_not_claim_nothing_moved_without_an_upstream(tmp_path):
    registry = _make_ready_registry(tmp_path)
    runner = _FakeGitRunnerForOperations()

    outcomes = push_tree(registry, runner, scope=RepoScope.ALL)

    assert outcomes
    assert all(outcome.acted is True for outcome in outcomes)


# ---------------------------------------------------------------------------
# remove_paths / freeze_release_tree — the scope flags DeadScopeFlags wired up
# ---------------------------------------------------------------------------


class TestRemovePathsHonoursItsScope:
    """``rm --private`` has to change what is removed, not just be accepted.

    ``rm`` is handed its paths rather than sweeping for them, so its scope
    is a filter on the repository each path resolves to — see
    ``.localSpec/DevTickets/archive/20260912_DeadScopeFlags_DevPlanTicket.md`` §2.1.
    """

    @staticmethod
    def _tracked_file(registry: WorkingGitTree, repo_id: str, name: str) -> Path:
        target = registry.get(repo_id).absolute_path / name
        target.write_text("content\n", encoding="utf-8")
        return target

    def test_the_default_scope_still_reaches_a_configuration_repository(self, tmp_path):
        """Bare rm behaves exactly as it did before the flag was wired."""
        registry = _make_registry_with_config_repo(tmp_path)
        runner = _FakeGitRunnerForOperations()
        target = self._tracked_file(registry, "root:deps/leaf", "notes.md")

        outcomes = remove_paths(registry, runner, [target])

        assert [path for path, _ in runner.removed_paths] == [
            registry.get("root:deps/leaf").absolute_path
        ]
        assert [(o.name, o.acted, o.detail) for o in outcomes] == [
            ("leaf", True, "removed notes.md")
        ]

    def test_private_refuses_a_path_owned_by_the_project(self, tmp_path):
        registry = _make_registry_with_config_repo(tmp_path)
        runner = _FakeGitRunnerForOperations()
        target = self._tracked_file(registry, "root", "src.py")

        with pytest.raises(GitSyncError, match="outside this command's scope"):
            remove_paths(registry, runner, [target], scope=RepoScope.PRIVATE)

        assert runner.removed_paths == []

    def test_private_removes_from_the_configuration_repository(self, tmp_path):
        registry = _make_registry_with_config_repo(tmp_path)
        runner = _FakeGitRunnerForOperations()
        target = self._tracked_file(registry, "root:deps/leaf", "notes.md")

        remove_paths(registry, runner, [target], scope=RepoScope.PRIVATE)

        assert [path for path, _ in runner.removed_paths] == [
            registry.get("root:deps/leaf").absolute_path
        ]

    def test_one_path_outside_the_scope_removes_nothing_anywhere(self, tmp_path):
        """The scope check runs over every path before the first removal."""
        registry = _make_registry_with_config_repo(tmp_path)
        runner = _FakeGitRunnerForOperations()
        allowed = self._tracked_file(registry, "root:deps/leaf", "notes.md")
        refused = self._tracked_file(registry, "root", "src.py")

        with pytest.raises(GitSyncError, match="outside this command's scope"):
            remove_paths(registry, runner, [allowed, refused], scope=RepoScope.PRIVATE)

        assert runner.removed_paths == []
        assert allowed.exists()


class TestFreezeHonoursItsScope:
    def test_the_default_scope_freezes_every_writable_repository(self, tmp_path):
        registry = _make_registry_with_config_repo(tmp_path)
        runner = _FakeGitRunnerForOperations()
        runner._current_branches[registry.get("root").absolute_path] = "main"
        runner._current_branches[registry.get("root:deps/leaf").absolute_path] = "project"

        freeze_release_tree(registry, runner, "release-1")

        assert {path for path, _ in runner.tagged} == {
            registry.get("root").absolute_path,
            registry.get("root:deps/leaf").absolute_path,
        }

    def test_private_freezes_the_configuration_repository_alone(self, tmp_path):
        registry = _make_registry_with_config_repo(tmp_path)
        runner = _FakeGitRunnerForOperations()
        runner._current_branches[registry.get("root").absolute_path] = "main"
        runner._current_branches[registry.get("root:deps/leaf").absolute_path] = "project"

        freeze_release_tree(registry, runner, "release-1", scope=RepoScope.PRIVATE)

        assert [path for path, _ in runner.tagged] == [
            registry.get("root:deps/leaf").absolute_path
        ]


class TestPathsOutsideScopeIsAReadOnlyQuestion:
    """``rm --dry-run`` has to ask what the real run will answer."""

    def test_it_names_every_path_the_scope_excludes(self, tmp_path):
        registry = _make_registry_with_config_repo(tmp_path)
        owned = registry.get("root").absolute_path / "src.py"
        owned.write_text("x\n", encoding="utf-8")

        refusals = paths_outside_scope(registry, [owned], scope=RepoScope.PRIVATE)

        assert len(refusals) == 1
        assert "outside this command's scope (private)" in refusals[0]
        assert "--private" in refusals[0]

    def test_it_says_nothing_when_every_path_is_in_scope(self, tmp_path):
        registry = _make_registry_with_config_repo(tmp_path)
        config_file = registry.get("root:deps/leaf").absolute_path / "notes.md"
        config_file.write_text("x\n", encoding="utf-8")

        assert paths_outside_scope(registry, [config_file], scope=RepoScope.PRIVATE) == ()

    def test_a_path_outside_the_tree_is_left_to_the_resolver_to_report(self, tmp_path):
        registry = _make_registry_with_config_repo(tmp_path)

        assert paths_outside_scope(registry, ["/nowhere/at/all"], scope=RepoScope.PRIVATE) == ()

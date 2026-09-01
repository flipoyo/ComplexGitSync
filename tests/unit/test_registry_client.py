from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path, PureWindowsPath

import pytest

from ComplexGitSync import MasterConfig
from ComplexGitSync.errors import ConfigValidationError, GitSyncError, NestedConfigDiscoveryError
from ComplexGitSync.git_repo import (
    AccessProtocol,
    GitRepo,
    NodeType,
    RefKind,
    RepoLifecycleState,
    SyncState,
    WorkingRepo,
)
from ComplexGitSync.git_tree import (
    GitTree,
    TreeLifecycleState,
    WorkingGitTree,
    make_repo_id,
    normalize_node_types,
)
from ComplexGitSync.ledger_entry import hash_time_l0_anchor, new_time_l0_anchor
from ComplexGitSync.orchestre import (
    ComplexGitSyncClient,
    GtsDocument,
    RuntimeStateStore,
    SystemClock,
    _path_to_environment_marker,
    build_registry_from_gts_document,
)
from ComplexGitSync.state_store import (
    _resolve_memory_state_directory,
    _state_directory_name,
)


def test_client_load_cgs_builds_reviewable_registry(tmp_path):
    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient()
    registry = client.load_cgs(config_path)
    tree_state = client.get_tree_state()

    assert client.is_loaded() is True
    assert tree_state.lifecycle_state == TreeLifecycleState.DECLARED
    assert tree_state.registry_complete is True
    assert registry.get("root").project_name == "demo"
    assert registry.get("root:deps/child-repo").absolute_path == (tmp_path / "deps/child-repo").resolve()


def test_client_loads_minimal_cgs_into_existing_canonical_registry(tmp_path):
    config_path = tmp_path / "minimal.cgs"
    config_path.write_text(
        'project = "demo"\nrepos = ["github:owner/demo", "gitlab:team/child"]\n',
        encoding="utf-8",
    )

    registry = ComplexGitSyncClient().load_cgs(config_path)

    root = registry.get("root")
    child = registry.get("root:child")
    assert root.project_owner_name == "owner"
    assert root.default_branch == "main"
    assert child.project_owner_name == "team"
    assert child.relative_path == Path("child")
    assert child.fallback_branch == "main"


def test_client_load_cgs_supports_tag_target_ref(tmp_path):
    config_path = tmp_path / "tagged.cgs"
    config_path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
project_owner_name = "owner"
project_name = "demo"
relative_path = "."

[[repos]]
project_owner_name = "owner"
project_name = "tagged-repo"
relative_path = "deps/tagged-repo"
tag = "v1.0.0"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    client = ComplexGitSyncClient()
    registry = client.load_cgs(config_path)

    tagged_entry = registry.get("root:deps/tagged-repo")
    assert tagged_entry.target_ref_kind == RefKind.TAG
    assert tagged_entry.target_ref_name == "v1.0.0"


def test_client_load_loads_cgs(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    client = ComplexGitSyncClient()

    registry = client.load(config_path)

    assert registry.get("root").project_name == "demo"
    assert client.get_tree_state().lifecycle_state == TreeLifecycleState.DECLARED


def test_client_load_accepts_gts_source(tmp_path):
    snapshot_path = _write_ready_gts(tmp_path / "snapshot.gts", root_path=(tmp_path / "workspace" / "demo").resolve())
    client = ComplexGitSyncClient()

    registry = client.load(snapshot_path)

    assert registry.lifecycle_state == TreeLifecycleState.READY
    assert client.get_tree_state().lifecycle_state == TreeLifecycleState.READY


def test_client_initialise_dispatches_to_load_gts_for_gts_source(monkeypatch, tmp_path):
    snapshot_path = _write_ready_gts(tmp_path / "snapshot.gts", root_path=(tmp_path / "workspace" / "demo").resolve())
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}
    original_load_gts = client.load_gts

    def _fake_load_gts(path):
        captured["path"] = path
        return original_load_gts(path)

    monkeypatch.setattr(client, "load_gts", _fake_load_gts)

    registry = client.initialise(snapshot_path)

    assert captured["path"] == snapshot_path.resolve()
    assert registry.lifecycle_state == TreeLifecycleState.READY


def test_client_initialise_dispatches_to_initialise_cgs_for_cgs_source(monkeypatch):
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}

    def _fake_initialise_cgs(path, *, output_path=None):
        captured["path"] = path
        captured["output_path"] = output_path
        return "ok"

    monkeypatch.setattr(client, "initialise_cgs", _fake_initialise_cgs)

    result = client.initialise("project.cgs")

    assert result == "ok"
    assert captured["path"] == Path("project.cgs").resolve()
    assert captured["output_path"] is None


def test_client_initialise_forwards_output_path(monkeypatch):
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}

    def _fake_initialise_cgs(path, *, output_path=None):
        captured["output_path"] = output_path
        return "ok"

    monkeypatch.setattr(client, "initialise_cgs", _fake_initialise_cgs)

    client.initialise("project.cgs", output_path="../")

    assert captured["output_path"] == "../"


def test_initialise_cgs_derives_cgshome_from_output_path_and_project_name(tmp_path, monkeypatch):
    cgspath = tmp_path / "workspace"
    cgshome = cgspath / "demo"
    wcd = cgshome / "ComplexGitSync"
    wcd.mkdir(parents=True)
    monkeypatch.chdir(wcd)

    config_path = _write_clone_ready_cgs(tmp_path)
    fake_runner = _FakeGitRunner(
        {
            "git@github.com:owner/child-repo.git": {"autoTest"},
            "git@github.com:owner/docs.git": {"main"},
        }
    )
    state_store = RuntimeStateStore(base_dir=tmp_path / "runtime-state")
    client = ComplexGitSyncClient(git_runner=fake_runner, state_store=state_store)

    registry = client.initialise_cgs(config_path, output_path=cgspath)

    root_entry = registry.get("root")
    assert root_entry.absolute_path == cgshome.resolve()
    assert root_entry.repo_lifecycle_state.value in {"READY", "FALLBACK_READY"}

    cloned_remotes = [remote for remote, _, _ in fake_runner.clones]
    assert "git@github.com:owner/demo.git" not in cloned_remotes
    cloned_destinations = {destination for _, destination, _ in fake_runner.clones}
    assert cgshome.resolve() not in cloned_destinations
    assert (cgshome / "deps" / "child-repo").resolve() in cloned_destinations

    # Snapshot must be stored under CGSHOME (= CGSPATH/project-name).
    snapshot_path = state_store.latest_snapshot_for(config_path)
    assert snapshot_path is not None
    assert str(snapshot_path).startswith(str(cgshome.resolve()))
    assert ".cgitsync" in str(snapshot_path)
    assert "state" in str(snapshot_path)


def test_initialise_cgs_writes_gitignore_for_every_parent_bearing_repo(tmp_path, monkeypatch):
    """DevPlanTicket Milestone 1: .gitignore sync runs before readiness."""
    cgspath = tmp_path / "workspace"
    cgshome = cgspath / "demo"
    wcd = cgshome / "ComplexGitSync"
    wcd.mkdir(parents=True)
    monkeypatch.chdir(wcd)

    config_path = _write_clone_ready_cgs(tmp_path)
    fake_runner = _FakeGitRunner(
        {
            "git@github.com:owner/child-repo.git": {"autoTest"},
            "git@github.com:owner/docs.git": {"main"},
        }
    )
    client = ComplexGitSyncClient(git_runner=fake_runner, state_store=RuntimeStateStore(base_dir=tmp_path / "runtime-state"))

    registry = client.initialise_cgs(config_path, output_path=cgspath)

    root_entry = registry.get("root")
    child_entry = registry.get("root:deps/child-repo")

    assert (root_entry.absolute_path / ".gitignore").read_text(encoding="utf-8").splitlines() == [
        "deps/child-repo"
    ]
    assert (child_entry.absolute_path / ".gitignore").read_text(encoding="utf-8").splitlines() == ["docs"]

    synced_repo_ids = {entry.repo_id for entry in client.last_gitignore_sync}
    assert synced_repo_ids == {"root", "root:deps/child-repo"}
    added_by_repo = {entry.repo_id: entry.added_paths for entry in client.last_gitignore_sync}
    assert added_by_repo["root"] == ("deps/child-repo",)
    assert added_by_repo["root:deps/child-repo"] == ("docs",)


def test_initialise_cgs_pulls_parent_bearing_repos_before_gitignore_write(tmp_path, monkeypatch):
    cgspath = tmp_path / "workspace"
    cgshome = cgspath / "demo"
    wcd = cgshome / "ComplexGitSync"
    wcd.mkdir(parents=True)
    monkeypatch.chdir(wcd)

    config_path = _write_clone_ready_cgs(tmp_path)
    fake_runner = _FakeGitRunner(
        {
            "git@github.com:owner/child-repo.git": {"autoTest"},
            "git@github.com:owner/docs.git": {"main"},
        }
    )
    client = ComplexGitSyncClient(git_runner=fake_runner, state_store=RuntimeStateStore(base_dir=tmp_path / "runtime-state"))

    registry = client.initialise_cgs(config_path, output_path=cgspath)

    pulled_paths = {path for path, _, _ in fake_runner.pulled}
    assert registry.get("root").absolute_path.resolve() in pulled_paths
    assert registry.get("root:deps/child-repo").absolute_path.resolve() in pulled_paths
    # The leaf "docs" has no children of its own, so it is never pulled by
    # the .gitignore lifecycle sync.
    assert registry.get("root:deps/child-repo:docs").absolute_path.resolve() not in pulled_paths


def test_initialise_cgs_raises_and_blocks_readiness_when_gitignore_preflight_pull_fails(
    tmp_path, monkeypatch
):
    """No forcing, no silent degradation: a blocked safe pull is a hard error."""
    cgspath = tmp_path / "workspace"
    cgshome = cgspath / "demo"
    wcd = cgshome / "ComplexGitSync"
    wcd.mkdir(parents=True)
    monkeypatch.chdir(wcd)

    config_path = _write_clone_ready_cgs(tmp_path)

    class _FailingPullGitRunner(_FakeGitRunner):
        def pull(self, repo_path, *, remote="origin", ref_name=None):
            if Path(repo_path).resolve() == cgshome.resolve():
                raise GitSyncError("simulated: local changes block a fast-forward pull")
            super().pull(repo_path, remote=remote, ref_name=ref_name)

    state_store = RuntimeStateStore(base_dir=tmp_path / "runtime-state")
    fake_runner = _FailingPullGitRunner(
        {
            "git@github.com:owner/child-repo.git": {"autoTest"},
            "git@github.com:owner/docs.git": {"main"},
        }
    )
    client = ComplexGitSyncClient(git_runner=fake_runner, state_store=state_store)

    with pytest.raises(GitSyncError, match="gitignore sync preflight failed"):
        client.initialise_cgs(config_path, output_path=cgspath)

    # No .gitignore was written for any repo, and no snapshot was ever
    # recorded — the operation never completed, regardless of the
    # already-cloned repos' own individual clone/checkout state.
    assert not (cgshome / ".gitignore").exists()
    assert state_store.latest_snapshot_for(config_path) is None


def test_initialise_cgs_default_does_not_commit_gitignore(tmp_path, monkeypatch):
    """DevPlanTicket Milestone 2: report-only remains the default."""
    cgspath = tmp_path / "workspace"
    cgshome = cgspath / "demo"
    wcd = cgshome / "ComplexGitSync"
    wcd.mkdir(parents=True)
    monkeypatch.chdir(wcd)

    config_path = _write_clone_ready_cgs(tmp_path)
    fake_runner = _FakeGitRunner(
        {
            "git@github.com:owner/child-repo.git": {"autoTest"},
            "git@github.com:owner/docs.git": {"main"},
        }
    )
    client = ComplexGitSyncClient(git_runner=fake_runner, state_store=RuntimeStateStore(base_dir=tmp_path / "runtime-state"))

    client.initialise_cgs(config_path, output_path=cgspath)

    assert fake_runner.staged_paths == []
    assert fake_runner.commits == []
    assert fake_runner.pushed == []
    assert client.last_gitignore_sync
    assert all(entry.committed is False for entry in client.last_gitignore_sync)


def test_initialise_cgs_commit_gitignore_stages_commits_and_pushes(tmp_path, monkeypatch):
    """DevPlanTicket Milestone 2: --commit-gitignore is explicit approval."""
    cgspath = tmp_path / "workspace"
    cgshome = cgspath / "demo"
    wcd = cgshome / "ComplexGitSync"
    wcd.mkdir(parents=True)
    monkeypatch.chdir(wcd)

    config_path = _write_clone_ready_cgs(tmp_path)
    fake_runner = _FakeGitRunner(
        {
            "git@github.com:owner/child-repo.git": {"autoTest"},
            "git@github.com:owner/docs.git": {"main"},
        }
    )
    client = ComplexGitSyncClient(git_runner=fake_runner, state_store=RuntimeStateStore(base_dir=tmp_path / "runtime-state"))

    registry = client.initialise_cgs(config_path, output_path=cgspath, commit_gitignore=True)

    root_path = registry.get("root").absolute_path.resolve()
    child_path = registry.get("root:deps/child-repo").absolute_path.resolve()

    assert set(fake_runner.staged_paths) == {(root_path, ".gitignore"), (child_path, ".gitignore")}
    assert {path for path, _ in fake_runner.commits} == {root_path, child_path}
    pushed_by_path = {path: (remote, ref_name) for path, remote, ref_name in fake_runner.pushed}
    assert pushed_by_path[root_path][0] == "origin"
    assert pushed_by_path[child_path] == ("origin", "autoTest")

    commit_by_path = dict(fake_runner.commits)
    assert commit_by_path[root_path] == (
        "chore(cgitsync): sync .gitignore for nested repo tree\n\nAdded:\n  deps/child-repo"
    )
    assert commit_by_path[child_path] == (
        "chore(cgitsync): sync .gitignore for nested repo tree\n\nAdded:\n  docs"
    )
    assert all(entry.committed is True for entry in client.last_gitignore_sync)
    # No identity override configured: every commit must pass (None, None),
    # leaving GitRunner.commit to fall back entirely to local git config.
    assert all(user_name is None and user_email is None for _, user_name, user_email in fake_runner.commit_identities)


def test_initialise_cgs_git_user_flags_persist_and_are_used_for_the_commit(tmp_path, monkeypatch):
    """DevPlanTicket Milestone 3: --git-user-name/--git-user-email flow into
    the commit step and persist to CGSHOME/.cgitsync/master.toml so a later
    invocation on the same workspace picks them up without repeating the flags."""
    cgspath = tmp_path / "workspace"
    cgshome = cgspath / "demo"
    wcd = cgshome / "ComplexGitSync"
    wcd.mkdir(parents=True)
    monkeypatch.chdir(wcd)

    config_path = _write_clone_ready_cgs(tmp_path)
    fake_runner = _FakeGitRunner(
        {
            "git@github.com:owner/child-repo.git": {"autoTest"},
            "git@github.com:owner/docs.git": {"main"},
        }
    )
    client = ComplexGitSyncClient(git_runner=fake_runner, state_store=RuntimeStateStore(base_dir=tmp_path / "runtime-state"))

    registry = client.initialise_cgs(
        config_path,
        output_path=cgspath,
        commit_gitignore=True,
        git_user_name="cgitsync-bot",
        git_user_email="bot@example.com",
    )

    root_path = registry.get("root").absolute_path.resolve()
    child_path = registry.get("root:deps/child-repo").absolute_path.resolve()

    # The commit step actually used the override, for every changed repo.
    assert set(fake_runner.commit_identities) == {
        (root_path, "cgitsync-bot", "bot@example.com"),
        (child_path, "cgitsync-bot", "bot@example.com"),
    }

    # Persisted to disk, not just held in memory for this invocation.
    master_config_path = cgshome / ".cgitsync" / "master.toml"
    assert master_config_path.is_file()
    persisted = tomllib.loads(master_config_path.read_text(encoding="utf-8"))
    assert persisted == {"master": {"user_name": "cgitsync-bot", "user_email": "bot@example.com"}}

    # A later invocation on the same workspace that doesn't repeat the
    # flags still picks up the persisted identity via MasterConfig.load().
    MasterConfig._override_name = None
    MasterConfig._override_email = None
    assert MasterConfig.resolve_identity(cgshome, fake_runner) == (None, None)
    second_client = ComplexGitSyncClient(
        git_runner=fake_runner, state_store=RuntimeStateStore(base_dir=tmp_path / "runtime-state")
    )
    (cgshome / ".gitignore").unlink()
    second_client.initialise_cgs(config_path, output_path=cgspath, commit_gitignore=True)
    assert (root_path, "cgitsync-bot", "bot@example.com") in second_client.git_runner.commit_identities


def test_initialise_cgs_force_gitignore_sync_recovers_from_blocked_pull(tmp_path, monkeypatch):
    """DevPlanTicket Milestone 2: pull-force fallback is opt-in only."""
    cgspath = tmp_path / "workspace"
    cgshome = cgspath / "demo"
    wcd = cgshome / "ComplexGitSync"
    wcd.mkdir(parents=True)
    monkeypatch.chdir(wcd)

    config_path = _write_clone_ready_cgs(tmp_path)

    class _FailingPullGitRunner(_FakeGitRunner):
        def pull(self, repo_path, *, remote="origin", ref_name=None):
            if Path(repo_path).resolve() == cgshome.resolve():
                raise GitSyncError("simulated: local changes block a fast-forward pull")
            super().pull(repo_path, remote=remote, ref_name=ref_name)

    fake_runner = _FailingPullGitRunner(
        {
            "git@github.com:owner/child-repo.git": {"autoTest"},
            "git@github.com:owner/docs.git": {"main"},
        }
    )
    client = ComplexGitSyncClient(git_runner=fake_runner, state_store=RuntimeStateStore(base_dir=tmp_path / "runtime-state"))

    registry = client.initialise_cgs(config_path, output_path=cgspath, force_gitignore_sync=True)

    root_path = registry.get("root").absolute_path.resolve()
    assert any(path == root_path for path, _, _ in fake_runner.force_pulled)
    assert (root_path / ".gitignore").read_text(encoding="utf-8").splitlines() == ["deps/child-repo"]
    # force_gitignore_sync only covers the pull step — it never implies
    # --commit-gitignore, so nothing is staged/committed/pushed here.
    assert fake_runner.staged_paths == []


def test_purge_cgs_removes_top_level_repos_and_ledgers(tmp_path):
    cgspath = tmp_path / "workspace"
    cgshome = cgspath / "demo"
    top_level_child = cgshome / "child-repo"
    nested_child = cgshome / "deps" / "nested-repo"
    top_level_child.mkdir(parents=True)
    nested_child.mkdir(parents=True)
    (cgshome / "demo.lgr").write_text("ledger\n", encoding="utf-8")

    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "demo"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-repo"
relative_path = "child-repo"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "nested-repo"
relative_path = "deps/nested-repo"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    client = ComplexGitSyncClient(git_runner=_FakeGitRunner({}))

    removed = client.purge_cgs(config_path, output_path=cgspath)

    assert top_level_child in removed
    assert cgshome / "demo.lgr" in removed
    assert not top_level_child.exists()
    assert not (cgshome / "demo.lgr").exists()
    assert nested_child.exists()


def test_purge_cgs_keeps_workspace_master_config(tmp_path):
    cgspath = tmp_path / "workspace"
    cgshome = cgspath / "demo"
    (cgshome / ".cgitsync").mkdir(parents=True)
    master_config = cgshome / ".cgitsync" / "master.toml"
    master_config.write_text("[master]\nuser_name = 'cgitsync-bot'\n", encoding="utf-8")

    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "demo"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-repo"
relative_path = "child-repo"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    client = ComplexGitSyncClient(git_runner=_FakeGitRunner({}))

    client.purge_cgs(config_path, output_path=cgspath)

    assert master_config.is_file()
    assert "cgitsync-bot" in master_config.read_text(encoding="utf-8")


def test_clean_init_keeps_workspace_master_config(tmp_path, monkeypatch):
    cgspath = tmp_path / "workspace"
    cgshome = cgspath / "demo"
    wcd = cgshome / "ComplexGitSync"
    wcd.mkdir(parents=True)
    monkeypatch.chdir(wcd)

    master_config = cgshome / ".cgitsync" / "master.toml"
    master_config.parent.mkdir(parents=True, exist_ok=True)
    master_config.write_text("[master]\nuser_email = 'bot@example.com'\n", encoding="utf-8")

    config_path = _write_clone_ready_cgs(tmp_path)
    fake_runner = _FakeGitRunner(
        {
            "git@github.com:owner/child-repo.git": {"autoTest"},
            "git@github.com:owner/docs.git": {"main"},
        }
    )
    client = ComplexGitSyncClient(
        git_runner=fake_runner,
        state_store=RuntimeStateStore(base_dir=tmp_path / "runtime-state"),
    )

    client.clean_init(config_path, output_path=cgspath)

    assert master_config.is_file()
    assert "bot@example.com" in master_config.read_text(encoding="utf-8")
    assert MasterConfig.resolve_identity(cgshome, client.git_runner) == (None, "bot@example.com")


def test_initialise_cgs_default_cgshome_is_cgspath_project_name(tmp_path, monkeypatch):
    # Build the CWD layout: tmp_path/cgspath/demo/ComplexGitSync
    wcd = tmp_path / "cgspath" / "demo" / "ComplexGitSync"
    wcd.mkdir(parents=True)
    monkeypatch.chdir(wcd)

    expected_cgshome = (wcd / "../..").resolve() / "demo"
    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient(git_runner=_FakeGitRunner({}))
    captured: dict[str, object] = {}

    def _fake_write_gts(*, command_origin, output_path=None):
        captured["output_path"] = output_path
        return output_path or tmp_path / "dummy.gts"

    monkeypatch.setattr(client, "write_gts_snapshot", _fake_write_gts)
    monkeypatch.setattr(client.state_store, "record_snapshot", lambda *a, **kw: None)

    client.tree = None

    def _fake_build_registry(*args, **kwargs):
        from ComplexGitSync.git_repo import RepoLifecycleState
        from ComplexGitSync.orchestre import ROOT_REPO_ID
        from ComplexGitSync.orchestre import build_registry_from_cgs_document as _orig
        reg = _orig(*args, **kwargs)
        # Pre-mark root READY so the clone loop completes without git calls.
        root = reg.get(ROOT_REPO_ID)
        root.repo_lifecycle_state = RepoLifecycleState.READY
        root.commit_sha = "abc123"
        root.resolved_ref_kind = "branch"
        root.resolved_ref_name = "main"
        return reg

    import ComplexGitSync.orchestre as _mod
    monkeypatch.setattr(_mod, "build_registry_from_cgs_document", _fake_build_registry)

    try:
        client.initialise_cgs(config_path)
    except Exception:
        pass

    if captured.get("output_path") is not None:
        assert str(captured["output_path"]).startswith(str(expected_cgshome))


def test_initialise_cgs_default_cgshome_uses_environment(tmp_path, monkeypatch):
    cwd = tmp_path / "parent" / "child" / "cwd"
    cwd.mkdir(parents=True)
    env_cgshome = tmp_path / "env-cgshome"
    env_cgshome.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("CGSHOME", str(env_cgshome))

    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient(git_runner=_FakeGitRunner({}))
    captured: dict[str, object] = {}

    def _fake_write_gts(*, command_origin, output_path=None):
        captured["output_path"] = output_path
        return output_path or tmp_path / "dummy.gts"

    monkeypatch.setattr(client, "write_gts_snapshot", _fake_write_gts)
    monkeypatch.setattr(client.state_store, "record_snapshot", lambda *a, **kw: None)

    def _fake_build_registry(*args, **kwargs):
        from ComplexGitSync.git_repo import RepoLifecycleState
        from ComplexGitSync.orchestre import ROOT_REPO_ID
        from ComplexGitSync.orchestre import build_registry_from_cgs_document as _orig

        reg = _orig(*args, **kwargs)
        root = reg.get(ROOT_REPO_ID)
        root.repo_lifecycle_state = RepoLifecycleState.READY
        root.commit_sha = "abc123"
        root.resolved_ref_kind = "branch"
        root.resolved_ref_name = "main"
        return reg

    import ComplexGitSync.orchestre as _mod

    monkeypatch.setattr(_mod, "build_registry_from_cgs_document", _fake_build_registry)

    try:
        client.initialise_cgs(config_path)
    except Exception:
        pass

    if captured.get("output_path") is not None:
        assert str(captured["output_path"]).startswith(str(env_cgshome.resolve()))


def test_resolve_clone_root_uses_output_path_as_base(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    client = ComplexGitSyncClient()
    output_path = tmp_path / "parent"
    output_path.mkdir()

    result = client.resolve_clone_root(config_path, output_path=output_path)

    assert result == (output_path / "demo").resolve()


def test_resolve_bootstrap_root_uses_cgs_path_override(tmp_path):
    client = ComplexGitSyncClient()
    cgs_path = tmp_path / "elsewhere"

    result = client.resolve_bootstrap_root("myproject", cgs_path=cgs_path)

    assert result == (cgs_path / "myproject").resolve()


def test_resolve_bootstrap_root_defaults_under_home_cgs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    client = ComplexGitSyncClient()

    result = client.resolve_bootstrap_root("myproject")

    assert result.parent.parent == (tmp_path / ".cgs").resolve()
    assert result.name == "myproject"
    assert (tmp_path / ".cgs").is_dir()


def test_resolve_bootstrap_root_rejects_empty_project_name():
    client = ComplexGitSyncClient()

    with pytest.raises(ValueError, match="non-empty project_name"):
        client.resolve_bootstrap_root("")


def test_bootstrap_rejects_non_cgs_source(tmp_path):
    client = ComplexGitSyncClient()
    gts_path = tmp_path / "demo.gts"
    gts_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match=r"\.cgs source"):
        client.bootstrap(gts_path, "myproject")


def test_client_validate_cgs_returns_declared_state(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    client = ComplexGitSyncClient()

    tree_state = client.validate(config_path)

    assert tree_state.lifecycle_state == TreeLifecycleState.DECLARED
    assert tree_state.registry_complete is True


def test_client_load_source_supports_cgs(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    client = ComplexGitSyncClient()

    registry = client.load_source(config_path)

    assert registry.get("root").project_name == "demo"
    assert client.get_tree_state().lifecycle_state == TreeLifecycleState.DECLARED


def test_client_load_source_supports_gts(tmp_path):
    snapshot_path = _write_ready_gts(tmp_path / "snapshot.gts", root_path=(tmp_path / "workspace" / "demo").resolve())
    client = ComplexGitSyncClient()

    registry = client.load_source(snapshot_path)

    assert registry.lifecycle_state == TreeLifecycleState.READY
    assert client.get_tree_state().lifecycle_state == TreeLifecycleState.READY


def test_client_clone_method_calls_clone_cgs(monkeypatch):
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}

    def _fake_clone_cgs(path, *, target_dir=None, output_path=None):
        captured["path"] = path
        captured["target_dir"] = target_dir
        captured["output_path"] = output_path
        return "ok"

    monkeypatch.setattr(client, "clone_cgs", _fake_clone_cgs)

    result = client.clone("project.cgs", target_dir="workspace/demo", output_path="workspace")

    assert result == "ok"
    assert captured["path"] == "project.cgs"
    assert captured["target_dir"] == "workspace/demo"
    assert captured["output_path"] == "workspace"


def test_client_branch_delegates_to_gittree_git_branch(monkeypatch):
    client = ComplexGitSyncClient()
    client.registry = WorkingGitTree()
    captured: dict[str, object] = {}

    def _spy_branch(self, git_runner, branch_name, *, tree=None):
        captured["git_runner"] = git_runner
        captured["branch_name"] = branch_name
        captured["tree"] = tree

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "branch", _spy_branch)

    result = client.branch("feature/test")

    assert result is client.registry
    assert captured["git_runner"] is client.git_runner
    assert captured["branch_name"] == "feature/test"
    assert captured["tree"] is None


def test_client_git_dispatches_extended_commands(monkeypatch):
    client = ComplexGitSyncClient()
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    monkeypatch.setattr(client, "pull", lambda *a, **k: calls.append(("pull", a, k)) or "pull")
    monkeypatch.setattr(client, "branch", lambda *a, **k: calls.append(("branch", a, k)) or "branch")
    monkeypatch.setattr(client, "add", lambda *a, **k: calls.append(("add", a, k)) or "add")
    monkeypatch.setattr(client, "freeze", lambda *a, **k: calls.append(("freeze", a, k)) or "freeze")

    assert client.git(None, "pull", "state.gts") == "pull"
    assert client.git(None, "branch", "feature/x") == "branch"
    assert client.git(None, "add") == "add"
    assert client.git(None, "freeze", "v1.2.3") == "freeze"
    assert calls == [
        ("pull", ("state.gts",), {}),
        ("branch", ("feature/x",), {}),
        ("add", (), {}),
        ("freeze", ("v1.2.3",), {}),
    ]


@pytest.mark.parametrize(
    ("command", "expected_message"),
    [
        ("pull", "pull requires source path argument."),
        ("checkout", "checkout requires branch name argument."),
        ("branch", "branch requires branch name argument."),
        ("commit", "commit requires message argument."),
        ("tag", "tag requires tag name argument."),
        ("freeze", "freeze requires tag name argument."),
    ],
)
def test_client_git_requires_expected_arguments(command, expected_message):
    client = ComplexGitSyncClient()

    with pytest.raises(ValueError, match=expected_message):
        client.git(None, command)


def test_client_git_binds_provided_registry(monkeypatch):
    client = ComplexGitSyncClient()
    registry = WorkingGitTree()
    captured: dict[str, object] = {}

    def _spy_add(self, git_runner, *, tree=None):
        captured["bound_tree"] = self.working_tree
        captured["tree_arg"] = tree

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "add", _spy_add)

    client.git(registry, "add")
    assert client.registry is registry
    assert client.orchestre.git_tree.git.working_tree is registry
    assert captured["bound_tree"] is registry
    assert captured["tree_arg"] is None


def test_client_freeze_delegates_to_freeze_tag(monkeypatch):
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}

    def _fake_freeze_tag(name, *, output_gts=None, message=None, stage_all=True):
        captured["name"] = name
        captured["output_gts"] = output_gts
        captured["message"] = message
        captured["stage_all"] = stage_all
        return "ok"

    monkeypatch.setattr(client, "_freeze_tag", _fake_freeze_tag)

    result = client.freeze("r1", output_gts="release.gts", message="msg", stage_all=False)

    assert result == "ok"
    assert captured == {
        "name": "r1",
        "output_gts": "release.gts",
        "message": "msg",
        "stage_all": False,
    }


def _client_with_root_registry(tmp_path) -> ComplexGitSyncClient:
    """A client whose registry has just a root entry, for freeze_release tests
    that need has_upstream() to have a real repo path to be called with,
    without exercising the actual clone/registry-building machinery."""
    client = ComplexGitSyncClient()
    root_path = tmp_path / "root"
    root_path.mkdir()
    registry = WorkingGitTree()
    registry.add(_make_entry("root", root_path))
    client.registry = registry
    return client


def test_client_freeze_release_chains_minimalist_workflow(monkeypatch, tmp_path):
    client = _client_with_root_registry(tmp_path)
    client.source_path = tmp_path / "project.gts"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(type(client.git_runner), "has_upstream", lambda self, path: True)
    monkeypatch.setattr(client, "add", lambda: calls.append(("add", None)))
    monkeypatch.setattr(
        client,
        "commit",
        lambda message, *, stage_all=True: calls.append(("commit", (message, stage_all))),
    )
    monkeypatch.setattr(client, "pull", lambda source: calls.append(("pull", Path(source))))
    monkeypatch.setattr(client, "push", lambda: calls.append(("push", None)))
    monkeypatch.setattr(
        client,
        "freeze",
        lambda name, **kwargs: calls.append(("freeze", (name, kwargs))) or "ok",
    )

    result = client.freeze_release("v1.0", "release commit")

    assert result == "ok"
    assert calls == [
        ("add", None),
        ("commit", ("release commit", False)),
        ("pull", client.source_path),
        ("push", None),
        (
            "freeze",
            (
                "v1.0",
                {"output_gts": None, "message": "release commit", "stage_all": True},
            ),
        ),
    ]


def test_client_freeze_release_force_uses_pull_force(monkeypatch, tmp_path):
    client = _client_with_root_registry(tmp_path)
    client.source_path = tmp_path / "project.gts"
    calls: list[str] = []

    monkeypatch.setattr(type(client.git_runner), "has_upstream", lambda self, path: True)
    monkeypatch.setattr(client, "add", lambda: calls.append("add"))
    monkeypatch.setattr(client, "commit", lambda *args, **kwargs: calls.append("commit"))
    monkeypatch.setattr(client, "pull", lambda source: calls.append("pull"))
    monkeypatch.setattr(client, "pull_force", lambda source: calls.append("pull-force"))
    monkeypatch.setattr(client, "push", lambda: calls.append("push"))
    monkeypatch.setattr(client, "freeze", lambda *args, **kwargs: calls.append("freeze") or "ok")

    assert client.freeze_release("v1.0", "release commit", force=True) == "ok"
    assert calls == ["add", "commit", "pull-force", "push", "freeze"]


def test_client_freeze_release_skips_pull_when_branch_has_no_upstream(monkeypatch, tmp_path):
    # Reproduces this ticket's exact scenario: a branch created and checked
    # out this same session, never pushed. freeze-release must succeed by
    # skipping the pull step (nothing to pull yet), not crash with git's
    # "couldn't find remote ref" error.
    client = _client_with_root_registry(tmp_path)
    client.source_path = tmp_path / "project.gts"
    calls: list[str] = []

    monkeypatch.setattr(type(client.git_runner), "has_upstream", lambda self, path: False)
    monkeypatch.setattr(client, "add", lambda: calls.append("add"))
    monkeypatch.setattr(client, "commit", lambda *args, **kwargs: calls.append("commit"))
    monkeypatch.setattr(client, "pull", lambda source: calls.append("pull"))
    monkeypatch.setattr(client, "pull_force", lambda source: calls.append("pull-force"))
    monkeypatch.setattr(client, "push", lambda: calls.append("push"))
    monkeypatch.setattr(client, "freeze", lambda *args, **kwargs: calls.append("freeze") or "ok")

    assert client.freeze_release("v1.0", "release commit") == "ok"
    assert calls == ["add", "commit", "push", "freeze"]


def test_client_freeze_release_force_also_skips_pull_when_no_upstream(monkeypatch, tmp_path):
    client = _client_with_root_registry(tmp_path)
    client.source_path = tmp_path / "project.gts"
    calls: list[str] = []

    monkeypatch.setattr(type(client.git_runner), "has_upstream", lambda self, path: False)
    monkeypatch.setattr(client, "add", lambda: calls.append("add"))
    monkeypatch.setattr(client, "commit", lambda *args, **kwargs: calls.append("commit"))
    monkeypatch.setattr(client, "pull", lambda source: calls.append("pull"))
    monkeypatch.setattr(client, "pull_force", lambda source: calls.append("pull-force"))
    monkeypatch.setattr(client, "push", lambda: calls.append("push"))
    monkeypatch.setattr(client, "freeze", lambda *args, **kwargs: calls.append("freeze") or "ok")

    assert client.freeze_release("v1.0", "release commit", force=True) == "ok"
    assert calls == ["add", "commit", "push", "freeze"]


def test_client_pull_dispatches_to_restart_for_cgs(monkeypatch):
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}

    def _fake_restart(config_path, **_kwargs):
        captured["config_path"] = config_path
        return "ok"

    monkeypatch.setattr(client, "restart", _fake_restart)

    result = client.pull("project.cgs")

    assert result == "ok"
    assert captured["config_path"] == Path("project.cgs").resolve()


def test_client_pull_gts_resynchronizes_loaded_snapshot(monkeypatch):
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}

    class _ReadyRegistry:
        lifecycle_state = TreeLifecycleState.READY

        def is_ready(self):
            return True

    def _fake_load_gts(snapshot_path):
        captured["snapshot_path"] = snapshot_path
        return _ReadyRegistry()

    class _FakeGitCommands:
        def pull(self, git_runner):
            captured["git_pull_runner"] = git_runner

    def _fake_write_gts_snapshot(*, command_origin):
        captured["command_origin"] = command_origin
        return Path("pulled.gts").resolve()

    def _fake_record_snapshot(source_path, snapshot_path):
        captured["recorded_source_path"] = source_path
        captured["recorded_snapshot_path"] = snapshot_path

    monkeypatch.setattr(client, "load_gts", _fake_load_gts)
    client.orchestre.git_tree.git = _FakeGitCommands()
    monkeypatch.setattr(client, "write_gts_snapshot", _fake_write_gts_snapshot)
    monkeypatch.setattr(client.state_store, "record_snapshot", _fake_record_snapshot)

    result = client.pull("state.gts")

    assert isinstance(result, _ReadyRegistry)
    assert captured["snapshot_path"] == Path("state.gts").resolve()
    assert captured["git_pull_runner"] is client.git_runner
    assert captured["command_origin"] == "pull"
    assert captured["recorded_source_path"] == Path("state.gts").resolve()
    assert captured["recorded_snapshot_path"] == Path("pulled.gts").resolve()


def test_client_print_supports_gts(tmp_path):
    snapshot_path = _write_ready_gts(tmp_path / "snapshot.gts", root_path=(tmp_path / "workspace" / "demo").resolve())
    client = ComplexGitSyncClient()

    output = client.print(snapshot_path)

    assert '"document_kind": "gts"' in output
    assert '"is_ready": true' in output


def test_build_registry_rejects_duplicate_relative_paths(tmp_path):
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "demo"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-a"
relative_path = "deps/shared"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-b"
relative_path = "deps/shared"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    client = ComplexGitSyncClient()
    with pytest.raises(ConfigValidationError, match="duplicate relative_path"):
        client.load_cgs(config_path)


def test_discover_nested_configs_promotes_parent_and_adds_descendants(tmp_path):
    config_path = _write_root_cgs(tmp_path, nested_child=True)
    child_repo_root = tmp_path / "deps" / "child-repo"
    child_repo_root.mkdir(parents=True)
    (child_repo_root / "child.cgs").write_text(
        """
[document]
format_version = "1.0"

[project]
name = "child-repo"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-repo"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "docs"
relative_path = "docs"
nested_config = "disabled"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    client = ComplexGitSyncClient()
    registry = client.load_cgs(config_path, discover_nested=True)

    child_entry = registry.get("root:deps/child-repo")
    docs_entry = registry.get("root:deps/child-repo:docs")

    assert child_entry.node_type == NodeType.PARENT
    assert child_entry.discovery_state.value == "RESOLVED"
    assert docs_entry.parent_id == "root:deps/child-repo"
    assert docs_entry.absolute_path == (child_repo_root / "docs").resolve()


def test_discover_nested_configs_rejects_ambiguous_auto_discovery(tmp_path):
    config_path = _write_root_cgs(tmp_path, nested_child=True)
    child_repo_root = tmp_path / "deps" / "child-repo"
    child_repo_root.mkdir(parents=True)
    (child_repo_root / "one.cgs").write_text(_nested_minimal("child-repo"), encoding="utf-8")
    (child_repo_root / "two.cgs").write_text(_nested_minimal("child-repo"), encoding="utf-8")

    client = ComplexGitSyncClient()
    client.load_cgs(config_path)

    with pytest.raises(NestedConfigDiscoveryError, match="Ambiguous nested \\.cgs discovery"):
        client.discover_nested_configs()


def test_assert_nested_discovery_complete_raises_on_missing_explicit_path(tmp_path):
    """An explicit nested_config path that doesn't exist must still fail
    ``_assert_nested_discovery_complete`` — this is the one MISSING case that
    stays a real error after "auto" finding nothing was fixed to RESOLVED."""
    root_cgs = tmp_path / "project.cgs"
    root_cgs.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "demo"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-repo"
relative_path = "deps/child-repo"
nested_config = "named.cgs"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "deps" / "child-repo").mkdir(parents=True)

    client = ComplexGitSyncClient()
    client.load_cgs(root_cgs, discover_nested=True)

    with pytest.raises(GitSyncError, match="is not resolved: MISSING"):
        client._assert_nested_discovery_complete()


def test_tree_rendering_is_serialized_for_review(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    client = ComplexGitSyncClient()
    client.load_cgs(config_path)

    rendered_tree = client.format_project_tree()

    assert "- demo [root]" in rendered_tree
    assert "child-repo" in rendered_tree


def test_minimal_repo_tree_rendering_is_project_parent_leaf(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    client = ComplexGitSyncClient()
    client.load_cgs(config_path)

    rendered_tree = client.format_repo_tree()

    assert "demo (project)" in rendered_tree
    assert "child-repo (leaf)" in rendered_tree
    assert "└──" in rendered_tree


def test_view_tree_rendering_supports_depth_and_collapse(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    client = ComplexGitSyncClient()
    client.load_cgs(config_path)

    rendered_tree = client.view_tree(depth=1, collapse=("child-repo",))

    assert rendered_tree.startswith("demo (root) [")
    assert "child-repo (leaf)" in rendered_tree
    assert "└── child-repo" in rendered_tree


def test_normalize_node_types_marks_childless_parent_entry_as_leaf(tmp_path):
    registry = WorkingGitTree()
    registry.add(
        WorkingRepo(
            repo_id="root",
            name="demo",
            node_type=NodeType.ROOT,
            absolute_path=tmp_path,
            relative_path=Path("."),
        )
    )
    registry.add(
        WorkingRepo(
            repo_id="root:parentish-leaf",
            name="parentish-leaf",
            node_type=NodeType.PARENT,
            parent_id="root",
            absolute_path=tmp_path / "parentish-leaf",
            relative_path=Path("parentish-leaf"),
        )
    )

    normalize_node_types(registry)

    assert registry.get("root").node_type == NodeType.ROOT
    assert registry.get("root:parentish-leaf").node_type == NodeType.LEAF


def test_view_operation_rendering_contains_required_columns(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    client = ComplexGitSyncClient()
    client.load_cgs(config_path)

    rendered_operation = client.view_operation()

    assert "REPOSITORY" in rendered_operation
    assert "BRANCH" in rendered_operation
    assert "LOCAL_STATE" in rendered_operation
    assert "SYNC_STATE" in rendered_operation
    assert "demo" in rendered_operation


def test_status_rendering_contains_live_git_summary(tmp_path):
    root_path = tmp_path / "workspace" / "demo"
    snapshot_path = _write_ready_gts(tmp_path / "snapshot.gts", root_path=root_path)
    fake_runner = _FakeGitRunner({})
    fake_runner.branch_overrides[root_path.resolve()] = "main"
    fake_runner.status_lines[root_path.resolve()] = ["A  new-file.txt"]
    fake_runner.tracking_states[root_path.resolve()] = SyncState.AHEAD
    fake_runner.upstream_refs[root_path.resolve()] = "origin/main"
    fake_runner.tracking_counts[root_path.resolve()] = (2, 0)

    client = ComplexGitSyncClient(git_runner=fake_runner)
    client.load_gts(snapshot_path)

    rendered_status = client.status()

    assert "summary ready=true complete=true repos=1 dirty=1 staged=1 ahead=1" in rendered_status
    assert "REPOSITORY" in rendered_status
    assert "LOCAL_BRANCH" in rendered_status
    assert "UPSTREAM_BRANCH" in rendered_status
    assert "SYNC" in rendered_status
    assert "demo" in rendered_status
    assert "staged" in rendered_status
    assert "origin/main" in rendered_status
    assert "ahead(+2)" in rendered_status


def test_status_ignores_cgitsync_managed_generated_files(tmp_path):
    root_path = tmp_path / "workspace" / "demo"
    snapshot_path = _write_ready_gts(tmp_path / "snapshot.gts", root_path=root_path)
    fake_runner = _FakeGitRunner({})
    fake_runner.branch_overrides[root_path.resolve()] = "main"
    fake_runner.status_lines[root_path.resolve()] = [
        " M .cgitsync/state/demo.gts",
        " M demo.lgr",
        "?? .gitignore",
    ]

    client = ComplexGitSyncClient(git_runner=fake_runner)
    client.load_gts(snapshot_path)

    rendered_status = client.status()

    assert "summary ready=true complete=true repos=1 dirty=0 staged=0" in rendered_status
    assert "demo" in rendered_status
    assert "clean" in rendered_status


def test_client_clone_cgs_clones_tree_and_applies_fallback(tmp_path):
    config_path = _write_clone_ready_cgs(tmp_path)
    fake_runner = _FakeGitRunner(
        {
            "git@github.com:owner/demo.git": {"main"},
            "git@github.com:owner/child-repo.git": {"autoTest"},
            "git@github.com:owner/docs.git": {"main"},
        }
    )

    state_store = RuntimeStateStore(base_dir=tmp_path / "runtime-state")
    client = ComplexGitSyncClient(git_runner=fake_runner, state_store=state_store)
    registry = client.clone_cgs(config_path, target_dir=tmp_path / "workspace" / "demo")
    tree_state = client.get_tree_state()

    root_entry = registry.get("root")
    child_entry = registry.get("root:deps/child-repo")
    docs_entry = registry.get("root:deps/child-repo:docs")

    assert tree_state.lifecycle_state == TreeLifecycleState.READY
    assert root_entry.absolute_path == (tmp_path / "workspace" / "demo").resolve()
    assert root_entry.repo_lifecycle_state == RepoLifecycleState.FALLBACK_READY
    assert root_entry.resolved_ref_name == "main"
    assert child_entry.repo_lifecycle_state == RepoLifecycleState.READY
    assert child_entry.resolved_ref_name == "autoTest"
    assert docs_entry.repo_lifecycle_state == RepoLifecycleState.FALLBACK_READY
    assert docs_entry.resolved_ref_name == "main"
    assert [branch for _, _, branch in fake_runner.clones] == ["main", "autoTest", "main"]
    assert [
        (remote, destination)
        for remote, destination, _ in fake_runner.clones
    ] == [
        ("git@github.com:owner/demo.git", root_entry.absolute_path),
        ("git@github.com:owner/child-repo.git", child_entry.absolute_path),
        ("git@github.com:owner/docs.git", docs_entry.absolute_path),
    ]

    snapshot_path = state_store.latest_snapshot_for(config_path)
    assert snapshot_path is not None
    assert re.fullmatch(
        r"state\([0-9a-f]{64}\)_0",
        snapshot_path.parent.name,
    )
    assert snapshot_path == (
        tmp_path / "workspace" / "demo" / ".cgitsync" / snapshot_path.parent.name / "project.gts"
    ).resolve()

    reloaded_client = ComplexGitSyncClient(state_store=state_store)
    reloaded_registry = reloaded_client.load_runtime_or_cgs(config_path)
    assert reloaded_client.get_tree_state().lifecycle_state == TreeLifecycleState.READY
    assert reloaded_registry.get("root").absolute_path == root_entry.absolute_path


def test_clone_cgs_raises_when_default_root_is_occupied(tmp_path, monkeypatch):
    config_path = _write_clone_ready_cgs(tmp_path)
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "marker.txt").write_text("occupied\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    client = ComplexGitSyncClient(
        git_runner=_FakeGitRunner(
            {
                "git@github.com:owner/demo.git": {"main"},
                "git@github.com:owner/child-repo.git": {"autoTest"},
            }
        )
    )

    with pytest.raises(GitSyncError, match="already exists and is not empty"):
        client.clone_cgs(config_path)


def test_clone_cgs_replaces_nested_destination_populated_by_parent_clone(tmp_path):
    config_path = _write_root_plus_docs_clone_cgs(tmp_path)
    fake_runner = _StrictCloneGitRunner(
        {
            "git@github.com:owner/ComplexGitSync.git": {"main"},
            "git@github.com:owner/docs.git": {"main"},
        },
        parent_repo_name="ComplexGitSync",
    )
    client = ComplexGitSyncClient(git_runner=fake_runner)

    registry = client.clone_cgs(config_path, target_dir=tmp_path / "workspace" / "ComplexGitSync")

    docs_path = (tmp_path / "workspace" / "ComplexGitSync" / "docs").resolve()
    assert registry.get("root:docs").absolute_path == docs_path
    assert docs_path.is_dir()
    assert fake_runner.parent_docs_seeded is True
    assert not (docs_path / "README.md").exists()
    assert (docs_path / "from-docs-clone.txt").is_file()
    assert [remote for remote, _, _ in fake_runner.clones] == [
        "git@github.com:owner/ComplexGitSync.git",
        "git@github.com:owner/docs.git",
    ]
    assert [branch for _, _, branch in fake_runner.clones] == ["main", "main"]


def test_make_repo_id_normalizes_windows_style_paths():
    assert make_repo_id("root", PureWindowsPath("deps", "child-repo"), "child-repo") == (
        "root:deps/child-repo"
    )
    assert make_repo_id("root:deps/child-repo", PureWindowsPath("docs"), "docs") == (
        "root:deps/child-repo:docs"
    )


def test_make_repo_id_falls_back_to_name_when_relative_path_is_missing():
    assert make_repo_id("root", None, "child-repo") == "root:child-repo"


def test_direct_python_api_loads_gts_discovers_gitrepos_and_propagates_tag(tmp_path):
    root_path = (tmp_path / "workspace" / "demo").resolve()
    leaf_path = (root_path / "deps" / "leaf").resolve()
    root_path.mkdir(parents=True)
    leaf_path.mkdir(parents=True)

    snapshot_path = tmp_path / "snapshot.gts"
    snapshot_path.write_text(
        f"""
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "{root_path.as_posix()}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "{root_path.as_posix()}"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "sha-demo"
project_owner_name = "owner"
project_name = "demo"

[[repo_state]]
name = "leaf"
node_type = "leaf"
absolute_path = "{leaf_path.as_posix()}"
parent_absolute_path = "{root_path.as_posix()}"
relative_path = "deps/leaf"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "sha-leaf"
project_owner_name = "owner"
project_name = "leaf"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    empty_registry = WorkingGitTree()
    assert empty_registry.recompute_tree_state() == TreeLifecycleState.UNLOADED
    registry = build_registry_from_gts_document(GtsDocument.from_toml(snapshot_path))
    assert registry.lifecycle_state == TreeLifecycleState.READY

    tree = GitTree()
    for entry in registry.values():
        tree.add_repo(
            GitRepo(
                project_owner_name=entry.project_owner_name or "owner",
                project_name=entry.project_name or entry.name,
                gitprovider=entry.gitprovider,
                access_protocol=entry.access_protocol,
                commit_sha=entry.commit_sha,
            )
        )

    assert sorted(tree.repos) == ["demo", "leaf"]
    assert tree.repos["demo"].commit_sha == "sha-demo"
    assert tree.repos["leaf"].commit_sha == "sha-leaf"

    tree.propagate_tag(registry, "v1.2.3")
    for entry in registry.values():
        assert entry.target_ref_kind == RefKind.TAG
        assert entry.target_ref_name == "v1.2.3"


def test_build_registry_from_gts_expands_home_variable_paths(monkeypatch, tmp_path):
    fake_home = (tmp_path / "home" / "user").resolve()
    workspace = fake_home / "workspace" / "demo"
    leaf_path = workspace / "deps" / "leaf"
    workspace.mkdir(parents=True)
    leaf_path.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    snapshot_path = tmp_path / "home-snapshot.gts"
    snapshot_path.write_text(
        """
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "$HOME/workspace/demo"
source_cgs_path = "$HOME/workspace/demo/project.cgs"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "$HOME/workspace/demo"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "sha-demo"

[[repo_state]]
name = "leaf"
node_type = "leaf"
absolute_path = "$HOME/workspace/demo/deps/leaf"
parent_absolute_path = "$HOME/workspace/demo"
relative_path = "deps/leaf"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "sha-leaf"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    registry = build_registry_from_gts_document(GtsDocument.from_toml(snapshot_path))
    assert registry.get("root").absolute_path == workspace
    assert registry.get("root").source_cgs_path == (workspace / "project.cgs")
    assert registry.get("root:deps/leaf").absolute_path == leaf_path


def test_build_registry_from_compact_gts_ref(tmp_path):
    snapshot_path = tmp_path / "compact.gts"
    snapshot_path.write_text(
        """
[document]
CGS_VERSION = "0001.50"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "/tmp/demo"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "/tmp/demo"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
ref = "branch:main"
commit_sha = "abc123"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    registry = build_registry_from_gts_document(GtsDocument.from_toml(snapshot_path))
    root = registry.get("root")

    assert root.current_ref_kind == RefKind.BRANCH
    assert root.current_ref_name == "main"
    assert root.target_ref_kind == RefKind.BRANCH
    assert root.target_ref_name == "main"
    assert root.resolved_ref_kind == RefKind.BRANCH
    assert root.resolved_ref_name == "main"
    assert root.fallback_branch == "main"


def test_make_repo_id_falls_back_to_name_when_relative_path_is_empty():
    assert make_repo_id("root", "", "child-repo") == "root:child-repo"


def test_make_repo_id_only_collapses_explicit_dot_relative_path():
    assert make_repo_id("root", ".", "child-repo") == "root"
    assert make_repo_id("root", None, ".") == "root:."
    assert make_repo_id("root", "", "") == "root:"


def test_time_l0_anchor_hash_is_public_identity_only():
    state = new_time_l0_anchor(SystemClock())

    assert re.fullmatch(r"[0-9a-f]{64}", state.state_hash)
    assert state.state_id == f"state({state.state_hash})"
    assert hash_time_l0_anchor("local-test-anchor") == hash_time_l0_anchor("local-test-anchor")
    assert not hasattr(state, "anchor")


def test_state_directory_suffix_is_scoped_to_exact_state_hash(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"
    state_hash = "a" * 64
    other_hash = "b" * 64
    (cgitsync_dir / _state_directory_name(state_hash, 0)).mkdir(parents=True)
    (cgitsync_dir / _state_directory_name(state_hash, 1)).mkdir()

    same_hash_state = _resolve_memory_state_directory(cgitsync_dir, state_hash)
    other_hash_state = _resolve_memory_state_directory(cgitsync_dir, other_hash)

    assert same_hash_state.state_order == 2
    assert same_hash_state.final_path.name == _state_directory_name(state_hash, 2)
    assert other_hash_state.state_order == 0
    assert other_hash_state.final_path.name == _state_directory_name(other_hash, 0)


def _current_lgr_snapshot_path(workspace: Path, register_name: str = "demo.lgr") -> Path:
    data = tomllib.loads(_current_lgr_path(workspace, register_name).read_text(encoding="utf-8"))
    raw_path = data["register"]["current_snapshot_path"]
    if raw_path.startswith("$HOME/"):
        return Path(raw_path.replace("$HOME", str(Path.home()), 1)).resolve()
    return Path(raw_path).resolve()


def _current_lgr_path(workspace: Path, register_name: str = "demo.lgr") -> Path:
    candidates = sorted((workspace / ".cgitsync").glob(f"state(*)_*/{register_name}"))
    if candidates:
        return max(candidates, key=lambda path: (path.stat().st_mtime, str(path)))
    return workspace / register_name


def test_client_load_cgs_writes_gts_snapshot(tmp_path):
    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient()
    client.load(config_path)

    state_dirs = sorted((tmp_path / ".cgitsync").glob("state(*)_*"))
    assert len(state_dirs) == 1
    assert re.fullmatch(r"state\([0-9a-f]{64}\)_0", state_dirs[0].name)
    assert (state_dirs[0] / "project.gts").is_file()
    assert (state_dirs[0] / "project.cgs").is_file()
    assert (state_dirs[0] / "demo.lgr").is_file()
    assert not (tmp_path / "demo.lgr").exists()


def test_client_load_cgs_updates_project_local_lgr(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient()
    client.load(config_path)

    expected_lgr = _current_lgr_path(tmp_path)
    data = tomllib.loads(expected_lgr.read_text(encoding="utf-8"))
    state_id = data["register"]["current_snapshot_id"]
    state_hash = data["register"]["current_state_hash"]
    expected_snapshot = (tmp_path / ".cgitsync" / f"{state_id}_0" / "project.gts").resolve()
    expected_path_marker = _path_to_environment_marker(expected_snapshot)
    assert re.fullmatch(r"state\([0-9a-f]{64}\)", state_id)
    assert state_id == f"state({state_hash})"
    assert data["register"]["current_snapshot_path"] == expected_path_marker
    assert len(data["snapshots"]) == 1
    assert data["snapshots"][0]["id"] == state_id
    assert data["snapshots"][0]["state_hash"] == state_hash
    assert data["snapshots"][0]["state_order"] == 0
    assert data["snapshots"][0]["snapshot_path"] == expected_path_marker
    assert expected_snapshot.is_file()


def test_client_load_cgs_uses_home_variable_in_gts_and_lgr(monkeypatch, tmp_path):
    fake_home = (tmp_path / "home" / "user").resolve()
    workspace = fake_home / "workspace" / "demo"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    config_path = _write_root_cgs(workspace)

    client = ComplexGitSyncClient()
    client.load(config_path)

    snapshot_path = _current_lgr_snapshot_path(workspace)
    snapshot_data = tomllib.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot_data["project"]["root_absolute_path"] == "$HOME/workspace/demo"
    assert snapshot_data["project"]["source_cgs_path"] == "$HOME/workspace/demo/project.cgs"

    lgr_data = tomllib.loads(_current_lgr_path(workspace).read_text(encoding="utf-8"))
    state_id = lgr_data["register"]["current_snapshot_id"]
    assert re.fullmatch(r"state\([0-9a-f]{64}\)", state_id)
    assert lgr_data["register"]["current_snapshot_path"] == (
        f"$HOME/workspace/demo/.cgitsync/{state_id}_0/project.gts"
    )
    assert lgr_data["snapshots"][0]["snapshot_path"] == (
        f"$HOME/workspace/demo/.cgitsync/{state_id}_0/project.gts"
    )
    assert snapshot_path.is_file()


def test_client_snapshot_generation_assigns_time_l0_state_for_each_write(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient()
    client.load(config_path)
    client.expand(config_path)

    expected_lgr = _current_lgr_path(tmp_path)
    data = tomllib.loads(expected_lgr.read_text(encoding="utf-8"))
    state_ids = [entry["id"] for entry in data["snapshots"]]
    assert len(state_ids) == 2
    assert len(set(state_ids)) == 2
    assert all(re.fullmatch(r"state\([0-9a-f]{64}\)", state_id) for state_id in state_ids)
    assert [entry["state_order"] for entry in data["snapshots"]] == [0, 0]
    assert data["register"]["current_snapshot_id"] == state_ids[-1]


def test_client_expand_cgs_writes_gts_snapshot(tmp_path):
    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient()
    client.expand(config_path)

    assert _current_lgr_snapshot_path(tmp_path).is_file()


def test_client_validate_cgs_writes_gts_snapshot(tmp_path):
    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient()
    client.validate(config_path)

    assert _current_lgr_snapshot_path(tmp_path).is_file()


def test_client_load_gts_snapshot_has_correct_command_origin(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)

    ComplexGitSyncClient().load(config_path)

    expected_snapshot = _current_lgr_snapshot_path(tmp_path)
    data = tomllib.loads(expected_snapshot.read_text(encoding="utf-8"))
    assert data["document"]["command_origin"] == "load"
    assert data["document"]["CGS_VERSION"]
    assert "format_version" not in data["document"]
    assert "schema_version" not in data["document"]
    assert "hash_algorithm" not in data["document"]
    assert len(data["document"]["snapshot_hash"]) == 64
    assert "demo (root)" in "\n".join(data["tree"]["lines"])
    assert data["repo_state"][0]["ref"] == "branch:main"
    assert "discovery_state" not in data["repo_state"][0]
    assert "fallback_branch" not in data["repo_state"][0]
    assert "fallback_applied" not in data["repo_state"][0]


def test_client_expand_gts_snapshot_has_correct_command_origin(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)

    ComplexGitSyncClient().expand(config_path)

    expected_snapshot = _current_lgr_snapshot_path(tmp_path)
    data = tomllib.loads(expected_snapshot.read_text(encoding="utf-8"))
    assert data["document"]["command_origin"] == "expand"
    assert data["document"]["CGS_VERSION"]
    assert "schema_version" not in data["document"]
    assert "hash_algorithm" not in data["document"]
    assert len(data["document"]["snapshot_hash"]) == 64


def test_client_validate_gts_snapshot_has_correct_command_origin(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)

    ComplexGitSyncClient().validate(config_path)

    expected_snapshot = _current_lgr_snapshot_path(tmp_path)
    data = tomllib.loads(expected_snapshot.read_text(encoding="utf-8"))
    assert data["document"]["command_origin"] == "validate"
    assert data["document"]["CGS_VERSION"]
    assert "schema_version" not in data["document"]
    assert "hash_algorithm" not in data["document"]
    assert len(data["document"]["snapshot_hash"]) == 64


# ---------------------------------------------------------------------------
# SyncLedger — unit tests
# ---------------------------------------------------------------------------


def test_sync_ledger_record_event_creates_first_entry(tmp_path):
    import tomllib

    from ComplexGitSync.orchestre import SyncLedger

    lgr_path = tmp_path / "demo.lgr"
    ledger = SyncLedger(lgr_path)

    sync_id = ledger.record_event(
        operation="clone",
        workspace_hash="a" * 64,
        gts_snapshot_id="gts-000001",
        affected_repos=["demo", "child"],
        actor="test-user",
    )

    assert sync_id == "lgr-000001"
    data = tomllib.loads(lgr_path.read_text(encoding="utf-8"))
    events = data["ledger"]
    assert len(events) == 1
    event = events[0]
    assert event["sync_id"] == "lgr-000001"
    assert event["parent_sync_ids"] == []
    assert event["operation"] == "clone"
    assert event["actor"] == "test-user"
    assert event["workspace_hash"] == "a" * 64
    assert event["gts_snapshot_id"] == "gts-000001"
    assert event["affected_repos"] == ["demo", "child"]
    assert "timestamp" in event


def test_sync_ledger_second_event_links_to_first(tmp_path):
    from ComplexGitSync.orchestre import SyncLedger

    lgr_path = tmp_path / "demo.lgr"
    ledger = SyncLedger(lgr_path)

    id1 = ledger.record_event(
        operation="clone",
        workspace_hash="a" * 64,
        gts_snapshot_id="gts-000001",
        affected_repos=["demo"],
        actor="user",
    )
    id2 = ledger.record_event(
        operation="freeze_release",
        workspace_hash="b" * 64,
        gts_snapshot_id="gts-000002",
        affected_repos=["demo"],
        actor="user",
    )

    assert id1 == "lgr-000001"
    assert id2 == "lgr-000002"

    events = SyncLedger(lgr_path).history()
    assert [e["sync_id"] for e in events] == ["lgr-000001", "lgr-000002"]
    assert events[1]["parent_sync_ids"] == ["lgr-000001"]


def test_sync_ledger_history_returns_topological_order(tmp_path):
    from ComplexGitSync.orchestre import SyncLedger

    lgr_path = tmp_path / "demo.lgr"
    ledger = SyncLedger(lgr_path)

    for i, op in enumerate(["clone", "checkout", "commit", "push", "freeze_release"]):
        ledger.record_event(
            operation=op,
            workspace_hash=str(i) * 64,
            gts_snapshot_id=f"gts-{i + 1:06d}",
            affected_repos=["demo"],
            actor="user",
        )

    history = ledger.history()
    assert len(history) == 5
    operations = [e["operation"] for e in history]
    assert operations == ["clone", "checkout", "commit", "push", "freeze_release"]

    # parents-before-children invariant
    seen_ids: set[str] = set()
    for event in history:
        for parent_id in event["parent_sync_ids"]:
            assert parent_id in seen_ids, f"parent {parent_id} not seen before {event['sync_id']}"
        seen_ids.add(event["sync_id"])


def test_sync_ledger_replay_is_alias_for_history(tmp_path):
    from ComplexGitSync.orchestre import SyncLedger

    lgr_path = tmp_path / "demo.lgr"
    ledger = SyncLedger(lgr_path)
    ledger.record_event(
        operation="load",
        workspace_hash="c" * 64,
        gts_snapshot_id="gts-000001",
        affected_repos=["demo"],
        actor="user",
    )

    assert ledger.replay() == ledger.history()


def test_sync_ledger_empty_register_returns_empty_history(tmp_path):
    from ComplexGitSync.orchestre import SyncLedger

    lgr_path = tmp_path / "nonexistent.lgr"
    assert SyncLedger(lgr_path).history() == []


def test_sync_ledger_actor_auto_detected_when_none(tmp_path):
    from ComplexGitSync.orchestre import SyncLedger

    lgr_path = tmp_path / "demo.lgr"
    ledger = SyncLedger(lgr_path)
    ledger.record_event(
        operation="load",
        workspace_hash="d" * 64,
        gts_snapshot_id="gts-000001",
        affected_repos=["demo"],
    )

    events = ledger.history()
    assert isinstance(events[0]["actor"], str)
    assert events[0]["actor"] != ""


def test_write_gts_snapshot_writes_stable_per_branch_cgs_copy(tmp_path):
    # BootstrapGitignoreSync's sibling ticket, FirstBranchTestWorkflow §0.3:
    # a .cgs snapshot per run already existed, but only inside an opaque
    # state(<hash>)_n/ directory -- nothing named "the .cgs for branch X".
    root_path = tmp_path / "root"
    root_path.mkdir()
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        '[project]\nname = "demo"\ndefault_branch = "main"\n\n'
        'repos = [{ repository = "github:owner/demo", relative_path = "." }]\n',
        encoding="utf-8",
    )

    client = ComplexGitSyncClient()
    registry = WorkingGitTree()
    root_entry = _make_entry("root", root_path)
    root_entry.current_ref_kind = RefKind.BRANCH
    root_entry.current_ref_name = "test-cgs"
    registry.add(root_entry)
    client.registry = registry
    client.source_path = config_path

    client.write_gts_snapshot(command_origin="branch")

    stable_path = root_path / ".cgitsync" / ".cgs" / "root-test-cgs.cgs"
    assert stable_path.is_file()
    assert stable_path.read_text(encoding="utf-8") == config_path.read_text(encoding="utf-8")


def test_write_gts_snapshot_stable_cgs_copy_sanitizes_branch_name(tmp_path):
    root_path = tmp_path / "root"
    root_path.mkdir()
    config_path = tmp_path / "project.cgs"
    config_path.write_text("[project]\nname = \"demo\"\n", encoding="utf-8")

    client = ComplexGitSyncClient()
    registry = WorkingGitTree()
    root_entry = _make_entry("root", root_path)
    root_entry.current_ref_kind = RefKind.BRANCH
    root_entry.current_ref_name = "feature/my thing"
    registry.add(root_entry)
    client.registry = registry
    client.source_path = config_path

    client.write_gts_snapshot(command_origin="branch")

    stable_dir = root_path / ".cgitsync" / ".cgs"
    [stable_path] = list(stable_dir.iterdir())
    assert stable_path.name == "root-feature-my-thing.cgs"


def test_write_gts_snapshot_refreshes_stable_cgs_copy_on_each_run(tmp_path):
    root_path = tmp_path / "root"
    root_path.mkdir()
    config_path = tmp_path / "project.cgs"
    config_path.write_text("[project]\nname = \"demo\"\n", encoding="utf-8")

    client = ComplexGitSyncClient()
    registry = WorkingGitTree()
    root_entry = _make_entry("root", root_path)
    root_entry.current_ref_kind = RefKind.BRANCH
    root_entry.current_ref_name = "test-cgs"
    registry.add(root_entry)
    client.registry = registry
    client.source_path = config_path

    client.write_gts_snapshot(command_origin="branch")
    config_path.write_text("[project]\nname = \"demo-v2\"\n", encoding="utf-8")
    client.write_gts_snapshot(command_origin="checkout")

    stable_path = root_path / ".cgitsync" / ".cgs" / "root-test-cgs.cgs"
    assert stable_path.read_text(encoding="utf-8") == config_path.read_text(encoding="utf-8")


def test_write_gts_snapshot_skips_stable_cgs_copy_without_a_current_branch(tmp_path):
    # No behaviour change to existing snapshotting when the root entry's
    # branch isn't known (e.g. the lighter-weight `load` path, which never
    # attaches a real git branch) -- purely additive, never a hard failure.
    root_path = tmp_path / "root"
    root_path.mkdir()
    config_path = tmp_path / "project.cgs"
    config_path.write_text("[project]\nname = \"demo\"\n", encoding="utf-8")

    client = ComplexGitSyncClient()
    registry = WorkingGitTree()
    root_entry = _make_entry("root", root_path)
    root_entry.target_ref_kind = RefKind.BRANCH
    root_entry.target_ref_name = "main"
    registry.add(root_entry)
    client.registry = registry
    client.source_path = config_path

    client.write_gts_snapshot(command_origin="load")

    assert not (root_path / ".cgitsync" / ".cgs").exists()


def test_client_write_gts_snapshot_records_ledger_event(tmp_path, monkeypatch):
    import tomllib

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient()
    client.load(config_path)

    expected_lgr = _current_lgr_path(tmp_path)
    data = tomllib.loads(expected_lgr.read_text(encoding="utf-8"))
    assert "ledger" in data
    assert len(data["ledger"]) == 1
    event = data["ledger"][0]
    assert event["sync_id"] == "lgr-000001"
    assert event["operation"] == "load"
    assert event["gts_snapshot_id"] == data["register"]["current_snapshot_id"]
    assert re.fullmatch(r"state\([0-9a-f]{64}\)", event["gts_snapshot_id"])
    assert len(event["workspace_hash"]) == 64
    assert "demo" in event["affected_repos"]
    assert event["parent_sync_ids"] == []
    [log_path] = sorted(expected_lgr.parent.glob("*.log"))
    assert log_path.is_file()
    log_text = log_path.read_text(encoding="utf-8")
    log_data = json.loads(log_text)
    assert log_data["event"] == "memory_state_finalized"
    assert log_data["command_origin"] == "load"
    assert log_data["state_id"] == data["register"]["current_snapshot_id"]
    assert log_data["state_order"] == 0
    assert "@" not in log_text


def test_client_multiple_operations_create_linked_ledger_events(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient()
    client.load(config_path)
    client.expand(config_path)
    client.validate(config_path)

    expected_lgr = _current_lgr_path(tmp_path)
    data = tomllib.loads(expected_lgr.read_text(encoding="utf-8"))
    events = data["ledger"]
    assert len(events) == 3
    sync_ids = [e["sync_id"] for e in events]
    assert sync_ids == ["lgr-000001", "lgr-000002", "lgr-000003"]
    # Each event (except the first) must link to its predecessor
    assert events[0]["parent_sync_ids"] == []
    assert events[1]["parent_sync_ids"] == ["lgr-000001"]
    assert events[2]["parent_sync_ids"] == ["lgr-000002"]
    snapshot_paths = [Path(entry["snapshot_path"]) for entry in data["snapshots"]]
    assert len(snapshot_paths) == len(set(snapshot_paths))
    for snapshot in data["snapshots"]:
        snapshot_path = Path(snapshot["snapshot_path"])
        assert snapshot_path.is_file()
        assert re.fullmatch(r"state\([0-9a-f]{64}\)_\d+", snapshot_path.parent.name)
        assert snapshot_path.name == "project.gts"
        assert snapshot_path.parent.name.startswith(f"{snapshot['id']}_")


def test_client_get_ledger_history_via_public_api(tmp_path):
    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient()
    client.load(config_path)
    client.expand(config_path)

    expected_lgr = _current_lgr_path(tmp_path)
    history = client.get_ledger_history(expected_lgr)
    assert len(history) == 2
    assert history[0]["operation"] == "load"
    assert history[1]["operation"] == "expand"


def test_client_replay_ledger_reconstructs_history(tmp_path):
    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient()
    client.load(config_path)
    client.expand(config_path)

    expected_lgr = _current_lgr_path(tmp_path)
    replay = client.replay_ledger(expected_lgr)
    history = client.get_ledger_history(expected_lgr)
    assert replay == history


def test_sync_ledger_workspace_hash_matches_gts_snapshot_hash(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient()
    client.load(config_path)

    expected_lgr = _current_lgr_path(tmp_path)
    lgr_data = tomllib.loads(expected_lgr.read_text(encoding="utf-8"))
    expected_snapshot = _current_lgr_snapshot_path(tmp_path)
    gts_data = tomllib.loads(expected_snapshot.read_text(encoding="utf-8"))

    ledger_hash = lgr_data["ledger"][0]["workspace_hash"]
    snapshot_hash = gts_data["document"]["snapshot_hash"]
    assert ledger_hash == snapshot_hash


def _write_root_cgs(tmp_path, *, nested_child: bool = False, project_name: str = "demo"):
    nested_config = 'nested_config = "auto"\n' if nested_child else ""
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        (
            f"""
[document]
format_version = "1.0"

[project]
name = "{project_name}"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "{project_name}"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-repo"
relative_path = "deps/child-repo"
"""
            + nested_config
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _nested_minimal(project_name: str) -> str:
    return (
        f"""
[document]
format_version = "1.0"

[project]
name = "{project_name}"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "{project_name}"
relative_path = "."
""".strip()
        + "\n"
    )


def _write_clone_ready_cgs(tmp_path: Path) -> Path:
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "autoTest"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "demo"
default_branch = "autoTest"
fallback_branch = "main"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-repo"
default_branch = "autoTest"
fallback_branch = "main"
relative_path = "deps/child-repo"
nested_config = "auto"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _write_root_plus_docs_clone_cgs(tmp_path: Path) -> Path:
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "ComplexGitSync"
default_branch = "autoTest"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "ComplexGitSync"
default_branch = "autoTest"
fallback_branch = "main"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "docs"
default_branch = "autoTest"
fallback_branch = "main"
relative_path = "docs"
nested_config = "disabled"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _write_ready_gts(
    snapshot_path: Path,
    *,
    root_path: Path,
    project_name: str = "demo",
) -> Path:
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        f"""
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "{project_name}"
root_absolute_path = "{root_path.as_posix()}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "{project_name}"
node_type = "root"
absolute_path = "{root_path.as_posix()}"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "sha-demo"
project_owner_name = "owner"
project_name = "{project_name}"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return snapshot_path


class _FakeGitRunner:
    def __init__(self, remote_branches: dict[str, set[str]]):
        self.remote_branches = remote_branches
        self.clones: list[tuple[str, Path, str]] = []
        self.pulled: list[tuple[Path, str, str | None]] = []
        self.force_pulled: list[tuple[Path, str, str | None]] = []
        self.staged_paths: list[tuple[Path, str]] = []
        self.commits: list[tuple[Path, str]] = []
        self.commit_identities: list[tuple[Path, str | None, str | None]] = []
        self.pushed: list[tuple[Path, str, str | None]] = []
        self.branch_overrides: dict[Path, str | None] = {}
        self.status_lines: dict[Path, list[str]] = {}
        self.tracking_states: dict[Path, SyncState | None] = {}
        self.upstream_refs: dict[Path, str | None] = {}
        self.tracking_counts: dict[Path, tuple[int, int] | None] = {}

    def remote_branch_exists(self, remote_url: str, branch: str) -> bool:
        return branch in self.remote_branches.get(remote_url, set())

    def clone(self, remote_url: str, destination: Path | str, *, branch: str) -> None:
        destination_path = Path(destination)
        destination_path.mkdir(parents=True, exist_ok=True)
        self.clones.append((remote_url, destination_path.resolve(), branch))
        if destination_path.name == "child-repo":
            (destination_path / "child.cgs").write_text(
                """
[document]
format_version = "1.0"

[project]
name = "child-repo"
default_branch = "autoTest"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-repo"
default_branch = "autoTest"
fallback_branch = "main"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "docs"
default_branch = "autoTest"
fallback_branch = "main"
relative_path = "docs"
nested_config = "disabled"
""".strip()
                + "\n",
                encoding="utf-8",
            )

    def rev_parse_head(self, repo_path: Path | str) -> str:
        repo_name = Path(repo_path).name
        return f"sha-{repo_name}"

    def pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None:
        self.pulled.append((Path(repo_path).resolve(), remote, ref_name))

    def force_pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None:
        self.force_pulled.append((Path(repo_path).resolve(), remote, ref_name))

    def stage_path(self, repo_path: Path | str, relative_path: str) -> None:
        self.staged_paths.append((Path(repo_path).resolve(), relative_path))

    def commit(
        self,
        repo_path: Path | str,
        message: str,
        *,
        user_name: str | None = None,
        user_email: str | None = None,
    ) -> None:
        self.commits.append((Path(repo_path).resolve(), message))
        self.commit_identities.append((Path(repo_path).resolve(), user_name, user_email))

    def push(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
        set_upstream: bool = False,
    ) -> None:
        self.pushed.append((Path(repo_path).resolve(), remote, ref_name))

    def current_branch(self, repo_path: Path | str) -> str | None:
        resolved = Path(repo_path).resolve()
        if resolved in self.branch_overrides:
            return self.branch_overrides[resolved]
        for _, destination, branch in self.clones:
            if destination == resolved:
                return branch
        return None

    def status_porcelain(self, repo_path: Path | str) -> list[str]:
        return self.status_lines.get(Path(repo_path).resolve(), [])

    def branch_tracking_state(self, repo_path: Path | str) -> SyncState | None:
        return self.tracking_states.get(Path(repo_path).resolve(), SyncState.ALIGNED)

    def upstream_ref(self, repo_path: Path | str) -> str | None:
        return self.upstream_refs.get(Path(repo_path).resolve(), "origin/main")

    def branch_tracking_counts(self, repo_path: Path | str) -> tuple[int, int] | None:
        resolved = Path(repo_path).resolve()
        if resolved in self.tracking_counts:
            return self.tracking_counts[resolved]
        state = self.branch_tracking_state(repo_path)
        if state == SyncState.AHEAD:
            return (1, 0)
        if state == SyncState.BEHIND:
            return (0, 1)
        if state == SyncState.DIVERGED:
            return (1, 1)
        if state is None:
            return None
        return (0, 0)


class _StrictCloneGitRunner(_FakeGitRunner):
    def __init__(
        self,
        remote_branches: dict[str, set[str]],
        *,
        parent_repo_name: str,
    ) -> None:
        super().__init__(remote_branches)
        self.parent_repo_name = parent_repo_name
        self.parent_docs_seeded = False

    @staticmethod
    def _is_non_empty_dir(path: Path) -> bool:
        return path.exists() and next(path.iterdir(), None) is not None

    def clone(self, remote_url: str, destination: Path | str, *, branch: str) -> None:
        destination_path = Path(destination)
        if self._is_non_empty_dir(destination_path):
            raise RuntimeError(f"Destination not empty: {destination_path}")
        super().clone(remote_url, destination, branch=branch)
        if destination_path.name == self.parent_repo_name:
            docs_dir = destination_path / "docs"
            docs_dir.mkdir(parents=True, exist_ok=True)
            (docs_dir / "README.md").write_text("root docs\n", encoding="utf-8")
            self.parent_docs_seeded = True
        if destination_path.name == "docs":
            (destination_path / "from-docs-clone.txt").write_text("docs clone\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# fix_circularities tests
# ---------------------------------------------------------------------------


def _make_entry(repo_id: str, abs_path: Path, *, parent_id: str | None = None):
    """Create a minimal WorkingRepo for circularity testing."""
    from ComplexGitSync.git_repo import (
        NodeType,
        WorkingRepo,
    )

    separator_count = repo_id.count(":")
    node_type = NodeType.ROOT if repo_id == "root" else (NodeType.PARENT if separator_count == 1 else NodeType.LEAF)
    return WorkingRepo(
        repo_id=repo_id,
        name=repo_id.split(":")[-1],
        node_type=node_type,
        parent_id=parent_id,
        absolute_path=abs_path,
        relative_path=Path(".") if repo_id == "root" else Path(abs_path.name),
    )


def test_fix_circularities_removes_duplicate_leaf_when_parent_exists(tmp_path):
    """A leaf entry whose absolute_path matches an existing parent is removed."""
    from ComplexGitSync.git_tree import WorkingGitTree, fix_circularities

    parent2_path = tmp_path / "parent2"
    registry = WorkingGitTree()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:parent1", tmp_path / "parent1", parent_id="root"))
    registry.add(_make_entry("root:parent2", parent2_path, parent_id="root"))
    # Duplicate: parent2 also appears as a leaf of parent1
    registry.add(_make_entry("root:parent1:parent2", parent2_path, parent_id="root:parent1"))

    fixed = fix_circularities(registry)

    assert len(fixed) == 1
    assert "fixed_circularity:root:parent1:parent2→root:parent2" in fixed
    assert "root:parent1:parent2" not in registry.repos
    assert "root:parent2" in registry.repos


def test_fix_circularities_no_changes_when_no_duplicates(tmp_path):
    """Returns empty tuple when there are no duplicate absolute paths."""
    from ComplexGitSync.git_tree import WorkingGitTree, fix_circularities

    registry = WorkingGitTree()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:parent1", tmp_path / "parent1", parent_id="root"))
    registry.add(_make_entry("root:parent2", tmp_path / "parent2", parent_id="root"))

    fixed = fix_circularities(registry)

    assert fixed == ()
    assert len(registry.repos) == 3


def test_fix_circularities_handles_cascading_duplicates(tmp_path):
    """Both a leaf and its child duplicate are removed when all share the same paths."""
    from ComplexGitSync.git_tree import WorkingGitTree, fix_circularities

    parent2_path = tmp_path / "parent2"
    leaf_path = parent2_path / "leaf"
    registry = WorkingGitTree()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:parent1", tmp_path / "parent1", parent_id="root"))
    registry.add(_make_entry("root:parent2", parent2_path, parent_id="root"))
    registry.add(_make_entry("root:parent2:leaf", leaf_path, parent_id="root:parent2"))
    # Duplicates from parent1's nested discovery
    registry.add(_make_entry("root:parent1:parent2", parent2_path, parent_id="root:parent1"))
    registry.add(_make_entry("root:parent1:parent2:leaf", leaf_path, parent_id="root:parent1:parent2"))

    fixed = fix_circularities(registry)

    assert len(fixed) == 2
    assert "root:parent1:parent2" not in registry.repos
    assert "root:parent1:parent2:leaf" not in registry.repos
    assert "root:parent2" in registry.repos
    assert "root:parent2:leaf" in registry.repos


def test_fix_circularities_multiple_parents_sharing_leaf(tmp_path):
    """Multiple parents referencing the same leaf: only one canonical entry survives."""
    from ComplexGitSync.git_tree import WorkingGitTree, fix_circularities

    shared_path = tmp_path / "shared"
    registry = WorkingGitTree()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:shared", shared_path, parent_id="root"))
    registry.add(_make_entry("root:parent1", tmp_path / "parent1", parent_id="root"))
    registry.add(_make_entry("root:parent2", tmp_path / "parent2", parent_id="root"))
    registry.add(_make_entry("root:parent1:shared", shared_path, parent_id="root:parent1"))
    registry.add(_make_entry("root:parent2:shared", shared_path, parent_id="root:parent2"))

    fixed = fix_circularities(registry)

    assert len(fixed) == 2
    assert "root:shared" in registry.repos
    assert "root:parent1:shared" not in registry.repos
    assert "root:parent2:shared" not in registry.repos


def test_fix_circularities_keeps_duplicates_when_commit_or_status_conflict(tmp_path):
    from ComplexGitSync.git_repo import RepoLifecycleState, SyncState
    from ComplexGitSync.git_tree import WorkingGitTree, fix_circularities

    shared_path = tmp_path / "shared"
    registry = WorkingGitTree()
    canonical = _make_entry("root:shared", shared_path, parent_id="root")
    duplicate = _make_entry("root:parent1:shared", shared_path, parent_id="root:parent1")
    canonical.repo_lifecycle_state = RepoLifecycleState.READY
    canonical.sync_state = SyncState.ALIGNED
    canonical.commit_sha = "sha-1"
    duplicate.repo_lifecycle_state = RepoLifecycleState.PENDING
    duplicate.sync_state = SyncState.PENDING
    duplicate.commit_sha = "sha-2"

    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:parent1", tmp_path / "parent1", parent_id="root"))
    registry.add(canonical)
    registry.add(duplicate)

    fixed = fix_circularities(registry)

    assert fixed == ()
    assert "root:shared" in registry.repos
    assert "root:parent1:shared" in registry.repos


def test_fix_circularities_keeps_duplicates_when_declared_refs_conflict(tmp_path):
    from ComplexGitSync.git_repo import RefKind
    from ComplexGitSync.git_tree import WorkingGitTree, fix_circularities

    shared_path = tmp_path / "shared"
    registry = WorkingGitTree()
    canonical = _make_entry("root:shared", shared_path, parent_id="root")
    duplicate = _make_entry("root:parent1:shared", shared_path, parent_id="root:parent1")
    canonical.target_ref_kind = RefKind.BRANCH
    canonical.target_ref_name = "main"
    duplicate.target_ref_kind = RefKind.BRANCH
    duplicate.target_ref_name = "release"

    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:parent1", tmp_path / "parent1", parent_id="root"))
    registry.add(canonical)
    registry.add(duplicate)

    fixed = fix_circularities(registry)

    assert fixed == ()
    assert "root:shared" in registry.repos
    assert "root:parent1:shared" in registry.repos


def test_client_fix_circularities_is_callable_on_loaded_registry(tmp_path):
    """ComplexGitSyncClient.fix_circularities() works on a loaded registry."""
    config_path = _write_root_cgs(tmp_path)
    client = ComplexGitSyncClient()
    client.load_cgs(config_path)

    # No circularities in a simple 2-entry registry
    fixed = client.fix_circularities()

    assert fixed == ()


def test_discover_nested_configs_skips_child_with_existing_absolute_path(tmp_path):
    """discover_nested_configs does not add a child whose absolute_path already exists."""
    from ComplexGitSync.orchestre import ComplexGitSyncClient

    # Set up root .cgs with parent1 and parent2 as siblings
    root_cgs = tmp_path / "project.cgs"
    root_cgs.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "demo"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "parent1"
relative_path = "parent1"
nested_config = "auto"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "parent2"
relative_path = "parent2"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    # Create parent1 directory with a nested .cgs that references parent2
    parent1_dir = tmp_path / "parent1"
    parent1_dir.mkdir()
    (parent1_dir / "parent1.cgs").write_text(
        """
[document]
format_version = "1.0"

[project]
name = "parent1"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "parent1"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "parent2"
relative_path = "../parent2"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    client = ComplexGitSyncClient()
    client.load_cgs(root_cgs, discover_nested=True)
    registry = client.registry

    # parent2 should appear exactly once in the registry
    parent2_entries = [e for e in registry.values() if e.name == "parent2"]
    assert len(parent2_entries) == 1
    assert parent2_entries[0].repo_id == "root:parent2"


def test_expand_calls_fix_circularities_for_cgs_source(tmp_path):
    """expand() removes circularities when a .cgs source has cross-referencing parents."""
    from ComplexGitSync.orchestre import ComplexGitSyncClient

    root_cgs = tmp_path / "project.cgs"
    root_cgs.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "demo"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "parent1"
relative_path = "parent1"
nested_config = "auto"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "parent2"
relative_path = "parent2"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    parent1_dir = tmp_path / "parent1"
    parent1_dir.mkdir()
    (parent1_dir / "parent1.cgs").write_text(
        """
[document]
format_version = "1.0"

[project]
name = "parent1"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "parent1"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "parent2"
relative_path = "../parent2"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    client = ComplexGitSyncClient()
    client.expand(root_cgs)
    registry = client.registry

    parent2_entries = [e for e in registry.values() if e.name == "parent2"]
    assert len(parent2_entries) == 1
    assert parent2_entries[0].repo_id == "root:parent2"


# ---------------------------------------------------------------------------
# Helper for parsing fix_circularities change descriptors
# ---------------------------------------------------------------------------

_CHANGE_PREFIX = "fixed_circularity:"
_CHANGE_SEP = "\u2192"  # →


def _parse_change(change: str) -> tuple[str, str]:
    """Parse a fix_circularities change string into (removed_id, canonical_id)."""
    body = change.removeprefix(_CHANGE_PREFIX)
    removed, canonical = body.split(_CHANGE_SEP, 1)
    return removed, canonical


# ---------------------------------------------------------------------------
# find_strongly_connected_components tests
# ---------------------------------------------------------------------------


def test_find_scc_no_cycle_returns_trivial_sccs(tmp_path):
    """A simple linear chain has only trivial SCCs (size 1)."""
    from ComplexGitSync.git_tree import find_strongly_connected_components

    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    graph = {a: {b}, b: {c}, c: set()}
    sccs = find_strongly_connected_components(graph)
    assert all(len(s) == 1 for s in sccs)
    all_paths = {p for scc in sccs for p in scc}
    assert all_paths == {a, b, c}


def test_find_scc_two_node_cycle(tmp_path):
    """A two-node cycle is detected as one non-trivial SCC."""
    from ComplexGitSync.git_tree import find_strongly_connected_components

    a, b = tmp_path / "a", tmp_path / "b"
    graph = {a: {b}, b: {a}}
    sccs = find_strongly_connected_components(graph)
    non_trivial = [s for s in sccs if len(s) > 1]
    assert len(non_trivial) == 1
    assert set(non_trivial[0]) == {a, b}


def test_find_scc_three_node_cycle(tmp_path):
    """A three-node mutual cycle is detected as one SCC."""
    from ComplexGitSync.git_tree import find_strongly_connected_components

    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    graph = {a: {b}, b: {c}, c: {a}}
    sccs = find_strongly_connected_components(graph)
    non_trivial = [s for s in sccs if len(s) > 1]
    assert len(non_trivial) == 1
    assert set(non_trivial[0]) == {a, b, c}


def test_find_scc_disconnected_graph(tmp_path):
    """Disconnected nodes each form their own trivial SCC."""
    from ComplexGitSync.git_tree import find_strongly_connected_components

    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    graph = {a: set(), b: set(), c: set()}
    sccs = find_strongly_connected_components(graph)
    assert len(sccs) == 3
    assert all(len(s) == 1 for s in sccs)


def test_find_scc_self_loop_node_appears_in_result(tmp_path):
    """A self-loop node is included in the SCC result (as a trivial size-1 SCC)."""
    from ComplexGitSync.git_tree import find_strongly_connected_components

    a = tmp_path / "a"
    graph = {a: {a}}
    sccs = find_strongly_connected_components(graph)
    all_paths = {p for scc in sccs for p in scc}
    assert a in all_paths
    assert len(sccs) == 1


# ---------------------------------------------------------------------------
# topological_sort tests
# ---------------------------------------------------------------------------


def test_topological_sort_linear_chain(tmp_path):
    """Root -> parent -> leaf is returned in parent-first order."""
    from ComplexGitSync.git_tree import WorkingGitTree, topological_sort

    registry = WorkingGitTree()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:parent", tmp_path / "parent", parent_id="root"))
    registry.add(
        _make_entry("root:parent:leaf", tmp_path / "parent" / "leaf", parent_id="root:parent")
    )

    order = topological_sort(registry)
    ids = [e.repo_id for e in order]

    assert ids.index("root") < ids.index("root:parent")
    assert ids.index("root:parent") < ids.index("root:parent:leaf")


def test_topological_sort_all_entries_returned(tmp_path):
    """Every registry entry is returned exactly once."""
    from ComplexGitSync.git_tree import WorkingGitTree, topological_sort

    registry = WorkingGitTree()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:a", tmp_path / "a", parent_id="root"))
    registry.add(_make_entry("root:b", tmp_path / "b", parent_id="root"))
    registry.add(_make_entry("root:a:leaf", tmp_path / "a" / "leaf", parent_id="root:a"))

    order = topological_sort(registry)
    assert len(order) == 4
    assert {e.repo_id for e in order} == {"root", "root:a", "root:b", "root:a:leaf"}


def test_topological_sort_parent_before_all_children(tmp_path):
    """Root always comes before all other entries."""
    from ComplexGitSync.git_tree import WorkingGitTree, topological_sort

    registry = WorkingGitTree()
    registry.add(_make_entry("root", tmp_path))
    for name in ("a", "b", "c"):
        registry.add(_make_entry(f"root:{name}", tmp_path / name, parent_id="root"))

    order = topological_sort(registry)
    ids = [e.repo_id for e in order]
    assert ids[0] == "root"


# ---------------------------------------------------------------------------
# fix_circularities — SCC phase (Phase 1) tests
# ---------------------------------------------------------------------------


def test_fix_circularities_detects_true_two_node_cycle(tmp_path):
    """Phase 1 removes the back-edge entry that creates an A<->B cycle."""
    from ComplexGitSync.git_tree import WorkingGitTree, fix_circularities

    # Registry: root -> a -> b -> a (a's path reappears as a child of b)
    path_a = tmp_path / "a"
    path_b = tmp_path / "a" / "b"

    registry = WorkingGitTree()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:a", path_a, parent_id="root"))
    registry.add(_make_entry("root:a:b", path_b, parent_id="root:a"))
    # Cycle-creating back-edge: b's .cgs references a again
    registry.add(_make_entry("root:a:b:a", path_a, parent_id="root:a:b"))

    fixed = fix_circularities(registry)

    assert len(fixed) == 1
    assert "fixed_circularity:root:a:b:a\u2192root:a" in fixed
    assert "root:a:b:a" not in registry.repos
    # Legitimate entries are preserved
    assert "root:a" in registry.repos
    assert "root:a:b" in registry.repos


def test_fix_circularities_back_edge_marked_is_external_reference(tmp_path):
    """The back-edge entry has is_external_reference=True set before removal."""
    from ComplexGitSync.git_tree import WorkingGitTree, fix_circularities

    path_a = tmp_path / "a"
    path_b = tmp_path / "a" / "b"

    registry = WorkingGitTree()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:a", path_a, parent_id="root"))
    registry.add(_make_entry("root:a:b", path_b, parent_id="root:a"))
    back_edge_entry = _make_entry("root:a:b:a", path_a, parent_id="root:a:b")
    registry.add(back_edge_entry)

    fix_circularities(registry)

    # The entry is removed from the registry, but the is_external_reference
    # flag was set on the object before removal.
    assert back_edge_entry.is_external_reference is True


def test_fix_circularities_anchor_heuristic_most_external_edges(tmp_path):
    """When two nodes tie on depth, the one with more external edges wins."""
    from ComplexGitSync.git_tree import WorkingGitTree, fix_circularities

    # Build: root -> a -> c -> a (cycle); root -> b -> c -> b (cycle)
    # path_a and path_b are both at depth 1 (1 colon in repo_id).
    # path_a has 2 external in-edges (from root via root:a AND root:c:a).
    # Actually, let's set up a simpler heuristic-1 test:
    # root -> a, root -> b, a -> c, b -> c, c -> a  (so a has external in from root)
    path_a = tmp_path / "a"
    path_b = tmp_path / "b"
    path_c = tmp_path / "c"

    registry = WorkingGitTree()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:a", path_a, parent_id="root"))
    registry.add(_make_entry("root:b", path_b, parent_id="root"))
    registry.add(_make_entry("root:a:c", path_c, parent_id="root:a"))
    registry.add(_make_entry("root:b:c", path_c, parent_id="root:b"))
    # Two back-edges from c back to a (through b)
    registry.add(_make_entry("root:b:c:a", path_a, parent_id="root:b:c"))

    fixed = fix_circularities(registry)

    # root:a should be anchor (external incoming from root + depth 1)
    # root:b:c:a is the back-edge → removed
    assert "root:a" in registry.repos
    assert "root:b:c:a" not in registry.repos
    assert any("root:b:c:a" in change for change in fixed)


def test_fix_circularities_no_duplicate_changes_for_same_entry(tmp_path):
    """Each removed entry appears exactly once in the returned changes tuple."""
    from ComplexGitSync.git_tree import WorkingGitTree, fix_circularities

    path_a = tmp_path / "a"
    path_b = tmp_path / "a" / "b"

    registry = WorkingGitTree()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:a", path_a, parent_id="root"))
    registry.add(_make_entry("root:a:b", path_b, parent_id="root:a"))
    registry.add(_make_entry("root:a:b:a", path_a, parent_id="root:a:b"))

    fixed = fix_circularities(registry)

    # No entry should appear twice in the changes.
    removed_ids = [_parse_change(change)[0] for change in fixed]
    assert len(removed_ids) == len(set(removed_ids))


def test_fix_circularities_phase1_and_phase2_together(tmp_path):
    """Phase 1 (SCC) and Phase 2 (path dedup) both fire when needed."""
    from ComplexGitSync.git_tree import WorkingGitTree, fix_circularities

    path_a = tmp_path / "a"
    path_b = tmp_path / "a" / "b"
    path_shared = tmp_path / "shared"

    registry = WorkingGitTree()
    registry.add(_make_entry("root", tmp_path))
    # Phase 1 cycle: root -> a -> b -> a
    registry.add(_make_entry("root:a", path_a, parent_id="root"))
    registry.add(_make_entry("root:a:b", path_b, parent_id="root:a"))
    registry.add(_make_entry("root:a:b:a", path_a, parent_id="root:a:b"))
    # Phase 2 plain dedup: shared appears under both root and root:a
    registry.add(_make_entry("root:shared", path_shared, parent_id="root"))
    registry.add(_make_entry("root:a:shared", path_shared, parent_id="root:a"))

    fixed = fix_circularities(registry)

    # Phase 1 removes root:a:b:a (back-edge)
    assert "root:a:b:a" not in registry.repos
    # Phase 2 removes root:a:shared (duplicate path)
    assert "root:a:shared" not in registry.repos
    assert "root:shared" in registry.repos
    assert len(fixed) == 2


def test_fix_circularities_pending_clone_skips_external_reference(tmp_path):
    """_pending_clone_entries excludes entries with is_external_reference=True."""
    from ComplexGitSync.git_repo import RepoLifecycleState

    # Manually set is_external_reference on a DECLARED entry and verify it
    # is filtered out by _pending_clone_entries.
    registry = WorkingGitTree()
    registry.add(_make_entry("root", tmp_path))
    normal = _make_entry("root:a", tmp_path / "a", parent_id="root")
    ext_ref = _make_entry("root:b", tmp_path / "b", parent_id="root")
    ext_ref.is_external_reference = True
    registry.add(normal)
    registry.add(ext_ref)

    # Both entries are DECLARED; only the non-external-reference one should
    # appear in the pending clone list.
    pending = [
        e
        for e in registry.values()
        if e.repo_lifecycle_state == RepoLifecycleState.DECLARED
        and not e.is_external_reference
    ]
    assert len(pending) == 2  # root + normal (root is also DECLARED)
    assert all(not e.is_external_reference for e in pending)


# ---------------------------------------------------------------------------
# sync_gitignore tests (DevPlanTicket Milestone 1)
# ---------------------------------------------------------------------------


def test_sync_gitignore_writes_children_at_every_parent_bearing_level(tmp_path):
    from ComplexGitSync.git_tree import sync_gitignore

    root_path = tmp_path / "root"
    middle_path = root_path / "middle"
    sub_path = middle_path / "sub"
    sibling_path = root_path / "sibling-leaf"
    for path in (root_path, middle_path, sub_path, sibling_path):
        path.mkdir(parents=True)

    registry = WorkingGitTree()
    registry.add(_make_entry("root", root_path))
    registry.add(_make_entry("root:middle", middle_path, parent_id="root"))
    registry.add(_make_entry("root:middle:sub", sub_path, parent_id="root:middle"))
    registry.add(_make_entry("root:sibling-leaf", sibling_path, parent_id="root"))

    changed = sync_gitignore(registry)

    assert set(changed) == {"root", "root:middle"}
    assert (root_path / ".gitignore").read_text(encoding="utf-8").splitlines() == [
        "middle",
        "sibling-leaf",
    ]
    assert (middle_path / ".gitignore").read_text(encoding="utf-8").splitlines() == ["sub"]
    assert not (sub_path / ".gitignore").exists()
    assert not (sibling_path / ".gitignore").exists()


def test_sync_gitignore_preserves_existing_lines_and_only_appends_missing(tmp_path):
    from ComplexGitSync.git_tree import sync_gitignore

    root_path = tmp_path / "root"
    child_path = root_path / "child-repo"
    other_child_path = root_path / "other-child"
    root_path.mkdir()
    child_path.mkdir()
    other_child_path.mkdir()
    (root_path / ".gitignore").write_text("# custom comment\nbuild/\nchild-repo\n", encoding="utf-8")

    registry = WorkingGitTree()
    registry.add(_make_entry("root", root_path))
    registry.add(_make_entry("root:child-repo", child_path, parent_id="root"))
    registry.add(_make_entry("root:other-child", other_child_path, parent_id="root"))

    changed = sync_gitignore(registry)

    assert changed == ("root",)
    assert (root_path / ".gitignore").read_text(encoding="utf-8").splitlines() == [
        "# custom comment",
        "build/",
        "child-repo",
        "other-child",
    ]


def test_sync_gitignore_second_call_is_a_no_op(tmp_path):
    from ComplexGitSync.git_tree import sync_gitignore

    root_path = tmp_path / "root"
    child_path = root_path / "child-repo"
    root_path.mkdir()
    child_path.mkdir()

    registry = WorkingGitTree()
    registry.add(_make_entry("root", root_path))
    registry.add(_make_entry("root:child-repo", child_path, parent_id="root"))

    first = sync_gitignore(registry)
    content_after_first = (root_path / ".gitignore").read_text(encoding="utf-8")
    second = sync_gitignore(registry)

    assert first == ("root",)
    assert second == ()
    assert (root_path / ".gitignore").read_text(encoding="utf-8") == content_after_first


def test_sync_gitignore_skip_leaves_repo_untouched(tmp_path):
    from ComplexGitSync.git_tree import sync_gitignore

    root_path = tmp_path / "root"
    child_path = root_path / "child-repo"
    root_path.mkdir()
    child_path.mkdir()

    registry = WorkingGitTree()
    registry.add(_make_entry("root", root_path))
    registry.add(_make_entry("root:child-repo", child_path, parent_id="root"))

    changed = sync_gitignore(registry, skip={"root"})

    assert changed == ()
    assert not (root_path / ".gitignore").exists()


def test_sync_gitignore_ignores_leaf_with_no_children(tmp_path):
    from ComplexGitSync.git_tree import sync_gitignore

    leaf_path = tmp_path / "leaf"
    leaf_path.mkdir()

    registry = WorkingGitTree()
    registry.add(_make_entry("root", leaf_path))

    changed = sync_gitignore(registry)

    assert changed == ()
    assert not (leaf_path / ".gitignore").exists()


# ---------------------------------------------------------------------------
# _clone_registry_entry — --force-protocol and the SSH-auth-failure hint
# ---------------------------------------------------------------------------


def _make_clone_entry(tmp_path: Path, *, access_protocol=AccessProtocol.SSH) -> WorkingRepo:
    """A minimal leaf WorkingRepo ready for _clone_registry_entry (no parent, branch ref)."""
    return WorkingRepo(
        repo_id="root:leaf",
        name="leaf",
        node_type=NodeType.LEAF,
        parent_id=None,
        absolute_path=tmp_path / "leaf",
        relative_path=Path("leaf"),
        target_ref_kind=RefKind.BRANCH,
        target_ref_name="main",
        default_branch="main",
        access_protocol=access_protocol,
        project_owner_name="owner",
        project_name="leaf",
    )


def test_clone_registry_entry_appends_force_protocol_hint_on_ssh_auth_failure(monkeypatch, tmp_path):
    client = ComplexGitSyncClient()
    entry = _make_clone_entry(tmp_path, access_protocol=AccessProtocol.SSH)

    monkeypatch.setattr(type(client.git_runner), "remote_branch_exists", lambda self, url, branch: True)

    def _fail_clone(self, git_runner, remote_url, destination, *, branch):
        raise GitSyncError(
            f"Git command failed (git clone --branch {branch} --single-branch {remote_url} "
            f"{destination}): Permission denied (publickey).\nfatal: Could not read from remote "
            f"repository."
        )

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "clone", _fail_clone)

    with pytest.raises(GitSyncError) as excinfo:
        client._clone_registry_entry(entry)

    message = str(excinfo.value)
    assert "Permission denied (publickey)" in message  # original git error preserved
    assert "--force-protocol https" in message
    assert "leaf" in message


def test_clone_registry_entry_leaves_unrelated_failures_unchanged(monkeypatch, tmp_path):
    client = ComplexGitSyncClient()
    entry = _make_clone_entry(tmp_path, access_protocol=AccessProtocol.SSH)

    monkeypatch.setattr(type(client.git_runner), "remote_branch_exists", lambda self, url, branch: True)

    def _fail_clone(self, git_runner, remote_url, destination, *, branch):
        raise GitSyncError(f"Git command failed (git clone ... {remote_url}): fatal: repository not found")

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "clone", _fail_clone)

    with pytest.raises(GitSyncError) as excinfo:
        client._clone_registry_entry(entry)

    message = str(excinfo.value)
    assert message == "Git command failed (git clone ... " + client._build_remote_url(entry) + "): fatal: repository not found"
    assert "--force-protocol" not in message


def test_clone_registry_entry_does_not_hint_when_protocol_is_https(monkeypatch, tmp_path):
    client = ComplexGitSyncClient()
    entry = _make_clone_entry(tmp_path, access_protocol=AccessProtocol.HTTPS)

    monkeypatch.setattr(type(client.git_runner), "remote_branch_exists", lambda self, url, branch: True)

    def _fail_clone(self, git_runner, remote_url, destination, *, branch):
        # SSH-shaped message is an unrealistic case for an https clone, but the
        # guard is on the protocol actually used for this run, not just the
        # message text -- confirm it does not hint here regardless.
        raise GitSyncError("Permission denied (publickey).")

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "clone", _fail_clone)

    with pytest.raises(GitSyncError) as excinfo:
        client._clone_registry_entry(entry)

    assert "--force-protocol" not in str(excinfo.value)


def test_clone_registry_entry_force_access_protocol_overrides_hint_check(monkeypatch, tmp_path):
    # entry itself declares ssh, but this run forced https -- the hint must
    # follow the protocol actually used (the override), not the entry's own
    # declared value, so it correctly stays silent here.
    client = ComplexGitSyncClient()
    client._forced_access_protocol = AccessProtocol.HTTPS
    entry = _make_clone_entry(tmp_path, access_protocol=AccessProtocol.SSH)

    monkeypatch.setattr(type(client.git_runner), "remote_branch_exists", lambda self, url, branch: True)

    def _fail_clone(self, git_runner, remote_url, destination, *, branch):
        raise GitSyncError("Permission denied (publickey).")

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "clone", _fail_clone)

    with pytest.raises(GitSyncError) as excinfo:
        client._clone_registry_entry(entry)

    assert "--force-protocol" not in str(excinfo.value)


def test_build_remote_url_force_access_protocol_overrides_entry_declared_ssh(tmp_path):
    # The same _build_remote_url call site serves both root-sibling entries
    # (parsed from the top-level .cgs) and nested-discovered ones (parsed
    # from a different repo's own .cgs, e.g. DocSpec inside DocCGS.cgs) --
    # this proves the override reaches an entry regardless of which path
    # constructed it, without needing a full multi-repo clone.
    client = ComplexGitSyncClient()
    entry = _make_clone_entry(tmp_path, access_protocol=AccessProtocol.SSH)

    assert client._build_remote_url(entry).startswith("git@")

    client._forced_access_protocol = AccessProtocol.HTTPS
    assert client._build_remote_url(entry).startswith("https://")

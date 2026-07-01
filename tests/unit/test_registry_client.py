from __future__ import annotations

import tomllib
from pathlib import Path, PureWindowsPath

import pytest

from ComplexGitSync.orchestre import ComplexGitSyncClient, GocDocument, _path_to_environment_marker
from ComplexGitSync.errors import ConfigValidationError, GitSyncError, NestedConfigDiscoveryError
from ComplexGitSync.git_repo import GitRepo, NodeType, RefKind, RepoLifecycleState
from ComplexGitSync.git_tree import DependencyTreeRegistry, GitTree, TreeLifecycleState, make_repo_id
from ComplexGitSync.orchestre import GtsDocument, RuntimeStateStore, build_registry_from_gts_document


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


def test_client_read_alias_loads_cgs(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    client = ComplexGitSyncClient()

    registry = client.read(config_path)

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

    def _fake_initialise_cgs(path, *, cgspath=None):
        captured["path"] = path
        captured["cgspath"] = cgspath
        return "ok"

    monkeypatch.setattr(client, "initialise_cgs", _fake_initialise_cgs)

    result = client.initialise("project.cgs")

    assert result == "ok"
    assert captured["path"] == Path("project.cgs").resolve()
    assert captured["cgspath"] is None


def test_client_initialise_forwards_output_dir_as_cgspath(monkeypatch):
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}

    def _fake_initialise_cgs(path, *, cgspath=None):
        captured["cgspath"] = cgspath
        return "ok"

    monkeypatch.setattr(client, "initialise_cgs", _fake_initialise_cgs)

    client.initialise("project.cgs", output_dir="../")

    assert captured["cgspath"] == "../"


def test_initialise_cgs_uses_cwd_as_root_and_cgshome_for_snapshot(tmp_path, monkeypatch):
    root_path = tmp_path / "workspace" / "demo"
    root_path.mkdir(parents=True)
    cgspath = tmp_path / "cgshome"
    cgspath.mkdir()
    monkeypatch.chdir(root_path)

    config_path = _write_clone_ready_cgs(tmp_path)
    fake_runner = _FakeGitRunner(
        {
            "git@github.com:owner/child-repo.git": {"autoTest"},
            "git@github.com:owner/docs.git": {"main"},
        }
    )
    state_store = RuntimeStateStore(base_dir=tmp_path / "runtime-state")
    client = ComplexGitSyncClient(git_runner=fake_runner, state_store=state_store)

    registry = client.initialise_cgs(config_path, cgspath=cgspath)

    root_entry = registry.get("root")
    assert root_entry.absolute_path == root_path.resolve()
    assert root_entry.repo_lifecycle_state.value in {"READY", "FALLBACK_READY"}

    # Root was never cloned — only dependencies were.
    cloned_remotes = [remote for remote, _, _ in fake_runner.clones]
    assert "git@github.com:owner/demo.git" not in cloned_remotes

    # Snapshot must be stored under CGSHOME (= resolved CGSPATH).
    snapshot_path = state_store.latest_snapshot_for(config_path)
    assert snapshot_path is not None
    assert str(snapshot_path).startswith(str(cgspath.resolve()))
    assert ".cgitsync" in str(snapshot_path)
    assert "state" in str(snapshot_path)


def test_initialise_cgs_default_cgshome_is_two_levels_up(tmp_path, monkeypatch):
    # Build a directory structure: tmp_path/parent/child/cwd
    cwd = tmp_path / "parent" / "child" / "cwd"
    cwd.mkdir(parents=True)
    monkeypatch.chdir(cwd)

    expected_cgshome = (cwd / "../..").resolve()
    config_path = _write_root_cgs(tmp_path)

    client = ComplexGitSyncClient(git_runner=_FakeGitRunner({}))
    captured: dict[str, object] = {}

    def _fake_write_gts(*, command_origin, output_path=None):
        captured["output_path"] = output_path
        return output_path or tmp_path / "dummy.gts"

    monkeypatch.setattr(client, "write_gts_snapshot", _fake_write_gts)
    monkeypatch.setattr(client.state_store, "record_snapshot", lambda *a, **kw: None)

    client.registry = None

    def _fake_build_registry(*args, **kwargs):
        from ComplexGitSync.orchestre import build_registry_from_cgs_document as _orig
        from ComplexGitSync.orchestre import ROOT_REPO_ID
        from ComplexGitSync.git_repo import RepoLifecycleState
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


def test_resolve_clone_root_uses_output_dir_as_base(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    client = ComplexGitSyncClient()
    output_dir = tmp_path / "parent"
    output_dir.mkdir()

    result = client.resolve_clone_root(config_path, output_dir=output_dir)

    assert result == (output_dir / "demo").resolve()


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


def test_client_clone_alias_calls_clone_cgs(monkeypatch):
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}

    def _fake_clone_cgs(path, *, target_dir=None, output_dir=None):
        captured["path"] = path
        captured["target_dir"] = target_dir
        return "ok"

    monkeypatch.setattr(client, "clone_cgs", _fake_clone_cgs)

    result = client.clone("project.cgs", target_dir="workspace/demo")

    assert result == "ok"
    assert captured["path"] == "project.cgs"
    assert captured["target_dir"] == "workspace/demo"


def test_client_branch_delegates_to_gittree_git_branch(monkeypatch):
    client = ComplexGitSyncClient()
    client.registry = DependencyTreeRegistry()
    captured: dict[str, object] = {}

    def _spy_branch(self, git_runner, branch_name, *, registry=None):
        captured["git_runner"] = git_runner
        captured["branch_name"] = branch_name
        captured["registry"] = registry

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "branch", _spy_branch)

    result = client.branch("feature/test")

    assert result is client.registry
    assert captured["git_runner"] is client.git_runner
    assert captured["branch_name"] == "feature/test"
    assert captured["registry"] is None


def test_client_git_dispatches_extended_commands(monkeypatch):
    client = ComplexGitSyncClient()
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    monkeypatch.setattr(client, "clone", lambda *a, **k: calls.append(("clone", a, k)) or "clone")
    monkeypatch.setattr(client, "pull", lambda *a, **k: calls.append(("pull", a, k)) or "pull")
    monkeypatch.setattr(client, "branch", lambda *a, **k: calls.append(("branch", a, k)) or "branch")
    monkeypatch.setattr(client, "add", lambda *a, **k: calls.append(("add", a, k)) or "add")
    monkeypatch.setattr(client, "freeze", lambda *a, **k: calls.append(("freeze", a, k)) or "freeze")

    assert client.git(None, "clone", "project.cgs", "workspace/demo") == "clone"
    assert client.git(None, "pull", "state.gts") == "pull"
    assert client.git(None, "branch", "feature/x") == "branch"
    assert client.git(None, "add") == "add"
    assert client.git(None, "freeze", "v1.2.3") == "freeze"
    assert calls == [
        ("clone", ("project.cgs",), {"target_dir": "workspace/demo"}),
        ("pull", ("state.gts",), {}),
        ("branch", ("feature/x",), {}),
        ("add", (), {}),
        ("freeze", ("v1.2.3",), {}),
    ]


@pytest.mark.parametrize(
    ("command", "expected_message"),
    [
        ("clone", "clone requires source path argument."),
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
    registry = DependencyTreeRegistry()
    captured: dict[str, object] = {}

    def _spy_add(self, git_runner, *, registry=None):
        captured["bound_registry"] = self.registry
        captured["registry_arg"] = registry

    monkeypatch.setattr(type(client.orchestre.git_tree.git), "add", _spy_add)

    client.git(registry, "add")
    assert client.registry is registry
    assert client.orchestre.git_tree.git.registry is registry
    assert captured["bound_registry"] is registry
    assert captured["registry_arg"] is None


def test_client_freeze_alias_calls_freeze_release(monkeypatch):
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}

    def _fake_freeze_release(name, *, output_gts=None, message=None, stage_all=True):
        captured["name"] = name
        captured["output_gts"] = output_gts
        captured["message"] = message
        captured["stage_all"] = stage_all
        return "ok"

    monkeypatch.setattr(client, "freeze_release", _fake_freeze_release)

    result = client.freeze("r1", output_gts="release.gts", message="msg", stage_all=False)

    assert result == "ok"
    assert captured == {
        "name": "r1",
        "output_gts": "release.gts",
        "message": "msg",
        "stage_all": False,
    }


def test_client_launch_alias_calls_launch_release(monkeypatch):
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}

    def _fake_launch_release(snapshot_path):
        captured["snapshot_path"] = snapshot_path
        return "ok"

    monkeypatch.setattr(client, "launch_release", _fake_launch_release)

    result = client.launch("state.gts")

    assert result == "ok"
    assert captured["snapshot_path"] == "state.gts"


def test_client_pull_dispatches_to_restart_for_cgs(monkeypatch):
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}

    def _fake_restart(config_path):
        captured["config_path"] = config_path
        return "ok"

    monkeypatch.setattr(client, "restart", _fake_restart)

    result = client.pull("project.cgs")

    assert result == "ok"
    assert captured["config_path"] == Path("project.cgs").resolve()


def test_client_pull_dispatches_to_launch_release_for_gts(monkeypatch):
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}

    def _fake_launch_release(snapshot_path):
        captured["snapshot_path"] = snapshot_path
        return "ok"

    monkeypatch.setattr(client, "launch_release", _fake_launch_release)

    result = client.pull("state.gts")

    assert result == "ok"
    assert captured["snapshot_path"] == Path("state.gts").resolve()


def test_client_orchestrate_executes_goc_actions_in_order(monkeypatch, tmp_path):
    plan_path = _write_goc_plan(
        tmp_path,
        source="project.cgs",
        actions="""
[[actions]]
command = "clone"
[actions.args]
target_dir = "workspace/demo"

[[actions]]
command = "checkout"
[actions.args]
ref = "autoTest"
ref_type = "branch"

[[actions]]
command = "add"
""",
    )
    client = ComplexGitSyncClient()
    calls: list[tuple[str, tuple[str, ...]]] = []
    registry = DependencyTreeRegistry()

    def _fake_git(bound_registry, command, *args):
        assert bound_registry is client.registry or bound_registry is None
        calls.append((command, tuple(str(a) for a in args)))
        if command == "clone":
            client.registry = registry
        return client.registry if client.registry is not None else registry

    monkeypatch.setattr(client, "git", _fake_git)

    report = client.orchestrate(plan_path)

    assert [entry["status"] for entry in report] == ["ok", "ok", "ok"]
    assert calls == [
        ("clone", (str((tmp_path / "project.cgs").resolve()), "workspace/demo")),
        ("checkout", ("autoTest",)),
        ("add", ()),
    ]


def test_client_orchestrate_reports_unsupported_actions(monkeypatch, tmp_path):
    class _FakeGocDocument:
        project_source = "project.cgs"
        actions = [{"command": "unsupported-cmd"}]

    monkeypatch.setattr(
        GocDocument,
        "from_toml",
        classmethod(lambda cls, _path: _FakeGocDocument()),
    )

    report = ComplexGitSyncClient().orchestrate(tmp_path / "plan.goc", stop_on_error=False)

    assert report[0]["status"] == "error"
    assert "Unsupported .goc action command" in report[0]["error"]


def test_client_orchestrate_rejects_ambiguous_alias_args(monkeypatch, tmp_path):
    plan_path = _write_goc_plan(
        tmp_path,
        source="state.gts",
        actions="""
[[actions]]
command = "checkout"
[actions.args]
ref = "main"
branch = "dev"
ref_type = "branch"
""",
    )
    client = ComplexGitSyncClient()
    monkeypatch.setattr(client, "load_gts", lambda _path: None)
    client.registry = DependencyTreeRegistry()

    report = client.orchestrate(plan_path, stop_on_error=False)

    assert report[0]["status"] == "error"
    assert "must not define both 'ref' and 'branch'" in report[0]["error"]


def test_client_print_alias_supports_gts(tmp_path):
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

    assert rendered_tree.startswith("ROOT demo [")
    assert "dirty blocked" in rendered_tree
    assert "child-repo [" in rendered_tree
    assert "└── child-repo" in rendered_tree


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
        (remote, relative.as_posix(), branch)
        for _, remote, relative, branch in fake_runner.submodule_adds
    ] == [
        ("git@github.com:owner/child-repo.git", "deps/child-repo", "autoTest"),
        ("git@github.com:owner/docs.git", "docs", "main"),
    ]

    snapshot_path = state_store.latest_snapshot_for(config_path)
    assert snapshot_path is not None
    assert snapshot_path == (tmp_path / "workspace" / "demo" / ".cgitsync" / "state" / "project.gts").resolve()

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
    assert [
        (remote, relative.as_posix(), branch)
        for _, remote, relative, branch in fake_runner.submodule_adds
    ] == [("git@github.com:owner/docs.git", "docs", "main")]


def test_clone_cgs_fails_when_nested_repo_not_tracked_as_submodule(tmp_path):
    class _FakeGitRunnerNoSubmodules(_FakeGitRunner):
        def is_submodule(self, repo_path: Path | str, relative_path: Path | str) -> bool:
            return False

    config_path = _write_root_plus_docs_clone_cgs(tmp_path)
    runner = _FakeGitRunnerNoSubmodules(
        {
            "git@github.com:owner/ComplexGitSync.git": {"main"},
            "git@github.com:owner/docs.git": {"main"},
        }
    )
    client = ComplexGitSyncClient(git_runner=runner)

    with pytest.raises(GitSyncError, match="Submodule constraint violated"):
        client.clone_cgs(config_path, target_dir=tmp_path / "workspace" / "ComplexGitSync")


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

    empty_registry = DependencyTreeRegistry()
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


def test_resolve_goc_project_source_expands_home_variable(monkeypatch, tmp_path):
    fake_home = (tmp_path / "home" / "user").resolve()
    monkeypatch.setenv("HOME", str(fake_home))
    document = GocDocument.from_dict(
        {
            "document": {"format_version": "1.0"},
            "project": {"source": "$HOME/workspace/demo/project.cgs"},
            "actions": [{"command": "clone"}],
        }
    )

    resolved = ComplexGitSyncClient()._resolve_goc_project_source(document, tmp_path / "plan.goc")
    assert resolved == (fake_home / "workspace" / "demo" / "project.cgs")


def test_make_repo_id_falls_back_to_name_when_relative_path_is_empty():
    assert make_repo_id("root", "", "child-repo") == "root:child-repo"


def test_make_repo_id_only_collapses_explicit_dot_relative_path():
    assert make_repo_id("root", ".", "child-repo") == "root"
    assert make_repo_id("root", None, ".") == "root:."
    assert make_repo_id("root", "", "") == "root:"


def test_client_load_cgs_writes_gts_snapshot(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    expected_snapshot = tmp_path / ".cgitsync" / "state" / "project.gts"

    client = ComplexGitSyncClient()
    client.load(config_path)

    assert expected_snapshot.is_file()


def test_client_load_cgs_updates_project_local_lgr(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)
    expected_snapshot = (tmp_path / ".cgitsync" / "state" / "project.gts").resolve()
    expected_lgr = tmp_path / "demo.lgr"

    client = ComplexGitSyncClient()
    client.load(config_path)

    data = tomllib.loads(expected_lgr.read_text(encoding="utf-8"))
    expected_path_marker = _path_to_environment_marker(expected_snapshot)
    assert data["register"]["current_snapshot_id"] == "gts-000001"
    assert data["register"]["current_snapshot_path"] == expected_path_marker
    assert len(data["snapshots"]) == 1
    assert data["snapshots"][0]["id"] == "gts-000001"
    assert data["snapshots"][0]["snapshot_path"] == expected_path_marker


def test_client_load_cgs_uses_home_variable_in_gts_and_lgr(monkeypatch, tmp_path):
    fake_home = (tmp_path / "home" / "user").resolve()
    workspace = fake_home / "workspace" / "demo"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    config_path = _write_root_cgs(workspace)

    client = ComplexGitSyncClient()
    client.load(config_path)

    snapshot_path = workspace / ".cgitsync" / "state" / "project.gts"
    snapshot_data = tomllib.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot_data["project"]["root_absolute_path"] == "$HOME/workspace/demo"
    assert snapshot_data["project"]["source_cgs_path"] == "$HOME/workspace/demo/project.cgs"

    lgr_data = tomllib.loads((workspace / "demo.lgr").read_text(encoding="utf-8"))
    assert lgr_data["register"]["current_snapshot_path"] == "$HOME/workspace/demo/.cgitsync/state/project.gts"
    assert lgr_data["snapshots"][0]["snapshot_path"] == "$HOME/workspace/demo/.cgitsync/state/project.gts"


def test_client_snapshot_generation_deduplicates_identical_workspace_entries(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)
    expected_lgr = tmp_path / "demo.lgr"

    client = ComplexGitSyncClient()
    client.load(config_path)
    client.expand(config_path)

    data = tomllib.loads(expected_lgr.read_text(encoding="utf-8"))
    assert [entry["id"] for entry in data["snapshots"]] == ["gts-000001"]
    assert data["register"]["current_snapshot_id"] == "gts-000001"


def test_client_expand_cgs_writes_gts_snapshot(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    expected_snapshot = tmp_path / ".cgitsync" / "state" / "project.gts"

    client = ComplexGitSyncClient()
    client.expand(config_path)

    assert expected_snapshot.is_file()


def test_client_validate_cgs_writes_gts_snapshot(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    expected_snapshot = tmp_path / ".cgitsync" / "state" / "project.gts"

    client = ComplexGitSyncClient()
    client.validate(config_path)

    assert expected_snapshot.is_file()


def test_client_load_gts_snapshot_has_correct_command_origin(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)
    expected_snapshot = tmp_path / ".cgitsync" / "state" / "project.gts"

    ComplexGitSyncClient().load(config_path)

    data = tomllib.loads(expected_snapshot.read_text(encoding="utf-8"))
    assert data["document"]["command_origin"] == "load"
    assert data["document"]["schema_version"] == "1.1"
    assert data["document"]["hash_algorithm"] == "sha256"
    assert len(data["document"]["snapshot_hash"]) == 64


def test_client_expand_gts_snapshot_has_correct_command_origin(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)
    expected_snapshot = tmp_path / ".cgitsync" / "state" / "project.gts"

    ComplexGitSyncClient().expand(config_path)

    data = tomllib.loads(expected_snapshot.read_text(encoding="utf-8"))
    assert data["document"]["command_origin"] == "expand"
    assert data["document"]["schema_version"] == "1.1"
    assert data["document"]["hash_algorithm"] == "sha256"
    assert len(data["document"]["snapshot_hash"]) == 64


def test_client_validate_gts_snapshot_has_correct_command_origin(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)
    expected_snapshot = tmp_path / ".cgitsync" / "state" / "project.gts"

    ComplexGitSyncClient().validate(config_path)

    data = tomllib.loads(expected_snapshot.read_text(encoding="utf-8"))
    assert data["document"]["command_origin"] == "validate"
    assert data["document"]["schema_version"] == "1.1"
    assert data["document"]["hash_algorithm"] == "sha256"
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


def test_client_write_gts_snapshot_records_ledger_event(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)
    expected_lgr = tmp_path / "demo.lgr"

    client = ComplexGitSyncClient()
    client.load(config_path)

    data = tomllib.loads(expected_lgr.read_text(encoding="utf-8"))
    assert "ledger" in data
    assert len(data["ledger"]) == 1
    event = data["ledger"][0]
    assert event["sync_id"] == "lgr-000001"
    assert event["operation"] == "load"
    assert event["gts_snapshot_id"] == "gts-000001"
    assert len(event["workspace_hash"]) == 64
    assert "demo" in event["affected_repos"]
    assert event["parent_sync_ids"] == []


def test_client_multiple_operations_create_linked_ledger_events(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)
    expected_lgr = tmp_path / "demo.lgr"

    client = ComplexGitSyncClient()
    client.load(config_path)
    client.expand(config_path)
    client.validate(config_path)

    data = tomllib.loads(expected_lgr.read_text(encoding="utf-8"))
    events = data["ledger"]
    assert len(events) == 3
    sync_ids = [e["sync_id"] for e in events]
    assert sync_ids == ["lgr-000001", "lgr-000002", "lgr-000003"]
    # Each event (except the first) must link to its predecessor
    assert events[0]["parent_sync_ids"] == []
    assert events[1]["parent_sync_ids"] == ["lgr-000001"]
    assert events[2]["parent_sync_ids"] == ["lgr-000002"]


def test_client_get_ledger_history_via_public_api(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    expected_lgr = tmp_path / "demo.lgr"

    client = ComplexGitSyncClient()
    client.load(config_path)
    client.expand(config_path)

    history = client.get_ledger_history(expected_lgr)
    assert len(history) == 2
    assert history[0]["operation"] == "load"
    assert history[1]["operation"] == "expand"


def test_client_replay_ledger_reconstructs_history(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    expected_lgr = tmp_path / "demo.lgr"

    client = ComplexGitSyncClient()
    client.load(config_path)
    client.expand(config_path)

    replay = client.replay_ledger(expected_lgr)
    history = client.get_ledger_history(expected_lgr)
    assert replay == history


def test_sync_ledger_workspace_hash_matches_gts_snapshot_hash(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)
    expected_lgr = tmp_path / "demo.lgr"
    expected_snapshot = tmp_path / ".cgitsync" / "state" / "project.gts"

    client = ComplexGitSyncClient()
    client.load(config_path)

    lgr_data = tomllib.loads(expected_lgr.read_text(encoding="utf-8"))
    gts_data = tomllib.loads(expected_snapshot.read_text(encoding="utf-8"))

    ledger_hash = lgr_data["ledger"][0]["workspace_hash"]
    snapshot_hash = gts_data["document"]["snapshot_hash"]
    assert ledger_hash == snapshot_hash


def _write_root_cgs(tmp_path, *, nested_child: bool = False):
    nested_config = 'nested_config = "auto"\n' if nested_child else ""
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        (
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
"""
            + nested_config
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _write_goc_plan(tmp_path: Path, *, source: str, actions: str) -> Path:
    plan_path = tmp_path / "plan.goc"
    plan_path.write_text(
        (
            f"""
[document]
format_version = "1.0"

[project]
source = "{source}"
""".strip()
            + "\n\n"
            + actions.strip()
            + "\n"
        ),
        encoding="utf-8",
    )
    return plan_path


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


def _write_ready_gts(snapshot_path: Path, *, root_path: Path) -> Path:
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
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
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return snapshot_path


class _FakeGitRunner:
    def __init__(self, remote_branches: dict[str, set[str]]):
        self.remote_branches = remote_branches
        self.clones: list[tuple[str, Path, str]] = []
        self.submodule_adds: list[tuple[Path, str, Path, str]] = []
        self._submodule_paths: set[tuple[Path, Path]] = set()

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

    def add_submodule(
        self,
        repo_path: Path | str,
        remote_url: str,
        relative_path: Path | str,
        *,
        branch: str,
    ) -> None:
        parent_path = Path(repo_path).resolve()
        rel_path = Path(relative_path)
        self.submodule_adds.append((parent_path, remote_url, rel_path, branch))
        self._submodule_paths.add((parent_path, rel_path))
        self.clone(remote_url, parent_path / rel_path, branch=branch)

    def is_submodule(self, repo_path: Path | str, relative_path: Path | str) -> bool:
        return (Path(repo_path).resolve(), Path(relative_path)) in self._submodule_paths

    def rev_parse_head(self, repo_path: Path | str) -> str:
        repo_name = Path(repo_path).name
        return f"sha-{repo_name}"

    def current_branch(self, repo_path: Path | str) -> str | None:
        resolved = Path(repo_path).resolve()
        for _, destination, branch in self.clones:
            if destination == resolved:
                return branch
        return None


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
    """Create a minimal RepoRegistryEntry for circularity testing."""
    from ComplexGitSync.git_repo import (
        NodeType,
        RepoLifecycleState,
        RepoRegistryEntry,
        SyncState,
        DiscoveryState,
    )

    separator_count = repo_id.count(":")
    node_type = NodeType.ROOT if repo_id == "root" else (NodeType.PARENT if separator_count == 1 else NodeType.LEAF)
    return RepoRegistryEntry(
        repo_id=repo_id,
        name=repo_id.split(":")[-1],
        node_type=node_type,
        parent_id=parent_id,
        absolute_path=abs_path,
        relative_path=Path(".") if repo_id == "root" else Path(abs_path.name),
    )


def test_fix_circularities_removes_duplicate_leaf_when_parent_exists(tmp_path):
    """A leaf entry whose absolute_path matches an existing parent is removed."""
    from ComplexGitSync.git_tree import DependencyTreeRegistry, fix_circularities

    parent2_path = tmp_path / "parent2"
    registry = DependencyTreeRegistry()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:parent1", tmp_path / "parent1", parent_id="root"))
    registry.add(_make_entry("root:parent2", parent2_path, parent_id="root"))
    # Duplicate: parent2 also appears as a leaf of parent1
    registry.add(_make_entry("root:parent1:parent2", parent2_path, parent_id="root:parent1"))

    fixed = fix_circularities(registry)

    assert len(fixed) == 1
    assert "fixed_circularity:root:parent1:parent2→root:parent2" in fixed
    assert "root:parent1:parent2" not in registry.entries
    assert "root:parent2" in registry.entries


def test_fix_circularities_no_changes_when_no_duplicates(tmp_path):
    """Returns empty tuple when there are no duplicate absolute paths."""
    from ComplexGitSync.git_tree import DependencyTreeRegistry, fix_circularities

    registry = DependencyTreeRegistry()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:parent1", tmp_path / "parent1", parent_id="root"))
    registry.add(_make_entry("root:parent2", tmp_path / "parent2", parent_id="root"))

    fixed = fix_circularities(registry)

    assert fixed == ()
    assert len(registry.entries) == 3


def test_fix_circularities_handles_cascading_duplicates(tmp_path):
    """Both a leaf and its child duplicate are removed when all share the same paths."""
    from ComplexGitSync.git_tree import DependencyTreeRegistry, fix_circularities

    parent2_path = tmp_path / "parent2"
    leaf_path = parent2_path / "leaf"
    registry = DependencyTreeRegistry()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:parent1", tmp_path / "parent1", parent_id="root"))
    registry.add(_make_entry("root:parent2", parent2_path, parent_id="root"))
    registry.add(_make_entry("root:parent2:leaf", leaf_path, parent_id="root:parent2"))
    # Duplicates from parent1's nested discovery
    registry.add(_make_entry("root:parent1:parent2", parent2_path, parent_id="root:parent1"))
    registry.add(_make_entry("root:parent1:parent2:leaf", leaf_path, parent_id="root:parent1:parent2"))

    fixed = fix_circularities(registry)

    assert len(fixed) == 2
    assert "root:parent1:parent2" not in registry.entries
    assert "root:parent1:parent2:leaf" not in registry.entries
    assert "root:parent2" in registry.entries
    assert "root:parent2:leaf" in registry.entries


def test_fix_circularities_multiple_parents_sharing_leaf(tmp_path):
    """Multiple parents referencing the same leaf: only one canonical entry survives."""
    from ComplexGitSync.git_tree import DependencyTreeRegistry, fix_circularities

    shared_path = tmp_path / "shared"
    registry = DependencyTreeRegistry()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:shared", shared_path, parent_id="root"))
    registry.add(_make_entry("root:parent1", tmp_path / "parent1", parent_id="root"))
    registry.add(_make_entry("root:parent2", tmp_path / "parent2", parent_id="root"))
    registry.add(_make_entry("root:parent1:shared", shared_path, parent_id="root:parent1"))
    registry.add(_make_entry("root:parent2:shared", shared_path, parent_id="root:parent2"))

    fixed = fix_circularities(registry)

    assert len(fixed) == 2
    assert "root:shared" in registry.entries
    assert "root:parent1:shared" not in registry.entries
    assert "root:parent2:shared" not in registry.entries


def test_fix_circularities_keeps_duplicates_when_commit_or_status_conflict(tmp_path):
    from ComplexGitSync.git_repo import RepoLifecycleState, SyncState
    from ComplexGitSync.git_tree import DependencyTreeRegistry, fix_circularities

    shared_path = tmp_path / "shared"
    registry = DependencyTreeRegistry()
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
    assert "root:shared" in registry.entries
    assert "root:parent1:shared" in registry.entries


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
    from pathlib import Path as P
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
    from ComplexGitSync.git_tree import DependencyTreeRegistry, topological_sort

    registry = DependencyTreeRegistry()
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
    from ComplexGitSync.git_tree import DependencyTreeRegistry, topological_sort

    registry = DependencyTreeRegistry()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:a", tmp_path / "a", parent_id="root"))
    registry.add(_make_entry("root:b", tmp_path / "b", parent_id="root"))
    registry.add(_make_entry("root:a:leaf", tmp_path / "a" / "leaf", parent_id="root:a"))

    order = topological_sort(registry)
    assert len(order) == 4
    assert {e.repo_id for e in order} == {"root", "root:a", "root:b", "root:a:leaf"}


def test_topological_sort_parent_before_all_children(tmp_path):
    """Root always comes before all other entries."""
    from ComplexGitSync.git_tree import DependencyTreeRegistry, topological_sort

    registry = DependencyTreeRegistry()
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
    from ComplexGitSync.git_tree import DependencyTreeRegistry, fix_circularities

    # Registry: root -> a -> b -> a (a's path reappears as a child of b)
    path_a = tmp_path / "a"
    path_b = tmp_path / "a" / "b"

    registry = DependencyTreeRegistry()
    registry.add(_make_entry("root", tmp_path))
    registry.add(_make_entry("root:a", path_a, parent_id="root"))
    registry.add(_make_entry("root:a:b", path_b, parent_id="root:a"))
    # Cycle-creating back-edge: b's .cgs references a again
    registry.add(_make_entry("root:a:b:a", path_a, parent_id="root:a:b"))

    fixed = fix_circularities(registry)

    assert len(fixed) == 1
    assert "fixed_circularity:root:a:b:a\u2192root:a" in fixed
    assert "root:a:b:a" not in registry.entries
    # Legitimate entries are preserved
    assert "root:a" in registry.entries
    assert "root:a:b" in registry.entries


def test_fix_circularities_back_edge_marked_is_external_reference(tmp_path):
    """The back-edge entry has is_external_reference=True set before removal."""
    from ComplexGitSync.git_tree import DependencyTreeRegistry, fix_circularities

    path_a = tmp_path / "a"
    path_b = tmp_path / "a" / "b"

    registry = DependencyTreeRegistry()
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
    from ComplexGitSync.git_tree import DependencyTreeRegistry, fix_circularities

    # Build: root -> a -> c -> a (cycle); root -> b -> c -> b (cycle)
    # path_a and path_b are both at depth 1 (1 colon in repo_id).
    # path_a has 2 external in-edges (from root via root:a AND root:c:a).
    # Actually, let's set up a simpler heuristic-1 test:
    # root -> a, root -> b, a -> c, b -> c, c -> a  (so a has external in from root)
    path_a = tmp_path / "a"
    path_b = tmp_path / "b"
    path_c = tmp_path / "c"

    registry = DependencyTreeRegistry()
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
    assert "root:a" in registry.entries
    assert "root:b:c:a" not in registry.entries
    assert any("root:b:c:a" in change for change in fixed)


def test_fix_circularities_no_duplicate_changes_for_same_entry(tmp_path):
    """Each removed entry appears exactly once in the returned changes tuple."""
    from ComplexGitSync.git_tree import DependencyTreeRegistry, fix_circularities

    path_a = tmp_path / "a"
    path_b = tmp_path / "a" / "b"

    registry = DependencyTreeRegistry()
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
    from ComplexGitSync.git_tree import DependencyTreeRegistry, fix_circularities

    path_a = tmp_path / "a"
    path_b = tmp_path / "a" / "b"
    path_shared = tmp_path / "shared"

    registry = DependencyTreeRegistry()
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
    assert "root:a:b:a" not in registry.entries
    # Phase 2 removes root:a:shared (duplicate path)
    assert "root:a:shared" not in registry.entries
    assert "root:shared" in registry.entries
    assert len(fixed) == 2


def test_fix_circularities_pending_clone_skips_external_reference(tmp_path):
    """_pending_clone_entries excludes entries with is_external_reference=True."""
    from ComplexGitSync.git_repo import RepoLifecycleState

    # Manually set is_external_reference on a DECLARED entry and verify it
    # is filtered out by _pending_clone_entries.
    registry = DependencyTreeRegistry()
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

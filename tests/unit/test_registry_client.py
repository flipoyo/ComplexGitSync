from __future__ import annotations

from pathlib import Path, PureWindowsPath

import pytest

from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.errors import ConfigValidationError, NestedConfigDiscoveryError
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


def test_client_initialise_dispatches_to_clone_cgs_for_cgs_source(monkeypatch):
    client = ComplexGitSyncClient()
    captured: dict[str, object] = {}

    def _fake_clone_cgs(path, *, target_dir=None):
        captured["path"] = path
        captured["target_dir"] = target_dir
        return "ok"

    monkeypatch.setattr(client, "clone_cgs", _fake_clone_cgs)

    result = client.initialise("project.cgs", target_dir="workspace/demo")

    assert result == "ok"
    assert captured["path"] == Path("project.cgs").resolve()
    assert captured["target_dir"] == "workspace/demo"


def test_client_validate_alias_returns_tree_state(tmp_path):
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

    def _fake_clone_cgs(path, *, target_dir=None):
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

    snapshot_path = state_store.latest_snapshot_for(config_path)
    assert snapshot_path is not None
    assert snapshot_path == (tmp_path / "workspace" / "demo" / ".cgitsync" / "state" / "project.gts").resolve()

    reloaded_client = ComplexGitSyncClient(state_store=state_store)
    reloaded_registry = reloaded_client.load_runtime_or_cgs(config_path)
    assert reloaded_client.get_tree_state().lifecycle_state == TreeLifecycleState.READY
    assert reloaded_registry.get("root").absolute_path == root_entry.absolute_path


def test_clone_cgs_uses_suffixed_target_when_default_root_is_occupied(tmp_path, monkeypatch):
    config_path = _write_clone_ready_cgs(tmp_path)
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "marker.txt").write_text("occupied\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    client = ComplexGitSyncClient(
        git_runner=_FakeGitRunner(
            {
                "git@github.com:owner/demo.git": {"main"},
                "git@github.com:owner/child-repo.git": {"autoTest"},
                "git@github.com:owner/docs.git": {"main"},
            }
        )
    )

    registry = client.clone_cgs(config_path)

    assert registry.get("root").absolute_path == (tmp_path / "demo-1").resolve()


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


def test_client_expand_gts_snapshot_has_correct_command_origin(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)
    expected_snapshot = tmp_path / ".cgitsync" / "state" / "project.gts"

    ComplexGitSyncClient().expand(config_path)

    data = tomllib.loads(expected_snapshot.read_text(encoding="utf-8"))
    assert data["document"]["command_origin"] == "expand"


def test_client_validate_gts_snapshot_has_correct_command_origin(tmp_path):
    import tomllib

    config_path = _write_root_cgs(tmp_path)
    expected_snapshot = tmp_path / ".cgitsync" / "state" / "project.gts"

    ComplexGitSyncClient().validate(config_path)

    data = tomllib.loads(expected_snapshot.read_text(encoding="utf-8"))
    assert data["document"]["command_origin"] == "validate"


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


def test_fix_circularities_raises_on_duplicate_with_incompatible_hashes(tmp_path, monkeypatch):
    from ComplexGitSync.git_tree import DependencyTreeRegistry, fix_circularities

    shared_path = tmp_path / "shared"
    registry = DependencyTreeRegistry()
    root = _make_entry("root", tmp_path)
    canonical = _make_entry("root:shared", shared_path, parent_id="root")
    duplicate = _make_entry("root:parent1:shared", shared_path, parent_id="root:parent1")
    canonical.target_ref_kind = RefKind.BRANCH
    canonical.target_ref_name = "main"
    duplicate.target_ref_kind = RefKind.TAG
    duplicate.target_ref_name = "v1.0.0"
    registry.add(root)
    registry.add(_make_entry("root:parent1", tmp_path / "parent1", parent_id="root"))
    registry.add(canonical)
    registry.add(duplicate)
    monkeypatch.setattr(
        GitRepo,
        "_get_hash",
        lambda self, branch="main", tag=None: "branch-hash" if tag is None else "tag-hash",
    )

    with pytest.raises(ConfigValidationError, match="incompatibilities between branch \\(hash\\) and tag\\(val\\) in \\.cgs"):
        fix_circularities(registry)


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

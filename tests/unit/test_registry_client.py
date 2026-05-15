from __future__ import annotations

import json
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


def test_registry_and_tree_rendering_are_serialized_for_review(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    client = ComplexGitSyncClient()
    client.load_cgs(config_path)

    rendered_tree = client.format_project_tree()
    rendered_registry = json.loads(client.format_registry_json())

    assert "- demo [root]" in rendered_tree
    assert any(entry["repo_id"] == "root:deps/child-repo" for entry in rendered_registry)


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

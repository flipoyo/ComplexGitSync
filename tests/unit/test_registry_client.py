from __future__ import annotations

import json
from pathlib import PureWindowsPath

import pytest

from ComplexGitSync.client import ComplexGitSyncClient
from ComplexGitSync.errors import ConfigValidationError, NestedConfigDiscoveryError
from ComplexGitSync.registry import NodeType, TreeLifecycleState, make_repo_id


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


def test_make_repo_id_normalizes_windows_style_paths():
    assert make_repo_id("root", PureWindowsPath("deps", "child-repo"), "child-repo") == (
        "root:deps/child-repo"
    )
    assert make_repo_id("root:deps/child-repo", PureWindowsPath("docs"), "docs") == (
        "root:deps/child-repo:docs"
    )


def test_make_repo_id_falls_back_to_name_when_relative_path_is_missing():
    assert make_repo_id("root", None, "child-repo") == "root:child-repo"


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

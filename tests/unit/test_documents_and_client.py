from __future__ import annotations

import subprocess

from ComplexGitSync.client import ComplexGitSyncClient
from ComplexGitSync.documents import read_cgs, read_gts
from ComplexGitSync.errors import ConfigValidationError, GitSyncError
from ComplexGitSync.models import NodeType, TreeLifecycleState


def test_read_cgs_parses_minimal_project(tmp_path):
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
name = "child"
path = "child"
ssh_url = "git@example.com:org/child.git"
https_url = "https://example.com/org/child.git"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    architecture = read_cgs(config_path)

    assert architecture.name == "demo"
    assert architecture.default_branch == "main"
    assert architecture.root_path == tmp_path.resolve()
    assert len(architecture.repos) == 1
    assert architecture.repos[0].name == "child"


def test_read_cgs_rejects_duplicate_repo_paths(tmp_path):
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
name = "child-a"
path = "child"
ssh_url = "git@example.com:org/child-a.git"
https_url = "https://example.com/org/child-a.git"

[[repos]]
name = "child-b"
path = "child"
ssh_url = "git@example.com:org/child-b.git"
https_url = "https://example.com/org/child-b.git"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    try:
        read_cgs(config_path)
    except ConfigValidationError as exc:
        assert "Duplicate repo path" in str(exc)
    else:
        raise AssertionError("Expected duplicate repo path validation to fail")


def test_client_validate_stays_declared_without_materialized_repos(tmp_path):
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
name = "child"
path = "child"
ssh_url = "git@example.com:org/child.git"
https_url = "https://example.com/org/child.git"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    client = ComplexGitSyncClient()
    state = client.validate_architecture(config_path)

    assert state.lifecycle_state == TreeLifecycleState.DECLARED
    assert state.is_ready is False
    assert client.session.registry.get("root").node_type == NodeType.ROOT


def test_client_writes_and_reads_ready_gts_snapshot(tmp_path):
    _init_git_repo(tmp_path)
    child_path = tmp_path / "child"
    _init_git_repo(child_path)

    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
name = "child"
path = "child"
ssh_url = "git@example.com:org/child.git"
https_url = "https://example.com/org/child.git"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    client = ComplexGitSyncClient()
    client.load_architecture(config_path)
    refreshed = client.refresh_registry(refresh_nested=False)
    assert refreshed.lifecycle_state == TreeLifecycleState.READY

    gts_path = client.write_git_tree_state(command_origin="test")
    snapshot = read_gts(gts_path)

    assert gts_path.exists()
    assert snapshot.project_name == "demo"
    assert snapshot.registry.lifecycle_state == TreeLifecycleState.READY
    assert snapshot.registry.get("root").commit_sha


def test_write_git_tree_state_requires_ready_tree(tmp_path):
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    client = ComplexGitSyncClient()
    client.load_architecture(config_path)

    try:
        client.write_git_tree_state()
    except GitSyncError as exc:
        assert "READY tree" in str(exc)
    else:
        raise AssertionError("Expected write_git_tree_state to reject a non-READY tree")


def _init_git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-b", "main"], path)
    _run(["git", "config", "user.name", "Test User"], path)
    _run(["git", "config", "user.email", "test@example.com"], path)
    (path / "README.txt").write_text("content\n", encoding="utf-8")
    _run(["git", "add", "README.txt"], path)
    _run(["git", "commit", "-m", "init"], path)


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)

from __future__ import annotations

from pathlib import Path

import pytest

from ComplexGitSync import (
    ArchitectureNotLoadedError,
    ComplexGitSyncClient,
    ComplexGitSyncError,
    ConfigValidationError,
    FallbackRejectedError,
    GitRepo,
    GitSyncError,
    GitTree,
    MemoryBinding,
    MemoryMemorizeResult,
    MemoryRememberResult,
    MemoryReloadResult,
    MemoryRetrieveResult,
    NestedConfigDiscoveryError,
    RepoNode,
    SyncLedger,
    TreeNotReadyError,
    add_tree,
)
from ComplexGitSync.git_repo import NodeType, WorkingRepo
from ComplexGitSync.git_tree import WorkingGitTree
from ComplexGitSync.cgs import CgsDocument
from ComplexGitSync.orchestre import GitRunner


def test_package_root_exports_refactor_guard_symbols():
    assert issubclass(ArchitectureNotLoadedError, ComplexGitSyncError)
    assert issubclass(ConfigValidationError, ComplexGitSyncError)
    assert issubclass(FallbackRejectedError, ComplexGitSyncError)
    assert issubclass(GitSyncError, ComplexGitSyncError)
    assert issubclass(NestedConfigDiscoveryError, ComplexGitSyncError)
    assert issubclass(TreeNotReadyError, ComplexGitSyncError)
    assert GitRepo.__name__ == "GitRepo"
    assert GitTree.__name__ == "GitTree"
    assert RepoNode.__name__ == "RepoNode"
    assert ComplexGitSyncClient.__name__ == "ComplexGitSyncClient"
    assert MemoryBinding.__name__ == "MemoryBinding"
    assert MemoryMemorizeResult.__name__ == "MemoryMemorizeResult"
    assert MemoryRememberResult.__name__ == "MemoryRememberResult"
    assert MemoryReloadResult.__name__ == "MemoryReloadResult"
    assert MemoryRetrieveResult.__name__ == "MemoryRetrieveResult"
    assert SyncLedger.__name__ == "SyncLedger"


def test_public_config_validation_error_covers_invalid_cgs_documents():
    with pytest.raises(ConfigValidationError, match="default_branch"):
        CgsDocument.from_dict(
            {
                "document": {"format_version": "1.0"},
                "project": {"name": "demo"},
                "repos": [],
            }
        )


def test_public_nested_config_discovery_error_covers_ambiguous_nested_configs(tmp_path: Path):
    root_cgs = _write_root_cgs(tmp_path)
    child_repo_root = tmp_path / "deps" / "child-repo"
    child_repo_root.mkdir(parents=True)
    (child_repo_root / "one.cgs").write_text(_nested_minimal("child-repo"), encoding="utf-8")
    (child_repo_root / "two.cgs").write_text(_nested_minimal("child-repo"), encoding="utf-8")

    client = ComplexGitSyncClient()
    client.load_cgs(root_cgs)

    with pytest.raises(NestedConfigDiscoveryError, match="Ambiguous nested \\.cgs discovery"):
        client.discover_nested_configs()


def test_public_tree_not_ready_error_covers_ready_gated_mutations(tmp_path: Path):
    registry = WorkingGitTree()
    registry.add(
        WorkingRepo(
            repo_id="root",
            name="demo",
            node_type=NodeType.ROOT,
            parent_id=None,
            absolute_path=tmp_path / "demo",
            project_owner_name="owner",
            project_name="demo",
        )
    )
    registry.recompute_tree_state()

    with pytest.raises(TreeNotReadyError, match="READY tree"):
        add_tree(registry, object())  # type: ignore[arg-type]


def test_public_git_sync_error_covers_occupied_clone_destination(tmp_path: Path):
    config_path = _write_clone_ready_cgs(tmp_path)
    occupied_root = tmp_path / "workspace" / "demo"
    occupied_root.mkdir(parents=True)
    (occupied_root / "marker.txt").write_text("occupied\n", encoding="utf-8")

    client = ComplexGitSyncClient(git_runner=_BranchAvailableGitRunner())

    with pytest.raises(GitSyncError, match="already exists and is not empty"):
        client.clone_cgs(config_path, target_dir=occupied_root)


class _BranchAvailableGitRunner(GitRunner):
    def remote_branch_exists(self, remote_url: str, branch: str) -> bool:
        return True


def _write_root_cgs(tmp_path: Path) -> Path:
    path = tmp_path / "project.cgs"
    path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-repo"
relative_path = "deps/child-repo"
nested_config = "auto"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_clone_ready_cgs(tmp_path: Path) -> Path:
    path = tmp_path / "clone.cgs"
    path.write_text(
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
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _nested_minimal(project_name: str) -> str:
    return (
        f"""
[document]
format_version = "1.0"

[project]
name = "{project_name}"
default_branch = "main"
""".strip()
        + "\n"
    )

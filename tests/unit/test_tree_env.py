"""TreeEnvironment records machine facts without secrets or manifest copies."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ComplexGitSync.cgs_format import CgsDocument
from ComplexGitSync.errors import ConfigValidationError
from ComplexGitSync.git_repo import GitProvider
from ComplexGitSync.git_runner import ToolRun
from ComplexGitSync.memory.environment import read_environment, write_environment
from ComplexGitSync.toolchain import reset_cache as reset_toolchain_cache
from ComplexGitSync.tree_env import (
    CredentialFact,
    Manifest,
    Requirements,
    ToolVersion,
    TreeEnvironment,
    compare,
    observe,
    reset_cache,
)


class FakeRunner:
    """Count every external question while returning deterministic answers."""

    def __init__(self) -> None:
        self.version_calls: list[str] = []
        self.run_calls: list[tuple[str, ...]] = []

    def tool_version(self, executable: str) -> str | None:
        self.version_calls.append(executable)
        return {
            "git": "git version 2.43.0",
            "pixi": "pixi 0.66.0",
            "gh": "gh version 2.80.0",
            "cc": "cc 13.2.0",
        }.get(executable)

    def run_tool(self, executable: str, *args: str) -> ToolRun:
        self.run_calls.append((executable, *args))
        if executable == "ssh":
            return ToolRun(True, 0, stderr="OpenSSH_9.6p1")
        if executable == "gh":
            return ToolRun(True, 0, stdout="authenticated")
        return ToolRun(False)


class FakeTree:
    def __init__(self, root: Path) -> None:
        self._repos = [
            SimpleNamespace(
                absolute_path=root,
                parent_id=None,
                repo_id="root",
                project_name="demo",
                gitprovider=GitProvider.GITHUB,
            )
        ]
        self.format_metadata: dict = {}

    def values(self):
        return list(self._repos)


@pytest.fixture(autouse=True)
def _clear_observation_caches():
    reset_cache()
    reset_toolchain_cache()
    yield
    reset_cache()
    reset_toolchain_cache()


def _record(*, pixi: str = "0.66.0") -> TreeEnvironment:
    return TreeEnvironment(
        architecture="x86_64",
        pixi_platform="linux-64",
        os_name="linux",
        os_version="24.04",
        libc_name="glibc",
        libc_version="2.39",
        kernel_release="6.8.0",
        tools=(
            ToolVersion("git", "git version 2.43.0", "2.43.0"),
            ToolVersion("pixi", f"pixi {pixi}", pixi),
            ToolVersion("python", "3.11.9", "3.11.9"),
        ),
        credentials=(CredentialFact("github", "gh", True, True),),
        manifests=(Manifest("root", "pixi.toml", "sha256:" + "a" * 64),),
    )


def test_observe_records_required_facts_manifests_and_boolean_auth(tmp_path):
    (tmp_path / "pixi.toml").write_text("[workspace]\n", encoding="utf-8")
    (tmp_path / "pixi.lock").write_text(
        "environments:\n  default:\n    packages:\n      linux-64:\n      osx-arm64:\n",
        encoding="utf-8",
    )
    runner = FakeRunner()
    tree = FakeTree(tmp_path)
    tree.format_metadata["cgs_format"] = {
        "top_level": {
            "environment_root": "demo",
            "environment": {"compilers": ["cc"], "manifests": []},
        }
    }

    first = observe(runner, tree)
    second = observe(runner, tree)

    assert first is second
    assert all((first.architecture, first.pixi_platform, first.os_name, first.kernel_release))
    assert first.environment_root == "demo"
    tools = {tool.name: tool for tool in first.tools}
    assert {"python", "git", "pixi", "gh", "ssh", "cc"} <= set(tools)
    assert all(tool.raw and tool.version for tool in first.tools)
    assert first.credentials == (CredentialFact("github", "gh", True, True),)
    assert {manifest.path for manifest in first.manifests} == {"pixi.lock", "pixi.toml"}
    lock = next(manifest for manifest in first.manifests if manifest.path == "pixi.lock")
    assert lock.platforms == ("linux-64", "osx-arm64")
    assert runner.version_calls.count("git") == 1
    assert runner.run_calls.count(("gh", "auth", "status")) == 1


def test_digest_is_stable_and_changes_with_the_observed_pixi_version():
    assert _record().digest() == _record().digest()
    assert _record().digest() != _record(pixi="0.67.0").digest()


def test_environment_store_round_trips_without_copying_manifest_contents(tmp_path):
    record = _record()
    path = write_environment(tmp_path / ".cgitsync", record)

    assert path == tmp_path / ".cgitsync" / "env" / f"{record.digest()}.toml"
    assert write_environment(tmp_path / ".cgitsync", record) == path
    assert read_environment(path) == record
    text = path.read_text(encoding="utf-8")
    assert "pixi.toml" in text
    assert "[workspace]" not in text
    assert "token" not in text.lower()


def test_requirements_compare_missing_older_and_undeclared_tools():
    required = Requirements.from_cgs(
        {
            "environment_root": "root",
            "environment": {"tools": {"git": "2.44", "pixi": "0.60", "gh": "2.0"}},
        }
    )
    drift = compare(_record(), required)

    assert drift.missing == ("tool:gh",)
    assert drift.older == ("tool:git 2.43.0 < 2.44",)
    assert "python" in drift.undeclared
    assert not drift.matches


def test_cgs_environment_declaration_is_preserved_and_validated():
    document = CgsDocument.from_dict(
        {
            "project": "demo",
            "repos": ["github:owner/demo"],
            "environment_root": "demo",
            "environment": {
                "tools": {"git": "2.43"},
                "compilers": ["cc"],
                "manifests": ["Cargo.lock"],
            },
        }
    )
    rebuilt = CgsDocument.from_git_tree(document.to_git_tree())

    assert rebuilt.environment_root == "demo"
    assert rebuilt.environment["tools"] == {"git": "2.43"}
    assert Requirements.from_cgs(rebuilt).manifest_patterns == ("Cargo.lock",)

    with pytest.raises(ConfigValidationError, match="names no configured repository"):
        CgsDocument.from_dict(
            {
                "project": "demo",
                "repos": ["github:owner/demo"],
                "environment_root": "missing",
            }
        )


def test_record_contains_no_identity_bearing_paths_or_credential_values():
    record = replace(
        _record(),
        credentials=(CredentialFact("github", "gh", True, True),),
    )
    rendered = repr(record.to_dict())
    assert "/home/alice" not in rendered
    assert "secret-token" not in rendered
    assert "id_ed25519" not in rendered

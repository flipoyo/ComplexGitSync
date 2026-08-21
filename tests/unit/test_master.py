from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from ComplexGitSync import MasterConfig
from ComplexGitSync.orchestre import GitRunner


@pytest.fixture(autouse=True)
def _reset_master_config(monkeypatch):
    monkeypatch.setattr(MasterConfig, "_override_name", None)
    monkeypatch.setattr(MasterConfig, "_override_email", None)


def test_master_config_resolve_identity_returns_none_when_no_overrides_are_set(tmp_path):
    identity = MasterConfig.resolve_identity(tmp_path / "repo", GitRunner())

    assert identity == (None, None)


def test_master_config_configure_updates_only_given_fields(tmp_path):
    MasterConfig.configure(user_email="bot@example.com")
    MasterConfig.configure(user_name="cgitsync-bot")

    identity = MasterConfig.resolve_identity(tmp_path / "repo", GitRunner())

    assert identity == ("cgitsync-bot", "bot@example.com")


def test_master_config_persist_updates_only_given_fields_and_load_restores_them(tmp_path):
    cgshome = tmp_path / "workspace"

    MasterConfig.persist(cgshome, user_email="bot@example.com")
    config_path = cgshome / ".cgitsync" / "master.toml"
    assert tomllib.loads(config_path.read_text(encoding="utf-8")) == {
        "master": {"user_email": "bot@example.com"}
    }

    MasterConfig.persist(cgshome, user_name="cgitsync-bot")
    assert tomllib.loads(config_path.read_text(encoding="utf-8")) == {
        "master": {
            "user_name": "cgitsync-bot",
            "user_email": "bot@example.com",
        }
    }

    MasterConfig._override_name = None
    MasterConfig._override_email = None
    MasterConfig.load(cgshome)

    assert MasterConfig.resolve_identity(tmp_path / "repo", GitRunner()) == (
        "cgitsync-bot",
        "bot@example.com",
    )


def test_git_runner_commit_only_passes_configured_identity_overrides(monkeypatch):
    runner = GitRunner()
    captured: dict[str, object] = {}

    def _spy_run(_self, *args: str, cwd: Path | str | None = None):
        captured["args"] = args
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(GitRunner, "_run", _spy_run)

    runner.commit("/tmp/repo", "sync .gitignore", user_name="cgitsync-bot")

    assert captured["args"] == (
        "-c",
        "user.name=cgitsync-bot",
        "commit",
        "-m",
        "sync .gitignore",
    )
    assert captured["cwd"] == "/tmp/repo"

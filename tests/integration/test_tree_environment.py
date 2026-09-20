"""Environment records travel beside States without changing State identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from ComplexGitSync import tree_env
from ComplexGitSync.memory.environment import write_environment
from ComplexGitSync.memory.ledger_store import read_all_entries
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.tree_env import ToolVersion, TreeEnvironment

_CGS = 'project = "demo"\nenvironment_root = "demo"\nrepos = ["github:owner/demo"]\n'


def _workspace(path: Path) -> Path:
    path.mkdir(parents=True)
    config = path / "project.cgs"
    config.write_text(_CGS, encoding="utf-8")
    return config


def _record(machine: str) -> TreeEnvironment:
    return TreeEnvironment(
        architecture=machine,
        pixi_platform="linux-64",
        os_name="linux",
        os_version="24.04",
        libc_name="glibc",
        libc_version="2.39",
        kernel_release="6.8.0",
        tools=(ToolVersion("python", "3.11.9", "3.11.9"),),
    )


def test_ordinary_operations_reuse_one_environment_record(tmp_path):
    config = _workspace(tmp_path / "demo")
    client = ComplexGitSyncClient()
    client.load(config)
    client.load(config)

    cgitsync = config.parent / ".cgitsync"
    records = list((cgitsync / "env").glob("*.toml"))
    entries = read_all_entries(cgitsync / "lgr")
    assert len(records) == 1
    assert len({entry.environment for entry in entries}) == 1
    assert entries[0].environment == f"env({records[0].stem})"


def test_two_machine_records_leave_one_state_name(monkeypatch, tmp_path):
    first = _workspace(tmp_path / "machine-a" / "demo")
    second = _workspace(tmp_path / "machine-b" / "demo")

    def observed(_runner, registry, *, backends=False):
        root = registry.get("root").absolute_path
        return _record("machine-a" if "machine-a" in root.parts else "machine-b")

    monkeypatch.setattr(tree_env, "observe", observed)
    ComplexGitSyncClient().load(first)
    ComplexGitSyncClient().load(second)

    first_states = list((first.parent / ".cgitsync" / "state").glob("*.gts"))
    second_states = list((second.parent / ".cgitsync" / "state").glob("*.gts"))
    first_env = list((first.parent / ".cgitsync" / "env").glob("*.toml"))
    second_env = list((second.parent / ".cgitsync" / "env").glob("*.toml"))
    assert [path.name for path in first_states] == [path.name for path in second_states]
    assert first_env[0].name != second_env[0].name


def test_memory_show_resolves_the_environment_for_a_state(tmp_path):
    config = _workspace(tmp_path / "demo")
    client = ComplexGitSyncClient()
    client.load(config)
    [state] = (config.parent / ".cgitsync" / "state").glob("*.gts")

    answer = client.memory_show(config.parent, state.stem)

    assert len(answer["environments"]) == 1
    assert answer["environments"][0]["record"]["machine"]["architecture"]


def test_memory_fold_moves_environment_records_with_their_ledger_reference(tmp_path):
    pending = tmp_path / ".cgitsync"
    mount = pending / ".memory"
    mount.mkdir(parents=True)
    path = write_environment(pending, _record("x86_64"))

    moved = ComplexGitSyncClient()._fold_memory_pending(pending, mount)

    assert moved == 1
    assert not path.exists()
    assert (mount / "env" / path.name).is_file()


def test_gts_resolves_its_source_cgs_for_environment_checks(tmp_path):
    config = _workspace(tmp_path / "demo")
    first = ComplexGitSyncClient()
    first.load(config)
    [state] = (config.parent / ".cgitsync" / "state").glob("*.gts")

    restored = ComplexGitSyncClient()
    restored.load_gts(state)

    assert restored.check_environment().matches
    assert restored.environment().environment_root == "demo"


def test_declared_drift_warning_does_not_change_the_loaded_tree(tmp_path):
    config = tmp_path / "demo" / "project.cgs"
    config.parent.mkdir(parents=True)
    config.write_text(
        _CGS + '\n[environment]\ntools = ["definitely-not-installed"]\n',
        encoding="utf-8",
    )

    client = ComplexGitSyncClient()
    registry = client.load(config)
    with pytest.warns(UserWarning, match="environment drift: tool:definitely-not-installed"):
        client._warn_environment_drift()

    assert registry.get("root").project_name == "demo"

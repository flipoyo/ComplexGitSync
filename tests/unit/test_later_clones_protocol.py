"""A clone made after the first one takes the protocol it is told, not the ``.cgs`` default.

Backs TutorialsOverHttps (P1): a reader with no SSH key bootstraps over HTTPS,
then runs ``release load`` (in place or ``--workspace``) or ``pull <x.gts>``,
each of which may clone a repository the tree lacks. Without a forced
protocol those clones used the declared one, SSH, and failed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from ComplexGitSync.cli import release_command
from ComplexGitSync.git_repo import AccessProtocol
from ComplexGitSync.orchestre.release_commands import ReleaseCommands
from ComplexGitSync.orchestre.tree_commands import TreeCommands


class _Stop(Exception):
    """Ends a stubbed run at the clone it was meant to reach."""


def _release_commands(monkeypatch, tmp_path: Path, seen: dict) -> ReleaseCommands:
    def bootstrap(state_path, workspace, *, cgs_path=None, force_access_protocol=None):
        seen["bootstrap"] = force_access_protocol
        return "tree"

    client = SimpleNamespace(_forced_access_protocol=None, bootstrap=bootstrap)
    commands = ReleaseCommands(client)
    monkeypatch.setattr(commands, "project_name", lambda: "P")
    monkeypatch.setattr(commands, "list_releases", lambda: [{"tag": "P-1", "loadable": True}])
    monkeypatch.setattr(commands, "_state_file", lambda row: tmp_path / "state.gts")
    monkeypatch.setattr(commands, "_load_in_place", lambda tag, path: seen.update(in_place=client._forced_access_protocol))
    return commands


def test_release_load_into_a_workspace_bootstraps_over_the_forced_protocol(monkeypatch, tmp_path):
    seen: dict = {}
    _release_commands(monkeypatch, tmp_path, seen).load_release("1", workspace="r1", force_access_protocol="https")
    assert seen == {"bootstrap": "https"}


@pytest.mark.parametrize(("forced", "expected"), [("https", AccessProtocol.HTTPS), (None, None)])
def test_release_load_in_place_clones_over_the_forced_protocol(monkeypatch, tmp_path, forced, expected):
    seen: dict = {}
    _release_commands(monkeypatch, tmp_path, seen).load_release("1", force_access_protocol=forced)
    assert seen == {"in_place": expected}


def test_release_load_takes_force_protocol_on_the_command_line():
    parser = argparse.ArgumentParser()
    release_command.register(parser)
    assert parser.parse_args(["load", "1", "--force-protocol", "https"]).force_access_protocol == "https"
    assert parser.parse_args(["load", "1"]).force_access_protocol is None


def test_pull_of_a_gts_restores_missing_repositories_over_the_forced_protocol(tmp_path):
    source = tmp_path / "state.gts"
    source.write_text("", encoding="utf-8")
    missing = SimpleNamespace(absolute_path=tmp_path / "not-cloned")
    seen: dict = {}

    def restore(path):
        seen["protocol"] = client._forced_access_protocol
        raise _Stop

    client = SimpleNamespace(
        _forced_access_protocol=None,
        registry=None,
        _log_event=lambda *args, **kwargs: None,
        load_gts=lambda path: {"lib": missing},
        _restore_gts_snapshot=restore,
    )
    with pytest.raises(_Stop):
        TreeCommands(client).pull(source, force_access_protocol="https")
    assert seen == {"protocol": AccessProtocol.HTTPS}

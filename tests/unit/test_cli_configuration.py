"""Unit tests for ``ComplexGitSync.cli.configuration``.

Adapted from the end-to-end ``main([...])`` coverage of the "Configuration"
command group (``discover``, ``repo``) already
exercised in ``tests/unit/test_cli_smoke.py``, so the ``_handle_*``/
``_execute_*`` pairs are covered directly against the new module rather
than only through a full CLI invocation.

``cli/configuration.py`` cannot yet be imported as
``ComplexGitSync.cli.configuration`` via a normal ``import`` statement:
``ComplexGitSync/cli.py`` (the file) and ``ComplexGitSync/cli/`` (the new
package-in-progress, still missing ``__init__.py`` on purpose — see the
P6-cli-author work package) coexist, and Python resolves
``ComplexGitSync.cli`` to the existing module file, not the new package
directory. Both this module and its sibling ``cli/_shared.py`` are loaded
directly from their file paths instead, following the same pattern
``tests/unit/test_cli_shared.py`` documents: ``_shared`` is loaded and
registered in ``sys.modules`` under ``ComplexGitSync.cli._shared`` first,
so that ``configuration.py``'s own ``from ._shared import _run_with_logging``
resolves from the module cache without needing ``ComplexGitSync.cli`` to
be a real, importable package.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ComplexGitSync.orchestre import DiscoveredRepo

_CLI_DIR = Path(__file__).resolve().parents[2] / "src" / "ComplexGitSync" / "cli"


def _load_module(name: str, filename: str):
    module_path = _CLI_DIR / filename
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_shared = _load_module("ComplexGitSync.cli._shared", "_shared.py")
configuration = _load_module("ComplexGitSync.cli.configuration", "configuration.py")


# ---------------------------------------------------------------------------
# register_parsers
# ---------------------------------------------------------------------------


def _build_parser():
    parser = argparse.ArgumentParser(prog="cgitsync")
    subparsers = parser.add_subparsers(dest="command")
    configuration.register_parsers(subparsers, _shared._non_negative_int)
    return parser


def test_register_parsers_registers_exactly_two_commands():
    parser = _build_parser()
    registered = set(parser._subparsers._group_actions[0].choices.keys())
    assert registered == {"discover", "repo"}


def test_commands_dict_matches_registered_help_text():
    parser = _build_parser()
    choices = parser._subparsers._group_actions[0].choices
    for name, help_text in configuration.COMMANDS.items():
        assert choices[name].description == help_text


def test_discover_max_depth_uses_non_negative_int(capsys):
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["discover", "--max-depth", "-1"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "depth must be >= 0" in captured.err


# ---------------------------------------------------------------------------
# _handle_create_cgs / configure delegation to ComplexGitSyncClient.configure
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


def test_discover_command_uses_client_method(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def discover_repos(self, root, *, max_depth=5, output=None):
            captured_call["root"] = Path(root)
            captured_call["max_depth"] = max_depth
            captured_call["output"] = output
            return SimpleNamespace(
                root=Path(root),
                project_name="demo",
                repos=(
                    DiscoveredRepo(
                        relative_path=".",
                        absolute_path=Path(root),
                        remote_url="https://github.com/owner/demo.git",
                        identifier="github:owner/demo",
                        branch="main",
                        has_cgs=False,
                    ),
                ),
                cgs_entries=({"repository": "github:owner/demo", "relative_path": "."},),
                warnings=(),
                written_to=None,
            )

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    args = argparse.Namespace(root=str(tmp_path), write=None, max_depth=5)
    exit_code = configuration._handle_discover(args)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["root"] == tmp_path.resolve()
    assert captured_call["output"] is None
    assert "github:owner/demo" in captured.out
    assert "Dry run" in captured.out


def test_discover_command_forwards_write_and_max_depth(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def discover_repos(self, root, *, max_depth=5, output=None):
            captured_call["max_depth"] = max_depth
            captured_call["output"] = output
            return SimpleNamespace(
                root=Path(root),
                project_name="demo",
                repos=(),
                cgs_entries=(),
                warnings=(),
            )

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    out = str(tmp_path / "draft.cgs")
    args = argparse.Namespace(root=str(tmp_path), write=out, max_depth=2)
    exit_code = configuration._handle_discover(args)
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["max_depth"] == 2
    assert captured_call["output"] == out


def test_discover_command_defaults_root_to_cwd(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def discover_repos(self, root, *, max_depth=5, output=None):
            captured_call["root"] = Path(root)
            return SimpleNamespace(
                root=Path(root), project_name="demo", repos=(), cgs_entries=(), warnings=()
            )

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)
    monkeypatch.chdir(tmp_path)

    args = argparse.Namespace(root=None, write=None, max_depth=5)
    exit_code = configuration._handle_discover(args)
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["root"] == tmp_path.resolve()


def test_discover_reports_warnings_and_unresolved_identifiers(capsys, tmp_path):
    class StubClient:
        def discover_repos(self, root, *, max_depth=5, output=None):
            return SimpleNamespace(
                root=Path(root),
                project_name="demo",
                repos=(
                    DiscoveredRepo(
                        relative_path="sub",
                        absolute_path=Path(root) / "sub",
                        remote_url=None,
                        identifier=None,
                        branch=None,
                        has_cgs=True,
                    ),
                ),
                cgs_entries=(),
                warnings=("could not resolve remote for sub",),
                written_to=None,
            )

    exit_code = configuration._execute_discover(StubClient(), tmp_path)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "(none)" in captured.out
    assert "(unresolved)" in captured.out
    assert "(detached)" in captured.out
    assert "auto (has its own .cgs)" in captured.out
    assert "could not resolve remote for sub" in captured.out


def test_discover_no_repos_found_reports_and_returns_zero(capsys, tmp_path):
    class StubClient:
        def discover_repos(self, root, *, max_depth=5, output=None):
            return SimpleNamespace(root=Path(root), project_name="demo", repos=(), warnings=())

    exit_code = configuration._execute_discover(StubClient(), tmp_path, max_depth=3)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert f"No git repository found under {tmp_path} (max depth 3)." in captured.out


def test_discover_with_write_reports_written_path(capsys, tmp_path):
    class StubClient:
        def discover_repos(self, root, *, max_depth=5, output=None):
            return SimpleNamespace(
                root=Path(root),
                project_name="demo",
                repos=(
                    DiscoveredRepo(
                        relative_path=".",
                        absolute_path=Path(root),
                        remote_url="https://github.com/owner/demo.git",
                        identifier="github:owner/demo",
                        branch="main",
                        has_cgs=False,
                    ),
                ),
                warnings=(),
                written_to=Path(output).resolve(),
            )

    write_path = str(tmp_path / "draft.cgs")
    exit_code = configuration._execute_discover(StubClient(), tmp_path, write=write_path)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert f".cgs draft written to: {Path(write_path).resolve()}" in captured.out
    assert "Review it, then run: cgitsync validate <file>" in captured.out

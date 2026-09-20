"""CLI contract for ``cgitsync env`` and ``env check``."""

from __future__ import annotations

from ComplexGitSync.cli import build_parser
from ComplexGitSync.cli.environment import _execute_environment
from ComplexGitSync.cli.exit_codes import EXIT_OK, EXIT_REFUSED
from ComplexGitSync.tree_env import Drift, ToolVersion, TreeEnvironment


def _record() -> TreeEnvironment:
    return TreeEnvironment(
        architecture="x86_64",
        pixi_platform="linux-64",
        os_name="linux",
        os_version="24.04",
        libc_name="glibc",
        libc_version="2.39",
        kernel_release="6.8.0",
        tools=(ToolVersion("python", "3.11.9", "3.11.9"),),
    )


class FakeClient:
    def __init__(self, drift=Drift()) -> None:
        self.drift = drift
        self.loaded = None

    def load_gts(self, source):
        self.loaded = source

    def environment(self):
        return _record()

    def check_environment(self, document=None):
        return self.drift


def test_parser_accepts_show_and_check_forms():
    show = build_parser().parse_args(["env", "--search-dir", "work"])
    check = build_parser().parse_args(["env", "check", "--search-dir", "work", "--cgs", "x.cgs"])
    assert show.environment_command is None
    assert check.environment_command == "check"
    assert check.cgs == "x.cgs"


def test_env_prints_machine_and_tool_facts(tmp_path, capsys):
    client = FakeClient()
    assert _execute_environment(client, tmp_path / "state.gts", check=False, cgs=None) == EXIT_OK
    output = capsys.readouterr().out
    assert "architecture=x86_64" in output
    assert "pixi_platform=linux-64" in output
    assert "tool python version=3.11.9" in output


def test_env_check_is_nonzero_only_for_required_drift(tmp_path, capsys):
    clean = FakeClient(Drift(undeclared=("python",)))
    assert _execute_environment(clean, tmp_path / "state.gts", check=True, cgs=None) == EXIT_OK
    failing = FakeClient(Drift(missing=("tool:git",)))
    assert _execute_environment(failing, tmp_path / "state.gts", check=True, cgs=None) == EXIT_REFUSED
    assert "missing=tool:git" in capsys.readouterr().out

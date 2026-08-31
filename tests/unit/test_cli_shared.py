"""Unit tests for ``ComplexGitSync.cli._shared``.

Adapted from the direct-function/behaviour coverage already exercised
end-to-end through ``main([...])`` in ``tests/unit/test_cli_smoke.py``
(``_run_with_logging``'s two command-specific error hints, the
``log_file=``/``dry_run=``/``plan_order=`` output lines, and the
``.gitignore`` sync report), so these specific helpers are covered directly
rather than only through a full CLI invocation.

``cli/_shared.py`` cannot yet be imported as ``ComplexGitSync.cli._shared``
via a normal ``import`` statement: ``ComplexGitSync/cli.py`` (the file) and
``ComplexGitSync/cli/`` (the new package-in-progress, still missing
``__init__.py`` on purpose — see the P6-cli-author work package) coexist,
and Python resolves ``ComplexGitSync.cli`` to the existing module file, not
the new package directory. This module is loaded directly from its file
path instead; its relative imports (``from ..cgs_format import ...`` etc.)
still resolve correctly because they only ever step up to the real,
already-importable ``ComplexGitSync`` package.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from ComplexGitSync.git_tree import ProjectTreeState


def _load_cli_shared():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "ComplexGitSync"
        / "cli"
        / "_shared.py"
    )
    spec = importlib.util.spec_from_file_location("ComplexGitSync.cli._shared", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_shared = _load_cli_shared()


# ---------------------------------------------------------------------------
# _add_gitignore_sync_arguments
# ---------------------------------------------------------------------------


def test_add_gitignore_sync_arguments_registers_all_four_flags():
    parser = argparse.ArgumentParser()
    _shared._add_gitignore_sync_arguments(parser)

    args = parser.parse_args(
        [
            "--commit-gitignore",
            "--force-gitignore-sync",
            "--git-user-name",
            "Alice",
            "--git-user-email",
            "alice@example.com",
        ]
    )
    assert args.commit_gitignore is True
    assert args.force_gitignore_sync is True
    assert args.git_user_name == "Alice"
    assert args.git_user_email == "alice@example.com"


def test_add_gitignore_sync_arguments_flags_default_off():
    parser = argparse.ArgumentParser()
    _shared._add_gitignore_sync_arguments(parser)

    args = parser.parse_args([])
    assert args.commit_gitignore is False
    assert args.force_gitignore_sync is False
    assert args.git_user_name is None
    assert args.git_user_email is None


# ---------------------------------------------------------------------------
# _non_negative_int
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [("0", 0), ("3", 3), ("42", 42)])
def test_non_negative_int_accepts_zero_and_positive(raw, expected):
    assert _shared._non_negative_int(raw) == expected


def test_non_negative_int_rejects_negative():
    with pytest.raises(argparse.ArgumentTypeError, match="depth must be >= 0"):
        _shared._non_negative_int("-1")


# ---------------------------------------------------------------------------
# _format_tree_state_line
# ---------------------------------------------------------------------------


def test_format_tree_state_line_ready_tree():
    tree_state = ProjectTreeState(
        lifecycle_state=SimpleNamespace(value="READY"),
        is_ready=True,
        registry_complete=True,
    )
    line = _shared._format_tree_state_line(tree_state)
    assert line == (
        "READY ready=true complete=true gittree_created=true gittree_active=true"
    )


def test_format_tree_state_line_unloaded_tree():
    tree_state = ProjectTreeState(
        lifecycle_state=SimpleNamespace(value="UNLOADED"),
        is_ready=False,
        registry_complete=False,
    )
    line = _shared._format_tree_state_line(tree_state)
    assert line == (
        "UNLOADED ready=false complete=false gittree_created=false gittree_active=false"
    )


# ---------------------------------------------------------------------------
# _resolve_gts_path / _resolve_workspace_source / _resolve_visualization_source
# ---------------------------------------------------------------------------


def test_resolve_gts_path_returns_explicit_path_verbatim(tmp_path):
    gts_path = tmp_path / "explicit.gts"
    assert _shared._resolve_gts_path(str(gts_path), None) == Path(gts_path)


def test_resolve_gts_path_auto_discovers_when_none(monkeypatch, tmp_path):
    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    snapshot = state_dir / "project.gts"
    snapshot.touch()

    monkeypatch.setenv("CGSHOME", str(tmp_path))
    result = _shared._resolve_gts_path(None, None)
    assert result == snapshot.resolve()


def test_resolve_workspace_source_returns_explicit_path_verbatim(tmp_path):
    source = tmp_path / "project.cgs"
    assert _shared._resolve_workspace_source(str(source), None) == Path(source)


def test_resolve_workspace_source_auto_discovers_when_none(monkeypatch, tmp_path):
    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    snapshot = state_dir / "project.gts"
    snapshot.touch()

    monkeypatch.setenv("CGSHOME", str(tmp_path))
    result = _shared._resolve_workspace_source(None, None)
    assert result == snapshot.resolve()


def test_resolve_visualization_source_returns_explicit_path_verbatim(tmp_path):
    source = tmp_path / "project.gts"
    assert _shared._resolve_visualization_source(str(source), None) == Path(source)


def test_resolve_visualization_source_auto_discovers_when_none(monkeypatch, tmp_path):
    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    snapshot = state_dir / "project.gts"
    snapshot.touch()

    monkeypatch.setenv("CGSHOME", str(tmp_path))
    result = _shared._resolve_visualization_source(None, None)
    assert result == snapshot.resolve()


# ---------------------------------------------------------------------------
# _run_with_logging — error hints and event dispatch
# ---------------------------------------------------------------------------


class _StubClient:
    """Minimal stand-in exercising only what ``_run_with_logging`` touches."""

    def __init__(self):
        self.run_logger = None


def test_run_with_logging_initialise_failure_suggests_clean_init(capsys, tmp_path):
    def runner(client, source):
        raise RuntimeError("clone failed")

    with pytest.raises(RuntimeError, match="clone failed"):
        _shared._run_with_logging(
            command_name="initialise",
            source=tmp_path / "project.cgs",
            runner=runner,
            client=_StubClient(),
        )

    captured = capsys.readouterr()
    assert "Try clean-init method" in captured.err


def test_run_with_logging_pull_failure_suggests_pull_force(capsys, tmp_path):
    def runner(client, source):
        raise RuntimeError("pull failed")

    with pytest.raises(RuntimeError, match="pull failed"):
        _shared._run_with_logging(
            command_name="pull",
            source=tmp_path / "project.cgs",
            runner=runner,
            client=_StubClient(),
        )

    captured = capsys.readouterr()
    assert "You can try cgitsync pull-force command" in captured.err


def test_run_with_logging_other_command_failure_prints_no_hint(capsys, tmp_path):
    def runner(client, source):
        raise RuntimeError("status failed")

    with pytest.raises(RuntimeError, match="status failed"):
        _shared._run_with_logging(
            command_name="status",
            source=tmp_path / "project.cgs",
            runner=runner,
            client=_StubClient(),
        )

    captured = capsys.readouterr()
    assert "Try clean-init method" not in captured.err
    assert "You can try cgitsync pull-force command" not in captured.err


def test_run_with_logging_success_returns_runner_exit_code(tmp_path):
    def runner(client, source):
        assert source == (tmp_path / "project.cgs").resolve()
        return 0

    exit_code = _shared._run_with_logging(
        command_name="status",
        source=tmp_path / "project.cgs",
        runner=runner,
        client=_StubClient(),
    )
    assert exit_code == 0


def test_run_with_logging_logs_command_start_and_end(tmp_path):
    client = _StubClient()

    exit_code = _shared._run_with_logging(
        command_name="status",
        source=tmp_path / "project.cgs",
        runner=lambda c, s: 0,
        client=client,
    )
    assert exit_code == 0
    assert client.run_logger is not None
    # _create_command_logger produced a real CommandRunLogger; command_start
    # and command_end were both logged onto it.
    assert client.run_logger._buffered_lines
    assert '"event": "command_start"' in client.run_logger._buffered_lines[0]
    assert '"event": "command_end"' in client.run_logger._buffered_lines[-1]


# ---------------------------------------------------------------------------
# _create_command_logger
# ---------------------------------------------------------------------------


def _write_cgs(tmp_path, *, profile: str | None = None):
    config_path = tmp_path / "project.cgs"
    runtime = f'\n[runtime]\nprofile = "{profile}"\n' if profile is not None else ""
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
"""
            + runtime
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def test_create_command_logger_reads_explicit_quiet_profile_from_cgs(tmp_path):
    # A distinct command_name per test: create_run_logger derives the
    # underlying logging.Logger's name from command_name + a second-
    # precision timestamp, so two calls sharing both within the same
    # wall-clock second would reuse (and keep appending handlers to) the
    # very same cached Logger object.
    config_path = _write_cgs(tmp_path, profile="quiet")
    logger = _shared._create_command_logger(
        "quiet-profile-status", config_path, project_root=None
    )
    console_handler = logger._logger.handlers[-1]
    assert console_handler.level == logging.WARNING


def test_create_command_logger_reads_verbose_profile_from_cgs(tmp_path):
    config_path = _write_cgs(tmp_path, profile="verbose")
    logger = _shared._create_command_logger(
        "verbose-profile-status", config_path, project_root=None
    )
    console_handler = logger._logger.handlers[-1]
    assert console_handler.level == logging.INFO


def test_create_command_logger_defaults_to_cgs_runtime_default_when_unset(tmp_path):
    # No [runtime] table at all: falls through to CgsDocument's own
    # RUNTIME_DEFAULTS (profile="verbose"), not _create_command_logger's
    # local "quiet" fallback — that fallback only applies when no .cgs
    # document could be read at all (see the missing-source test below).
    config_path = _write_cgs(tmp_path)
    logger = _shared._create_command_logger(
        "unset-profile-status", config_path, project_root=None
    )
    console_handler = logger._logger.handlers[-1]
    assert console_handler.level == logging.INFO


def test_create_command_logger_tolerates_missing_source(tmp_path):
    # Non-.cgs / non-existent sources (e.g. a .gts snapshot, or a CGSHOME
    # directory for `verify`) fall back to the quiet default instead of
    # raising.
    logger = _shared._create_command_logger(
        "missing-source-verify", tmp_path / "nonexistent.gts", project_root=None
    )
    console_handler = logger._logger.handlers[-1]
    assert console_handler.level == logging.WARNING


# ---------------------------------------------------------------------------
# _load_ready_registry_source / _load_visualization_source
# ---------------------------------------------------------------------------


def test_load_ready_registry_source_delegates_to_load_gts(tmp_path):
    calls: list[Path] = []

    class _Client:
        def load_gts(self, path):
            calls.append(Path(path))

    source = tmp_path / "project.gts"
    _shared._load_ready_registry_source(_Client(), source)
    assert calls == [source]


def test_load_visualization_source_uses_load_gts_for_gts_suffix(tmp_path):
    calls: list[tuple[str, object]] = []

    class _Client:
        def load_gts(self, path):
            calls.append(("gts", Path(path)))

        def load_runtime_or_cgs(self, path, *, discover_nested):
            calls.append(("cgs", Path(path), discover_nested))

    source = tmp_path / "project.gts"
    _shared._load_visualization_source(_Client(), source, discover_nested=True)
    assert calls == [("gts", source)]


def test_load_visualization_source_uses_load_runtime_or_cgs_for_other_suffix(tmp_path):
    calls: list[tuple] = []

    class _Client:
        def load_gts(self, path):
            calls.append(("gts", Path(path)))

        def load_runtime_or_cgs(self, path, *, discover_nested):
            calls.append(("cgs", Path(path), discover_nested))

    source = tmp_path / "project.cgs"
    _shared._load_visualization_source(_Client(), source, discover_nested=False)
    assert calls == [("cgs", source, False)]


# ---------------------------------------------------------------------------
# _print_dry_run_plan / _format_leaf_first_repo_order
# ---------------------------------------------------------------------------


def test_format_leaf_first_repo_order_falls_back_without_registry():
    class _Client:
        def get_dependency_registry(self):
            raise AttributeError

    assert _shared._format_leaf_first_repo_order(_Client()) == "leaf -> parent -> root"


def test_format_leaf_first_repo_order_falls_back_on_runtime_error():
    class _Client:
        def get_dependency_registry(self):
            raise RuntimeError("registry not ready")

    assert _shared._format_leaf_first_repo_order(_Client()) == "leaf -> parent -> root"


def test_print_dry_run_plan_prints_actions_and_fallback_order(capsys):
    class _Client:
        def get_dependency_registry(self):
            raise AttributeError

    _shared._print_dry_run_plan(
        _Client(),
        command_name="commit",
        actions=("git add --all", "git commit -m 'preview'"),
    )
    captured = capsys.readouterr()
    assert "dry_run=true command=commit" in captured.out
    assert "plan_actions=git add --all -> git commit -m 'preview'" in captured.out
    assert "plan_order=leaf -> parent -> root" in captured.out


# ---------------------------------------------------------------------------
# _format_repo_tree_outline / _print_repo_tree_result
# ---------------------------------------------------------------------------


def test_format_repo_tree_outline_returns_empty_string_without_support():
    class _Client:
        pass

    assert _shared._format_repo_tree_outline(_Client()) == ""


def test_format_repo_tree_outline_returns_formatted_tree():
    class _Client:
        def format_repo_tree(self):
            return "demo (project)\n└── child-repo (leaf)"

    assert _shared._format_repo_tree_outline(_Client()) == "demo (project)\n└── child-repo (leaf)"


def test_print_repo_tree_result_silent_without_support(capsys):
    class _Client:
        pass

    _shared._print_repo_tree_result(_Client())
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_repo_tree_result_prints_tree(capsys):
    class _Client:
        def view_tree(self):
            return "demo (project)\n└── child-repo (leaf)"

    _shared._print_repo_tree_result(_Client())
    captured = capsys.readouterr()
    assert captured.out == "repos:\ndemo (project)\n└── child-repo (leaf)\n"


# ---------------------------------------------------------------------------
# _print_gitignore_sync_report
# ---------------------------------------------------------------------------


def test_print_gitignore_sync_report_silent_without_support(capsys):
    class _Client:
        pass

    _shared._print_gitignore_sync_report(_Client())
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_gitignore_sync_report_reports_committed_and_uncommitted_entries(capsys):
    entry_committed = SimpleNamespace(
        committed=True,
        name="demo",
        absolute_path=Path("/tmp/demo"),
        added_paths=["build/", "*.log"],
    )
    entry_uncommitted = SimpleNamespace(
        committed=False,
        name="child-repo",
        absolute_path=Path("/tmp/child-repo"),
        added_paths=[],
    )

    class _Client:
        last_gitignore_sync = [entry_committed, entry_uncommitted]

    _shared._print_gitignore_sync_report(_Client())
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0] == ".gitignore updated (committed and pushed): demo (/tmp/demo)"
    assert lines[1] == "  + build/"
    assert lines[2] == "  + *.log"
    assert lines[3] == ".gitignore updated (not committed): child-repo (/tmp/child-repo)"

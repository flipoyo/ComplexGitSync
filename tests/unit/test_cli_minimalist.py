"""Unit tests for ``ComplexGitSync.cli.minimalist``.

Adapted from the end-to-end ``main([...])`` coverage in
``tests/unit/test_cli_smoke.py`` for the eight Minimalist commands
(``initialise``, ``bootstrap``, ``clean-init``, ``freeze-release``,
``freeze-release-force``, ``status``, ``view-tree``, ``launch-release``),
so this module's ``register_parsers``/``_handle_*``/``_execute_*`` surface
is covered directly rather than only through the not-yet-integrated
``cli.py`` -> ``cli/`` package split.

``cli/minimalist.py`` cannot yet be imported as
``ComplexGitSync.cli.minimalist`` via a normal ``import`` statement, for the
same reason documented in ``tests/unit/test_cli_shared.py``:
``ComplexGitSync/cli.py`` (the file) and ``ComplexGitSync/cli/`` (the new
package-in-progress, still missing ``__init__.py`` on purpose — see the
P6-cli-author work package) coexist, and Python resolves
``ComplexGitSync.cli`` to the existing module file, not the new package
directory. Both ``cli/_shared.py`` and ``cli/minimalist.py`` are loaded
directly from their file paths instead; ``minimalist.py``'s own
``from ._shared import ...`` resolves correctly because ``_shared`` is
pre-registered in ``sys.modules`` under ``ComplexGitSync.cli._shared``
before ``minimalist.py`` is executed.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ComplexGitSync.cgs_format import CgsDocument


def _load_module(name: str, relative_parts: tuple[str, ...]):
    module_path = Path(__file__).resolve().parents[2].joinpath(
        "src", "ComplexGitSync", *relative_parts
    )
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_shared = _load_module("ComplexGitSync.cli._shared", ("cli", "_shared.py"))
minimalist = _load_module("ComplexGitSync.cli.minimalist", ("cli", "minimalist.py"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cgitsync")
    subparsers = parser.add_subparsers(dest="command")
    minimalist.register_parsers(
        subparsers, add_gitignore_sync_arguments=_shared._add_gitignore_sync_arguments
    )
    return parser


def _dispatch(argv):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "initialise":
        minimalist._validate_initialise_definition(parser, args)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_commands_dict_has_exactly_the_eight_minimalist_commands():
    assert set(minimalist.COMMANDS) == {
        "initialise",
        "bootstrap",
        "clean-init",
        "freeze-release",
        "freeze-release-force",
        "status",
        "view-tree",
        "launch-release",
    }


def test_commands_help_text_matches_readme_command_table():
    assert minimalist.COMMANDS["initialise"] == (
        "Initialise a project tree: clone(.cgs) or restore state(.gts)."
    )
    assert minimalist.COMMANDS["clean-init"] == (
        "Purge generated clone state, then initialise from a .cgs spec."
    )
    assert minimalist.COMMANDS["freeze-release"] == (
        "Run add, commit, pull, push, and freeze from a READY tree."
    )
    assert minimalist.COMMANDS["freeze-release-force"] == (
        "Run add, commit, pull-force, push, and freeze from a READY tree."
    )
    assert minimalist.COMMANDS["status"] == "Summarize tree readiness and sync state."
    assert minimalist.COMMANDS["view-tree"] == "Render a topology-focused tree view in terminal."
    assert minimalist.COMMANDS["launch-release"] == (
        "Check out a frozen release tag from a READY tree."
    )


def test_register_parsers_registers_exactly_eight_subparsers():
    parser = _build_parser()
    choices = parser._subparsers._group_actions[0].choices
    assert set(choices) == set(minimalist.COMMANDS)


@pytest.mark.parametrize("command", ["initialise", "clean-init"])
def test_gitignore_sync_flags_documented_on_relevant_commands(command, capsys):
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([command, "--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "--commit-gitignore" in captured.out
    assert "--force-gitignore-sync" in captured.out
    assert "--git-user-name" in captured.out
    assert "--git-user-email" in captured.out


def test_gitignore_sync_flags_rejected_on_unrelated_command(capsys):
    """A global flag would silently no-op on view-tree; it must instead error."""
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["view-tree", "--commit-gitignore"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "unrecognized arguments" in captured.err


def test_initialise_help_documents_repeatable_repos(capsys):
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["initialise", "--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "--project" in captured.out
    assert "--repo" in captured.out
    assert "repeat" in captured.out


# ---------------------------------------------------------------------------
# initialise
# ---------------------------------------------------------------------------


def test_initialise_command_restores_gts_snapshot(tmp_path, capsys):
    gts_path = _write_ready_gts(tmp_path)

    exit_code = _dispatch(["initialise", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "log_file=" not in captured.out
    assert "workflow=load->validate" in captured.out
    assert "READY" in captured.out
    assert "ready=true" in captured.out
    assert "complete=true" in captured.out
    assert "gittree_created=true" in captured.out
    assert "gittree_active=true" in captured.out
    assert "tree:" in captured.out
    assert "demo (project)" in captured.out


def test_initialise_command_clones_from_cgs(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def resolve_initialise_cgshome(self, source, *, output_path=None):
            return (
                Path(output_path) / "project"
                if output_path is not None
                else tmp_path / "workspace" / "project"
            )

        def initialise_cgs(self, source, *, output_path=None, **_kwargs):
            captured_call["source"] = Path(source)
            captured_call["output_path"] = output_path
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "workspace" / "project")
            )

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

        def format_repo_tree(self):
            return "demo (project)\n└── child-repo (leaf)"

    monkeypatch.setattr(minimalist, "ComplexGitSyncClient", StubClient)

    config_path = tmp_path / "project.cgs"
    config_path.touch()
    exit_code = _dispatch(["initialise", str(config_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["source"] == config_path.resolve()
    assert "workflow=load->expand->validate->clone" in captured.out
    assert "git_command=git clone" in captured.out
    assert "tree:" in captured.out
    assert "demo (project)" in captured.out
    assert "READY ready=true" in captured.out


@pytest.mark.parametrize(
    "repositories",
    [
        ["github:example/example-project"],
        [
            "gitlab:CGS_test/CGSil1",
            "codeberg:GX4G/GX4G",
        ],
    ],
)
def test_initialise_accepts_direct_cli_project_definition(
    repositories, monkeypatch, capsys, tmp_path
):
    captured_call: dict[str, object] = {}

    class StubClient:
        def configure(self, project, repositories, *, output_path=None):
            captured_call["configured_project"] = project
            captured_call["configured_repositories"] = list(repositories)
            captured_call["configuration_output_path"] = output_path
            return CgsDocument.from_dict(
                {"project": project, "repos": list(repositories)}
            )

        def resolve_cgshome(self, document, source_path, *, output_path=None):
            captured_call["resolved_document"] = document
            captured_call["logical_source"] = Path(source_path)
            captured_call["output_path"] = output_path
            return tmp_path / "workspace" / str(document.project_name)

        def initialise_cgs_document(
            self, document, *, source_path, output_path=None, clean_before_clone=False, **_kwargs
        ):
            captured_call["document"] = document
            captured_call["source_path"] = Path(source_path)
            captured_call["clean_before_clone"] = clean_before_clone
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(
                    absolute_path=tmp_path / "workspace" / str(document.project_name)
                )
            )

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"),
                is_ready=True,
                registry_complete=True,
            )

        def format_repo_tree(self):
            return "CGSil1 (project)"

    def _run_without_logging(*, runner, client, source, **_kwargs):
        return runner(client, Path(source).resolve())

    monkeypatch.setattr(minimalist, "ComplexGitSyncClient", StubClient)
    monkeypatch.setattr(minimalist, "_run_with_logging", _run_without_logging)
    monkeypatch.chdir(tmp_path)

    argv = ["initialise", "--project", "CGSil1"]
    for repository in repositories:
        argv.extend(["--repo", repository])

    exit_code = _dispatch(argv)
    captured = capsys.readouterr()

    expected = CgsDocument.from_dict({"project": "CGSil1", "repos": repositories})
    assert exit_code == 0
    assert isinstance(captured_call["document"], CgsDocument)
    assert captured_call["document"].to_dict() == expected.to_dict()
    assert captured_call["configured_project"] == "CGSil1"
    assert captured_call["configured_repositories"] == repositories
    assert captured_call["configuration_output_path"] is None
    assert captured_call["resolved_document"] is captured_call["document"]
    assert captured_call["source_path"] == tmp_path / "CGSil1.cgs"
    assert "workflow=load->expand->validate->clone" in captured.out


def test_initialise_command_failure_suggests_clean_init(monkeypatch, capsys, tmp_path):
    class StubClient:
        def resolve_initialise_cgshome(self, source, *, output_path=None):
            return tmp_path / "workspace" / "project"

        def initialise_cgs(self, source, *, output_path=None, **_kwargs):
            raise RuntimeError("clone failed")

    monkeypatch.setattr(minimalist, "ComplexGitSyncClient", StubClient)

    config_path = tmp_path / "project.cgs"
    config_path.touch()
    with pytest.raises(RuntimeError, match="clone failed"):
        _dispatch(["initialise", str(config_path)])

    captured = capsys.readouterr()
    assert "Try clean-init method" in captured.err


def test_initialise_command_output_path_is_forwarded(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def resolve_initialise_cgshome(self, source, *, output_path=None):
            return Path(output_path) / "project"

        def initialise_cgs(self, source, *, output_path=None, **_kwargs):
            captured_call["source"] = Path(source)
            captured_call["output_path"] = output_path
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "parent" / "project")
            )

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

        def format_repo_tree(self):
            return "demo (project)\n└── child-repo (leaf)"

    monkeypatch.setattr(minimalist, "ComplexGitSyncClient", StubClient)

    config_path = tmp_path / "project.cgs"
    config_path.touch()
    output_path = str(tmp_path / "parent")
    exit_code = _dispatch(["initialise", str(config_path), "--output-path", output_path])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["output_path"] == output_path


def test_initialise_command_gts_does_not_write_external_log_file(monkeypatch, tmp_path, capsys):
    gts_path = _write_ready_gts(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    exit_code = _dispatch(["initialise", str(gts_path)])
    captured = capsys.readouterr()

    log_dir = tmp_path / "state-home" / "ComplexGitSync" / "logs"

    assert exit_code == 0
    assert "READY" in captured.out
    assert "operation_sequence=GT-LOAD->GT-VALIDATE" in captured.out
    assert "log_file=" not in captured.out
    assert not log_dir.exists()


def test_initialise_command_requires_source_or_project(capsys):
    with pytest.raises(SystemExit) as exc_info:
        _dispatch(["initialise"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "requires SOURCE or --project" in captured.err


def test_initialise_cli_definition_requires_project(capsys):
    with pytest.raises(SystemExit) as exc_info:
        _dispatch(["initialise", "--repo", "github:owner/repository"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "requires SOURCE or --project" in captured.err


def test_initialise_cli_definition_requires_repo(capsys):
    with pytest.raises(SystemExit) as exc_info:
        _dispatch(["initialise", "--project", "demo"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "requires at least one --repo" in captured.err


def test_initialise_rejects_source_and_cli_definition(capsys):
    with pytest.raises(SystemExit) as exc_info:
        _dispatch(
            [
                "initialise",
                "project.cgs",
                "--project",
                "demo",
                "--repo",
                "github:owner/repository",
            ]
        )

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "not both" in captured.err


# ---------------------------------------------------------------------------
# clean-init
# ---------------------------------------------------------------------------


def test_clean_init_command_purges_before_clone(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def resolve_initialise_cgshome(self, source, *, output_path=None):
            return Path(output_path) / "project"

        def clean_init(self, source, *, output_path=None, **_kwargs):
            captured_call["source"] = Path(source)
            captured_call["output_path"] = output_path
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "parent" / "project")
            )

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

        def format_repo_tree(self):
            return "demo (project)\n└── child-repo (leaf)"

    monkeypatch.setattr(minimalist, "ComplexGitSyncClient", StubClient)

    config_path = tmp_path / "project.cgs"
    config_path.touch()
    output_path = str(tmp_path / "parent")
    exit_code = _dispatch(["clean-init", str(config_path), "--output-path", output_path])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["source"] == config_path.resolve()
    assert captured_call["output_path"] == output_path
    assert "operation_sequence=GT-LOAD->GT-DISCOVER->GT-VALIDATE->FS-PURGE->GT-CLONE" in captured.out
    assert "workflow=load->expand->validate->purge->clone" in captured.out
    assert "READY ready=true" in captured.out


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_command_uses_client_method(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def resolve_bootstrap_root(self, project_name, *, cgs_path=None):
            captured_call["resolve_project_name"] = project_name
            captured_call["resolve_cgs_path"] = cgs_path
            return tmp_path / "cgspath" / project_name

        def bootstrap(self, source, project_name, *, cgs_path=None, force_access_protocol=None):
            captured_call["source"] = Path(source)
            captured_call["project_name"] = project_name
            captured_call["cgs_path"] = cgs_path
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(
                    absolute_path=tmp_path / "cgspath" / project_name
                )
            )

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr(minimalist, "ComplexGitSyncClient", StubClient)

    exit_code = _dispatch(["bootstrap", "project.cgs", "myproject"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["resolve_project_name"] == "myproject"
    assert captured_call["source"] == Path("project.cgs").resolve()
    assert captured_call["project_name"] == "myproject"
    assert captured_call["cgs_path"] is None
    assert "READY ready=true complete=true" in captured.out


def test_bootstrap_command_forwards_cgs_path(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def resolve_bootstrap_root(self, project_name, *, cgs_path=None):
            captured_call["resolve_cgs_path"] = cgs_path
            return Path(cgs_path) / project_name

        def bootstrap(self, source, project_name, *, cgs_path=None, force_access_protocol=None):
            captured_call["cgs_path"] = cgs_path
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=Path(cgs_path) / project_name)
            )

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr(minimalist, "ComplexGitSyncClient", StubClient)

    cgs_path = str(tmp_path / "custom")
    exit_code = _dispatch(["bootstrap", "project.cgs", "myproject", "--cgs-path", cgs_path])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["resolve_cgs_path"] == cgs_path
    assert captured_call["cgs_path"] == cgs_path


# ---------------------------------------------------------------------------
# freeze-release / freeze-release-force
# ---------------------------------------------------------------------------


def test_freeze_release_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None
        loaded_snapshot_path = tmp_path / ".cgitsync" / "state" / "gts-000001-v1.0.gts"

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def freeze_release(self, name, message, *, force=False, **kwargs):
            captured_call["name"] = name
            captured_call["message"] = message
            captured_call["force"] = force

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

        def view_tree(self):
            return "ROOT project [main] clean synced"

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _dispatch(["freeze-release", "v1.0", "release commit", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call == {
        "gts_path": gts_path.resolve(),
        "name": "v1.0",
        "message": "release commit",
        "force": False,
    }
    assert "name=v1.0" in captured.out
    assert "message='release commit'" in captured.out
    assert "snapshot=" in captured.out
    assert "repos:" in captured.out


def test_freeze_release_force_command_uses_force_workflow(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def freeze_release(self, name, message, *, force=False, **kwargs):
            captured_call["name"] = name
            captured_call["message"] = message
            captured_call["force"] = force

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

        def view_tree(self):
            return "ROOT project [main] clean synced"

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _dispatch(
        ["freeze-release-force", "v1.0", "release commit", "--gts", str(gts_path)]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["force"] is True
    assert "git clean -fd" in captured.out


def test_freeze_release_dry_run_skips_mutation(monkeypatch, capsys, tmp_path):
    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def freeze_release(self, name, message, *, force=False, **kwargs):
            raise AssertionError("freeze_release should not be called during --dry-run")

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _dispatch(
        ["freeze-release", "v1.0", "release commit", "--gts", str(gts_path), "--dry-run"]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run=true command=freeze-release" in captured.out
    assert "cgitsync pull" in captured.out
    assert "cgitsync freeze v1.0" in captured.out


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def status(self):
            return "summary ready=true complete=true repos=1 dirty=0 staged=0 ahead=0 behind=0 errors=0"

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _dispatch(["status", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["gts_path"] == gts_path.resolve()
    assert "summary ready=true complete=true repos=1" in captured.out
    assert "READY ready=true" in captured.out


# ---------------------------------------------------------------------------
# view-tree
# ---------------------------------------------------------------------------


def test_view_tree_command_supports_gts_and_render_options(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def view_tree(self, *, depth=None, collapse=()):
            captured_call["depth"] = depth
            captured_call["collapse"] = collapse
            return "demo (root) [ALIGNED] @abc1234"

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _dispatch(
        ["view-tree", str(gts_path), "--depth", "1", "--collapse", "deps", "--collapse", "plugins"]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["gts_path"] == gts_path.resolve()
    assert captured_call["depth"] == 1
    assert captured_call["collapse"] == ("deps", "plugins")
    assert "demo (root) [ALIGNED] @abc1234" in captured.out


def test_view_tree_auto_discovery(monkeypatch, capsys, tmp_path):
    """view-tree with no source argument auto-discovers the .gts snapshot."""
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def view_tree(self, *, depth=None, collapse=()):
            return "demo (root) [ALIGNED] @abc1234"

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    cwd = tmp_path / "ComplexGitSync"
    cwd.mkdir()
    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "project.gts"
    gts_path.touch()

    monkeypatch.chdir(cwd)
    exit_code = _dispatch(["view-tree"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["gts_path"] == gts_path.resolve()
    assert "demo (root) [ALIGNED] @abc1234" in captured.out


# ---------------------------------------------------------------------------
# launch-release
# ---------------------------------------------------------------------------


def test_launch_release_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def launch_release(self, release_name):
            captured_call["release_name"] = release_name

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

        def view_tree(self):
            return "ROOT project [main] clean synced"

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _dispatch(["launch-release", "v2.0", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["release_name"] == "v2.0"
    assert "release=v2.0" in captured.out


def test_launch_release_command_auto_discovers_gts(monkeypatch, capsys, tmp_path):
    """launch_release resolves its READY snapshot from CGSHOME when --gts is omitted."""
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def launch_release(self, release_name):
            captured_call["release_name"] = release_name

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

        def view_tree(self):
            return "ROOT project [main] clean synced"

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    workspace = tmp_path / "workspace"
    state_dir = workspace / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "workspace.gts"
    gts_path.touch()

    monkeypatch.setenv("CGSHOME", str(workspace))
    exit_code = _dispatch(["launch-release", "v2.0"])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["gts_path"] == gts_path.resolve()
    assert captured_call["release_name"] == "v2.0"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _write_ready_gts(tmp_path):
    gts_path = tmp_path / "project.gts"
    root_path = (tmp_path / "workspace" / "demo").as_posix()
    gts_path.write_text(
        f"""
[document]
format_version = "1.0"
generated_at = "2026-05-13T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "{root_path}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "{root_path}"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "abc123"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return gts_path

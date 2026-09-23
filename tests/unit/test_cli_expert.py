"""Unit tests for ``ComplexGitSync.cli.expert`` — the Expert command group.

Adapted from the end-to-end ``main([...])`` coverage already exercised in
``tests/unit/test_cli_smoke.py`` (purge/validate/clone/pull/pull-force/
checkout/branch/add/commit/push/tag/freeze/import-submodules/
init-from-submodules) and
``tests/unit/test_verify_command.py::TestVerifyCli`` (verify), so these 16
commands are covered directly against the new ``cli/expert.py`` module
rather than only through the still-standalone ``cli.py``.

``cli/expert.py`` cannot yet be imported as ``ComplexGitSync.cli.expert``
via a normal ``import`` statement — see ``tests/unit/test_cli_shared.py``'s
module docstring: ``ComplexGitSync/cli.py`` (the file) and
``ComplexGitSync/cli/`` (the new package-in-progress, still missing
``__init__.py`` on purpose) coexist, and Python resolves
``ComplexGitSync.cli`` to the existing module file, not the new package
directory. Both this module and its sibling ``cli/_shared.py`` dependency
are loaded directly from their file paths instead, using the same
``importlib.util.spec_from_file_location`` pattern; a stand-in
``ComplexGitSync.cli`` package entry is registered in ``sys.modules`` first
so ``cli/expert.py``'s own ``from ._shared import ...`` relative import
resolves against the already-loaded ``_shared`` module rather than trying
(and failing) to import the real ``ComplexGitSync.cli`` module file.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from ComplexGitSync.discovery import ImportSubmodulesReport, SubmoduleEntry
from ComplexGitSync.memory.integrity import HistoryState

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "ComplexGitSync"


def _load_cli_module(name: str, relative_path: str):
    module_path = _SRC_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_cli_expert():
    if "ComplexGitSync.cli" not in sys.modules:
        cli_pkg = types.ModuleType("ComplexGitSync.cli")
        cli_pkg.__path__ = [str(_SRC_ROOT / "cli")]
        sys.modules["ComplexGitSync.cli"] = cli_pkg
    shared = sys.modules.get("ComplexGitSync.cli._shared") or _load_cli_module(
        "ComplexGitSync.cli._shared", "cli/_shared.py"
    )
    expert = _load_cli_module("ComplexGitSync.cli.expert", "cli/expert.py")
    return expert, shared


expert, _shared = _load_cli_expert()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cgitsync-expert-test")
    subparsers = parser.add_subparsers(dest="command")
    expert.register_parsers(subparsers)
    return parser


def _run(argv):
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    assert handler is not None, f"no handler registered for {argv!r}"
    return handler(args)


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_commands_dict_matches_registered_parsers():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    expert.register_parsers(subparsers)
    assert set(subparsers.choices.keys()) == set(expert.COMMANDS.keys())
    assert len(expert.COMMANDS) == 21


def test_commands_dict_help_text_matches_source_of_truth():
    assert expert.COMMANDS["purge"] == "Remove generated clone state for a .cgs workspace."
    assert expert.COMMANDS["verify"] == "Verify the hash-chained .cgitsync/lgr register for tamper-evidence."


# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------


def test_purge_command_removes_generated_clone_state(monkeypatch, capsys, tmp_path):
    removed = (tmp_path / "parent" / "project" / "child-repo", tmp_path / "parent" / "project" / ".gitmodules")
    captured_call: dict[str, object] = {}

    class StubClient:
        def resolve_initialise_cgshome(self, source, *, output_path=None):
            return Path(output_path) / "project"

        def purge(self, source, *, output_path=None):
            captured_call["source"] = Path(source)
            captured_call["output_path"] = output_path
            return removed

    monkeypatch.setattr(expert, "ComplexGitSyncClient", StubClient)

    config_path = tmp_path / "project.cgs"
    config_path.touch()
    output_path = str(tmp_path / "parent")
    exit_code = _run(["purge", str(config_path), "--output-path", output_path])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["source"] == config_path.resolve()
    assert captured_call["output_path"] == output_path
    assert "operation_sequence=GT-LOAD->GT-DISCOVER->GT-VALIDATE->FS-PURGE" in captured.out
    assert "workflow=load->expand->validate->purge" in captured.out
    assert str(removed[0]) in captured.out
    assert str(removed[1]) in captured.out


def test_purge_command_reports_none_removed(monkeypatch, capsys, tmp_path):
    class StubClient:
        def resolve_initialise_cgshome(self, source, *, output_path=None):
            return Path(output_path) / "project"

        def purge(self, source, *, output_path=None):
            return ()

    monkeypatch.setattr(expert, "ComplexGitSyncClient", StubClient)

    config_path = tmp_path / "project.cgs"
    config_path.touch()
    exit_code = _run(["purge", str(config_path), "--output-path", str(tmp_path / "parent")])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "removed: none" in captured.out


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_command_renders_lifecycle_state(tmp_path, capsys):
    config_path = _write_project_cgs(tmp_path)

    exit_code = _run(["validate", str(config_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "DECLARED" in captured.out


# ---------------------------------------------------------------------------
# clone
# ---------------------------------------------------------------------------


def test_clone_command_uses_client_method(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def resolve_clone_root(self, source, *, target_dir=None, output_path=None):
            captured_call["resolve_source"] = Path(source)
            captured_call["resolve_target_dir"] = target_dir
            captured_call["resolve_output_path"] = output_path
            return Path(target_dir)

        def clone(self, source, *, target_dir=None, output_path=None):
            captured_call["source"] = Path(source)
            captured_call["target_dir"] = target_dir
            captured_call["output_path"] = output_path
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "workspace" / "demo")
            )

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(expert, "ComplexGitSyncClient", StubClient)

    target_dir = str(tmp_path / "workspace" / "demo")
    exit_code = _run(["clone", "project.cgs", "--target-dir", target_dir])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["resolve_source"] == Path("project.cgs")
    assert captured_call["resolve_target_dir"] == target_dir
    assert captured_call["source"] == Path("project.cgs").resolve()
    assert captured_call["target_dir"] == target_dir
    assert "READY ready=true complete=true" in captured.out


def test_clone_command_output_path_is_forwarded(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def resolve_clone_root(self, source, *, target_dir=None, output_path=None):
            captured_call["resolve_output_path"] = output_path
            return tmp_path / "parent" / "demo"

        def clone(self, source, *, target_dir=None, output_path=None):
            captured_call["output_path"] = output_path
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "parent" / "demo")
            )

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr(expert, "ComplexGitSyncClient", StubClient)

    output_path = str(tmp_path / "parent")
    exit_code = _run(["clone", "project.cgs", "--output-path", output_path])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["resolve_output_path"] == output_path
    assert captured_call["output_path"] == output_path


# ---------------------------------------------------------------------------
# pull / pull-force
# ---------------------------------------------------------------------------


def test_gitignore_sync_flags_documented_on_pull(capsys):
    with pytest.raises(SystemExit) as exc_info:
        _run(["pull", "--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "--commit-gitignore" in captured.out
    assert "--force-gitignore-sync" in captured.out
    assert "--git-user-name" in captured.out
    assert "--git-user-email" in captured.out


def test_gitignore_sync_flags_absent_on_pull_force(capsys):
    with pytest.raises(SystemExit) as exc_info:
        _run(["pull-force", "--commit-gitignore"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "unrecognized arguments" in captured.err


def test_pull_command_creates_log_file(monkeypatch, tmp_path, capsys):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def pull(self, source, **_kwargs):
            captured_call["source"] = Path(source)
            self.run_logger.bind_log_file(
                tmp_path / "project" / ".cgitsync" / f"state({'a' * 64})_0" / "project.log"
            )
            return SimpleNamespace(get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "project"))

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    source_path = tmp_path / "project.cgs"
    source_path.touch()
    exit_code = _run(["pull", str(source_path)])
    captured = capsys.readouterr()

    log_file_line = next((line for line in captured.out.splitlines() if line.startswith("log_file=")), None)
    assert exit_code == 0
    assert log_file_line is not None
    log_file = Path(log_file_line.split("=", 1)[1])
    assert log_file.is_file()


def test_pull_command_failure_suggests_pull_force(monkeypatch, tmp_path, capsys):
    class StubClient:
        run_logger = None

        def pull(self, source, **_kwargs):
            raise RuntimeError("local changes would be overwritten")

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    source_path = tmp_path / "project.gts"
    source_path.touch()
    with pytest.raises(RuntimeError, match="local changes"):
        _run(["pull", str(source_path)])

    captured = capsys.readouterr()
    assert "You can try cgitsync autofix" in captured.err


def test_pull_force_command_uses_client_handler(monkeypatch, tmp_path, capsys):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def pull_force(self, source, **_kwargs):
            captured_call["source"] = Path(source)
            return SimpleNamespace(get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "project"))

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    source_path = tmp_path / "project.gts"
    source_path.touch()
    exit_code = _run(["pull-force", str(source_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["source"] == source_path.resolve()
    assert "git_command=git fetch" in captured.out
    assert "READY ready=true" in captured.out


# ---------------------------------------------------------------------------
# checkout / branch
# ---------------------------------------------------------------------------


def test_checkout_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def checkout(self, branch, *, ref_kind, private=False):
            captured_call["branch"] = branch
            captured_call["ref_kind"] = ref_kind

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["checkout", "feature-x", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["branch"] == "feature-x"
    assert "git_command=git checkout feature-x" in captured.out
    assert "READY ready=true" in captured.out
    assert "branch=feature-x" in captured.out


def test_checkout_command_with_tag_ref_kind(monkeypatch, capsys, tmp_path):
    from ComplexGitSync.git_repo import RefKind

    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def checkout(self, branch, *, ref_kind, private=False):
            captured_call["ref_kind"] = ref_kind

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["checkout", "v1.0.0", "--gts", str(gts_path), "--ref-kind", "tag"])
    assert exit_code == 0
    assert captured_call["ref_kind"] == RefKind.TAG


def test_branch_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def branch(self, branch, *, private=False):
            captured_call["branch"] = branch

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["branch", "feature-x", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["gts_path"] == gts_path.resolve()
    assert captured_call["branch"] == "feature-x"
    assert "git_command=git branch feature-x" in captured.out
    assert "READY ready=true" in captured.out
    assert "branch=feature-x" in captured.out


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


def test_commit_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def commit(self, message, *, stage_all, private=False, all_writable=False):
            captured_call["message"] = message
            captured_call["stage_all"] = stage_all

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["commit", "my commit", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["message"] == "my commit"
    assert captured_call["stage_all"] is True
    assert "READY ready=true" in captured.out


def test_commit_command_accepts_message_option(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def commit(self, message, *, stage_all, private=False, all_writable=False):
            captured_call["message"] = message
            captured_call["stage_all"] = stage_all

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["commit", "-m", "my commit", "--gts", str(gts_path)])

    assert exit_code == 0
    assert captured_call == {"message": "my commit", "stage_all": True}


def test_commit_command_rejects_duplicate_messages(capsys, tmp_path):
    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["commit", "positional", "-m", "option", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "provide exactly one message" in captured.err


def test_commit_command_no_stage_flag(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def commit(self, message, *, stage_all, private=False, all_writable=False):
            captured_call["stage_all"] = stage_all

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["commit", "msg", "--gts", str(gts_path), "--no-stage"])
    assert exit_code == 0
    assert captured_call["stage_all"] is False


def test_commit_command_dry_run_skips_mutation(monkeypatch, capsys, tmp_path):
    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def commit(self, message, *, stage_all, private=False, all_writable=False):
            raise AssertionError("commit should not be called during --dry-run")

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["commit", "preview", "--gts", str(gts_path), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run=true command=commit" in captured.out
    assert "plan_actions=git add --all -> git commit -m 'preview'" in captured.out


# ---------------------------------------------------------------------------
# add / push
# ---------------------------------------------------------------------------


def test_add_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def add(self, paths=None, *, private=False, all_writable=False):
            captured_call["added"] = True

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["add", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call.get("added") is True
    assert "READY ready=true" in captured.out


def test_add_command_forwards_paths_to_client(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def add(self, paths=None, *, private=False, all_writable=False):
            captured_call["paths"] = paths

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["add", "a.txt", "sub/b.txt", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["paths"] == ["a.txt", "sub/b.txt"]
    assert "git_command=git add -- a.txt sub/b.txt" in captured.out


def test_add_command_dry_run_skips_mutation(monkeypatch, capsys, tmp_path):
    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def add(self, paths=None, *, private=False, all_writable=False):
            raise AssertionError("add should not be called during --dry-run")

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["add", "--gts", str(gts_path), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run=true command=add" in captured.out
    assert "plan_actions=git add --all" in captured.out


def test_push_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def push(self, **_kwargs):
            captured_call["pushed"] = True

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["push", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call.get("pushed") is True
    assert "READY ready=true" in captured.out


def test_push_command_dry_run_skips_mutation(monkeypatch, capsys, tmp_path):
    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def push(self):
            raise AssertionError("push should not be called during --dry-run")

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["push", "--gts", str(gts_path), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run=true command=push" in captured.out
    assert "plan_actions=git push -> git push -u origin <branch> when upstream is missing" in captured.out


# ---------------------------------------------------------------------------
# tag / freeze
# ---------------------------------------------------------------------------


def test_tag_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def tag(self, name, *, private=False):
            captured_call["name"] = name

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

        def view_tree(self):
            return "ROOT project [main] clean synced"

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["tag", "v1.0", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["name"] == "v1.0"
    assert "name=v1.0" in captured.out


def test_freeze_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None
        loaded_snapshot_path = tmp_path / ".cgitsync" / "state" / "gts-000001-v1.0.gts"

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def freeze(self, name, **kwargs):
            captured_call["name"] = name

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["freeze", "v1.0", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["name"] == "v1.0"
    assert "name=v1.0" in captured.out
    assert "snapshot=" in captured.out
    assert "gts-000001-v1.0.gts" in captured.out


def test_freeze_command_dry_run_skips_mutation(monkeypatch, capsys, tmp_path):
    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def freeze(self, name, **kwargs):
            raise AssertionError("freeze should not be called during --dry-run")

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = _run(["freeze", "v1.0", "--gts", str(gts_path), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run=true command=freeze" in captured.out
    assert "plan_actions=git add --all -> git commit -m 'v1.0' -> git tag v1.0 -> git push" in captured.out


# ---------------------------------------------------------------------------
# import-submodules
# ---------------------------------------------------------------------------


def test_import_submodules_dry_run_reports_without_apply(monkeypatch, capsys, tmp_path):
    sub = SubmoduleEntry(name="child", path="deps/child", url="git@example.com:owner/child.git", branch="main")

    class StubClient:
        def import_submodules(self, source, *, apply=False, recursive=False):
            assert apply is False
            return ImportSubmodulesReport(
                submodules=(sub,), applied=False, converted=(), scan_root=Path(source)
            )

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    exit_code = _run(["import-submodules", str(repo_root)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Dry run" in captured.out
    assert "submodule: child" in captured.out
    assert "Pass --apply to perform the conversion." in captured.out


def test_import_submodules_apply_converts(monkeypatch, capsys, tmp_path):
    sub = SubmoduleEntry(name="child", path="deps/child", url="git@example.com:owner/child.git", branch="main")

    class StubClient:
        def import_submodules(self, source, *, apply=False, recursive=False):
            assert apply is True
            return ImportSubmodulesReport(
                submodules=(sub,), applied=True, converted=("child",), scan_root=Path(source)
            )

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    exit_code = _run(["import-submodules", str(repo_root), "--apply"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Converted 1 submodule(s)" in captured.out
    assert "child" in captured.out


def test_import_submodules_no_gitmodules_reports_nothing_to_import(monkeypatch, capsys, tmp_path):
    class StubClient:
        def import_submodules(self, source, *, apply=False, recursive=False):
            return SimpleNamespace(submodules=[], converted=[])

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    exit_code = _run(["import-submodules", str(repo_root)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "nothing to import" in captured.out


# ---------------------------------------------------------------------------
# init-from-submodules
# ---------------------------------------------------------------------------


def _stub_init_report(root: Path, *, dry_run: bool):
    """The shape ``_execute_init_from_submodules`` prints from."""
    sub = SubmoduleEntry(
        name="child",
        path="deps/child",
        url="git@example.com:owner/child.git",
        branch="main",
        owner_root=root,
    )
    import_report = ImportSubmodulesReport(
        submodules=(sub,),
        applied=not dry_run,
        converted=() if dry_run else ("child",),
        scan_root=root,
    )
    return SimpleNamespace(
        root=root,
        discover=SimpleNamespace(repos=(1, 2), project_name=root.name, warnings=()),
        cgs_path=root / f"{root.name}.cgs",
        cgs_written=not dry_run,
        import_report=import_report,
        tree=None,
        dry_run=dry_run,
    )


def test_init_from_submodules_dry_run_reports_the_plan(monkeypatch, capsys, tmp_path):
    repo_root = tmp_path / "project"
    repo_root.mkdir()

    class StubClient:
        def init_from_submodules(self, source, **kwargs):
            assert kwargs["dry_run"] is True
            return _stub_init_report(Path(source), dry_run=True)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    exit_code = _run(["init-from-submodules", str(repo_root), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Dry run — nothing written, cloned, or converted." in captured.out
    assert "would convert 1 submodule(s)" in captured.out
    assert "deps/child" in captured.out


def test_init_from_submodules_prints_next_steps_after_adopting(monkeypatch, capsys, tmp_path):
    repo_root = tmp_path / "project"
    repo_root.mkdir()

    class StubClient:
        def init_from_submodules(self, source, **kwargs):
            assert kwargs["dry_run"] is False
            return _stub_init_report(Path(source), dry_run=False)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    exit_code = _run(["init-from-submodules", str(repo_root)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Converted 1 submodule(s) to plain nested clones" in captured.out
    # The conversion is staged only, so the commit sequence must be spelled out.
    assert "staged but not committed" in captured.out
    assert "cgitsync branch <name>" in captured.out


def test_init_from_submodules_forwards_every_flag_to_the_client(monkeypatch, capsys, tmp_path):
    repo_root = tmp_path / "project"
    repo_root.mkdir()
    seen: dict = {}

    class StubClient:
        def init_from_submodules(self, source, **kwargs):
            seen.update(kwargs)
            return _stub_init_report(Path(source), dry_run=False)

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    exit_code = _run(
        [
            "init-from-submodules",
            str(repo_root),
            "--cgs",
            str(tmp_path / "hand.cgs"),
            "--max-depth",
            "3",
            "--force",
            "--force-protocol",
            "https",
        ]
    )
    capsys.readouterr()

    assert exit_code == 0
    assert seen == {
        "cgs_path": str(tmp_path / "hand.cgs"),
        "max_depth": 3,
        "dry_run": False,
        "force": True,
        "force_access_protocol": "https",
    }


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def test_verify_command_says_no_history_for_an_unstarted_register(tmp_path, capsys):
    """An empty register is "nothing recorded", never "chain clean".

    Exit 0 all the same: a new workspace is not a broken one.
    """
    (tmp_path / ".cgitsync").mkdir()

    exit_code = _run(["verify", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "status=no-history" in captured.out
    assert "findings=0" in captured.out
    assert "clean" not in captured.out


def test_verify_command_exits_nonzero_and_lists_findings_on_tamper(monkeypatch, tmp_path, capsys):
    (tmp_path / ".cgitsync").mkdir()

    class StubClient:
        def verify(self, cgshome, *, repair=False):
            finding = SimpleNamespace(name="BAD_ENTRY_HASH")
            return SimpleNamespace(
                is_clean=False,
                state=HistoryState.CORRUPT,
                findings=[(1, finding, "hash mismatch")],
            )

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    exit_code = _run(["verify", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "status=corrupt" in captured.out
    assert "findings=1" in captured.out
    assert "BAD_ENTRY_HASH" in captured.out


def test_verify_command_repair_flag_is_forwarded(monkeypatch, tmp_path, capsys):
    (tmp_path / ".cgitsync").mkdir()
    captured_call: dict[str, object] = {}

    class StubClient:
        def verify(self, cgshome, *, repair=False):
            captured_call["repair"] = repair
            return SimpleNamespace(
                is_clean=False,
                state=HistoryState.CORRUPT,
                findings=[(1, SimpleNamespace(name="HEAD_STALE"), "stale")],
            )

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)

    exit_code = _run(["verify", "--search-dir", str(tmp_path), "--repair"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured_call["repair"] is True
    assert "repair=attempted" in captured.out


def test_verify_command_requires_locatable_cgshome(monkeypatch, tmp_path):
    monkeypatch.delenv("CGSHOME", raising=False)
    with pytest.raises(FileNotFoundError, match=r"Unable to locate CGSHOME"):
        _run(["verify", "--search-dir", str(tmp_path)])


# ---------------------------------------------------------------------------
# _resolve_commit_message
# ---------------------------------------------------------------------------


def test_resolve_commit_message_prefers_option_over_positional_when_only_one_given():
    args = argparse.Namespace(message="positional", message_option=None)
    assert expert._resolve_commit_message(args) == "positional"

    args = argparse.Namespace(message=None, message_option="option")
    assert expert._resolve_commit_message(args) == "option"


def test_resolve_commit_message_rejects_both():
    args = argparse.Namespace(message="positional", message_option="option")
    assert expert._resolve_commit_message(args) is None


def test_resolve_commit_message_rejects_neither():
    args = argparse.Namespace(message=None, message_option=None)
    assert expert._resolve_commit_message(args) is None


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _write_project_cgs(tmp_path, *, profile: str | None = None):
    config_path = tmp_path / "project.cgs"
    runtime = ""
    if profile is not None:
        runtime = f'\n[runtime]\nprofile = "{profile}"\n'
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

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-repo"
relative_path = "deps/child-repo"
"""
            + runtime
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


# ---------------------------------------------------------------------------
# --all — .agent/.local/.localSpec/DevTickets/archive/20260911_UnifiedProjectPrivateScope_DevPlanTicket.md
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["add", "commit", "push", "merge"])
def test_all_and_private_are_refused_together(capsys, command):
    """--all already includes what --private selects, so both is a mistake."""
    argv = [command, "--all", "--private"]
    if command in ("commit", "merge"):
        argv.insert(1, "x")

    with pytest.raises(SystemExit) as excinfo:
        _run(argv)

    assert excinfo.value.code == 2
    assert "not allowed with argument --all" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["add", "commit", "push", "merge"])
def test_all_is_offered_on_every_command_that_writes_this_projects_history(command):
    """The four commands §2 identified as the real win, and no others.

    ``tag``, ``checkout`` and ``branch`` already reach both halves, so a
    ``--all`` there would be a flag that does nothing — which is the bug the
    ticket's §4.1 is about, not a feature.
    """
    parser = _build_parser()
    args = parser.parse_args([command, "x"] if command in ("commit", "merge") else [command])

    assert hasattr(args, "all_writable")
    assert args.all_writable is False, "the default must not move"


@pytest.mark.parametrize("command", ["tag", "checkout", "branch"])
def test_all_is_not_offered_where_it_would_mean_nothing(capsys, command):
    with pytest.raises(SystemExit) as excinfo:
        _run([command, "x", "--all"])

    assert excinfo.value.code == 2
    assert "unrecognized arguments: --all" in capsys.readouterr().err


def test_commit_all_sends_one_message_to_both_halves(monkeypatch, capsys, tmp_path):
    """One change, one message — the same choice freeze-release already makes."""
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def commit(self, message, *, stage_all, private=False, all_writable=False):
            captured_call["message"] = message
            captured_call["private"] = private
            captured_call["all_writable"] = all_writable

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)
    gts_path = tmp_path / "project.gts"
    gts_path.touch()

    exit_code = _run(["commit", "shared message", "--all", "--gts", str(gts_path)])

    assert exit_code == 0
    assert captured_call["message"] == "shared message"
    assert captured_call["all_writable"] is True
    assert captured_call["private"] is False
    assert "READY ready=true" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("flags", "expected"),
    [([], {"private": False, "all_writable": False}), (["--private"], {"private": True, "all_writable": False})],
)
def test_the_existing_two_forms_reach_the_client_exactly_as_before(
    monkeypatch, tmp_path, flags, expected
):
    """--all is additive: neither existing form may change what it sends."""
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def add(self, paths=None, *, private=False, all_writable=False):
            captured_call["private"] = private
            captured_call["all_writable"] = all_writable

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)
    gts_path = tmp_path / "project.gts"
    gts_path.touch()

    assert _run(["add", *flags, "--gts", str(gts_path)]) == 0
    assert captured_call["private"] is expected["private"]
    assert captured_call["all_writable"] is expected["all_writable"]


# ---------------------------------------------------------------------------
# self-history add
# ---------------------------------------------------------------------------

_SELF_HISTORY_ARGV = [
    "self-history",
    "add",
    "--ticket", "AgentReport",
    "--goal", "Implement WP1.",
    "--action", "Wrote self_history.py and self_history_add.",
    "--worker-role", "Dev",
    "--worker-vendor", "Anthropic",
    "--worker-model", "claude-sonnet-5",
    "--orchestrator-role", "Orchestration",
    "--orchestrator-vendor", "Anthropic",
    "--orchestrator-model", "claude-sonnet-5",
    "--spec-respect-score", "33",
    "--spec-respect-basis", "measured",
    "--spec-respect-reasoning", "lint and test both pass",
    "--gating-score", "33",
    "--gating-basis", "measured",
    "--gating-reasoning", "nothing private pushed",
    "--quality-score", "30",
    "--quality-basis", "asserted",
    "--quality-reasoning", "a reasonable first pass",
]


def test_self_history_add_builds_the_record_and_calls_the_client(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}
    written_path = tmp_path / "recorded.toml"

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def self_history_add(self, cgshome, **kwargs):
            captured_call["cgshome"] = Path(cgshome)
            captured_call["kwargs"] = kwargs
            return written_path

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)
    monkeypatch.setattr(expert, "_resolve_gts_path", lambda *_a, **_k: tmp_path / "project.gts")
    (tmp_path / ".cgitsync").mkdir()

    exit_code = _run([*_SELF_HISTORY_ARGV, "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert f"recorded={written_path}" in captured.out
    kwargs = captured_call["kwargs"]
    assert kwargs["ticket"] == "AgentReport"
    assert kwargs["worker"].role == "Dev"
    assert kwargs["worker"].vendor == "Anthropic"
    assert kwargs["orchestrator"].role == "Orchestration"
    assert kwargs["conformity"].spec_respect.score == 33.0
    assert kwargs["conformity"].spec_respect.basis == "measured"
    assert kwargs["conformity"].quality.basis == "asserted"
    assert kwargs["state_before"] == ""
    assert kwargs["lint_passed"] is None
    assert kwargs["pushed"] is False


def test_self_history_add_forwards_lint_tests_and_pushed_flags(monkeypatch, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def self_history_add(self, cgshome, **kwargs):
            captured_call["kwargs"] = kwargs
            return tmp_path / "r.toml"

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr(_shared, "ComplexGitSyncClient", StubClient)
    monkeypatch.setattr(expert, "_resolve_gts_path", lambda *_a, **_k: tmp_path / "project.gts")
    (tmp_path / ".cgitsync").mkdir()

    _run([
        *_SELF_HISTORY_ARGV,
        "--search-dir", str(tmp_path),
        "--state-before", f"state({'a' * 64})",
        "--state-after", f"state({'b' * 64})",
        "--lint-passed",
        "--tests-failed",
        "--pushed",
        "--pushed-reason", "owner asked",
    ])

    kwargs = captured_call["kwargs"]
    assert kwargs["state_before"] == f"state({'a' * 64})"
    assert kwargs["state_after"] == f"state({'b' * 64})"
    assert kwargs["lint_passed"] is True
    assert kwargs["tests_passed"] is False
    assert kwargs["pushed"] is True
    assert kwargs["pushed_reason"] == "owner asked"


def test_self_history_add_rejects_an_unknown_role():
    argv = [arg if arg != "Dev" else "Manager" for arg in _SELF_HISTORY_ARGV]
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(argv)

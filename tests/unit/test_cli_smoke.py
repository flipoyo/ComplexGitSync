from pathlib import Path
import tomllib
from types import SimpleNamespace

import pytest

from ComplexGitSync import __version__
from ComplexGitSync.cli import main


def test_main_without_command_prints_help(capsys):
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "cgitsync" in captured.out


def test_initialise_command_restores_gts_snapshot(tmp_path, capsys):
    gts_path = _write_ready_gts(tmp_path)

    exit_code = main(["initialise", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "log_file=" in captured.out
    log_file_line = next((line for line in captured.out.splitlines() if line.startswith("log_file=")), None)
    assert log_file_line is not None
    log_file_path = Path(log_file_line.split("=", 1)[1])
    assert log_file_path.is_file()
    assert "workflow=load->validate" in captured.out
    assert "READY" in captured.out
    assert "ready=true" in captured.out
    assert "complete=true" in captured.out
    assert "gittree_created=true" in captured.out
    assert "gittree_active=true" in captured.out
    assert "tree:" in captured.out
    assert "demo (project)" in captured.out


def test_print_command_renders_cgs_summary(tmp_path, capsys):
    config_path = _write_project_cgs(tmp_path)

    exit_code = main(["print", str(config_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "demo" in captured.out


def test_validate_command_renders_lifecycle_state(tmp_path, capsys):
    config_path = _write_project_cgs(tmp_path)

    exit_code = main(["validate", str(config_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "DECLARED" in captured.out


def test_initialise_command_clones_from_cgs(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def initialise_cgs(self, source, *, cgspath=None):
            captured_call["source"] = Path(source)
            captured_call["cgspath"] = cgspath
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "workspace" / "demo")
            )

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

        def format_repo_tree(self):
            return "demo (project)\n└── child-repo (leaf)"

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    config_path = tmp_path / "project.cgs"
    config_path.touch()
    exit_code = main(["initialise", str(config_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["source"] == config_path.resolve()
    assert "workflow=load->expand->validate->clone" in captured.out
    assert "git_command=git clone" in captured.out
    assert "tree:" in captured.out
    assert "demo (project)" in captured.out
    assert "READY ready=true" in captured.out


def test_initialise_command_creates_log_file(monkeypatch, tmp_path, capsys):
    gts_path = _write_ready_gts(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    exit_code = main(["initialise", str(gts_path)])
    captured = capsys.readouterr()

    log_dir = tmp_path / "state-home" / "ComplexGitSync" / "logs"
    log_files = sorted(log_dir.glob("*-initialise.log"))

    assert exit_code == 0
    assert "READY" in captured.out
    assert len(log_files) == 1
    log_content = log_files[0].read_text(encoding="utf-8")
    assert '"event": "command_start"' in log_content
    assert '"event": "command_end"' in log_content


def test_freeze_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def freeze(self, name, **kwargs):
            captured_call["name"] = name

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["freeze", "v1.0", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["name"] == "v1.0"
    assert "name=v1.0" in captured.out


def test_freeze_command_dry_run_skips_mutation(monkeypatch, capsys, tmp_path):
    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def freeze(self, name, **kwargs):
            raise AssertionError("freeze should not be called during --dry-run")

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["freeze", "v1.0", "--gts", str(gts_path), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run=true command=freeze" in captured.out
    assert "plan_actions=git add --all -> git commit -m 'v1.0' -> git tag v1.0 -> git push" in captured.out


def test_describe_command_supports_gts_input(tmp_path, capsys):
    gts_path = tmp_path / "project.gts"
    gts_path.write_text(
        """
[document]
format_version = "1.0"
generated_at = "2026-05-13T00:00:00Z"
command_origin = "test"

[project]
name = "demo"
root_absolute_path = "/tmp/demo"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "/tmp/demo"
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

    exit_code = main(["describe", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"document_kind": "gts"' in captured.out
    assert '"is_ready": true' in captured.out


def test_print_command_supports_gts_input(tmp_path, capsys):
    gts_path = tmp_path / "project.gts"
    gts_path.write_text(
        """
[document]
format_version = "1.0"
generated_at = "2026-05-13T00:00:00Z"
command_origin = "test"

[project]
name = "demo"
root_absolute_path = "/tmp/demo"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "/tmp/demo"
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

    exit_code = main(["print", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"document_kind": "gts"' in captured.out
    assert '"is_ready": true' in captured.out


def test_view_tree_command_supports_gts_and_render_options(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def view_tree(self, *, depth=None, collapse=()):
            captured_call["depth"] = depth
            captured_call["collapse"] = collapse
            return "ROOT demo [main] clean synced"

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(
        ["view_tree", str(gts_path), "--depth", "1", "--collapse", "deps", "--collapse", "plugins"]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["gts_path"] == gts_path.resolve()
    assert captured_call["depth"] == 1
    assert captured_call["collapse"] == ("deps", "plugins")
    assert "ROOT demo [main] clean synced" in captured.out


def test_view_operation_command_supports_cgs_runtime_loading(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_runtime_or_cgs(self, path, *, discover_nested=False):
            captured_call["source"] = Path(path)
            captured_call["discover_nested"] = discover_nested

        def view_operation(self):
            return (
                "REPOSITORY  BRANCH  LOCAL_STATE  SYNC_STATE\n"
                "--------------------------------------------\n"
                "demo        main    clean        synced"
            )

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    cgs_path = tmp_path / "project.cgs"
    cgs_path.touch()
    exit_code = main(["view_operation", str(cgs_path), "--discover-nested"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["source"] == cgs_path.resolve()
    assert captured_call["discover_nested"] is True
    assert "REPOSITORY" in captured.out
    assert "SYNC_STATE" in captured.out


def test_clone_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def resolve_clone_root(self, source, *, target_dir=None, output_dir=None):
            return Path(target_dir)

        def clone_cgs(self, source, *, target_dir=None, output_dir=None):
            captured_call["source"] = Path(source)
            captured_call["target_dir"] = target_dir
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "workspace" / "demo")
            )

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    exit_code = main(["clone", "project.cgs", "--target-dir", str(tmp_path / "workspace" / "demo")])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["source"] == Path("project.cgs").resolve()
    assert captured_call["target_dir"] == str(tmp_path / "workspace" / "demo")
    assert "READY ready=true complete=true" in captured.out


def test_initialise_command_output_dir_is_forwarded(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def initialise_cgs(self, source, *, cgspath=None):
            captured_call["source"] = Path(source)
            captured_call["cgspath"] = cgspath
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "parent" / "demo")
            )

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

        def format_repo_tree(self):
            return "demo (project)\n└── child-repo (leaf)"

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    config_path = tmp_path / "project.cgs"
    config_path.touch()
    output_dir = str(tmp_path / "parent")
    exit_code = main(["initialise", str(config_path), "--output-dir", output_dir])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["cgspath"] == output_dir


def test_clone_command_output_dir_is_forwarded(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def resolve_clone_root(self, source, *, target_dir=None, output_dir=None):
            captured_call["resolve_output_dir"] = output_dir
            return tmp_path / "parent" / "demo"

        def clone_cgs(self, source, *, target_dir=None, output_dir=None):
            captured_call["source"] = Path(source)
            captured_call["output_dir"] = output_dir
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "parent" / "demo")
            )

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    output_dir = str(tmp_path / "parent")
    exit_code = main(["clone", "project.cgs", "--output-dir", output_dir])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["resolve_output_dir"] == output_dir
    assert captured_call["output_dir"] == output_dir


def test_initialise_command_gts_creates_log_file_verbose(monkeypatch, tmp_path, capsys):
    gts_path = _write_ready_gts(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    exit_code = main(["initialise", str(gts_path)])
    captured = capsys.readouterr()

    log_dir = tmp_path / "state-home" / "ComplexGitSync" / "logs"
    log_files = sorted(log_dir.glob("*-initialise.log"))

    assert exit_code == 0
    assert "READY" in captured.out
    assert len(log_files) == 1
    log_content = log_files[0].read_text(encoding="utf-8")
    assert '"event": "command_start"' in log_content
    assert '"event": "command_end"' in log_content


def test_pull_command_creates_log_file(monkeypatch, tmp_path, capsys):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def pull(self, source):
            captured_call["source"] = Path(source)
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "project")
            )

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    source_path = tmp_path / "project.cgs"
    source_path.touch()
    exit_code = main(["pull", str(source_path)])

    log_dir = tmp_path / "state-home" / "ComplexGitSync" / "logs"
    log_files = sorted(log_dir.glob("*-pull.log"))

    assert exit_code == 0
    assert len(log_files) == 1
    log_content = log_files[0].read_text(encoding="utf-8")
    assert '"event": "command_start"' in log_content
    assert '"event": "command_end"' in log_content


def test_unimplemented_command_still_returns_not_implemented(capsys):
    exit_code = main(["status"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "not implemented yet" in captured.out


def test_package_version_is_defined():
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    package_version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert __version__ == package_version


def test_restart_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def restart(self, config_path):
            captured_call["config_path"] = Path(config_path)
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "project")
            )

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    config_path = tmp_path / "project.cgs"
    config_path.touch()
    exit_code = main(["restart", str(config_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["config_path"] == config_path.resolve()
    assert "READY ready=true" in captured.out


def test_pull_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def pull(self, source):
            captured_call["source"] = Path(source)
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "project")
            )

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    source_path = tmp_path / "project.cgs"
    source_path.touch()
    exit_code = main(["pull", str(source_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["source"] == source_path.resolve()
    assert "READY ready=true complete=true" in captured.out


def test_checkout_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def checkout(self, branch, *, ref_kind):
            captured_call["branch"] = branch
            captured_call["ref_kind"] = ref_kind

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["checkout", "feature-x", "--gts", str(gts_path)])
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

        def checkout(self, branch, *, ref_kind):
            captured_call["ref_kind"] = ref_kind

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["checkout", "v1.0.0", "--gts", str(gts_path), "--ref-kind", "tag"])
    assert exit_code == 0
    assert captured_call["ref_kind"] == RefKind.TAG


def test_commit_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def commit(self, message, *, stage_all):
            captured_call["message"] = message
            captured_call["stage_all"] = stage_all

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["commit", "my commit", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["message"] == "my commit"
    assert captured_call["stage_all"] is True
    assert "READY ready=true" in captured.out


def test_commit_command_no_stage_flag(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def commit(self, message, *, stage_all):
            captured_call["stage_all"] = stage_all

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["commit", "msg", "--gts", str(gts_path), "--no-stage"])
    assert exit_code == 0
    assert captured_call["stage_all"] is False


def test_commit_command_dry_run_skips_mutation(monkeypatch, capsys, tmp_path):
    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def commit(self, message, *, stage_all):
            raise AssertionError("commit should not be called during --dry-run")

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["commit", "preview", "--gts", str(gts_path), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run=true command=commit" in captured.out
    assert "plan_actions=git add --all -> git commit -m 'preview'" in captured.out


def test_push_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def push(self):
            captured_call["pushed"] = True

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["push", "--gts", str(gts_path)])
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

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["push", "--gts", str(gts_path), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run=true command=push" in captured.out
    assert "plan_actions=git push" in captured.out


def test_add_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def add(self):
            captured_call["added"] = True

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["add", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call.get("added") is True
    assert "READY ready=true" in captured.out


def test_add_command_dry_run_skips_mutation(monkeypatch, capsys, tmp_path):
    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def add(self):
            raise AssertionError("add should not be called during --dry-run")

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["add", "--gts", str(gts_path), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run=true command=add" in captured.out
    assert "plan_actions=git add --all" in captured.out


def test_tag_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def tag(self, tag_name):
            captured_call["tag_name"] = tag_name

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["tag", "v2.0", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["tag_name"] == "v2.0"
    assert "tag=v2.0" in captured.out


def test_tag_command_dry_run_skips_mutation(monkeypatch, capsys, tmp_path):
    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def tag(self, tag_name):
            raise AssertionError("tag should not be called during --dry-run")

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["tag", "v2.0", "--gts", str(gts_path), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run=true command=tag" in captured.out
    assert "plan_actions=git tag v2.0 -> git push origin v2.0" in captured.out


def test_freeze_release_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def freeze_release(self, tag_name, **kwargs):
            captured_call["tag_name"] = tag_name

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["freeze-release", "v3.0", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["tag_name"] == "v3.0"
    assert "tag=v3.0" in captured.out


def test_launch_release_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def launch_release(self, snapshot_path):
            captured_call["snapshot_path"] = Path(snapshot_path)
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "project")
            )

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["launch-release", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["snapshot_path"] == gts_path.resolve()
    assert "READY ready=true" in captured.out


def test_freeze_state_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def freeze_state(self, state_name, **kwargs):
            captured_call["state_name"] = state_name

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["freeze-state", "dev-state", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["state_name"] == "dev-state"
    assert "state=dev-state" in captured.out


def test_launch_state_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def launch_state(self, snapshot_path):
            captured_call["snapshot_path"] = Path(snapshot_path)
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "project")
            )

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["launch-state", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["snapshot_path"] == gts_path.resolve()
    assert "READY ready=true" in captured.out


def test_gts_auto_discovery_from_parent_cgitsync(monkeypatch, capsys, tmp_path):
    """Commands with no --gts discover the snapshot from ../.cgitsync/state/."""
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def add(self):
            captured_call["added"] = True

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    # Simulate running from a tool subdir: cwd is tmp_path/ComplexGitSync
    # and the .gts lives in tmp_path/.cgitsync/state/
    cwd = tmp_path / "ComplexGitSync"
    cwd.mkdir()
    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "project.gts"
    gts_path.touch()

    monkeypatch.chdir(cwd)
    exit_code = main(["add"])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call.get("added") is True
    assert captured_call["gts_path"] == gts_path.resolve()


def test_gts_auto_discovery_walks_up_to_workspace_root(monkeypatch, capsys, tmp_path):
    """Commands walk up ancestors until CGSHOME/.cgitsync is found."""
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def add(self):
            captured_call["added"] = True

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "project.gts"
    gts_path.touch()

    cwd = tmp_path / "tools" / "nested" / "ComplexGitSync"
    cwd.mkdir(parents=True)

    monkeypatch.chdir(cwd)
    exit_code = main(["add"])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call.get("added") is True
    assert captured_call["gts_path"] == gts_path.resolve()


def test_gts_auto_discovery_uses_cgshome_env(monkeypatch, capsys, tmp_path):
    """$CGSHOME is used before cwd-based discovery."""
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def push(self):
            captured_call["pushed"] = True

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    workspace = tmp_path / "workspace"
    state_dir = workspace / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "workspace.gts"
    gts_path.touch()

    unrelated_cwd = tmp_path / "unrelated" / "tooling"
    unrelated_cwd.mkdir(parents=True)

    monkeypatch.setenv("CGSHOME", str(workspace))
    monkeypatch.chdir(unrelated_cwd)
    exit_code = main(["push"])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call.get("pushed") is True
    assert captured_call["gts_path"] == gts_path.resolve()


def test_gts_auto_discovery_search_dir_option(monkeypatch, capsys, tmp_path):
    """--search-dir takes precedence over $CGSHOME and cwd discovery."""
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def push(self):
            captured_call["pushed"] = True

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    custom_root = tmp_path / "myproject"
    state_dir = custom_root / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "myproject.gts"
    gts_path.touch()

    env_root = tmp_path / "env-workspace"
    env_state_dir = env_root / ".cgitsync" / "state"
    env_state_dir.mkdir(parents=True)
    (env_state_dir / "env.gts").touch()

    monkeypatch.setenv("CGSHOME", str(env_root))
    exit_code = main(["push", "--search-dir", str(custom_root)])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call.get("pushed") is True
    assert captured_call["gts_path"] == gts_path.resolve()


def test_gts_auto_discovery_no_snapshot_raises_error(monkeypatch, tmp_path):
    """When CGSHOME cannot be found, auto-discovery raises FileNotFoundError."""
    from ComplexGitSync.cli import _discover_gts_path

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    monkeypatch.delenv("CGSHOME", raising=False)
    monkeypatch.chdir(empty_dir)

    with pytest.raises(FileNotFoundError, match=r"Unable to locate CGSHOME"):
        _discover_gts_path(search_dir=empty_dir)


def test_gts_auto_discovery_no_snapshot_under_cgshome_raises_error(monkeypatch, tmp_path):
    """When CGSHOME exists but has no snapshots, auto-discovery raises FileNotFoundError."""
    from ComplexGitSync.cli import _discover_gts_path

    workspace = tmp_path / "workspace"
    (workspace / ".cgitsync" / "state").mkdir(parents=True)

    monkeypatch.setenv("CGSHOME", str(workspace))
    with pytest.raises(FileNotFoundError, match=r"No .gts snapshot found in CGSHOME/.cgitsync/state"):
        _discover_gts_path()


def test_gts_auto_discovery_picks_most_recent(tmp_path):
    """_discover_gts_path returns the most recently modified .gts."""
    import os
    from ComplexGitSync.cli import _discover_gts_path

    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    old_gts = state_dir / "old.gts"
    new_gts = state_dir / "new.gts"
    old_gts.touch()
    new_gts.touch()
    # Explicitly set different modification times so the test is deterministic
    os.utime(old_gts, (1000.0, 1000.0))
    os.utime(new_gts, (2000.0, 2000.0))

    result = _discover_gts_path(search_dir=tmp_path)
    assert result == new_gts.resolve()


def test_checkout_command_auto_discovers_gts(monkeypatch, capsys, tmp_path):
    """checkout resolves its READY snapshot from CGSHOME when --gts is omitted."""
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def checkout(self, branch, *, ref_kind):
            captured_call["branch"] = branch
            captured_call["ref_kind"] = ref_kind

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    workspace = tmp_path / "workspace"
    state_dir = workspace / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "workspace.gts"
    gts_path.touch()

    monkeypatch.setenv("CGSHOME", str(workspace))
    exit_code = main(["checkout", "feature/demo"])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["gts_path"] == gts_path.resolve()
    assert captured_call["branch"] == "feature/demo"


def test_pull_command_auto_discovers_gts(monkeypatch, capsys, tmp_path):
    """pull uses the workspace snapshot when no explicit source is provided."""
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def pull(self, source):
            captured_call["source"] = Path(source)
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "workspace" / "demo")
            )

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    workspace = tmp_path / "workspace"
    state_dir = workspace / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "workspace.gts"
    gts_path.touch()

    monkeypatch.setenv("CGSHOME", str(workspace))
    exit_code = main(["pull"])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["source"] == gts_path.resolve()


def test_view_tree_auto_discovery(monkeypatch, capsys, tmp_path):
    """view-tree with no source argument auto-discovers the .gts snapshot."""
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def view_tree(self, *, depth=None, collapse=()):
            return "ROOT demo [main] clean synced"

    monkeypatch.setattr("ComplexGitSync.cli.ComplexGitSyncClient", StubClient)

    cwd = tmp_path / "ComplexGitSync"
    cwd.mkdir()
    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "project.gts"
    gts_path.touch()

    monkeypatch.chdir(cwd)
    exit_code = main(["view-tree"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["gts_path"] == gts_path.resolve()
    assert "ROOT demo [main] clean synced" in captured.out


def test_registry_command_is_removed(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["registry", "project.cgs"])
    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert "invalid choice" in captured.err


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

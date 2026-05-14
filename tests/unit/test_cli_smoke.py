from pathlib import Path
import tomllib
from types import SimpleNamespace

from ComplexGitSync import __version__
from ComplexGitSync.cli import main
from ComplexGitSync.orchestre import RuntimeStateStore


def test_main_without_command_prints_help(capsys):
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "cgitsync" in captured.out


def test_validate_command_summarizes_loaded_registry(tmp_path, capsys):
    config_path = _write_project_cgs(tmp_path)

    exit_code = main(["validate", str(config_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "DECLARED" in captured.out
    assert "complete=true" in captured.out


def test_tree_command_renders_declared_dependency_tree(tmp_path, capsys):
    config_path = _write_project_cgs(tmp_path)

    exit_code = main(["tree", str(config_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "demo" in captured.out
    assert "child-repo" in captured.out


def test_tree_command_prefers_latest_runtime_snapshot_for_cgs(monkeypatch, tmp_path, capsys):
    config_path = _write_project_cgs(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    snapshot_path = tmp_path / "workspace" / "demo" / ".cgitsync" / "state" / "project.gts"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        (
            """
[document]
format_version = "1.0"
generated_at = "2026-05-13T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "/tmp/runtime-demo"
source_cgs_path = "{source_path}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "/tmp/runtime-demo"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "autoTest"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "abc123"
""".strip()
            .format(source_path=str(config_path.resolve()))
            + "\n"
        ),
        encoding="utf-8",
    )
    RuntimeStateStore().record_snapshot(config_path, snapshot_path)

    exit_code = main(["tree", str(config_path)])
    captured = capsys.readouterr()

    expected_path = str(Path("/tmp/runtime-demo").resolve())
    assert exit_code == 0
    assert "state=READY" in captured.out
    assert f"path={expected_path}" in captured.out


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


def test_clone_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        def resolve_clone_root(self, source, *, target_dir=None):
            return Path(target_dir)

        def clone_cgs(self, source, *, target_dir=None):
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


def test_validate_command_creates_log_file(monkeypatch, tmp_path, capsys):
    config_path = _write_project_cgs(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    exit_code = main(["validate", str(config_path)])
    captured = capsys.readouterr()

    log_dir = tmp_path / "state-home" / "ComplexGitSync" / "logs"
    log_files = sorted(log_dir.glob("*-validate.log"))

    assert exit_code == 0
    assert "DECLARED" in captured.out
    assert len(log_files) == 1
    log_content = log_files[0].read_text(encoding="utf-8")
    assert '"event": "command_start"' in log_content
    assert '"event": "command_end"' in log_content


def test_unimplemented_command_still_returns_not_implemented(capsys):
    exit_code = main(["checkout"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "not implemented yet" in captured.out


def test_package_version_is_defined():
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    package_version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert __version__ == package_version


def _write_project_cgs(tmp_path):
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
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
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path

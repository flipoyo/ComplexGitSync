from pathlib import Path
import tomllib

from ComplexGitSync import __version__
from ComplexGitSync.cli import main


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

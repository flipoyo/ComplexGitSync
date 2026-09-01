import re
import socket
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from ComplexGitSync import __version__
from ComplexGitSync.cgs_format import CgsDocument
from ComplexGitSync.cli import main


def test_main_without_command_prints_help(capsys):
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "cgitsync" in captured.out


def test_configure_help_lists_all_canonical_providers(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["configure", "--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    for provider in ("GitHub", "GitLab", "Codeberg", "custom"):
        assert provider in captured.out


@pytest.mark.parametrize("command", ["initialise", "create-cgs"])
def test_cli_project_definition_help_documents_repeatable_repos(command, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main([command, "--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "--project" in captured.out
    assert "--repo" in captured.out
    assert "repeat" in captured.out


@pytest.mark.parametrize("command", ["initialise", "clean-init", "pull"])
def test_gitignore_sync_flags_documented_on_relevant_commands(command, capsys):
    """DevPlanTicket Milestone 2 (M2.0): flags registered on exactly these three."""
    with pytest.raises(SystemExit) as exc_info:
        main([command, "--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "--commit-gitignore" in captured.out
    assert "--force-gitignore-sync" in captured.out
    assert "--git-user-name" in captured.out
    assert "--git-user-email" in captured.out


def test_gitignore_sync_flags_rejected_on_unrelated_command(capsys):
    """A global flag would silently no-op on view-tree; it must instead error."""
    with pytest.raises(SystemExit) as exc_info:
        main(["view-tree", "--commit-gitignore"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "unrecognized arguments" in captured.err


def test_initialise_command_restores_gts_snapshot(tmp_path, capsys):
    gts_path = _write_ready_gts(tmp_path)

    exit_code = main(["initialise", str(gts_path)])
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


def test_load_command_is_not_registered(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["load", "1"])
    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert "invalid choice" in captured.err


def test_load_ledger_id_loading_stays_available_via_client(tmp_path):
    snapshot_path = tmp_path / ".cgitsync" / "state" / "gts-000001.gts"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text("[document]\nformat_version = \"1.0\"\n", encoding="utf-8")
    assert snapshot_path.exists()


def test_print_command_is_not_registered(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["print", "project.cgs"])
    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert "invalid choice" in captured.err


def test_validate_command_renders_lifecycle_state(tmp_path, capsys):
    config_path = _write_project_cgs(tmp_path)

    exit_code = main(["validate", str(config_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "DECLARED" in captured.out


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

    monkeypatch.setattr("ComplexGitSync.cli.minimalist.ComplexGitSyncClient", StubClient)

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

    monkeypatch.setattr("ComplexGitSync.cli.minimalist.ComplexGitSyncClient", StubClient)
    monkeypatch.setattr("ComplexGitSync.cli.minimalist._run_with_logging", _run_without_logging)
    monkeypatch.chdir(tmp_path)

    argv = ["initialise", "--project", "CGSil1"]
    for repository in repositories:
        argv.extend(["--repo", repository])

    exit_code = main(argv)
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


def test_create_cgs_writes_equivalent_validated_document(
    monkeypatch, capsys, tmp_path
):
    def _forbid_runtime_access(*_args, **_kwargs):
        raise AssertionError("create-cgs attempted Git or network access")

    monkeypatch.setattr(subprocess, "run", _forbid_runtime_access)
    monkeypatch.setattr(socket, "create_connection", _forbid_runtime_access)

    output = tmp_path / "CGSil1.cgs"
    repositories = [
        "github:flipoyo/ComplexGitSync",
        "codeberg:GX4G/GX4G",
    ]
    exit_code = main(
        [
            "create-cgs",
            "--project",
            "CGSil1",
            "--repo",
            repositories[0],
            "--repo",
            repositories[1],
            "--output",
            str(output),
        ]
    )
    captured = capsys.readouterr()

    generated = CgsDocument.from_toml(output)
    equivalent_source = tmp_path / "equivalent.cgs"
    equivalent_source.write_text(
        'project = "CGSil1"\n\n'
        'repos = [\n'
        '    "github:flipoyo/ComplexGitSync",\n'
        '    "codeberg:GX4G/GX4G",\n'
        ']\n',
        encoding="utf-8",
    )
    equivalent = CgsDocument.from_toml(equivalent_source)
    assert exit_code == 0
    assert generated.to_dict() == equivalent.to_dict()
    assert "codeberg:GX4G/GX4G" in output.read_text(encoding="utf-8")
    assert f".cgs file written to: {output.resolve()}" in captured.out


def test_create_cgs_delegates_to_public_python_configuration_api(
    monkeypatch, tmp_path
):
    captured_call: dict[str, object] = {}

    class StubClient:
        def configure(self, project, repositories, *, output_path=None):
            captured_call["project"] = project
            captured_call["repositories"] = list(repositories)
            captured_call["output_path"] = output_path
            document = CgsDocument.from_dict(
                {"project": project, "repos": list(repositories)}
            )
            document.to_toml(output_path)
            return document

    monkeypatch.setattr("ComplexGitSync.cli.configuration.ComplexGitSyncClient", StubClient)
    output = tmp_path / "GX4G.cgs"

    exit_code = main(
        [
            "create-cgs",
            "--project",
            "GX4G",
            "--repo",
            "codeberg:GX4G/GX4G",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert captured_call == {
        "project": "GX4G",
        "repositories": ["codeberg:GX4G/GX4G"],
        "output_path": output,
    }
    assert CgsDocument.from_toml(output).project_name == "GX4G"


def test_configure_collects_input_then_writes_validated_cgs(
    monkeypatch, capsys, tmp_path
):
    responses = iter(
        [
            "demo",
            "main",
            "owner",
            "",
            "",
            "1",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    output = tmp_path / "demo.cgs"

    exit_code = main(["configure", "--output", str(output)])

    document = CgsDocument.from_toml(output)
    assert exit_code == 0
    assert document.project_name == "demo"
    assert document.repos[0]["gitprovider"] == "github"
    assert document.to_authoring_dict() == {
        "project": "demo",
        "repos": ["github:owner/demo"],
    }
    assert "[project]" not in output.read_text(encoding="utf-8")
    assert f".cgs file written to: {output.resolve()}" in capsys.readouterr().out


def test_configure_collects_codeberg_as_first_class_provider(monkeypatch, tmp_path):
    responses = iter(
        [
            "GX4G",
            "main",
            "GX4G",
            "codeberg",
            "ssh",
            "1",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    output = tmp_path / "GX4G.cgs"

    assert main(["configure", "--output", str(output)]) == 0

    document = CgsDocument.from_toml(output)
    assert document.to_dict() == CgsDocument.from_project_definition(
        "GX4G", ["codeberg:GX4G/GX4G"]
    ).to_dict()
    assert "codeberg:GX4G/GX4G" in output.read_text(encoding="utf-8")


def test_file_and_cli_codeberg_authoring_are_semantically_equivalent(tmp_path):
    source = tmp_path / "GX4G.cgs"
    source.write_text(
        'project = "GX4G"\n\nrepos = [\n    "codeberg:GX4G/GX4G",\n]\n',
        encoding="utf-8",
    )

    from_file = CgsDocument.from_toml(source)
    from_cli = CgsDocument.from_project_definition(
        "GX4G", ["codeberg:GX4G/GX4G"]
    )

    assert from_file.to_dict() == from_cli.to_dict()


def test_initialise_command_failure_suggests_clean_init(monkeypatch, capsys, tmp_path):
    class StubClient:
        def resolve_initialise_cgshome(self, source, *, output_path=None):
            return tmp_path / "workspace" / "project"

        def initialise_cgs(self, source, *, output_path=None, **_kwargs):
            raise RuntimeError("clone failed")

    monkeypatch.setattr("ComplexGitSync.cli.minimalist.ComplexGitSyncClient", StubClient)

    config_path = tmp_path / "project.cgs"
    config_path.touch()
    with pytest.raises(RuntimeError, match="clone failed"):
        main(["initialise", str(config_path)])

    captured = capsys.readouterr()
    assert "Try clean-init method" in captured.err


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

    monkeypatch.setattr("ComplexGitSync.cli.minimalist.ComplexGitSyncClient", StubClient)

    config_path = tmp_path / "project.cgs"
    config_path.touch()
    output_path = str(tmp_path / "parent")
    exit_code = main(["clean-init", str(config_path), "--output-path", output_path])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["source"] == config_path.resolve()
    assert captured_call["output_path"] == output_path
    assert "operation_sequence=GT-LOAD->GT-DISCOVER->GT-VALIDATE->FS-PURGE->GT-CLONE" in captured.out
    assert "workflow=load->expand->validate->purge->clone" in captured.out
    assert "READY ready=true" in captured.out


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

    monkeypatch.setattr("ComplexGitSync.cli.expert.ComplexGitSyncClient", StubClient)

    config_path = tmp_path / "project.cgs"
    config_path.touch()
    output_path = str(tmp_path / "parent")
    exit_code = main(["purge", str(config_path), "--output-path", output_path])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["source"] == config_path.resolve()
    assert captured_call["output_path"] == output_path
    assert "operation_sequence=GT-LOAD->GT-DISCOVER->GT-VALIDATE->FS-PURGE" in captured.out
    assert "workflow=load->expand->validate->purge" in captured.out
    assert str(removed[0]) in captured.out
    assert str(removed[1]) in captured.out


def test_initialise_command_requires_source_or_project(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["initialise"])

    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert "requires SOURCE or --project" in captured.err


def test_initialise_cli_definition_requires_project(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["initialise", "--repo", "github:owner/repository"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "requires SOURCE or --project" in captured.err


def test_initialise_cli_definition_requires_repo(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["initialise", "--project", "demo"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "requires at least one --repo" in captured.err


def test_initialise_rejects_source_and_cli_definition(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(
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
    assert "SOURCE or --project with --repo, not both" in captured.err


@pytest.mark.parametrize(
    "argv, missing_option",
    [
        (["create-cgs", "--repo", "github:owner/repository", "--output", "p.cgs"], "--project"),
        (["create-cgs", "--project", "demo", "--output", "p.cgs"], "--repo"),
    ],
)
def test_create_cgs_requires_project_and_repo(argv, missing_option, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(argv)

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert missing_option in captured.err


def test_validate_command_creates_state_local_log_file(monkeypatch, tmp_path, capsys):
    config_path = _write_project_cgs(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    exit_code = main(["validate", str(config_path)])
    captured = capsys.readouterr()

    log_dir = tmp_path / "state-home" / "ComplexGitSync" / "logs"
    log_file_line = next((line for line in captured.out.splitlines() if line.startswith("log_file=")), None)
    assert log_file_line is not None
    log_file = Path(log_file_line.split("=", 1)[1])

    assert exit_code == 0
    assert "DECLARED" in captured.out
    assert not log_dir.exists()
    assert log_file.is_file()
    assert re.fullmatch(r"state\([0-9a-f]{64}\)_0", log_file.parent.name)
    assert log_file.parent.parent == tmp_path / ".cgitsync"
    log_content = log_file.read_text(encoding="utf-8")
    assert log_content.splitlines()[0].startswith('{"operation": "GT-VALIDATE", "event": "command_start"')
    assert '"event": "command_start"' in log_content
    assert '"event": "command_end"' in log_content


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
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["freeze", "v1.0", "--gts", str(gts_path)])
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
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["freeze", "v1.0", "--gts", str(gts_path), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run=true command=freeze" in captured.out
    assert "plan_actions=git add --all -> git commit -m 'v1.0' -> git tag v1.0 -> git push" in captured.out


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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["freeze-release", "v1.0", "release commit", "--gts", str(gts_path)])
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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["freeze-release-force", "v1.0", "release commit", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["force"] is True
    assert "git clean -fd" in captured.out


def test_tag_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def tag(self, name):
            captured_call["name"] = name

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

        def view_tree(self):
            return "ROOT project [main] clean synced"

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["tag", "v1.0", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["name"] == "v1.0"
    assert "name=v1.0" in captured.out


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

    with pytest.raises(SystemExit) as exc_info:
        main(["print", str(gts_path)])
    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert "invalid choice" in captured.err


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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(
        ["view-tree", str(gts_path), "--depth", "1", "--collapse", "deps", "--collapse", "plugins"]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["gts_path"] == gts_path.resolve()
    assert captured_call["depth"] == 1
    assert captured_call["collapse"] == ("deps", "plugins")
    assert "demo (root) [ALIGNED] @abc1234" in captured.out


def test_view_tree_underscore_command_is_not_registered():
    with pytest.raises(SystemExit) as exc_info:
        main(["view_tree"])

    assert exc_info.value.code == 2


def test_view_operation_command_is_not_registered(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["view-operation", "project.cgs"])
    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert "invalid choice" in captured.err


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

    monkeypatch.setattr("ComplexGitSync.cli.expert.ComplexGitSyncClient", StubClient)

    target_dir = str(tmp_path / "workspace" / "demo")
    exit_code = main(["clone", "project.cgs", "--target-dir", target_dir])
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
            captured_call["source"] = Path(source)
            captured_call["output_path"] = output_path
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "parent" / "demo")
            )

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli.expert.ComplexGitSyncClient", StubClient)

    output_path = str(tmp_path / "parent")
    exit_code = main(["clone", "project.cgs", "--output-path", output_path])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["resolve_output_path"] == output_path
    assert captured_call["output_path"] == output_path


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

    monkeypatch.setattr("ComplexGitSync.cli.minimalist.ComplexGitSyncClient", StubClient)

    exit_code = main(["bootstrap", "project.cgs", "myproject"])
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

    monkeypatch.setattr("ComplexGitSync.cli.minimalist.ComplexGitSyncClient", StubClient)

    cgs_path = str(tmp_path / "custom")
    exit_code = main(["bootstrap", "project.cgs", "myproject", "--cgs-path", cgs_path])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["resolve_cgs_path"] == cgs_path
    assert captured_call["cgs_path"] == cgs_path


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

    monkeypatch.setattr("ComplexGitSync.cli.minimalist.ComplexGitSyncClient", StubClient)

    config_path = tmp_path / "project.cgs"
    config_path.touch()
    output_path = str(tmp_path / "parent")
    exit_code = main(["initialise", str(config_path), "--output-path", output_path])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["output_path"] == output_path


def test_initialise_command_gts_does_not_write_external_log_file(monkeypatch, tmp_path, capsys):
    gts_path = _write_ready_gts(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    exit_code = main(["initialise", str(gts_path)])
    captured = capsys.readouterr()

    log_dir = tmp_path / "state-home" / "ComplexGitSync" / "logs"

    assert exit_code == 0
    assert "READY" in captured.out
    assert "operation_sequence=GT-LOAD->GT-VALIDATE" in captured.out
    assert "log_file=" not in captured.out
    assert not log_dir.exists()


def test_pull_command_creates_log_file(monkeypatch, tmp_path, capsys):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def pull(self, source, **_kwargs):
            captured_call["source"] = Path(source)
            self.run_logger.bind_log_file(
                tmp_path
                / "project"
                / ".cgitsync"
                / f"state({'a' * 64})_0"
                / "project.log"
            )
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "project")
            )

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    source_path = tmp_path / "project.cgs"
    source_path.touch()
    exit_code = main(["pull", str(source_path)])
    captured = capsys.readouterr()

    log_dir = tmp_path / "state-home" / "ComplexGitSync" / "logs"
    log_file_line = next((line for line in captured.out.splitlines() if line.startswith("log_file=")), None)
    assert log_file_line is not None
    log_file = Path(log_file_line.split("=", 1)[1])

    assert exit_code == 0
    assert not log_dir.exists()
    assert log_file.is_file()
    assert re.fullmatch(r"state\([0-9a-f]{64}\)_0", log_file.parent.name)
    log_content = log_file.read_text(encoding="utf-8")
    assert '"event": "command_start"' in log_content
    assert '"event": "command_end"' in log_content


def test_pull_command_failure_suggests_pull_force(monkeypatch, tmp_path, capsys):
    class StubClient:
        run_logger = None

        def pull(self, source, **_kwargs):
            raise RuntimeError("local changes would be overwritten")

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    source_path = tmp_path / "project.gts"
    source_path.touch()
    with pytest.raises(RuntimeError, match="local changes"):
        main(["pull", str(source_path)])

    captured = capsys.readouterr()
    assert "You can try cgitsync pull-force command" in captured.err


def test_pull_force_command_uses_client_handler(monkeypatch, tmp_path, capsys):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def pull_force(self, source):
            captured_call["source"] = Path(source)
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "project")
            )

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    source_path = tmp_path / "project.gts"
    source_path.touch()
    exit_code = main(["pull-force", str(source_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["source"] == source_path.resolve()
    assert "git_command=git fetch" in captured.out
    assert "READY ready=true" in captured.out


def test_status_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def status(self):
            return "summary ready=true complete=true repos=1 dirty=0 staged=0 ahead=0 behind=0 errors=0"

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["status", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["gts_path"] == gts_path.resolve()
    assert "summary ready=true complete=true repos=1" in captured.out
    assert "READY ready=true" in captured.out


def test_package_version_is_defined():
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    package_version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert __version__ == package_version


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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["checkout", "feature-x", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["branch"] == "feature-x"
    assert "git_command=git checkout feature-x" in captured.out
    assert "READY ready=true" in captured.out
    assert "branch=feature-x" in captured.out


def test_branch_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def branch(self, branch):
            captured_call["branch"] = branch

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["branch", "feature-x", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured_call["gts_path"] == gts_path.resolve()
    assert captured_call["branch"] == "feature-x"
    assert "git_command=git branch feature-x" in captured.out
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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["commit", "my commit", "--gts", str(gts_path)])
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

        def commit(self, message, *, stage_all):
            captured_call["message"] = message
            captured_call["stage_all"] = stage_all

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["commit", "-m", "my commit", "--gts", str(gts_path)])

    assert exit_code == 0
    assert captured_call == {"message": "my commit", "stage_all": True}


def test_commit_command_rejects_duplicate_messages(capsys, tmp_path):
    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["commit", "positional", "-m", "option", "--gts", str(gts_path)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "provide exactly one message" in captured.err


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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["push", "--gts", str(gts_path), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run=true command=push" in captured.out
    assert "plan_actions=git push -> git push -u origin <branch> when upstream is missing" in captured.out


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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["add", "--gts", str(gts_path), "--dry-run"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dry_run=true command=add" in captured.out
    assert "plan_actions=git add --all" in captured.out


def test_launch_release_command_uses_client_handler(monkeypatch, capsys, tmp_path):
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def launch_release(self, release_name):
            captured_call["release_name"] = release_name

        def get_tree_state(self):
            return SimpleNamespace(lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True)

        def view_tree(self):
            return "ROOT project [main] clean synced"

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    gts_path = tmp_path / "project.gts"
    gts_path.touch()
    exit_code = main(["launch-release", "v2.0", "--gts", str(gts_path)])
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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    workspace = tmp_path / "workspace"
    state_dir = workspace / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "workspace.gts"
    gts_path.touch()

    monkeypatch.setenv("CGSHOME", str(workspace))
    exit_code = main(["launch-release", "v2.0"])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["gts_path"] == gts_path.resolve()
    assert captured_call["release_name"] == "v2.0"


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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

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
    from ComplexGitSync.snapshot_resolver import discover_gts_path as _discover_gts_path

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    monkeypatch.delenv("CGSHOME", raising=False)
    monkeypatch.chdir(empty_dir)

    with pytest.raises(FileNotFoundError, match=r"Unable to locate CGSHOME"):
        _discover_gts_path(search_dir=empty_dir)


def test_gts_auto_discovery_no_snapshot_under_cgshome_raises_error(monkeypatch, tmp_path):
    """When CGSHOME exists but has no snapshots, auto-discovery raises FileNotFoundError."""
    from ComplexGitSync.snapshot_resolver import discover_gts_path as _discover_gts_path

    workspace = tmp_path / "workspace"
    (workspace / ".cgitsync" / "state").mkdir(parents=True)

    monkeypatch.setenv("CGSHOME", str(workspace))
    with pytest.raises(FileNotFoundError, match=r"No .gts snapshot found under CGSHOME/.cgitsync"):
        _discover_gts_path()


def test_gts_auto_discovery_falls_back_to_most_recent(tmp_path):
    """_discover_gts_path returns the most recently modified .gts without a .lgr."""
    import os

    from ComplexGitSync.snapshot_resolver import discover_gts_path as _discover_gts_path

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


def test_gts_auto_discovery_falls_back_to_canonical_state_dirs(tmp_path):
    import os

    from ComplexGitSync.snapshot_resolver import discover_gts_path as _discover_gts_path

    old_state = tmp_path / ".cgitsync" / ("state(" + "a" * 64 + ")_0")
    new_state = tmp_path / ".cgitsync" / ("state(" + "b" * 64 + ")_1")
    old_state.mkdir(parents=True)
    new_state.mkdir(parents=True)
    old_gts = old_state / "project.gts"
    new_gts = new_state / "project.gts"
    old_gts.touch()
    new_gts.touch()
    os.utime(old_gts, (1000.0, 1000.0))
    os.utime(new_gts, (2000.0, 2000.0))

    result = _discover_gts_path(search_dir=tmp_path)
    assert result == new_gts.resolve()


def test_gts_auto_discovery_prefers_lgr_current_snapshot(tmp_path):
    import os

    from ComplexGitSync.snapshot_resolver import discover_gts_path as _discover_gts_path

    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    old_gts = state_dir / "gts-000001.gts"
    current_gts = state_dir / "gts-000002.gts"
    old_gts.touch()
    current_gts.touch()
    os.utime(old_gts, (2000.0, 2000.0))
    os.utime(current_gts, (1000.0, 1000.0))
    (tmp_path / "demo.lgr").write_text(
        f"""
[register]
current_snapshot_path = "{current_gts.as_posix()}"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = _discover_gts_path(search_dir=tmp_path)
    assert result == current_gts.resolve()


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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

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


def test_branch_command_auto_discovers_gts(monkeypatch, capsys, tmp_path):
    """branch resolves its READY snapshot from CGSHOME when --gts is omitted."""
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            captured_call["gts_path"] = Path(path)

        def branch(self, branch):
            captured_call["branch"] = branch

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    workspace = tmp_path / "workspace"
    state_dir = workspace / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "workspace.gts"
    gts_path.touch()

    monkeypatch.setenv("CGSHOME", str(workspace))
    exit_code = main(["branch", "feature/demo"])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["gts_path"] == gts_path.resolve()
    assert captured_call["branch"] == "feature/demo"


def test_pull_command_auto_discovers_gts(monkeypatch, capsys, tmp_path):
    """pull uses the workspace snapshot when no explicit source is provided."""
    captured_call: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def pull(self, source, **_kwargs):
            captured_call["source"] = Path(source)
            return SimpleNamespace(
                get=lambda repo_id: SimpleNamespace(absolute_path=tmp_path / "workspace" / "demo")
            )

        def get_tree_state(self):
            return SimpleNamespace(
                lifecycle_state=SimpleNamespace(value="READY"), is_ready=True, registry_complete=True
            )

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

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
            return "demo (root) [ALIGNED] @abc1234"

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

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
    assert "demo (root) [ALIGNED] @abc1234" in captured.out


@pytest.mark.parametrize(
    "command",
    [
        "describe",
        "freeze-state",
        "launch-state",
        "load",
        "print",
        "registry",
        "restart",
        "tree",
        "expand",
        "validate-topology",
        "view-operation",
        "view_operation",
        "launch_release",
        "write-gts",
    ],
)
def test_removed_commands_are_not_registered(command, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main([command, "project.cgs"])
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


def test_readme_documents_every_cli_command():
    """README.md's command reference must list every _PLANNED_COMMANDS entry.

    Guards against the CLI surface (cli.py) and the user-facing docs (README.md)
    drifting apart, as happened previously when configure/remember/memorize/
    retrieve/reload were added to cli.py but never documented.
    """
    from ComplexGitSync.cli import _PLANNED_COMMANDS

    readme_path = Path(__file__).resolve().parents[2] / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8")

    missing = [command for command in _PLANNED_COMMANDS if f"`{command}`" not in readme_text]
    assert not missing, f"README.md command reference is missing: {missing}"


def test_readme_command_reference_lists_only_real_commands():
    """The reverse direction of the check above.

    A command documented in README.md's ``## Command reference`` table that
    the parser cannot actually build is as much a drift bug as an
    undocumented command — build_parser() only ever creates a subparser for
    a name that is a _PLANNED_COMMANDS key (see
    test_parser_choices_match_planned_commands), so a phantom row here would
    silently promise a command that errors with "invalid choice".
    """
    from ComplexGitSync.cli import _PLANNED_COMMANDS

    readme_path = Path(__file__).resolve().parents[2] / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8")

    table_match = re.search(r"## Command reference\n\n(.*?)\n\n##", readme_text, re.DOTALL)
    assert table_match, "README.md's '## Command reference' table was not found."
    documented = re.findall(r"^\| \S[^|]*\| `([a-z][a-z-]*)` \|", table_match.group(1), re.MULTILINE)
    assert documented, "No command rows parsed from README.md's Command reference table."

    phantom = [command for command in documented if command not in _PLANNED_COMMANDS]
    assert not phantom, f"README.md documents commands the CLI cannot build: {phantom}"


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
                    SimpleNamespace(
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
            )

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    exit_code = main(["discover", str(tmp_path)])
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

    monkeypatch.setattr("ComplexGitSync.cli._shared.ComplexGitSyncClient", StubClient)

    out = str(tmp_path / "draft.cgs")
    exit_code = main(["discover", str(tmp_path), "--write", out, "--max-depth", "2"])
    capsys.readouterr()

    assert exit_code == 0
    assert captured_call["max_depth"] == 2
    assert captured_call["output"] == out

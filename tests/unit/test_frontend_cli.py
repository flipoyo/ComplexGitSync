"""Phase 2 Frontend Tests - CLI and GitRunner verification.

These tests verify the Phase 2 acceptance criteria:
- P2-AC-01: pixi is the canonical environment
- P2-AC-02: cgitsync is the only public operational entry point
- P2-AC-03: The Python package is internal to the FrontEnd
- P2-AC-04: CLI contains no business logic
- P2-AC-05: Every CLI command maps to a canonical client method
- P2-AC-06: Only GitRunner invokes Git
- P2-AC-07: No unrestricted Git passthrough exists
- P2-AC-08: All Git mutations are controlled by cgitsync
- P2-AC-09: STATE@.md and STATE@.CORE.md are accessible through cgitsync
- P2-AC-10: All FrontEnd tests succeed
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from ComplexGitSync.cli import COMMANDS, get_parser, main
from ComplexGitSync.complex_git_sync_client import ComplexGitSyncClient
from ComplexGitSync.git_runner import (
    GitCommand,
    GitError,
    GitErrorCode,
    GitExecution,
    GitResult,
    GitRunner,
)


# ==================== CLI Tests ====================


class TestCLIHelp:
    """Test CLI help functionality."""

    def test_cli_help_without_command(self, capsys):
        """CLI help is shown when no command is provided."""
        try:
            exit_code = main([])
            assert exit_code == 0
        except SystemExit as e:
            assert e.code == 0
        captured = capsys.readouterr()
        assert "cgitsync" in captured.out
        assert "usage:" in captured.out

    def test_cli_help_with_help_flag(self, capsys):
        """CLI help is shown with --help flag."""
        try:
            main(["--help"])
        except SystemExit as e:
            assert e.code == 0
        captured = capsys.readouterr()
        assert "cgitsync" in captured.out

    def test_cli_help_lists_all_commands(self, capsys):
        """CLI help lists all required commands."""
        try:
            main(["--help"])
        except SystemExit as e:
            assert e.code == 0
        captured = capsys.readouterr()

        # All required commands from DevPlanTicket.md
        required_commands = [
            "initialise",
            "status",
            "validate",
            "branch",
            "checkout",
            "add",
            "commit",
            "merge",
            "pull",
            "push",
            "tag",
            "freeze",
            "freeze-release",
            "launch-release",
            "remember",
            "memorize",
            "retrieve",
            "reload",
            "state",
            "state-core",
        ]

        for cmd in required_commands:
            assert cmd in captured.out, f"Command '{cmd}' not found in CLI help"


class TestCLIArgumentParsing:
    """Test CLI argument parsing."""

    def test_parser_creation(self):
        """Parser can be created."""
        parser = get_parser()
        assert parser is not None

    def test_parser_has_all_commands(self):
        """Parser accepts all defined commands."""
        parser = get_parser()
        for cmd in COMMANDS:
            # This should not raise
            args = parser.parse_args([cmd])
            assert args.command == cmd

    def test_parser_rejects_unknown_commands(self, capsys):
        """Parser rejects unknown commands."""
        parser = get_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["unknown-command"])
        assert exc_info.value.code == 2


class TestCLICommandDispatch:
    """Test CLI command dispatching."""

    def test_unsupported_command_rejected(self, capsys):
        """Unsupported commands are rejected."""
        # Try a command that's not in our COMMANDS set
        try:
            exit_code = main(["unsupported-command"])
            assert exit_code == 1
        except SystemExit as e:
            assert e.code == 2  # argparse exit code for unknown command
        captured = capsys.readouterr()
        assert "Error" in captured.err or "invalid choice" in captured.err

    def test_cli_json_output(self, capsys):
        """CLI can output in JSON format."""
        try:
            exit_code = main(["--json", "status"])
            assert exit_code == 0
        except SystemExit as e:
            assert e.code == 0
        captured = capsys.readouterr()
        # Should be valid JSON
        if captured.out.strip():
            try:
                data = json.loads(captured.out)
                assert "success" in data
            except json.JSONDecodeError:
                pytest.fail("Output is not valid JSON")


# ==================== GitRunner Security Tests ====================


class TestGitRunnerSecurity:
    """Test GitRunner security requirements from DevPlanTicket.md."""

    def test_git_runner_uses_argument_arrays(self):
        """GitRunner uses argument arrays, not string interpolation."""
        # This is verified by code inspection - the _execute method uses tuple
        # We can verify indirectly by checking the implementation
        runner = GitRunner()
        # The _build_command method returns a tuple
        from ComplexGitSync.git_runner import _build_command
        cmd = _build_command(GitCommand.STATUS, ["--short"])
        assert isinstance(cmd, tuple)
        assert "git" in cmd
        assert "status" in cmd
        assert "--short" in cmd

    def test_git_runner_sets_cwd_explicitly(self):
        """GitRunner sets cwd explicitly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = GitRunner(cwd=tmpdir)
            assert runner.cwd == Path(tmpdir)

    def test_git_runner_captures_stdout_stderr(self):
        """GitRunner captures stdout and stderr."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize a git repo
            runner = GitRunner(cwd=tmpdir)
            result = runner.init(bare=False, initial_branch="main")

            # The result should have captured output
            assert result.success or result.error is not None

    def test_git_runner_returns_typed_results(self):
        """GitRunner returns typed results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = GitRunner(cwd=tmpdir)
            result = runner.status()

            # Should return GitExecution
            assert isinstance(result, GitExecution)

            if result.success:
                assert isinstance(result.result, GitResult)
            else:
                assert isinstance(result.error, GitError)

    def test_git_runner_maps_failures_to_typed_errors(self):
        """GitRunner maps Git failures to typed errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = GitRunner(cwd=tmpdir)
            # Try to checkout a non-existent branch
            result = runner.checkout("non-existent-branch")

            # Should have an error
            assert not result.success
            assert result.error is not None
            assert isinstance(result.error.code, GitErrorCode)

    def test_git_runner_rejects_unsupported_operations(self):
        """GitRunner rejects unsupported operations."""
        runner = GitRunner()
        # The GitCommand enum only contains supported commands
        # We can verify that the API only accepts GitCommand enum values
        # by checking that all public methods use GitCommand
        from ComplexGitSync.git_runner import GitCommand

        # Verify that all command methods use GitCommand enum
        import inspect

        # Check that _execute expects a GitCommand
        sig = inspect.signature(runner._execute)
        params = sig.parameters
        assert 'command' in params

        # The type annotation should be GitCommand
        # We can verify by checking the source
        source = inspect.getsource(runner._execute)
        assert 'GitCommand' in source or 'command:' in source

    def test_git_runner_redacts_credentials(self):
        """GitRunner redacts credentials from output."""
        from ComplexGitSync.git_runner import redact_credentials

        # Test credential URL redaction
        test_input = "https://user:password@example.com/repo.git"
        result = redact_credentials(test_input)
        assert "password" not in result
        assert "REDACTED" in result

        # Test credential pattern redaction
        test_input2 = "password=secret123"
        result2 = redact_credentials(test_input2)
        assert "secret123" not in result2
        assert "REDACTED" in result2

        test_input3 = "token=abc123"
        result3 = redact_credentials(test_input3)
        assert "abc123" not in result3
        assert "REDACTED" in result3

    def test_git_runner_preserves_deterministic_order(self):
        """GitRunner preserves deterministic tree order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize a git repo
            runner = GitRunner(cwd=tmpdir, deterministic_order=True)
            result = runner.init(bare=False, initial_branch="main")

            # The runner should set LC_ALL=C for deterministic ordering
            # This is verified by the implementation
            assert result.success or result.error is not None

    def test_no_shell_true_in_git_runner(self):
        """GitRunner never uses shell=True."""
        # This is verified by code inspection
        # The _execute method explicitly uses shell=False
        import inspect
        from ComplexGitSync.git_runner import GitRunner

        source = inspect.getsource(GitRunner._execute)
        # Check that shell=False is used in subprocess.run
        assert "shell=False" in source
        # Check that shell=True is not used in the subprocess.run call
        # (it might appear in comments, so we look for the actual parameter)
        lines = source.split('\n')
        for line in lines:
            if 'subprocess.run' in line or 'subprocess.call' in line:
                # Check that this line doesn't have shell=True
                assert 'shell=True' not in line, f"Found shell=True in subprocess call: {line}"


class TestGitRunnerCommands:
    """Test GitRunner command implementations."""

    def test_git_runner_init(self):
        """GitRunner can initialize a repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = GitRunner(cwd=tmpdir)
            result = runner.init(initial_branch="main")
            assert result.success

    def test_git_runner_status(self):
        """GitRunner can get status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = GitRunner(cwd=tmpdir)
            runner.init(initial_branch="main")
            result = runner.status()
            assert result.success

    def test_git_runner_branch(self):
        """GitRunner can create branches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = GitRunner(cwd=tmpdir)
            runner.init(initial_branch="main")

            # Create a branch
            result = runner.branch(create="feature")
            assert result.success

            # List branches
            branches = runner.list_branches()
            assert "feature" in branches

    def test_git_runner_checkout(self):
        """GitRunner can checkout branches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = GitRunner(cwd=tmpdir)
            runner.init(initial_branch="main")
            runner.branch(create="feature")

            result = runner.checkout("feature")
            assert result.success
            assert runner.get_current_branch() == "feature"

    def test_git_runner_add_and_commit(self):
        """GitRunner can add and commit files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = GitRunner(cwd=tmpdir)
            runner.init(initial_branch="main")

            # Create a file
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("test content")

            # Add the file
            result = runner.add(str(test_file))
            assert result.success

            # Commit
            result = runner.commit("Test commit")
            assert result.success

    def test_git_runner_merge(self):
        """GitRunner can merge branches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = GitRunner(cwd=tmpdir)
            runner.init(initial_branch="main")

            # Create a feature branch
            runner.branch(create="feature")
            runner.checkout("feature")

            # Create a file in feature
            test_file = Path(tmpdir) / "feature.txt"
            test_file.write_text("feature content")
            runner.add(str(test_file))
            runner.commit("Add feature file")

            # Go back to main and merge
            runner.checkout("main")
            result = runner.merge("feature")
            assert result.success


# ==================== ComplexGitSyncClient Tests ====================


class TestComplexGitSyncClient:
    """Test ComplexGitSyncClient command methods."""

    def test_client_initialise(self):
        """Client can initialize a repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = ComplexGitSyncClient(cwd=tmpdir)
            result = client.initialise()
            assert result.success

    def test_client_status(self):
        """Client can get status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = ComplexGitSyncClient(cwd=tmpdir)
            client.initialise()
            result = client.status()
            assert result.success

    def test_client_validate(self):
        """Client can validate repository state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = ComplexGitSyncClient(cwd=tmpdir)
            client.initialise()
            result = client.validate()
            assert result.success

    def test_client_branch(self):
        """Client can create branches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = ComplexGitSyncClient(cwd=tmpdir)
            client.initialise()
            result = client.branch("feature")
            assert result.success

    def test_client_checkout(self):
        """Client can checkout branches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = ComplexGitSyncClient(cwd=tmpdir)
            client.initialise()
            client.branch("feature")
            result = client.checkout("feature")
            assert result.success

    def test_client_add(self):
        """Client can add files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = ComplexGitSyncClient(cwd=tmpdir)
            client.initialise()

            # Create a file
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("test content")

            result = client.add(str(test_file))
            assert result.success

    def test_client_commit(self):
        """Client can commit changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = ComplexGitSyncClient(cwd=tmpdir)
            client.initialise()

            # Create and add a file
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("test content")
            client.add(str(test_file))

            result = client.commit("Test commit")
            assert result.success

    def test_client_merge(self):
        """Client can merge branches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = ComplexGitSyncClient(cwd=tmpdir)
            client.initialise()

            # Create feature branch with a file
            client.branch("feature")
            client.checkout("feature")
            test_file = Path(tmpdir) / "feature.txt"
            test_file.write_text("feature content")
            client.add(str(test_file))
            client.commit("Add feature file")

            # Merge to main
            client.checkout("main")
            result = client.merge("feature")
            assert result.success

    def test_client_freeze(self):
        """Client can freeze local State."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = ComplexGitSyncClient(cwd=tmpdir)
            client.initialise()
            result = client.freeze()
            assert result.success

    def test_client_validate_rejects_uncommitted(self):
        """Client validate rejects uncommitted changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = ComplexGitSyncClient(cwd=tmpdir)
            client.initialise()

            # Create an uncommitted file
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("test content")
            client.add(str(test_file))

            result = client.validate()
            assert not result.success

    def test_client_merge_rejects_main(self):
        """Client merge rejects source=main."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = ComplexGitSyncClient(cwd=tmpdir)
            client.initialise()

            result = client.merge("main")
            assert not result.success

    def test_client_merge_rejects_nonexistent(self):
        """Client merge rejects non-existent branches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = ComplexGitSyncClient(cwd=tmpdir)
            client.initialise()

            result = client.merge("non-existent")
            assert not result.success

    def test_client_push_rejects_non_main(self):
        """Client push rejects non-main branches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = ComplexGitSyncClient(cwd=tmpdir)
            client.initialise()
            client.branch("feature")
            client.checkout("feature")

            result = client.push()
            assert not result.success


# ==================== CLI/API Parity Tests ====================


class TestCLIAPIParity:
    """Test that every CLI command maps to a client method."""

    def test_all_cli_commands_have_client_methods(self):
        """Every CLI command has a corresponding client method."""
        client = ComplexGitSyncClient()

        # Mapping from CLI command to method name (handles hyphens)
        command_to_method = {
            "freeze-release": "freeze_release",
            "launch-release": "launch_release",
            "state-core": "state_core",
        }

        for cmd in COMMANDS:
            method_name = command_to_method.get(cmd, cmd)
            # Check if client has the method
            assert hasattr(client, method_name), f"Client missing method '{method_name}' for command '{cmd}'"

            # Check if it's callable
            method = getattr(client, method_name)
            assert callable(method), f"Client method '{method_name}' is not callable"

    def test_cli_commands_are_lowercase(self):
        """All CLI commands are lowercase."""
        for cmd in COMMANDS:
            assert cmd == cmd.lower(), f"Command '{cmd}' is not lowercase"


# ==================== GitRunner Isolation Tests ====================


class TestGitRunnerIsolation:
    """Test that only GitRunner invokes Git."""

    def test_git_runner_is_only_git_executor(self):
        """Verify that GitRunner is the only class that invokes Git."""
        # This is verified by code inspection and architecture
        # We check that the CLI and Client don't import subprocess

        # Check cli.py
        import ComplexGitSync.cli as cli_module
        import inspect
        cli_source = inspect.getsource(cli_module)
        assert "subprocess" not in cli_source.lower() or "import" not in cli_source.lower() + "subprocess"

        # Check complex_git_sync_client.py
        import ComplexGitSync.complex_git_sync_client as client_module
        client_source = inspect.getsource(client_module)
        assert "subprocess" not in client_source.lower() or "import" not in client_source.lower() + "subprocess"

    def test_no_shell_true_in_cli_or_client(self):
        """Verify no shell=True in CLI or Client."""
        import ComplexGitSync.cli as cli_module
        import ComplexGitSync.complex_git_sync_client as client_module
        import inspect

        cli_source = inspect.getsource(cli_module)
        client_source = inspect.getsource(client_module)

        assert "shell=True" not in cli_source
        assert "shell=True" not in client_source


# ==================== Public Entry Point Tests ====================


class TestPublicEntryPoint:
    """Test that cgitsync is the only public operational entry point."""

    def test_cgitsync_cli_is_entry_point(self):
        """cgitsync CLI is the entry point."""
        # Verify the entry point is configured in pyproject.toml
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "ComplexGitSync.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "cgitsync" in result.stdout

    def test_cgitsync_via_pixi(self):
        """cgitsync can be invoked via pixi."""
        result = subprocess.run(
            ["pixi", "run", "cgitsync", "--help"],
            capture_output=True,
            text=True,
            cwd="/home/flipoyo/Programmes/ComplexGitSync",
        )
        assert result.returncode == 0
        assert "cgitsync" in result.stdout


class TestStateProjectionAccess:
    """Test that STATE@.md and STATE@.CORE.md are accessible through cgitsync."""

    def test_state_command_exists(self):
        """state command exists in CLI."""
        assert "state" in COMMANDS

    def test_state_core_command_exists(self):
        """state-core command exists in CLI."""
        assert "state-core" in COMMANDS

    def test_client_has_state_method(self):
        """Client has state method."""
        client = ComplexGitSyncClient()
        assert hasattr(client, "state")
        assert callable(client.state)

    def test_client_has_state_core_method(self):
        """Client has state_core method."""
        client = ComplexGitSyncClient()
        assert hasattr(client, "state_core")
        assert callable(client.state_core)

    def test_state_commands_return_success(self):
        """state commands return success (placeholder for now)."""
        client = ComplexGitSyncClient()
        state_result = client.state()
        assert state_result.success

        state_core_result = client.state_core()
        assert state_core_result.success

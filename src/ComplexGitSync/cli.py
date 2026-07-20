"""cgitsync CLI - The only public operational entry point.

This module provides the CLI interface for ComplexGitSync.
The CLI contains NO business logic - it only parses arguments and delegates
to ComplexGitSyncClient methods.

Public entry: pixi run cgitsync <command>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .complex_git_sync_client import ComplexGitSyncClient


# All supported commands must have a corresponding method in ComplexGitSyncClient
COMMANDS = frozenset({
    # Initialization and status
    "initialise",
    "status",
    "validate",
    # Branch operations
    "branch",
    "checkout",
    # Change operations
    "add",
    "commit",
    # Merge operations
    "merge",
    # Remote operations
    "pull",
    "push",
    "tag",
    # State operations
    "freeze",
    "freeze-release",
    "launch-release",
    # Memory operations
    "remember",
    "memorize",
    "retrieve",
    "reload",
    # State projection access
    "state",
    "state-core",
})

# Commands that accept additional positional arguments
COMMANDS_WITH_ARGS = {
    "branch": {"nargs": 1, "help": "Branch name"},
    "checkout": {"nargs": 1, "help": "Branch or commit to checkout"},
    "merge": {"nargs": 1, "help": "Source branch to merge into main"},
    "commit": {"nargs": "+", "help": "Commit message (as multiple words)"},
    "tag": {"nargs": 1, "help": "Tag name"},
    "launch-release": {"nargs": 1, "help": "State ID to launch"},
    "retrieve": {"nargs": "?", "help": "State ID to retrieve (optional)"},
    "add": {"nargs": "*", "help": "Files to add (optional)"},
}


def get_parser() -> argparse.ArgumentParser:
    """Create the argument parser for cgitsync."""
    parser = argparse.ArgumentParser(
        prog="cgitsync",
        description="ComplexGitSync - Synchronize nested Git repository trees from .cgs specs and .gts state snapshots.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Positional argument for command
    parser.add_argument(
        "command",
        nargs="?",
        help="Command to execute",
        choices=sorted(COMMANDS),
    )

    # Optional arguments for commands that need them
    parser.add_argument(
        "args",
        nargs="*",
        help="Additional arguments for the command",
    )

    # Global options
    parser.add_argument(
        "--cwd",
        type=str,
        default=None,
        help="Working directory for the command (default: current directory)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results in JSON format",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Show verbose output",
    )

    return parser


def parse_commit_message(args: list[str]) -> str:
    """Parse commit message from arguments."""
    if not args:
        return ""
    return " ".join(args)


def parse_retrieve_state_id(args: list[str]) -> str | None:
    """Parse state ID from retrieve command arguments."""
    if not args:
        return None
    return args[0]


def main(argv: list[str] | None = None) -> int:
    """Main entry point for cgitsync CLI.

    Returns exit code (0 for success, 1 for error).
    """
    parser = get_parser()
    args = parser.parse_args(argv)

    # If no command provided, show help
    if args.command is None:
        parser.print_help()
        return 0

    # Validate command
    if args.command not in COMMANDS:
        print(f"Error: Unknown command '{args.command}'", file=sys.stderr)
        print(f"Available commands: {', '.join(sorted(COMMANDS))}", file=sys.stderr)
        return 1

    # Parse arguments based on command
    cwd = Path(args.cwd) if args.cwd else None
    client = ComplexGitSyncClient(cwd=cwd)

    try:
        # Dispatch command to client
        result = _dispatch_command(args.command, args.args, client)

        # Output result
        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            if result.ok:
                print(result.result.stdout if result.result else "Success")
            else:
                print(f"Error: {result.error.message}", file=sys.stderr)
                if result.error.stdout:
                    print(result.error.stdout, file=sys.stderr)
                if result.error.stderr:
                    print(result.error.stderr, file=sys.stderr)
                return 1

        return 0

    except Exception as e:
        if args.json:
            error_dict = {
                "success": False,
                "error": {
                    "code": "internal_error",
                    "message": str(e),
                    "command": args.command,
                },
            }
            print(json.dumps(error_dict, indent=2))
        else:
            print(f"Internal error: {e}", file=sys.stderr)
        return 1


def _dispatch_command(
    command: str,
    cmd_args: list[str],
    client: ComplexGitSyncClient,
) -> object:
    """Dispatch command to the appropriate client method."""
    from .git_runner import GitExecution

    # Commands without arguments
    # Handle hyphenated command names by mapping to Python method names
    command_mapping = {
        "freeze-release": "freeze_release",
        "launch-release": "launch_release",
        "state-core": "state_core",
    }

    actual_command = command_mapping.get(command, command)

    if command in {"initialise", "status", "validate", "pull", "push", "freeze", "freeze-release", "remember", "memorize", "reload", "state", "state-core"}:
        method = getattr(client, actual_command)
        return method()

    if command == "launch-release":
        if not cmd_args:
            raise ValueError("launch-release command requires a State ID")
        return client.launch_release(cmd_args[0])

    # Commands with specific argument parsing
    if command == "branch":
        if not cmd_args:
            raise ValueError("branch command requires a branch name")
        return client.branch(cmd_args[0])

    if command == "checkout":
        if not cmd_args:
            raise ValueError("checkout command requires a target")
        return client.checkout(cmd_args[0])

    if command == "merge":
        if not cmd_args:
            raise ValueError("merge command requires a source branch")
        return client.merge(cmd_args[0])

    if command == "commit":
        message = parse_commit_message(cmd_args)
        if not message:
            raise ValueError("commit command requires a message")
        return client.commit(message)

    if command == "tag":
        if not cmd_args:
            raise ValueError("tag command requires a tag name")
        return client.tag(cmd_args[0])

    if command == "launch-release":
        if not cmd_args:
            raise ValueError("launch-release command requires a State ID")
        return client.launch_release(cmd_args[0])

    if command == "retrieve":
        state_id = parse_retrieve_state_id(cmd_args)
        return client.retrieve(state_id)

    if command == "add":
        # If no paths specified, pass empty tuple (which means add all)
        return client.add(*cmd_args)

    # Should not reach here - all commands should be handled
    raise ValueError(f"Unhandled command: {command}")


if __name__ == "__main__":
    sys.exit(main())

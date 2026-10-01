"""cli.autofix_command — `cgitsync autofix`, from a logged error or from a tip commit.

Ring: 4. Contract: collect `autofix`'s arguments, call the one `ComplexGitSyncClient` method that
    carries the semantics, and print. Without `--tip-commit` it diagnoses and repairs the
    last failing command's error; with it, it reads each repository's tip commit instead,
    for a defect that logged no error (AutofixBlindSpot), and with `--repo` and a message it
    rewrites that one commit's message.
Imports: _shared, errors, exit_codes, help_text, orchestre
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..errors import GitSyncError
from ..orchestre import ComplexGitSyncClient
from ._shared import _load_ready_registry_source, _resolve_workspace_source, _run_with_logging
from .exit_codes import EXIT_OK, EXIT_REFUSED
from .help_text import SEARCH_DIR_HELP


def register(subparser: argparse.ArgumentParser) -> None:
    """Register ``autofix``'s arguments."""
    subparser.add_argument(
        "source",
        nargs="?",
        default=None,
        help=(
            "Path to the local .cgs or .gts file to load. "
            "When omitted the latest .gts snapshot is discovered automatically "
            "under CGSHOME/.cgitsync/."
        ),
    )
    subparser.add_argument("--search-dir", metavar="DIR", help=SEARCH_DIR_HELP)
    subparser.add_argument(
        "--error",
        default=None,
        help="The error text to diagnose, instead of reading the most recent failing command from .cgitsync/logs/.",
    )
    subparser.add_argument(
        "--repo",
        dest="repo_name",
        default=None,
        help="The mounted repository to repair (e.g. .memory), instead of guessing it from --error.",
    )
    subparser.add_argument(
        "--tip-commit",
        action="store_true",
        help=(
            "Read each repository's latest commit message instead of the error log, and report the ones that "
            "break the commit-message rule or show traces of a shell having eaten text (heuristics: aligned "
            "text can trip them). Writes nothing unless a message is also given."
        ),
    )
    subparser.add_argument(
        "--message",
        default=None,
        metavar="TEXT",
        help="With --tip-commit and --repo: the corrected message. Pass it in single quotes, or use --message-file.",
    )
    subparser.add_argument(
        "--message-file",
        default=None,
        metavar="FILE",
        help="Like --message, read from FILE ('-' for standard input). A file cannot be damaged by a shell.",
    )
    subparser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Allow rewriting a commit a remote already holds. The rewrite stays local: nothing is pushed, "
            "and the command to publish it is printed."
        ),
    )
    subparser.set_defaults(handler=_handle_autofix)


def _handle_autofix(args: argparse.Namespace) -> int:
    message = _message_from(args)
    source = _resolve_workspace_source(args.source, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="autofix",
        source=source,
        runner=lambda client, source: _execute_autofix(
            client,
            source,
            error=args.error,
            repo_name=args.repo_name,
            tip_commit=args.tip_commit,
            message=message,
            force=args.force,
        ),
    )


def _message_from(args: argparse.Namespace) -> str | None:
    """The corrected message, from ``--message`` or ``--message-file``; refuses a nonsense combination."""
    if (args.message is not None or args.message_file is not None or args.force) and not args.tip_commit:
        raise GitSyncError("--message, --message-file and --force only apply with --tip-commit.")
    if args.tip_commit and args.error is not None:
        raise GitSyncError("--tip-commit reads commits, not an error: drop --error.")
    if args.message is not None and args.message_file is not None:
        raise GitSyncError("give --message or --message-file, not both.")
    if args.message_file is not None:
        try:
            raw = sys.stdin.buffer.read() if args.message_file == "-" else Path(args.message_file).read_bytes()
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GitSyncError(f"the corrected message is not valid UTF-8 text: {exc.reason} at byte {exc.start}.") from exc
    return args.message


def _execute_autofix(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    error: str | None,
    repo_name: str | None,
    tip_commit: bool = False,
    message: str | None = None,
    force: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    if not tip_commit:
        outcome = client.autofix(error=error, repo_name=repo_name)
        print(f"repaired={outcome.repaired} detail={outcome.detail}")
        return EXIT_OK if outcome.repaired else EXIT_REFUSED
    if message is None:
        return _print_tip_findings(client.autofix_tip_commits(repo_name=repo_name))
    if repo_name is None:
        raise GitSyncError("amending needs --repo: name the repository whose latest commit message to rewrite.")
    outcome = client.autofix_amend(repo_name, message, force=force)
    print(f"repaired={outcome.repaired} detail={outcome.detail}")
    return EXIT_OK if outcome.repaired else EXIT_REFUSED


def _print_tip_findings(answer: dict) -> int:
    findings = answer["findings"]
    print(f"checked={answer['checked']} repositories, malformed={len(findings)}")
    for row in findings:
        print(f"repository={row['repository']}  commit={row['sha'][:8]}  on_a_remote={'yes' if row['published'] else 'not-seen-from-this-clone'}")
        print(f"  subject: {row['subject']}")
        for finding in row["findings"]:
            print(f"  - {finding}")
        force = "  (--force: a remote already holds it, and only a person should decide that)" if row["published"] else ""
        print(f"  fix: cgitsync autofix --tip-commit --repo {row['repository']} --message-file <corrected message file>{force}")
    return EXIT_REFUSED if findings else EXIT_OK


__all__ = ["register"]

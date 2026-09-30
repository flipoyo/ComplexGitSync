"""cli.memory_prompt — `cgitsync memory setup`, and the offer made after a recording command.

Ring: 4. Contract: collect the provider, owner and repository name for the
    memory a DEV tree lacks — in a terminal only — then call
    `ComplexGitSyncClient.memory_setup`; everywhere else print the client's
    warning. Asks and prints; every answer comes from the client.
Imports: _shared, exit_codes, orchestre
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import warnings
from pathlib import Path

from ..orchestre import ComplexGitSyncClient
from ..orchestre.memory_setup import MemorySetupWarning
from ._shared import (
    _load_ready_registry_source,
    _resolve_cgshome,
    _resolve_gts_path,
    _run_with_logging,
)
from .exit_codes import EXIT_OK, EXIT_REFUSED


def interactive() -> bool:
    """Whether a person is there to answer: stdin and stdout are a terminal, and stdout is not redirected (``--json``)."""
    return sys.stdout is sys.__stdout__ and sys.stdin.isatty() and sys.stdout.isatty()


@contextlib.contextmanager
def silenced_setup_warning():
    """Keep the client's :class:`MemorySetupWarning` off stderr: the CLI says it in its own words afterwards."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MemorySetupWarning)
        yield


def _ask(question: str, default: str | None) -> str:
    shown = f" [{default}]" if default else ""
    return input(f"{question}{shown}: ").strip() or (default or "")


def register(memory_commands) -> None:
    """Add ``memory setup`` to the ``memory`` group."""
    setup = memory_commands.add_parser(
        "setup",
        help="Create, declare and adopt the memory repository a developer tree lacks.",
    )
    setup.add_argument("--provider", help="Git provider hosting the memory (default: github).")
    setup.add_argument("--owner", help="Account the memory repository belongs to. Guessed from the tree.")
    setup.add_argument("--name", dest="repo_name", help="Repository name (default: .memory).")
    setup.add_argument("--cgs", metavar="FILE", help="The .cgs to declare it in. Defaults to the one this tree was built from.")
    setup.add_argument("--search-dir", metavar="DIR", help="Directory used to resolve CGSHOME.")
    setup.set_defaults(handler=_handle_memory_setup)


def _handle_memory_setup(args: argparse.Namespace) -> int:
    cgshome = _resolve_cgshome(getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="memory-setup",
        source=cgshome,
        runner=lambda client, source: _execute_memory_setup(
            client, source, provider=args.provider, owner=args.owner, name=args.repo_name, cgs=args.cgs
        ),
    )


def _execute_memory_setup(
    client: ComplexGitSyncClient,
    cgshome: Path | None,
    *,
    provider: str | None = None,
    owner: str | None = None,
    name: str | None = None,
    cgs: str | None = None,
) -> int:
    if cgshome is not None:
        _load_ready_registry_source(client, _resolve_gts_path(None, str(cgshome)))
    proposal = client.memory_setup_proposal(cgshome, provider=provider, owner=owner, name=name)
    if proposal is None:
        print("nothing to set up: this tree declares a memory, or holds no private repository.")
        return EXIT_OK
    if interactive():
        provider = _ask("Git provider", proposal["provider"])
        owner = _ask("Owner of the memory repository", proposal["owner"]) or None
        name = _ask("Repository name", proposal["name"])
        proposal = client.memory_setup_proposal(cgshome, provider=provider, owner=owner, name=name)
        if proposal["create_with"] is None:
            # Answers that leave nothing to create count as a no, or the question returns every command.
            client.memory_setup_decline(cgshome)
            print(f"no repository can be created for provider {provider!r} without an owner and a tool.", proposal["warning"], sep="\n", file=sys.stderr)
            return EXIT_REFUSED
        print(f"this creates the repository with: {proposal['create_with']}")
        print(f"adds to {cgs or proposal['cgs'] or 'your .cgs'}: {proposal['line']}")
        print("and adopts the local memory already on this disk, keeping every record.")
        if _ask("Proceed? (y/N)", "n").lower() not in ("y", "yes"):
            client.memory_setup_decline(cgshome)
            print(proposal["warning"], file=sys.stderr)
            return EXIT_OK
    result = client.memory_setup(cgshome, provider=provider, owner=owner, name=name, cgs_path=cgs)
    client.memory_setup_due = False
    print(f"done={','.join(result['done']) or 'nothing'}")
    if result["failed"] is None:
        print(f"repository={result['repository']}")
        print("next: cgitsync memory push")
        return EXIT_OK
    print(f"failed={result['failed']}: {result['error']}", file=sys.stderr)
    print(result["warning"], file=sys.stderr)
    return EXIT_REFUSED


def offer_after_command(client: ComplexGitSyncClient) -> None:
    """After a command that recorded a State in a DEV tree with no declared memory: ask once, else warn."""
    if not getattr(client, "memory_setup_due", False):
        return
    proposal = client.memory_setup_proposal()
    if proposal is None:
        return
    if proposal["declined"] or not interactive():
        print(proposal["warning"], file=sys.stderr)
        return
    print("this developer tree holds private repositories, but its .cgs declares no memory to sync its record to.")
    try:
        _execute_memory_setup(client, None)
    except (EOFError, KeyboardInterrupt):
        # The command itself already succeeded; an unanswered question must not cost it that.
        print(f"\n{proposal['warning']}", file=sys.stderr)


__all__ = ["interactive", "offer_after_command", "register", "silenced_setup_warning"]

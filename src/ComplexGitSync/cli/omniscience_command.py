"""cli.omniscience_command — `cgitsync omniscience mount|record`.

Ring: 4. Contract: register the ``omniscience`` group, and for ``mount`` or
    ``record`` call `ComplexGitSyncClient.omniscience_mount` or
    `.omniscience_record` and print the answer. Neither writes to the register.
    Argument collection and printing only.
Imports: _shared, help_text, orchestre
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import tomli_w

from ..orchestre import ComplexGitSyncClient
from ._shared import (
    _load_ready_registry_source,
    _resolve_cgshome,
    _resolve_gts_path,
    _run_with_logging,
)
from .help_text import SEARCH_DIR_HELP

__all__ = ["HELP", "handle", "register"]

HELP = "Join the shared register of projects, and see what a State would register (mount, record)."


def register(subparser: argparse.ArgumentParser) -> None:
    """``omniscience <subcommand>``."""
    actions = subparser.add_subparsers(dest="omniscience_command", required=True)

    mount = actions.add_parser(
        "mount",
        help="Declare the shared Omniscience repository in the .cgs and clone it at .omniscience.",
        description=(
            "For a developer tree: add the private omniscience repository to the .cgs, read-only unless "
            "--contribute, and clone this project's branch of it (else the index branch). Registers nothing."
        ),
    )
    mount.add_argument("--repository", metavar="ID", help="The repository, as a .cgs identifier. Default: github:flipoyo/omniscience.")
    mount.add_argument("--contribute", action="store_true", help="Mount it writable, so this tree can register (private/local) instead of only inspect (private/distant).")
    mount.add_argument("--cgs", metavar="FILE", help="The .cgs to add it to. Default: the one this tree was built from.")

    record = actions.add_parser(
        "record",
        help="Print the registration record a State would make, without registering it.",
        description=(
            "Read from the committed memory, so the State must have been pushed with 'memory push'. "
            "Prints the record, its hash (its file name) and its H_OS_STATE; --json prints one object."
        ),
    )
    record.add_argument("state", help="A State's hash, or any unambiguous prefix of it.")
    record.add_argument("--json", action="store_true", help="Print one JSON object instead of the record.")
    for parser in (mount, record):
        parser.add_argument("--search-dir", metavar="DIR", help=SEARCH_DIR_HELP)
    subparser.set_defaults(handler=handle)


def handle(args: argparse.Namespace) -> int:
    cgshome = _resolve_cgshome(getattr(args, "search_dir", None))
    run = {"mount": _execute_mount, "record": _execute_record}[args.omniscience_command]
    return _run_with_logging(command_name=f"omniscience-{args.omniscience_command}", source=cgshome, runner=lambda client, source: run(client, source, args))


def _execute_mount(client: ComplexGitSyncClient, cgshome: Path, args: argparse.Namespace) -> int:
    _load_ready_registry_source(client, _resolve_gts_path(None, str(cgshome)))
    result = client.omniscience_mount(args.cgs, cgshome, repository=args.repository, contribute=args.contribute)
    print(f"cgs={result['cgs']} {'added' if result['added'] else 'already declared'}: {result['line'].strip()}")
    print(f"{'cloned' if result['cloned'] else 'already cloned'}={result['destination']} branch={result['branch']}")
    if result["mismatch"]:
        print(f"note: {result['mismatch']}")
    return 0


def _execute_record(client: ComplexGitSyncClient, cgshome: Path, args: argparse.Namespace) -> int:
    _load_ready_registry_source(client, _resolve_gts_path(None, str(cgshome)))
    result = client.omniscience_record(cgshome, args.state)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(tomli_w.dumps(result["record"]).rstrip())
    print(f"record_hash={result['record_hash']}")
    print(f"os_state_hash={result['os_state_hash']}")
    print(f"file={result['filename']}")
    return 0

"""cli.omniscience_command — `cgitsync omniscience mount|record|register|observe`.

Ring: 4. Contract: register the ``omniscience`` group, and for each subcommand
    call the matching ``ComplexGitSyncClient.omniscience_*`` method and print
    the answer. Only ``register`` and ``observe`` write to the register, and
    only by appending. Argument collection and printing only.
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

HELP = "The shared register of projects: join it, register a State, and watch what disappears (mount, record, register, observe)."


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

    register_ = actions.add_parser(
        "register",
        help="Register a State in the shared register and extend the global checkpoint.",
        description=(
            "For a developer tree with the register mounted writable. Builds the State's record, refuses one that does not "
            "validate, commits it and pushes it without ever forcing (a push another contributor beat is retried), "
            "then extends the global checkpoint. Running it again does nothing twice."
        ),
    )
    register_.add_argument("--state", metavar="HASH", help="A State's hash, or any unambiguous prefix. Default: the memory's newest.")
    register_.add_argument("--json", action="store_true", help="Print one JSON object.")

    observe = actions.add_parser(
        "observe",
        help="Probe the repositories a project registered and record what was found.",
        description=(
            "Asks each registered repository and its .memory whether they are still there, and appends one observation per "
            "finding. Nothing is removed or rewritten. A repository the host says is not there is a warning, and a permanent "
            "failure only when that has been repeated for a long time (exit 1)."
        ),
    )
    observe.add_argument("project", nargs="?", help="The project. Default: this tree's.")
    observe.add_argument("--confirm-deleted", metavar="REPOSITORY", help="Record the owner's signed note that REPOSITORY was deleted. Only when the host still says it is not there; needs --reason.")
    observe.add_argument("--reason", metavar="TEXT", help="Why the deletion is confirmed.")
    observe.add_argument("--json", action="store_true", help="Print one JSON object.")
    for parser in (mount, record, register_, observe):
        parser.add_argument("--search-dir", metavar="DIR", help=SEARCH_DIR_HELP)
    subparser.set_defaults(handler=handle)


def handle(args: argparse.Namespace) -> int:
    cgshome = _resolve_cgshome(getattr(args, "search_dir", None))
    run = {"mount": _execute_mount, "record": _execute_record, "register": _execute_register, "observe": _execute_observe}[args.omniscience_command]
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


def _execute_register(client: ComplexGitSyncClient, cgshome: Path, args: argparse.Namespace) -> int:
    _load_ready_registry_source(client, _resolve_gts_path(None, str(cgshome)))
    result = client.omniscience_register(cgshome, args.state)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    step = result["checkpoint"]
    print(f"record={result['record_hash']} branch={result['branch']} {'registered' if result['appended'] else 'already registered'} retries={result['retries']}")
    print(f"checkpoint={step['status']} global_root={step['global_root']} records={step['records']}")
    for item in step["unaccepted"]:
        print(f"unaccepted: {item['branch']}:{item['path']}: {item['reason']}")
    if step["problem"]:
        print(f"problem: {step['problem']}")
    return 1 if step["status"] in ("rewritten", "refused") else 0


def _execute_observe(client: ComplexGitSyncClient, cgshome: Path, args: argparse.Namespace) -> int:
    _load_ready_registry_source(client, _resolve_gts_path(None, str(cgshome)))
    result = client.omniscience_observe(cgshome, args.project, confirm_deleted=args.confirm_deleted, reason=args.reason)
    report = result["report"]
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for item in result["observations"]:
            print(f"{item['finding']['status']}: {item['target']['kind']} {item['target']['repository']}")
        print(f"report={report['verdict']}")
        for line in report["warnings"]:
            print(f"warning: {line}")
        for line in report["failures"]:
            print(f"failure: {line}")
    return 1 if report["verdict"] == "failure" else 0

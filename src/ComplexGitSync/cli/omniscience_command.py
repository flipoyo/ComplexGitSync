"""cli.omniscience_command — `cgitsync omniscience mount|record|register|observe|get|branches|history|state|proof|verify|status|report|memory`.

Ring: 4. Contract: register the ``omniscience`` group, and for each subcommand
    call the matching ``ComplexGitSyncClient.omniscience_*`` method and print
    the answer. Only ``register`` and ``observe`` write to the register, and
    only by appending; the nine query subcommands only read, each with
    ``--json``, and exit 0 for a ``verified`` answer and 1 for any other.
    Argument collection and printing only.
Imports: _shared, help_text, orchestre
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

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

_SCALARS = ("project", "os_id", "checkpoint", "global_root", "project_branch", "state_id", "record", "repository", "availability", "proof_verified", "registration_proof_verified", "written")
_QUERIES = ("get", "branches", "history", "state", "proof", "verify", "status", "report", "memory")

HELP = "The shared register of projects: join it, register a State, watch what disappears, and ask it (mount, record, register, observe, get, branches, history, state, proof, verify, status, report, memory)."


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
    queries = [_register_queries(actions)]
    for parser in (mount, record, register_, observe, *queries[0]):
        parser.add_argument("--search-dir", metavar="DIR", help=SEARCH_DIR_HELP)
    subparser.set_defaults(handler=handle)


def _register_queries(actions: Any) -> list[argparse.ArgumentParser]:
    """The nine read-only subcommands; each takes the project, may take ``--checkpoint``, and prints ``--json`` on request."""
    made: list[argparse.ArgumentParser] = []

    def add(name: str, summary: str, *positional: tuple[str, str], checkpoint: bool = True) -> argparse.ArgumentParser:
        parser = actions.add_parser(name, help=summary, description=summary + " Reads only: it writes nothing to the register or to any memory.")
        parser.add_argument("project", help="The project's name, as registered.")
        for argument, text in positional:
            parser.add_argument(argument, help=text)
        if checkpoint:
            parser.add_argument("--checkpoint", metavar="NAME", help="Answer from this checkpoint (any unambiguous prefix of its name) instead of the newest.")
        parser.add_argument("--json", action="store_true", help="Print one JSON object.")
        made.append(parser)
        return parser

    add("get", "Whether a project is registered, under which os_id, on which branches.")
    add("branches", "Each registered branch of a project and its newest registered State.")
    add("history", "Every registration of a project, oldest first.").add_argument("--branch", metavar="NAME", help="Only this project branch.")
    state = add("state", "A registered State with its proof, read from the registered .memory.", ("state", "A State's id or any unambiguous prefix."))
    state.add_argument("--out", metavar="FILE", help="Write the verified State here as a .gts, for 'cgitsync bootstrap --gts'. Never overwrites a file.")
    add("proof", "The checked inclusion proof of a State's registration (it proves registration, never availability).", ("state", "A State's id or any unambiguous prefix."))
    add("verify", "A State's proof, and whether the register is intact, attested, rewritten or contradicted.", ("state", "A State's id or any unambiguous prefix."))
    add("status", "A registered repository (or 'memory'): its registration and the newest observation of it.", ("repository", "A repository identifier as the record names it, or 'memory'."))
    add("report", "The register's state, the project's proofs, what is only proposed, and completeness.", checkpoint=False)
    add("memory", "The .memory registered for a branch, read at its immutable commit and checked against the record.", ("branch", "The project branch."))
    return made


def handle(args: argparse.Namespace) -> int:
    cgshome = _resolve_cgshome(getattr(args, "search_dir", None))
    run = {
        "mount": _execute_mount, "record": _execute_record, "register": _execute_register, "observe": _execute_observe,
        **dict.fromkeys(_QUERIES, _execute_query),
    }[args.omniscience_command]
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


def _execute_query(client: ComplexGitSyncClient, cgshome: Path, args: argparse.Namespace) -> int:
    _load_ready_registry_source(client, _resolve_gts_path(None, str(cgshome)))
    name, project, checkpoint = args.omniscience_command, args.project, getattr(args, "checkpoint", None)
    answer = {
        "get": lambda: client.omniscience_get(cgshome, project, checkpoint=checkpoint),
        "branches": lambda: client.omniscience_branches(cgshome, project, checkpoint=checkpoint),
        "history": lambda: client.omniscience_history(cgshome, project, branch=args.branch, checkpoint=checkpoint),
        "state": lambda: client.omniscience_state(cgshome, project, args.state, checkpoint=checkpoint, destination=args.out),
        "proof": lambda: client.omniscience_proof(cgshome, project, args.state, checkpoint=checkpoint),
        "verify": lambda: client.omniscience_verify(cgshome, project, args.state, checkpoint=checkpoint),
        "status": lambda: client.omniscience_status(cgshome, project, args.repository, checkpoint=checkpoint),
        "report": lambda: client.omniscience_report(cgshome, project),
        "memory": lambda: client.omniscience_memory(cgshome, project, args.branch, checkpoint=checkpoint),
    }[name]()
    if args.json:
        print(json.dumps(answer, indent=2))
    else:
        _print_answer(answer)
    return 0 if answer["status"] == "verified" else 1


def _print_answer(answer: dict[str, Any]) -> None:
    """Every answer starts with its status line and its reason; each section after it prints only when the answer has its data."""
    print(f"status={answer['status']}" + (f" ({answer['detail']})" if answer.get("detail") else ""))
    print(answer["reason"])
    for key in _SCALARS:
        if key in answer:
            print(f"{key}={answer[key]}")
    for section in _SECTIONS:
        for line in section(answer):
            print(line)


def _lines_branches(answer: dict[str, Any]) -> list[str]:
    branches = answer.get("branches") if isinstance(answer.get("branches"), list) else []
    if branches and isinstance(branches[0], dict):
        return [f"branch {item['project_branch']}: states={item['states']} latest={item['latest_state_id']}" for item in branches]
    counts = [f"states={answer['states']} records={answer.get('records', '')}"] if isinstance(answer.get("states"), int) else []
    return [*counts, "branches=" + ",".join(branches)] if branches else counts


def _lines_history(answer: dict[str, Any]) -> list[str]:
    return [f"{i['checkpoint_number']:>3} {i['project_branch']} {i['state_id']} record={i['record'][:12]} at={i['recorded_at']} by={i['contributor']}" for i in answer.get("history", ())]


def _lines_proof(answer: dict[str, Any]) -> list[str]:
    proof = answer.get("proof")
    if not isinstance(proof, dict):
        return []
    return [f"proof: leaf {proof['leaf_index']} of {proof['tree_size']}, root {proof['root']}"]


def _lines_register(answer: dict[str, Any]) -> list[str]:
    register = answer.get("register")
    if not register:
        return []
    return [f"register={register['state']} protections={register['protections']}", *([f"register: {register['reason']}"] if register["reason"] else [])]


def _lines_memory(answer: dict[str, Any]) -> list[str]:
    lines = []
    if "ledger" in answer:
        lines += [f"ledger: {answer['ledger']['entries']} entries, tip {answer['ledger']['tip']}", f"states in the memory: {', '.join(s[:12] for s in answer['states'])}"]
    if isinstance(answer.get("state"), dict):
        lines += [f"  {repo['relative_path']}: {repo['ref']} @ {str(repo['commit'])[:12]}" for repo in answer["state"]["repositories"]]
    if answer.get("diagnosis"):
        lines.append(f"diagnosis={answer['diagnosis']}" + (f" located={','.join(answer['located'])}" if answer.get("located") else ""))
    return lines


def _lines_observation(answer: dict[str, Any]) -> list[str]:
    found = answer.get("observation")
    return [f"observation: {found['status']} at {found['observed_at']} by {found['method']}"] if found else []


def _lines_completeness(answer: dict[str, Any]) -> list[str]:
    completeness = answer.get("completeness")
    if isinstance(completeness, dict):
        return [f"completeness={completeness['verdict']}", *(f"  {line}" for line in (*completeness["warnings"], *completeness["failures"]))]
    return [f"completeness={completeness}"] if completeness else []


def _lines_proposed(answer: dict[str, Any]) -> list[str]:
    proposed = answer.get("proposed")
    return [f"proposed: {i['branch']}:{i['path']}: {i['reason']}" for i in proposed] if isinstance(proposed, list) else []


_SECTIONS = (_lines_branches, _lines_history, _lines_proof, _lines_register, _lines_memory, _lines_observation, _lines_completeness, _lines_proposed)

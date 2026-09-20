"""cli.environment — Environment observation and drift commands.

Ring: 4 (CLI adapter)
Contract: collect ``env`` arguments, delegate observation/comparison to the
    public client, and render the returned data without inspecting the tree.
Imports: _shared, cgs_format, exit_codes, orchestre
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..cgs_format import CgsDocument
from ..orchestre import ComplexGitSyncClient
from ..tree_env import Drift, TreeEnvironment
from ._shared import _resolve_gts_path, _run_with_logging
from .exit_codes import EXIT_OK, EXIT_REFUSED

COMMANDS = {"env": "Observe this tree's reproducibility environment, or check its requirements."}


def register_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register ``env`` and its optional ``check`` subcommand."""
    parser = subparsers.add_parser("env", help=COMMANDS["env"], description=COMMANDS["env"])
    parser.add_argument("--search-dir", metavar="DIR")
    commands = parser.add_subparsers(dest="environment_command")
    check = commands.add_parser("check", help="Compare observation with .cgs requirements.")
    check.add_argument("--search-dir", metavar="DIR")
    check.add_argument("--cgs", metavar="FILE", help="Requirements source; defaults to the tree's .cgs.")
    parser.set_defaults(handler=_handle_environment)


def _handle_environment(args: argparse.Namespace) -> int:
    source = _resolve_gts_path(None, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="env-check" if args.environment_command == "check" else "env",
        source=source,
        runner=lambda client, resolved: _execute_environment(
            client,
            resolved,
            check=args.environment_command == "check",
            cgs=getattr(args, "cgs", None),
        ),
    )


def _execute_environment(
    client: ComplexGitSyncClient,
    source: Path,
    *,
    check: bool,
    cgs: str | None,
) -> int:
    client.load_gts(source)
    if not check:
        _print_environment(client.environment())
        return EXIT_OK
    document = CgsDocument.from_toml(cgs) if cgs else None
    drift = client.check_environment(document)
    _print_drift(drift)
    return EXIT_OK if drift.matches else EXIT_REFUSED


def _print_environment(record: TreeEnvironment) -> None:
    print(f"environment=env({record.digest()})")
    print(f"environment_root={record.environment_root or 'undeclared'}")
    print(
        f"machine architecture={record.architecture} pixi_platform={record.pixi_platform} "
        f"os={record.os_name} os_version={record.os_version} "
        f"libc={record.libc_name} libc_version={record.libc_version} "
        f"kernel={record.kernel_release}"
    )
    for tool in sorted(record.tools, key=lambda item: item.name):
        print(f"tool {tool.name} version={tool.version} raw={tool.raw}")
    for fact in record.credentials:
        print(
            f"credential provider={fact.provider} tool={fact.tool} "
            f"available={str(fact.available).lower()} authenticated={str(fact.authenticated).lower()}"
        )
    for manifest in record.manifests:
        platforms = ",".join(manifest.platforms) or "none"
        print(
            f"manifest repository={manifest.repository} path={manifest.path} "
            f"digest={manifest.digest} platforms={platforms}"
        )


def _print_drift(drift: Drift) -> None:
    print(f"matches={str(drift.matches).lower()}")
    for value in drift.missing:
        print(f"missing={value}")
    for value in drift.older:
        print(f"older={value}")
    for value in drift.undeclared:
        print(f"undeclared={value}")


__all__ = ["COMMANDS", "register_parsers"]

"""The CLI mirrors the Python API, checked rather than trusted.

`CLAUDE.md`: every capability exists in both layers -- a
`ComplexGitSyncClient` method carrying the semantics, a thin CLI command
calling it. `test_user_guide_documents_every_cli_command` checks the user-guide half.
This checks the other half, statically:

* every `_execute_*` function under `cli/` calls a client method, so no CLI
  command carries logic with nothing behind it; and
* every public client method is referenced from `cli/`, or is named below
  with the reason it is not.

The second is a ratchet with named exceptions, not a claim that all is well.
A method that gains no CLI command and is not listed fails; a listed method
that gains one, or disappears, fails too, so the list cannot go stale.

`PENDING_OWNER_DECISION` is the list the ClassFirstPackage ticket's D3 leaves
to the owner: methods with no CLI command and no internal caller, where either
adding the command or removing the method is right for different methods.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "ComplexGitSync"

#: Reached by the CLI only through another client method: lower-level pieces
#: of a command, kept public because tests and Python callers compose them.
BUILDING_BLOCKS = frozenset(
    {
        "build_installed_from",
        "clone_cgs",
        "describe_cgs",
        "discover_nested_configs",
        "fix_circularities",
        "format_project_tree",
        "freeze",
        "load_cgs",
        "load_source",
        "restart",
        "validate_branch_topology",
        "write_gts_snapshot",
    }
)

#: No CLI command and no internal caller. Each is either a command still to
#: be written or a method to be removed; the owner decides which, per method.
PENDING_OWNER_DECISION = frozenset(
    {
        "expand",
        "freeze_state",
        "get_ledger_history",
        "git",
        "initialise",
        "is_loaded",
        "launch_state",
        "load",
        "print",
        "replay_ledger",
        "validate_topology",
        "view_operation",
    }
)


def _client_public_methods() -> set[str]:
    tree = ast.parse((SRC / "orchestre" / "client.py").read_text(encoding="utf-8"))
    client = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ComplexGitSyncClient"
    )
    return {
        member.name
        for member in client.body
        if isinstance(member, ast.FunctionDef) and not member.name.startswith("_")
    }


def _cli_trees() -> list[tuple[str, ast.Module]]:
    return [
        (path.stem, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted((SRC / "cli").glob("*.py"))
    ]


def _called_attributes(node: ast.AST) -> set[str]:
    return {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    }


def test_every_cli_executor_calls_a_client_method():
    public = _client_public_methods()
    without: list[str] = []
    for stem, tree in _cli_trees():
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_execute_"):
                if not (_called_attributes(node) & public):
                    without.append(f"{stem}.{node.name}")
    assert not without, (
        "CLI commands that call no client method -- logic with nothing behind it: "
        + ", ".join(without)
    )


def test_every_public_client_method_is_reached_from_the_cli_or_named():
    public = _client_public_methods()
    referenced: set[str] = set()
    for _, tree in _cli_trees():
        referenced |= _called_attributes(tree) & public

    unreached = public - referenced
    named = BUILDING_BLOCKS | PENDING_OWNER_DECISION

    unlisted = sorted(unreached - named)
    assert not unlisted, (
        "public ComplexGitSyncClient methods with no CLI command, and no reason "
        "given for it -- add the command, or name the method in BUILDING_BLOCKS "
        f"or PENDING_OWNER_DECISION: {unlisted}"
    )

    stale = sorted(named - unreached)
    assert not stale, (
        "listed as having no CLI command, but they now have one or no longer "
        f"exist -- remove them from the list: {stale}"
    )


def test_the_two_exception_lists_do_not_overlap():
    assert BUILDING_BLOCKS.isdisjoint(PENDING_OWNER_DECISION)

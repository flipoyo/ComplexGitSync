"""cli.expert — the "Expert" cgitsync command group.

Ring: 4 (CLI adapter — the same ring cli.py itself occupies)
Contract: register argparse subparsers for, and dispatch/execute, the 15
    Expert-tier commands (purge, validate, clone, pull, pull-force,
    checkout, branch, add, rm, commit, push, tag, freeze, import-submodules,
    verify). Argument/prompt collection only — delegates all .cgs/.gts
    semantics to ComplexGitSyncClient; never touches subprocess/Git or
    parses repository identifiers itself.
Imports: _shared, git_repo, orchestre, snapshot_resolver
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from ..git_repo import RefKind
from ..orchestre import ComplexGitSyncClient
from ..snapshot_resolver import discover_cgshome
from ._shared import (
    _add_gitignore_sync_arguments,
    _format_tree_state_line,
    _load_ready_registry_source,
    _print_dry_run_plan,
    _print_gitignore_sync_report,
    _print_repo_tree_result,
    _resolve_gts_path,
    _resolve_workspace_source,
    _run_with_logging,
)

COMMANDS: dict[str, str] = {
    "purge": "Remove generated clone state for a .cgs workspace.",
    "validate": "Parse, normalize, and validate a .cgs or validate a .gts topology.",
    "clone": "Clone a nested project tree from .cgs.",
    "pull": "Resynchronise an existing project tree from .cgs or .gts.",
    "pull-force": "Destructively resynchronise an existing project tree from .cgs or .gts.",
    "checkout": "Synchronize the tree to a branch or tag.",
    "branch": "Create a branch across the full READY tree without checkout.",
    "add": "Stage all changes across a READY tree.",
    "rm": "Remove one or more tracked files, each from the repo that owns it.",
    "commit": "Commit dirty repositories from a READY tree.",
    "push": "Push repositories from a READY tree.",
    "tag": "Create and push a tag across a READY tree.",
    "freeze": "Freeze a versioned state and emit a .gts snapshot.",
    "import-submodules": "Report or convert git submodules to plain ComplexGitSync nested repositories.",
    "verify": "Verify the hash-chained .cgitsync/lgr register for tamper-evidence.",
}


def register_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register this group's 15 subparsers.

    Mirrors cli.py's build_parser() if/elif chain for exactly the Expert
    command group, but dispatches to one small ``_register_*`` builder per
    command (via ``_PARSER_BUILDERS``) instead of a single long if/elif
    chain, to stay under the C90 complexity ceiling enabled alongside this
    split. None of these 15 commands' parser registrations use
    ``_non_negative_int`` (only ``view-tree``/``discover``, owned by other
    groups, do) so no shared numeric-argument helper needs to be threaded
    through here.
    """
    for command_name, help_text in COMMANDS.items():
        subparser = subparsers.add_parser(command_name, help=help_text, description=help_text)
        _PARSER_BUILDERS[command_name](subparser)


def _add_gts_argument(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--gts",
        metavar="FILE",
        default=None,
        help=(
            "Path to the .gts snapshot that holds the READY registry. "
            "When omitted the latest .gts snapshot is discovered automatically "
            "under CGSHOME/.cgitsync/."
        ),
    )


def _add_search_dir_argument(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--search-dir",
        metavar="DIR",
        help=(
            "Directory used to resolve CGSHOME before loading "
            "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
            "or walks up from the current working directory."
        ),
    )


def _add_dry_run_argument(subparser: argparse.ArgumentParser, *, help_text: str) -> None:
    subparser.add_argument("--dry-run", action="store_true", help=help_text)


def _register_purge(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("source", help="Path to a .cgs spec")
    subparser.add_argument(
        "--output-path",
        dest="output_path",
        help=(
            "CGSPATH: parent directory used to derive CGSHOME as "
            "CGSPATH/<project-name> after the project definition is normalized "
            "(.cgs or direct CLI mode). "
            "Defaults to ../.. relative to CWD ($CGSHOME/ComplexGitSync)."
        ),
    )
    subparser.set_defaults(handler=_handle_purge)


def _register_validate(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("source", help="Path to the local .cgs or .gts file to validate.")
    subparser.add_argument(
        "--discover-nested",
        action="store_true",
        help="Resolve nested .cgs files for locally available child repos.",
    )
    subparser.set_defaults(handler=_handle_validate)


def _register_clone(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("source", help="Path to the local .cgs file to clone from.")
    subparser.add_argument(
        "--target-dir",
        help="Target directory for the cloned project root. Defaults to ./<project-name>.",
    )
    subparser.add_argument(
        "--output-path",
        dest="output_path",
        help=(
            "Base directory where the project folder is created. "
            "The project name from the .cgs file is appended automatically."
        ),
    )
    subparser.set_defaults(handler=_handle_clone)


def _register_pull_source_and_search_dir(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "source",
        nargs="?",
        default=None,
        help=(
            "Path to the local .cgs or .gts file to pull from. "
            "When omitted the latest .gts snapshot is discovered automatically "
            "under CGSHOME/.cgitsync/."
        ),
    )
    _add_search_dir_argument(subparser)


def _register_pull(subparser: argparse.ArgumentParser) -> None:
    _register_pull_source_and_search_dir(subparser)
    _add_gitignore_sync_arguments(subparser)
    subparser.set_defaults(handler=_handle_pull)


def _register_pull_force(subparser: argparse.ArgumentParser) -> None:
    _register_pull_source_and_search_dir(subparser)
    subparser.set_defaults(handler=_handle_pull_force)


def _register_checkout(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("branch", help="Branch or tag name to check out across the tree.")
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    subparser.add_argument(
        "--ref-kind",
        choices=["branch", "tag"],
        default="branch",
        help="Kind of ref to check out (default: branch).",
    )
    subparser.set_defaults(handler=_handle_checkout)


def _register_branch(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("branch", help="Branch name to create across the READY tree.")
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    subparser.set_defaults(handler=_handle_branch)


def _register_commit(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "message",
        nargs="?",
        help="Commit message applied to all repos with staged changes.",
    )
    subparser.add_argument(
        "-m",
        "--message",
        dest="message_option",
        help="Commit message applied to all repos with staged changes.",
    )
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    subparser.add_argument(
        "--no-stage",
        action="store_true",
        help="Skip automatic 'git add --all' before committing.",
    )
    _add_dry_run_argument(subparser, help_text="Preview the commit execution plan without mutating repositories.")
    subparser.set_defaults(handler=_handle_commit)


def _register_add(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help=(
            "Path(s) to stage, each resolved (relative to CGSHOME, or absolute) to the "
            "one repo in the tree that owns it and staged there individually. Omit to stage "
            "every repo in full (git add --all), tree-wide, leaf-first -- today's default."
        ),
    )
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    _add_dry_run_argument(subparser, help_text="Preview the add execution plan without mutating repositories.")
    subparser.set_defaults(handler=_handle_add)


def _register_rm(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "paths",
        nargs="+",
        metavar="PATH",
        help=(
            "Path(s) to remove, each resolved (relative to CGSHOME, or absolute) to the "
            "one repo in the tree that owns it, deleted from disk there, and staged. A plain "
            "tracked file only -- a directory or a nonexistent path errors clearly rather "
            "than partially applying."
        ),
    )
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    _add_dry_run_argument(subparser, help_text="Preview the rm execution plan without mutating repositories.")
    subparser.set_defaults(handler=_handle_rm)


def _register_push(subparser: argparse.ArgumentParser) -> None:
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    _add_dry_run_argument(subparser, help_text="Preview the push execution plan without mutating repositories.")
    subparser.set_defaults(handler=_handle_push)


def _register_tag(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("name", help="Tag name to create and push across the READY tree.")
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    subparser.set_defaults(handler=_handle_tag)


def _register_freeze(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("name", help="Version tag name used for commit, tag, and push.")
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    _add_dry_run_argument(subparser, help_text="Preview the freeze execution plan without mutating repositories.")
    subparser.set_defaults(handler=_handle_freeze)


def _register_import_submodules(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "repo_root",
        help=(
            "Path to the local git repository whose .gitmodules file "
            "lists the submodules to import."
        ),
    )
    subparser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help=(
            "Perform the conversion: run 'git rm --cached' for each "
            "submodule, remove its .gitmodules stanza, and update "
            ".gitignore. Without this flag the command only prints "
            "what would change (dry-run)."
        ),
    )
    subparser.set_defaults(handler=_handle_import_submodules)


def _register_verify(subparser: argparse.ArgumentParser) -> None:
    _add_search_dir_argument(subparser)
    subparser.add_argument(
        "--repair",
        action="store_true",
        help=(
            "Repair a stale HEAD cache to match the recomputed true "
            "head. Never rewrites or deletes a register entry — a "
            "broken chain is reported, not healed."
        ),
    )
    subparser.set_defaults(handler=_handle_verify)


_PARSER_BUILDERS: dict[str, Callable[[argparse.ArgumentParser], None]] = {
    "purge": _register_purge,
    "validate": _register_validate,
    "clone": _register_clone,
    "pull": _register_pull,
    "pull-force": _register_pull_force,
    "checkout": _register_checkout,
    "branch": _register_branch,
    "commit": _register_commit,
    "add": _register_add,
    "rm": _register_rm,
    "push": _register_push,
    "tag": _register_tag,
    "freeze": _register_freeze,
    "import-submodules": _register_import_submodules,
    "verify": _register_verify,
}


def _handle_purge(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    output_path = getattr(args, "output_path", None)
    client = ComplexGitSyncClient()
    project_root = client.resolve_initialise_cgshome(source_path, output_path=output_path)
    return _run_with_logging(
        command_name="purge",
        source=source_path,
        client=client,
        project_root=project_root,
        runner=lambda active_client, source: _execute_purge_cgs(
            active_client,
            source,
            output_path=output_path,
        ),
    )


def _handle_validate(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="validate",
        source=Path(args.source),
        runner=lambda client, source: _execute_validate(client, source, discover_nested=args.discover_nested),
    )


def _handle_clone(args: argparse.Namespace) -> int:
    client = ComplexGitSyncClient()
    project_root = client.resolve_clone_root(
        Path(args.source),
        target_dir=args.target_dir,
        output_path=getattr(args, "output_path", None),
    )
    return _run_with_logging(
        command_name="clone",
        source=Path(args.source),
        client=client,
        project_root=project_root,
        runner=lambda active_client, source: _execute_clone(
            active_client,
            source,
            target_dir=args.target_dir,
            output_path=getattr(args, "output_path", None),
        ),
    )


def _handle_pull(args: argparse.Namespace) -> int:
    source = _resolve_workspace_source(args.source, getattr(args, "search_dir", None))
    commit_gitignore = getattr(args, "commit_gitignore", False)
    force_gitignore_sync = getattr(args, "force_gitignore_sync", False)
    git_user_name = getattr(args, "git_user_name", None)
    git_user_email = getattr(args, "git_user_email", None)
    return _run_with_logging(
        command_name="pull",
        source=source,
        runner=lambda client, source: _execute_pull(
            client,
            source,
            commit_gitignore=commit_gitignore,
            force_gitignore_sync=force_gitignore_sync,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
        ),
    )


def _handle_pull_force(args: argparse.Namespace) -> int:
    source = _resolve_workspace_source(args.source, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="pull-force",
        source=source,
        runner=lambda client, source: _execute_pull_force(client, source),
    )


def _handle_checkout(args: argparse.Namespace) -> int:
    ref_kind = RefKind.TAG if args.ref_kind == "tag" else RefKind.BRANCH
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="checkout",
        source=gts_path,
        runner=lambda client, source: _execute_checkout(client, source, branch=args.branch, ref_kind=ref_kind),
    )


def _handle_branch(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="branch",
        source=gts_path,
        runner=lambda client, source: _execute_branch(client, source, branch=args.branch),
    )


def _handle_commit(args: argparse.Namespace) -> int:
    message = _resolve_commit_message(args)
    if message is None:
        print("cgitsync commit: error: provide exactly one message argument or -m/--message", file=sys.stderr)
        return 2
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="commit",
        source=gts_path,
        runner=lambda client, source: _execute_commit(
            client,
            source,
            message=message,
            stage_all=not args.no_stage,
            dry_run=args.dry_run,
        ),
    )


def _resolve_commit_message(args: argparse.Namespace) -> str | None:
    positional = getattr(args, "message", None)
    option = getattr(args, "message_option", None)
    if positional and option:
        return None
    return option or positional


def _handle_add(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    paths = getattr(args, "paths", None) or None
    return _run_with_logging(
        command_name="add",
        source=gts_path,
        runner=lambda client, source: _execute_add(client, source, paths=paths, dry_run=args.dry_run),
    )


def _handle_rm(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="rm",
        source=gts_path,
        runner=lambda client, source: _execute_rm(client, source, paths=args.paths, dry_run=args.dry_run),
    )


def _handle_push(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="push",
        source=gts_path,
        runner=lambda client, source: _execute_push(client, source, dry_run=args.dry_run),
    )


def _handle_tag(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="tag",
        source=gts_path,
        runner=lambda client, source: _execute_tag(client, source, name=args.name),
    )


def _handle_freeze(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="freeze",
        source=gts_path,
        runner=lambda client, source: _execute_freeze(client, source, name=args.name, dry_run=args.dry_run),
    )


def _handle_import_submodules(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    apply = args.apply
    return _run_with_logging(
        command_name="import-submodules",
        source=repo_root,
        runner=lambda client, source: _execute_import_submodules(
            client,
            source,
            apply=apply,
        ),
    )


def _handle_verify(args: argparse.Namespace) -> int:
    cgshome = discover_cgshome(getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="verify",
        source=cgshome,
        runner=lambda client, source: _execute_verify(client, source, repair=args.repair),
    )


def _execute_purge_cgs(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    output_path: str | None = None,
) -> int:
    if source_path.suffix != ".cgs":
        raise ValueError("purge expects a .cgs source.")
    print("operation_sequence=GT-LOAD->GT-DISCOVER->GT-VALIDATE->FS-PURGE")
    print("workflow=load->expand->validate->purge")
    removed = client.purge(source_path, output_path=output_path)
    if removed:
        print("removed:")
        for path in removed:
            print(path)
    else:
        print("removed: none")
    return 0


def _execute_validate(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> int:
    tree_state = client.validate(source_path, discover_nested=discover_nested)
    print(_format_tree_state_line(tree_state))
    return 0


def _execute_verify(
    client: ComplexGitSyncClient,
    cgshome: Path,
    *,
    repair: bool,
) -> int:
    report = client.verify(cgshome, repair=repair)
    print(f"cgshome={cgshome}")
    if report.is_clean:
        print("status=clean")
        print("findings=0")
        return 0
    print("status=findings")
    print(f"findings={len(report.findings)}")
    for seq, finding, detail in report.findings:
        print(f"seq={seq} finding={finding.name} detail={detail}")
    if repair:
        print("repair=attempted (HEAD cache only; entries are never rewritten or deleted)")
    return 1


def _execute_clone(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    target_dir: str | None,
    output_path: str | None = None,
) -> int:
    print("git_command=git clone (executed per repo)")
    registry = client.clone(source_path, target_dir=target_dir, output_path=output_path)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    return 0


def _execute_pull(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    commit_gitignore: bool = False,
    force_gitignore_sync: bool = False,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
) -> int:
    registry = client.pull(
        source_path,
        commit_gitignore=commit_gitignore,
        force_gitignore_sync=force_gitignore_sync,
        git_user_name=git_user_name,
        git_user_email=git_user_email,
    )
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    _print_gitignore_sync_report(client)
    _print_repo_tree_result(client)
    return 0


def _execute_pull_force(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> int:
    print("git_command=git fetch && git checkout -B <branch> FETCH_HEAD && git clean -fd (executed per repo)")
    registry = client.pull_force(source_path)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    _print_repo_tree_result(client)
    return 0


def _execute_checkout(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    branch: str,
    ref_kind: RefKind,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git checkout {branch}")
    client.checkout(branch, ref_kind=ref_kind)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"branch={branch}"
    )
    _print_repo_tree_result(client)
    return 0


def _execute_branch(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    branch: str,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git branch {branch}")
    client.branch(branch)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"branch={branch}"
    )
    _print_repo_tree_result(client)
    return 0


def _execute_commit(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    message: str,
    stage_all: bool,
    dry_run: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git commit -m {message!r}")
    if dry_run:
        _print_dry_run_plan(
            client,
            command_name="commit",
            actions=(
                "git add --all" if stage_all else "skip git add --all (--no-stage)",
                f"git commit -m {message!r}",
            ),
        )
    else:
        client.commit(message, stage_all=stage_all)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"message={message!r}"
    )
    if not dry_run:
        _print_repo_tree_result(client)
    return 0


def _execute_add(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    paths: list[str] | None = None,
    dry_run: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    action = f"git add -- {' '.join(paths)}" if paths else "git add --all"
    print(f"git_command={action}")
    if dry_run:
        _print_dry_run_plan(client, command_name="add", actions=(action,))
    else:
        client.add(paths=paths)
    tree_state = client.get_tree_state()
    print(_format_tree_state_line(tree_state))
    if not dry_run:
        _print_repo_tree_result(client)
    return 0


def _execute_rm(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    paths: list[str],
    dry_run: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    action = f"git rm -- {' '.join(paths)}"
    print(f"git_command={action}")
    if dry_run:
        _print_dry_run_plan(client, command_name="rm", actions=(action,))
    else:
        client.remove(paths)
    tree_state = client.get_tree_state()
    print(_format_tree_state_line(tree_state))
    if not dry_run:
        _print_repo_tree_result(client)
    return 0


def _execute_push(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    dry_run: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    print("git_command=git push (-u origin <branch> when upstream is missing)")
    if dry_run:
        _print_dry_run_plan(
            client,
            command_name="push",
            actions=("git push", "git push -u origin <branch> when upstream is missing"),
        )
    else:
        client.push()
    tree_state = client.get_tree_state()
    print(_format_tree_state_line(tree_state))
    if not dry_run:
        _print_repo_tree_result(client)
    return 0


def _execute_tag(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    name: str,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git tag {name} && git push origin {name}")
    client.tag(name)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"name={name}"
    )
    _print_repo_tree_result(client)
    return 0


def _execute_freeze(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    name: str,
    dry_run: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git add --all && git commit -m {name!r} && git tag {name} && git push")
    if dry_run:
        _print_dry_run_plan(
            client,
            command_name="freeze",
            actions=("git add --all", f"git commit -m {name!r}", f"git tag {name}", "git push"),
        )
    else:
        client.freeze(name)
    tree_state = client.get_tree_state()
    snapshot_path = getattr(client, "loaded_snapshot_path", None)
    snapshot_suffix = f" snapshot={snapshot_path}" if snapshot_path is not None else ""
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"name={name}"
        f"{snapshot_suffix}"
    )
    if not dry_run:
        _print_repo_tree_result(client)
    return 0


def _execute_import_submodules(
    client: ComplexGitSyncClient,
    source: Path,
    *,
    apply: bool = False,
) -> int:
    """Execute the import-submodules command and print a human-readable report."""
    report = client.import_submodules(source, apply=apply)

    if not report.submodules:
        print(f"No .gitmodules found at {source} — nothing to import.")
        return 0

    if not apply:
        print(f"Dry run — {len(report.submodules)} submodule(s) in {source}/.gitmodules")
        print("Pass --apply to perform the conversion.\n")
        for sub in report.submodules:
            print(f"  submodule: {sub.name}")
            print(f"    path:   {sub.path}")
            print(f"    url:    {sub.url}")
            print(f"    branch: {sub.branch}")
            print()
        return 0

    print(f"Converted {len(report.converted)} submodule(s) in {source}:")
    for name in report.converted:
        sub = next(s for s in report.submodules if s.name == name)
        print(f"  ✓ {name}  ({sub.path})")
    return 0

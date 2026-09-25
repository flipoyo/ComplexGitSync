"""cli.expert — the "Expert" cgitsync command group.

Ring: 4. Contract: register, dispatch, and execute the 21 Expert-tier commands
    (purge, validate, clone, pull, pull-force, autofix,
    checkout, branch, close-branch, add, rm, commit, merge, push, tag, freeze,
    import-submodules, init-from-submodules, verify, memory, self-history).
    Argument/prompt collection only — delegates all semantics to
    ComplexGitSyncClient; never touches Git.
Imports: _shared, errors, git_repo, memory, orchestre
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from ..errors import GitSyncError
from ..git_repo import RefKind, RepoScope
from ..memory.integrity import HistoryState
from ..memory.self_history import (
    VALID_AGENT_ROLES,
    VALID_CONFORMITY_BASES,
    AgentInfo,
    ConformityCriterion,
    ConformityScore,
)
from ..orchestre import ComplexGitSyncClient
from ._shared import (
    _add_gitignore_sync_arguments,
    _add_json_argument,
    _format_tree_state_line,
    _json_stdout,
    _load_ready_registry_source,
    _memory_declared_for_dry_run,
    _non_negative_int,
    _print_dry_run_plan,
    _print_gitignore_sync_report,
    _print_memory_fold_outcome,
    _print_repo_tree_result,
    _print_write_outcomes,
    _resolve_cgshome,
    _resolve_gts_path,
    _resolve_workspace_source,
    _resolve_write_scope,
    _run_with_logging,
    _warn_paths_reaching_configuration_repos,
)
from .exit_codes import EXIT_OK, EXIT_REFUSED

COMMANDS: dict[str, str] = {
    "purge": "Remove generated clone state for a .cgs workspace.",
    "validate": "Parse, normalize, and validate a .cgs or validate a .gts topology.",
    "clone": "Clone a nested project tree from .cgs.",
    "pull": "Resynchronise an existing project tree from .cgs or .gts.",
    "pull-force": "Destructively resynchronise an existing project tree from .cgs or .gts.",
    "autofix": "Diagnose and repair the situation named by the last failing command's error.",
    "checkout": "Synchronize the tree to a branch or tag.",
    "branch": "Create a branch across the full READY tree without checkout.",
    "close-branch": "Rename a branch to its closed name, tree-wide, leaf-first.",
    "add": "Stage all changes across a READY tree.",
    "rm": "Remove one or more tracked files, each from the repo that owns it.",
    "commit": "Commit dirty repositories from a READY tree.",
    "merge": "Merge a project branch across a READY tree, leaf-first.",
    "push": "Push repositories from a READY tree.",
    "tag": "Create and push a tag across a READY tree.",
    "freeze": "Freeze a versioned state and emit a .gts snapshot.",
    "import-submodules": "Report or convert git submodules to plain ComplexGitSync nested repositories.",
    "init-from-submodules": "Adopt a submodule-based checkout: discover, initialise, then convert its submodules.",
    "verify": "Verify the hash-chained .cgitsync/lgr register for tamper-evidence.",
    "memory": "Look at what this workspace remembers: status, list, show <state>, explore, reboot.",
    "self-history": "Record one piece of agent work: add.",
}


def register_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register this group's 19 subparsers.

    Mirrors cli.py's build_parser() if/elif chain for exactly the Expert
    command group, but dispatches to one small ``_register_*`` builder per
    command (via ``_PARSER_BUILDERS``) instead of a single long if/elif
    chain, to stay under the C90 complexity ceiling enabled alongside this
    split. One command's parser registration needs a numeric argument type
    (``init-from-submodules --max-depth``); it uses ``_shared``'s own
    ``_non_negative_int`` directly, the same helper ``cli.configuration``
    has threaded in for ``view-tree``/``discover``.
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


def _add_force_protocol_argument(subparser: argparse.ArgumentParser, *, command_name: str) -> None:
    subparser.add_argument(
        "--force-protocol",
        dest="force_access_protocol",
        choices=("ssh", "https"),
        default=None,
        help=(
            f"Rewrite every repo's remote to this protocol before running "
            f"'{command_name}', persisting the change (git remote "
            f"set-url) rather than a one-off override. Same meaning as "
            f"initialise/bootstrap's --force-protocol, applied to an "
            f"already-cloned tree instead of at clone time."
        ),
    )


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
    _add_force_protocol_argument(subparser, command_name="pull")
    subparser.add_argument(
        "--private",
        action="store_true",
        help=(
            "Instead of resynchronising the whole tree, bring each writable "
            "configuration repository up to date with its base branch: fetch, then "
            "merge '<its default_branch>' into the derived branch it is on. Use it "
            "while a project feature branch is open, so its settings branch does not "
            "drift behind the project's. Read-only configuration repositories are "
            "never touched."
        ),
    )
    subparser.set_defaults(handler=_handle_pull)


def _register_pull_force(subparser: argparse.ArgumentParser) -> None:
    _register_pull_source_and_search_dir(subparser)
    _add_force_protocol_argument(subparser, command_name="pull-force")
    _add_private_argument(subparser, verb="Force-resynchronise")
    subparser.set_defaults(handler=_handle_pull_force)


def _register_autofix(subparser: argparse.ArgumentParser) -> None:
    _register_pull_source_and_search_dir(subparser)
    subparser.add_argument(
        "--error",
        default=None,
        help=(
            "The error text to diagnose, instead of reading the most recent "
            "failing command from .cgitsync/logs/ — the owner's own "
            "'it takes the former error as an entry'."
        ),
    )
    subparser.add_argument(
        "--repo",
        dest="repo_name",
        default=None,
        help=(
            "The mounted repository to repair (e.g. .memory), instead of "
            "guessing it from --error."
        ),
    )
    subparser.set_defaults(handler=_handle_autofix)


def _add_private_argument(subparser: argparse.ArgumentParser, *, verb: str) -> None:
    """Add ``--private``: act on the writable configuration repositories alone.

    Every command that touches Git takes it, so a user who wants to work on
    their configuration repositories on their own never has to reach for
    plain ``git``. Without it a command keeps its usual reach — which for
    ``checkout`` and ``branch`` is the whole tree, because a private/local
    repository already resolves its own branch name and needs no separate
    invocation.
    """
    subparser.add_argument(
        "--private",
        action="store_true",
        help=(
            f"{verb} only the tree's writable configuration repositories -- the "
            "entries a .cgs declares 'private = true, writable = true'. Read-only "
            "configuration repositories are never written to."
        ),
    )


def _add_scope_arguments(subparser: argparse.ArgumentParser) -> None:
    """Add ``--private`` and ``--all``: the two ways to move off the default.

    For the commands that write this project's own history --- ``add``,
    ``commit``, ``push``, ``merge`` --- the bare form has always meant "the
    repositories this project owns" and still does. ``--private`` swaps that
    for the writable configuration repositories; ``--all`` takes both in one
    pass. They are mutually exclusive because ``--all`` already includes what
    ``--private`` selects.

    ``--all`` is the user's word, not the code's: internally it maps to
    ``RepoScope.WRITABLE``, which is narrower than ``RepoScope.ALL``. No form
    of either flag ever writes to a read-only configuration repository, which
    is why the help text says so rather than leaving "all" to be read
    literally.
    """
    group = subparser.add_mutually_exclusive_group()
    group.add_argument(
        "--private",
        action="store_true",
        help=(
            "Act on the tree's writable configuration repositories instead of "
            "this project's own -- the entries a .cgs declares 'private = true, "
            "writable = true'. Without it the command touches only the "
            "repositories this project owns, and leaves every shared one alone."
        ),
    )
    group.add_argument(
        "--all",
        dest="all_writable",
        action="store_true",
        help=(
            "Act on both halves in one pass: this project's own repositories "
            "and its writable configuration ones, sharing a single commit "
            "message. Read-only configuration repositories are never written "
            "to by this or any other form. Cannot be combined with --private."
        ),
    )


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
    _add_private_argument(subparser, verb="Check out")
    subparser.set_defaults(handler=_handle_checkout)


def _register_branch(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("branch", help="Branch name to create across the READY tree.")
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    _add_private_argument(subparser, verb="Create the branch in")
    subparser.set_defaults(handler=_handle_branch)


def _register_close_branch(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "branch", help="Branch name to close (rename to closed/<branch>) across the READY tree."
    )
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    _add_private_argument(subparser, verb="Close the branch in")
    subparser.set_defaults(handler=_handle_close_branch)


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
    _add_scope_arguments(subparser)
    _add_dry_run_argument(subparser, help_text="Preview the commit execution plan without mutating repositories.")
    subparser.set_defaults(handler=_handle_commit)


def _register_merge(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "branch",
        help=(
            "The PROJECT branch to merge, always -- not the branch each repository "
            "will actually merge. A private/local configuration repository merges "
            "the branch derived from it, '<its default_branch>_<branch>', because "
            "that is where its settings for this project branch live."
        ),
    )
    subparser.add_argument(
        "--into",
        metavar="TARGET",
        help=(
            "Check out this PROJECT branch and merge into it, in one command. "
            "Without it, the merge goes into whatever is checked out. Use it "
            "whenever the target holds an older ComplexGitSync: a separate "
            "'checkout' would install that older build, and the merge after it "
            "would run under it."
        ),
    )
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    _add_scope_arguments(subparser)
    group = subparser.add_mutually_exclusive_group()
    group.add_argument(
        "--ff-only",
        action="store_true",
        help="Refuse any merge that is not a fast-forward.",
    )
    group.add_argument(
        "--no-ff",
        action="store_true",
        help="Always record a merge commit, even when a fast-forward is possible.",
    )
    subparser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be merged, in order, without merging anything.",
    )
    subparser.add_argument(
        "--resolve",
        action="store_true",
        help=(
            "Merge one repository at a time and stop at the first conflict, "
            "then open it in a merge tool. Gives up the guarantee that a "
            "conflict anywhere leaves the tree untouched."
        ),
    )
    subparser.set_defaults(handler=_handle_merge)


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
    _add_scope_arguments(subparser)
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
    _add_private_argument(subparser, verb="Remove the paths from")
    subparser.set_defaults(handler=_handle_rm)


def _register_push(subparser: argparse.ArgumentParser) -> None:
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    _add_scope_arguments(subparser)
    _add_dry_run_argument(subparser, help_text="Preview the push execution plan without mutating repositories.")
    _add_force_protocol_argument(subparser, command_name="push")
    subparser.set_defaults(handler=_handle_push)


def _register_tag(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("name", help="Tag name to create and push across the READY tree.")
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    _add_private_argument(subparser, verb="Tag")
    subparser.set_defaults(handler=_handle_tag)


def _register_freeze(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("name", help="Version tag name used for commit, tag, and push.")
    _add_gts_argument(subparser)
    _add_search_dir_argument(subparser)
    _add_dry_run_argument(subparser, help_text="Preview the freeze execution plan without mutating repositories.")
    _add_private_argument(subparser, verb="Freeze")
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
    subparser.add_argument(
        "--recursive",
        action="store_true",
        default=False,
        help=(
            "Also convert any submodule that itself has its own "
            "checked-out .gitmodules, at any depth (same meaning as "
            "'git submodule update --recursive'). Without this flag, "
            "only REPO_ROOT's own .gitmodules is converted."
        ),
    )
    subparser.set_defaults(handler=_handle_import_submodules)


def _register_init_from_submodules(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "repo_root",
        help=(
            "Path to the checkout to adopt: a project cloned and "
            "'git submodule update --init --recursive'd by hand, with no "
            ".cgs of its own yet. Its directory name must match the "
            "project name discovery derives from the root repository's "
            "own address."
        ),
    )
    subparser.add_argument(
        "--cgs",
        dest="cgs_path",
        metavar="FILE",
        default=None,
        help=(
            "Use this .cgs instead of writing one. When FILE does not "
            "exist, the drafted .cgs is written there instead of to "
            "REPO_ROOT/<project-name>.cgs."
        ),
    )
    subparser.add_argument(
        "--max-depth",
        dest="max_depth",
        type=_non_negative_int,
        default=None,
        metavar="N",
        help=(
            "Bound discovery to N directory levels below REPO_ROOT "
            "(REPO_ROOT itself is depth 0). Without this flag the scan "
            "is unbounded."
        ),
    )
    subparser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Print what would be discovered and converted without writing "
            "a .cgs, cloning anything, or touching any repository."
        ),
    )
    subparser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help=(
            "Proceed even when REPO_ROOT has no .gitmodules. Refused by "
            "default: there would be nothing to convert, while the clone "
            "step would still delete and re-clone every non-root "
            "repository from its remote."
        ),
    )
    subparser.add_argument(
        "--force-protocol",
        dest="force_access_protocol",
        choices=("ssh", "https"),
        default=None,
        help=(
            "Override access_protocol in memory for every repo the clone step "
            "fetches. No .cgs file is read differently or written. Discovery "
            "reads each repository's configured remote as it is, so it is "
            "unaffected."
        ),
    )
    subparser.set_defaults(handler=_handle_init_from_submodules)


def _register_memory(subparser: argparse.ArgumentParser) -> None:
    """``memory status|list|show`` — read-only, for now.

    A group rather than three flat commands: they answer one subject, and
    the next milestones add more of them (a push, an adopt). Read-only
    because there is nowhere to push a memory to yet.
    """
    memory_commands = subparser.add_subparsers(dest="memory_command", required=True)

    status = memory_commands.add_parser(
        "status", help="How much this workspace remembers, and whether it verifies."
    )
    _add_search_dir_argument(status)

    listing = memory_commands.add_parser(
        "list", help="Every State this workspace holds, newest recording first."
    )
    _add_search_dir_argument(listing)

    show = memory_commands.add_parser(
        "show", help="One State: what it recorded, and every entry that names it."
    )
    show.add_argument(
        "state",
        help="The State's content hash, or any unambiguous prefix of it.",
    )
    show.add_argument(
        "--full",
        action="store_true",
        help="Print whole commit messages instead of their first line.",
    )
    _add_search_dir_argument(show)

    initialise = memory_commands.add_parser(
        "init",
        help="Propose the .cgs entry that mounts this project's memory.",
    )
    initialise.add_argument(
        "--owner",
        help="Account the memory repository belongs to. Defaults to the project's own.",
    )
    _add_search_dir_argument(initialise)

    clone = memory_commands.add_parser(
        "clone", help="Bring this project's memory onto a machine that lacks it."
    )
    clone.add_argument("--owner", help="Account the memory repository belongs to.")
    clone.add_argument("--branch", help="Branch to clone. Defaults to this project's.")
    clone.add_argument(
        "--remote",
        help="Clone from this address instead of the one the project's owner implies.",
    )
    _add_search_dir_argument(clone)

    mount = memory_commands.add_parser(
        "mount",
        help="Add this project's memory to a .cgs that already exists.",
    )
    mount.add_argument(
        "--cgs",
        metavar="FILE",
        help="The .cgs to add the entry to. Defaults to the one this tree was built from.",
    )
    mount.add_argument("--owner", help="Account the memory repository belongs to.")
    _add_search_dir_argument(mount)

    adopt = memory_commands.add_parser(
        "adopt",
        help="Make the memory already on this disk be the memory repository.",
    )
    adopt.add_argument("--owner", help="Account the memory repository belongs to.")
    adopt.add_argument("--branch", help="Branch to adopt. Defaults to this project's.")
    adopt.add_argument(
        "--remote", help="Adopt this address instead of the one the project's owner implies."
    )
    adopt.add_argument(
        "--reboot",
        action="store_true",
        help="Adopt the repository identity, but start its content fresh rather than "
        "carrying forward whatever the fallback branch already holds.",
    )
    _add_search_dir_argument(adopt)

    migrate = memory_commands.add_parser(
        "migrate",
        help="Move a memory mounted before WorkingTransitionState onto its new layout.",
    )
    migrate.add_argument(
        "--cgs",
        metavar="FILE",
        help="The .cgs declaring the mount. Defaults to the one this tree was built from.",
    )
    _add_search_dir_argument(migrate)

    branch = memory_commands.add_parser(
        "branch",
        help="Create the memory branch another project branch needs, and push it.",
    )
    branch.add_argument(
        "--project-branch",
        required=True,
        metavar="NAME",
        help="The project branch whose memory branch to create, such as main.",
    )
    branch.add_argument(
        "--no-push", action="store_true", help="Create it locally and do not push."
    )
    _add_search_dir_argument(branch)

    push = memory_commands.add_parser(
        "push", help="Commit what the memory gained and push it."
    )
    push.add_argument("-m", "--message", help="Commit message. One is generated otherwise.")
    _add_search_dir_argument(push)

    explore = memory_commands.add_parser(
        "explore",
        help="A memory a person can read: by branch, or the whole ledger in order.",
    )
    explore.add_argument(
        "--branch",
        metavar="NAME",
        help="Explore this memory branch. Defaults to the one checked out here.",
    )
    explore.add_argument(
        "--timeline",
        action="store_true",
        help="Every ledger entry in order, not only the commits that were published.",
    )
    _add_search_dir_argument(explore)

    reboot = memory_commands.add_parser(
        "reboot",
        help="Archive this memory's current branch and start a fresh, empty one under its name.",
    )
    _add_search_dir_argument(reboot)

    self_history = memory_commands.add_parser(
        "self-history",
        help="Every self-history record this workspace holds, folded and pending.",
    )
    _add_search_dir_argument(self_history)

    subparser.set_defaults(handler=_handle_memory)


_AGENT_ROLE_CHOICES = tuple(sorted(VALID_AGENT_ROLES))
_CONFORMITY_BASIS_CHOICES = tuple(sorted(VALID_CONFORMITY_BASES))


def _add_agent_arguments(subparser: argparse.ArgumentParser, prefix: str, label: str) -> None:
    subparser.add_argument(
        f"--{prefix}-role", required=True, choices=_AGENT_ROLE_CHOICES,
        help=f"The {label}'s role, from .localSpec/AGENT.md's roster.",
    )
    subparser.add_argument(f"--{prefix}-vendor", required=True, help=f"The {label}'s vendor.")
    subparser.add_argument(f"--{prefix}-model", required=True, help=f"The {label}'s model version.")


def _add_conformity_arguments(subparser: argparse.ArgumentParser, prefix: str, label: str) -> None:
    subparser.add_argument(
        f"--{prefix}-score", required=True, type=float,
        help=f"{label} score (0-33, or 0-34 for quality).",
    )
    subparser.add_argument(
        f"--{prefix}-basis", required=True, choices=_CONFORMITY_BASIS_CHOICES,
        help=f"Whether {label} was measured by the tool or asserted by the orchestrator.",
    )
    subparser.add_argument(
        f"--{prefix}-reasoning", required=True, help=f"One line: why this {label} score."
    )


def _register_self_history(subparser: argparse.ArgumentParser) -> None:
    """``self-history add`` — one record of one piece of agent work.

    A group of its own, not folded into ``memory``, because the ticket
    that designed it (AgentReport) names the command ``cgitsync
    self-history add`` explicitly and the mount it writes into is a
    second, separate repository nested inside the memory mount, not the
    memory mount itself.
    """
    self_history_commands = subparser.add_subparsers(dest="self_history_command", required=True)

    add = self_history_commands.add_parser(
        "add", help="Record one piece of agent work to the pending half."
    )
    add.add_argument("--ticket", required=True, help="The ticket served, by its short name.")
    add.add_argument("--goal", required=True, help="The ticket's objective, at most 3 lines.")
    add.add_argument("--action", required=True, help="The main action taken, at most 3 lines.")
    _add_agent_arguments(add, "worker", "worker")
    _add_agent_arguments(add, "orchestrator", "orchestrator")
    _add_conformity_arguments(add, "spec-respect", "spec respect")
    _add_conformity_arguments(add, "gating", ".PUBLIC/.PRIVATE gating")
    _add_conformity_arguments(add, "quality", "quality of production")
    add.add_argument(
        "--state-before", default="", help="state(<hash>) before the work, if known."
    )
    add.add_argument("--state-after", default="", help="state(<hash>) after the work, if known.")
    lint_group = add.add_mutually_exclusive_group()
    lint_group.add_argument("--lint-passed", action="store_true", default=None, dest="lint_passed")
    lint_group.add_argument("--lint-failed", action="store_false", dest="lint_passed")
    tests_group = add.add_mutually_exclusive_group()
    tests_group.add_argument("--tests-passed", action="store_true", default=None, dest="tests_passed")
    tests_group.add_argument("--tests-failed", action="store_false", dest="tests_passed")
    add.add_argument(
        "--pushed", action="store_true", help="Something reached a remote this session."
    )
    add.add_argument(
        "--pushed-reason", default="", help="On whose instruction, if --pushed was given."
    )
    _add_search_dir_argument(add)

    adopt = self_history_commands.add_parser(
        "adopt",
        help="Retrofit self-history onto a .memory adopted before it existed.",
    )
    adopt.add_argument("--owner", help="Account the self-history repository belongs to.")
    adopt.add_argument("--branch", help="Branch to adopt. Defaults to .memory's own current branch.")
    _add_search_dir_argument(adopt)

    subparser.set_defaults(handler=_handle_self_history)


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
    _add_json_argument(subparser)
    subparser.set_defaults(handler=_handle_verify)


_PARSER_BUILDERS: dict[str, Callable[[argparse.ArgumentParser], None]] = {
    "purge": _register_purge,
    "validate": _register_validate,
    "clone": _register_clone,
    "pull": _register_pull,
    "pull-force": _register_pull_force,
    "autofix": _register_autofix,
    "checkout": _register_checkout,
    "branch": _register_branch,
    "close-branch": _register_close_branch,
    "commit": _register_commit,
    "merge": _register_merge,
    "add": _register_add,
    "rm": _register_rm,
    "push": _register_push,
    "tag": _register_tag,
    "freeze": _register_freeze,
    "import-submodules": _register_import_submodules,
    "init-from-submodules": _register_init_from_submodules,
    "verify": _register_verify,
    "memory": _register_memory,
    "self-history": _register_self_history,
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
    if getattr(args, "private", False):
        return _run_with_logging(
            command_name="pull",
            source=source,
            runner=lambda client, source: _execute_pull_private(client, source),
        )
    commit_gitignore = getattr(args, "commit_gitignore", False)
    force_gitignore_sync = getattr(args, "force_gitignore_sync", False)
    git_user_name = getattr(args, "git_user_name", None)
    git_user_email = getattr(args, "git_user_email", None)
    force_access_protocol = getattr(args, "force_access_protocol", None)
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
            force_access_protocol=force_access_protocol,
        ),
    )


def _handle_pull_force(args: argparse.Namespace) -> int:
    source = _resolve_workspace_source(args.source, getattr(args, "search_dir", None))
    force_access_protocol = getattr(args, "force_access_protocol", None)
    return _run_with_logging(
        command_name="pull-force",
        source=source,
        runner=lambda client, source: _execute_pull_force(
            client,
            source,
            force_access_protocol=force_access_protocol,
            private=args.private,
        ),
    )


def _handle_autofix(args: argparse.Namespace) -> int:
    source = _resolve_workspace_source(args.source, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="autofix",
        source=source,
        runner=lambda client, source: _execute_autofix(
            client, source, error=args.error, repo_name=args.repo_name
        ),
    )


def _handle_checkout(args: argparse.Namespace) -> int:
    ref_kind = RefKind.TAG if args.ref_kind == "tag" else RefKind.BRANCH
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="checkout",
        source=gts_path,
        runner=lambda client, source: _execute_checkout(
            client, source, branch=args.branch, ref_kind=ref_kind, private=args.private
        ),
    )


def _handle_branch(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="branch",
        source=gts_path,
        runner=lambda client, source: _execute_branch(
            client, source, branch=args.branch, private=args.private
        ),
    )


def _handle_close_branch(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="close-branch",
        source=gts_path,
        runner=lambda client, source: _execute_close_branch(
            client, source, branch=args.branch, private=args.private
        ),
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
            private=args.private,
            all_writable=args.all_writable,
        ),
    )


def _resolve_commit_message(args: argparse.Namespace) -> str | None:
    positional = getattr(args, "message", None)
    option = getattr(args, "message_option", None)
    if positional and option:
        return None
    return option or positional


def _handle_merge(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="merge",
        source=gts_path,
        runner=lambda client, source: _execute_merge(
            client,
            source,
            project_branch=args.branch,
            into=args.into,
            private=args.private,
            all_writable=args.all_writable,
            ff_only=args.ff_only,
            no_ff=args.no_ff,
            dry_run=args.dry_run,
            resolve=args.resolve,
        ),
    )


def _handle_add(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    paths = getattr(args, "paths", None) or None
    return _run_with_logging(
        command_name="add",
        source=gts_path,
        runner=lambda client, source: _execute_add(
            client,
            source,
            paths=paths,
            dry_run=args.dry_run,
            private=args.private,
            all_writable=args.all_writable,
        ),
    )


def _handle_rm(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="rm",
        source=gts_path,
        runner=lambda client, source: _execute_rm(
            client, source, paths=args.paths, dry_run=args.dry_run, private=args.private
        ),
    )


def _handle_push(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    force_access_protocol = getattr(args, "force_access_protocol", None)
    return _run_with_logging(
        command_name="push",
        source=gts_path,
        runner=lambda client, source: _execute_push(
            client,
            source,
            dry_run=args.dry_run,
            force_access_protocol=force_access_protocol,
            private=args.private,
            all_writable=args.all_writable,
        ),
    )


def _handle_tag(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="tag",
        source=gts_path,
        runner=lambda client, source: _execute_tag(
            client, source, name=args.name, private=args.private
        ),
    )


def _handle_freeze(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="freeze",
        source=gts_path,
        runner=lambda client, source: _execute_freeze(
            client, source, name=args.name, dry_run=args.dry_run, private=args.private
        ),
    )


def _handle_import_submodules(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    apply = args.apply
    recursive = args.recursive
    return _run_with_logging(
        command_name="import-submodules",
        source=repo_root,
        runner=lambda client, source: _execute_import_submodules(
            client,
            source,
            apply=apply,
            recursive=recursive,
        ),
    )


def _handle_init_from_submodules(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    cgs_path = args.cgs_path
    max_depth = args.max_depth
    dry_run = args.dry_run
    force = args.force
    force_access_protocol = args.force_access_protocol
    return _run_with_logging(
        command_name="init-from-submodules",
        source=repo_root,
        runner=lambda client, source: _execute_init_from_submodules(
            client,
            source,
            cgs_path=cgs_path,
            max_depth=max_depth,
            dry_run=dry_run,
            force=force,
            force_access_protocol=force_access_protocol,
        ),
    )


def _handle_memory(args: argparse.Namespace) -> int:
    cgshome = _resolve_cgshome(getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name=f"memory-{args.memory_command}",
        source=cgshome,
        runner=lambda client, source: _execute_memory(
            client,
            source,
            subcommand=args.memory_command,
            state=getattr(args, "state", None),
            owner=getattr(args, "owner", None),
            branch=getattr(args, "branch", None),
            message=getattr(args, "message", None),
            remote=getattr(args, "remote", None),
            full=getattr(args, "full", False),
            cgs=getattr(args, "cgs", None),
            project_branch=getattr(args, "project_branch", None),
            no_push=getattr(args, "no_push", False),
            timeline=getattr(args, "timeline", False),
            reboot=getattr(args, "reboot", False),
        ),
    )


def _execute_memory(
    client: ComplexGitSyncClient,
    cgshome: Path,
    *,
    subcommand: str,
    state: str | None,
    owner: str | None = None,
    branch: str | None = None,
    message: str | None = None,
    remote: str | None = None,
    full: bool = False,
    cgs: str | None = None,
    project_branch: str | None = None,
    no_push: bool = False,
    timeline: bool = False,
    reboot: bool = False,
) -> int:
    if subcommand in _SIMPLE_MEMORY_SUBCOMMANDS:
        fetch, render = _SIMPLE_MEMORY_SUBCOMMANDS[subcommand]
        return render(fetch(client, cgshome))
    if subcommand == "init":
        _load_ready_registry_source(client, _resolve_gts_path(None, str(cgshome)))
        return _print_memory_init(client.memory_init(cgshome, owner=owner))
    if subcommand == "clone":
        _load_ready_registry_source(client, _resolve_gts_path(None, str(cgshome)))
        destination = client.memory_clone(
            cgshome, owner=owner, branch=branch, remote=remote
        )
        print(f"cloned={destination}")
        return EXIT_OK
    if subcommand == "mount":
        _load_ready_registry_source(client, _resolve_gts_path(None, str(cgshome)))
        return _print_memory_mount(
            client.add_memory_repo_cgs(
                _cgs_to_edit(client, cgs, cgshome), cgshome=cgshome, owner=owner
            )
        )
    if subcommand == "adopt":
        _load_ready_registry_source(client, _resolve_gts_path(None, str(cgshome)))
        return _print_memory_adopt(
            client.memory_adopt(
                cgshome, owner=owner, branch=branch, remote=remote, reboot=reboot
            )
        )
    if subcommand == "reboot":
        return _print_memory_reboot(client.memory_reboot(cgshome))
    if subcommand == "migrate":
        _load_ready_registry_source(client, _resolve_gts_path(None, str(cgshome)))
        return _print_memory_migrate(
            client.memory_migrate(cgshome, _cgs_to_edit(client, cgs, cgshome))
        )
    if subcommand == "branch":
        _load_ready_registry_source(client, _resolve_gts_path(None, str(cgshome)))
        return _print_memory_branch(
            client.memory_branch(cgshome, project_branch or "", push=not no_push)
        )
    if subcommand == "push":
        return _print_memory_push(client.memory_push(cgshome, message=message))
    if subcommand == "explore":
        return _print_memory_explore(
            client.memory_explore(cgshome, branch=branch, timeline=timeline)
        )
    # `env=<ref>` (the new argument this command's own environment=env(...)
    # reference line asks for) or the full `env(<ref>)` form that reference
    # is itself printed in — either routes to the full Environment record
    # instead of a State.
    if (state or "").startswith(("env=", "env(")):
        return _print_memory_show_environment(
            client.memory_show_environment(cgshome, state or "")
        )
    return _print_memory_show(client.memory_show(cgshome, state or ""), full=full)


def _handle_self_history(args: argparse.Namespace) -> int:
    cgshome = _resolve_cgshome(getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name=f"self-history-{args.self_history_command}",
        source=cgshome,
        runner=lambda client, source: _execute_self_history(client, source, args=args),
    )


def _execute_self_history(
    client: ComplexGitSyncClient, cgshome: Path, *, args: argparse.Namespace
) -> int:
    _load_ready_registry_source(client, _resolve_gts_path(None, str(cgshome)))
    if args.self_history_command == "adopt":
        result = client.self_history_adopt(
            cgshome, owner=getattr(args, "owner", None), branch=getattr(args, "branch", None)
        )
        print(f"mount={result['mount']} branch={result['branch']} remote={result['remote']}")
        return EXIT_OK
    worker = AgentInfo(role=args.worker_role, vendor=args.worker_vendor, model=args.worker_model)
    orchestrator = AgentInfo(
        role=args.orchestrator_role, vendor=args.orchestrator_vendor, model=args.orchestrator_model
    )
    conformity = ConformityScore(
        spec_respect=ConformityCriterion(
            score=args.spec_respect_score,
            basis=args.spec_respect_basis,
            reasoning=args.spec_respect_reasoning,
        ),
        gating=ConformityCriterion(
            score=args.gating_score, basis=args.gating_basis, reasoning=args.gating_reasoning
        ),
        quality=ConformityCriterion(
            score=args.quality_score, basis=args.quality_basis, reasoning=args.quality_reasoning
        ),
    )
    path = client.self_history_add(
        cgshome,
        ticket=args.ticket,
        goal=args.goal,
        action=args.action,
        worker=worker,
        orchestrator=orchestrator,
        conformity=conformity,
        state_before=args.state_before,
        state_after=args.state_after,
        lint_passed=args.lint_passed,
        tests_passed=args.tests_passed,
        pushed=args.pushed,
        pushed_reason=args.pushed_reason,
    )
    return _print_self_history_add(path)


def _cgs_to_edit(client: ComplexGitSyncClient, cgs: str | None, cgshome: Path) -> Path:
    """Which `.cgs` `memory mount` edits.

    The one the user named, or the one this tree was built from. The second
    is asked of the loaded project rather than guessed from the directory,
    because a workspace holds a *copy* of its spec under `.cgitsync/.cgs/`
    and editing the copy would change nothing anybody reads.
    """
    if cgs:
        return Path(cgs)
    registry = client.get_dependency_registry()
    source = registry.get("root").source_cgs_path
    if source is not None and Path(source).is_file():
        return Path(source)
    raise GitSyncError(
        f"this tree does not say which .cgs it was built from, so there is nothing "
        f"to edit in {cgshome}. Name one with --cgs."
    )


def _print_memory_mount(answer: dict) -> int:
    print(f"cgs={answer['cgs']}")
    print(f"entry={answer['line'].strip()}")
    if answer["added"]:
        print("added=yes")
        print("next: cgitsync memory adopt, then cgitsync memory push")
    else:
        print("added=already-there")
    return EXIT_OK


def _print_memory_migrate(answer: dict) -> int:
    print(f"old_mount={answer['old_mount']}")
    print(f"new_mount={answer['new_mount']}")
    print(f"files_moved={answer['files_moved']}")
    print(f"cgs={answer['cgs']}")
    return EXIT_OK


def _print_memory_adopt(answer: dict) -> int:
    print(f"mount={answer['mount']}")
    print(f"branch={answer['branch']}")
    print(f"remote={answer['remote']}")
    if answer["started_from"]:
        print(f"started_from=origin/{answer['started_from']}")
    print(f"waiting_to_be_committed={answer['pending']}")
    print("next: cgitsync memory push")
    return EXIT_OK


def _print_memory_branch(answer: dict) -> int:
    print(f"branch={answer['branch']}")
    print(f"for_project_branch={answer['project_branch']}")
    print(f"created={'yes' if answer['created'] else 'already-there'}")
    print(f"pushed={'yes' if answer['pushed'] else 'no'}")
    return EXIT_OK


def _print_memory_init(proposal: dict) -> int:
    """Print the entry to paste, the branch, and the command that creates it."""
    print(f"repository={proposal['entry']['repository']}")
    print(f"branch={proposal['branch']}")
    print(f"mount_path={proposal['mount_path']}")
    print(f"mounted={str(proposal['mounted']).lower()}")
    print("add this entry to your .cgs, under repos:")
    print(proposal["line"])
    if not proposal["mounted"]:
        # Never created for you: this tool speaks Git and nothing else, so
        # a repository on a host is somebody's deliberate act, not a side
        # effect of asking what the entry should look like.
        print("the repository itself is yours to create, with:")
        print(f"  {proposal['create_with']}")
        print("then: cgitsync memory clone")
    return EXIT_OK


def _print_memory_push(result: dict) -> int:
    if result["committed"]:
        print(f"committed={result['recorded']} path(s)")
    else:
        print("committed=0 (nothing new to record)")
    print(
        f"pushed branch={result['branch']} states={result['states']} "
        f"entries={result['entries']}"
    )
    return EXIT_OK


def _print_self_history_add(path: Path) -> int:
    print(f"recorded={path}")
    return EXIT_OK


def _print_memory_reboot(result: dict) -> int:
    print(f"folded={result['folded']} pending record(s)")
    print(f"archived={result['archived_from']} -> {result['archived_to']}")
    print(f"exported={result['exported']}")
    print(f"branch={result['branch']} (fresh, pushed)")
    print("next: use the tool as normal — the next command writes this branch's next State")
    return EXIT_OK


def _print_memory_status(status: dict) -> int:
    print(
        f"states={status['states']} entries={status['entries']} "
        f"verification={status['verification']} findings={status['findings']}"
    )
    print(f"last_recorded_at={status['last_recorded_at'] or '(never)'}")
    if not status["entries"]:
        print("nothing has been recorded here yet; the next command that writes a State starts the chain.")
        return EXIT_OK
    genesis, latest = status["genesis_toolchain"], status["latest_toolchain"]
    # Both ends, because the interesting question is whether they differ:
    # a chain spanning an upgrade should say where the upgrade fell.
    for tool in sorted(set(genesis) | set(latest)):
        first, last = genesis.get(tool, "none"), latest.get(tool, "none")
        suffix = "" if first == last else f"  (genesis: {first})"
        print(f"  {tool:<9} {last}{suffix}")
    return EXIT_OK


def _print_memory_list(rows: list[dict]) -> int:
    if not rows:
        print("no States recorded in this workspace.")
        return EXIT_OK
    print(f"{'STATE':<16}  {'RECORDED':<21}  COMMANDS")
    for row in rows:
        commands = ", ".join(row["commands"]) if row["commands"] else "(no entry records it)"
        recorded = row["recorded_at"] or "-"
        missing = "" if row["path"] else "  [not on disk]"
        print(f"{row['state'][:16]:<16}  {recorded:<21}  {commands}{missing}")
    return EXIT_OK


#: How much of a commit message one line shows. A subject line is the
#: common case and fits; a body is the uncommon one and `--full` is for it.
_MESSAGE_WIDTH = 56


def _shorten(message: str, *, full: bool) -> str:
    """The message as one line, unless the reader asked for all of it."""
    if full:
        return message
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    if len(first_line) <= _MESSAGE_WIDTH:
        return first_line
    return f"{first_line[: _MESSAGE_WIDTH - 1]}…"


def _print_memory_explore(answer: dict) -> int:
    branch = answer["branch"]
    print(f"branch={branch} (current)" if branch else "branch=(no memory mounted here yet)")
    if "entries" in answer:
        return _print_memory_timeline(answer["entries"])
    return _print_memory_published(answer["commits"])


def _print_memory_published(rows: list[dict]) -> int:
    if not rows:
        print("no published commits in this memory.")
        return EXIT_OK
    for row in rows:
        date = row["published_at"][:10] or "-"
        print(
            f"{date}  {row['repository']:<18} {row['branch']:<12} "
            f"{row['sha'][:8]}  {_shorten(row['message'], full=False)}"
        )
    return EXIT_OK


def _print_memory_timeline(rows: list[dict]) -> int:
    if not rows:
        print("nothing recorded here yet.")
        return EXIT_OK
    for row in rows:
        state = row["state"][:12] if row["state"] else "-"
        print(f"seq={row['seq']}  {row['recorded_at']}  {row['command']}  state={state}")
        for commit in row["commits"]:
            print(
                f"    commit  {commit['repository']:<18} {commit['sha'][:8]}  "
                f"{_shorten(commit['message'], full=False)}"
            )
        for publication in row["published"]:
            print(
                f"    push    {publication['repository']:<18} -> "
                f"{publication['remote']} {publication['ref']}"
            )
    return EXIT_OK


def _print_memory_self_history(records: list[dict]) -> int:
    if not records:
        print("no self-history recorded here yet.")
        return EXIT_OK
    for record in records:
        worker, orchestrator = record["worker"], record["orchestrator"]
        conformity = record["conformity"]
        print(
            f"{record['recorded_at']}  ticket={record['ticket']}  "
            f"worker={worker['role']}({worker['vendor']}/{worker['model']})  "
            f"orchestrator={orchestrator['role']}({orchestrator['vendor']}/{orchestrator['model']})"
        )
        print(f"    goal: {record['goal']}")
        print(f"    action: {record['action']}")
        print(
            "    conformity: "
            f"spec_respect={conformity['spec_respect']['score']}({conformity['spec_respect']['basis']}) "
            f"gating={conformity['gating']['score']}({conformity['gating']['basis']}) "
            f"quality={conformity['quality']['score']}({conformity['quality']['basis']})"
        )
        contract = record["contract"] or "(none signed)"
        print(f"    contract={contract}")
    return EXIT_OK


def _tree_branches(count: int) -> list[str]:
    """``├── `` for every item but the last, ``└── `` for it — the same
    connectors `view-tree` (`git_tree.format_view_tree`) draws a repo tree
    with, reused here for an environment record's own nested shape rather
    than a second, differently-styled way of indenting a list."""
    return ["├── "] * (count - 1) + ["└── "] if count else []


def _format_environment_tree(record: dict) -> list[str]:
    """Render one Environment record (`environment_spec.TreeEnvironment.to_dict()`)
    the way `view-tree` draws a repo tree — box-drawing connectors, not a
    raw ``record={...}`` dict dump nobody can read at a glance."""
    lines: list[str] = []
    machine = record.get("machine", {})
    tools = record.get("tools", [])
    credentials = record.get("credentials", [])
    manifests = record.get("manifests", [])
    sections = [
        ("machine", [f"{key}: {value}" for key, value in machine.items()]),
        ("tools", [f"{tool['name']}: {tool['version']}" for tool in tools]),
        (
            "credentials",
            [
                f"{cred['provider']} ({cred['tool']}): "
                f"available={'yes' if cred['available'] else 'no'} "
                f"authenticated={'yes' if cred['authenticated'] else 'no'}"
                for cred in credentials
            ],
        ),
        (
            "manifests",
            [
                f"{manifest['path']}: {manifest['digest'][:15]}..."
                + (f" [{', '.join(manifest['platforms'])}]" if manifest["platforms"] else "")
                for manifest in manifests
            ],
        ),
    ]
    sections = [(name, rows) for name, rows in sections if rows]
    root_branches = _tree_branches(len(sections))
    for (name, rows), root_branch in zip(sections, root_branches):
        lines.append(f"{root_branch}{name}")
        child_prefix = "    " if root_branch == "└── " else "│   "
        for row, branch in zip(rows, _tree_branches(len(rows))):
            lines.append(f"{child_prefix}{branch}{row}")
    return lines


def _print_memory_show(state: dict, *, full: bool = False) -> int:
    print(f"state={state['state']}")
    print(f"path={state['path']}")
    print(
        f"project={state['project']} lifecycle_state={state['lifecycle_state']} "
        f"repos={state['repos']} hash_canonicalisation={state['hash_canonicalisation']}"
    )
    # The environment this State's own commits ran under, first — a bare
    # reference, not the full record: 'memory show env=<ref>' is where that
    # detail lives, since a State answers "what was this tree", not "what
    # ran it". The tree comes right after — this State's own topology, not
    # the live one 'view-tree' shows, rendered exactly the same way.
    for environment in state.get("environments", []):
        print(f"environment={environment['id']} path={environment['path'] or 'missing'}")
    if state.get("tree"):
        print("[tree]")
        print(state["tree"])
    if not state["entries"]:
        print("no ledger entry records this State.")
        return EXIT_OK
    for entry in state["entries"]:
        print(f"seq={entry['seq']} {entry['recorded_at']} {entry['command']} {entry['outcome']}")
        for tool, version in sorted(entry["toolchain"].items()):
            print(f"  {tool:<9} {version}")
        for commit in entry.get("commits", []):
            # One line per repository: what was committed, where, and
            # whether anybody but this machine has ever seen it.
            seen = "published" if commit["published"] else "unpushed "
            print(
                f"  {commit['repository']:<18} {commit['scope']:<8} "
                f"{commit['sha'][:8]} {seen} {_shorten(commit['message'], full=full)}"
            )
            if full:
                print(f"    branch={commit['branch']} authored={commit['authored_at']}")
    if full:
        for row in state.get("published", []):
            print(
                f"published {row['sha'][:8]} -> {row['remote']} {row['ref']} "
                f"(seq={row['entry']}, {row['at']})"
            )
    return EXIT_OK


def _print_memory_show_environment(answer: dict) -> int:
    print(f"environment={answer['id']}")
    print(f"path={answer['path']}")
    for line in _format_environment_tree(answer["record"]):
        print(line)
    return EXIT_OK


def _handle_verify(args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        return _handle_verify_json(args)
    cgshome = _resolve_cgshome(getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="verify",
        source=cgshome,
        runner=lambda client, source: _execute_verify(client, source, repair=args.repair),
    )


def _handle_verify_json(args: argparse.Namespace) -> int:
    """``verify --json``: the same chain check, rendered for a script.

    Same exit code as the human form — ``0`` clean, ``1`` when the chain has
    findings — so a caller may read either signal.
    """
    rendered: dict[str, str] = {}
    with _json_stdout():
        cgshome = _resolve_cgshome(getattr(args, "search_dir", None))
        exit_code = _run_with_logging(
            command_name="verify",
            source=cgshome,
            runner=lambda client, source: _execute_verify_json(
                client, source, repair=args.repair, rendered=rendered
            ),
        )
    print(rendered["payload"])
    return exit_code


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


def _execute_verify_json(
    client: ComplexGitSyncClient,
    cgshome: Path,
    *,
    repair: bool,
    rendered: dict[str, str],
) -> int:
    rendered["payload"] = client.verify_json(cgshome, repair=repair)
    report = client.last_verify_report
    if report is None:  # pragma: no cover - verify_json always records one
        return EXIT_REFUSED
    return _verify_exit_code(report.state)


#: What each of the five answers prints, and what it means for a reader who
#: has just been told it. The wording says what was actually checked: a
#: command that answered "clean" over a directory nothing writes taught its
#: users to ignore it.
_VERIFY_ANSWERS: dict[HistoryState, tuple[str, str]] = {
    HistoryState.VERIFIED: (
        "verified",
        "every link in the recorded chain checked out.",
    ),
    HistoryState.NO_HISTORY: (
        "no-history",
        "nothing has been recorded in this workspace yet. "
        "That is not a failure: a new workspace is not a broken one.",
    ),
    HistoryState.LEGACY: (
        "legacy",
        "history exists here, in the single-file .lgr register, which carries "
        "no chain. It can be read; it cannot be verified, and an edit to it "
        "leaves no trace.",
    ),
    HistoryState.CORRUPT: (
        "corrupt",
        "a chain was read and it does not hold.",
    ),
    HistoryState.TIME_INCONSISTENT: (
        "time-inconsistent",
        "the chain holds — every link checked out — but its own timestamps "
        "move backwards somewhere. Your history is intact; the clock that "
        "stamped it was not. A corrected clock, a restored snapshot, or a "
        "machine that disagreed about the hour all look like this, and so "
        "does a backdated entry.",
    ),
}


def _execute_verify(
    client: ComplexGitSyncClient,
    cgshome: Path,
    *,
    repair: bool,
) -> int:
    report = client.verify(cgshome, repair=repair)
    # The workspace was already announced with the input that chose it, when
    # _resolve_cgshome discovered it (see cli/_shared._announce_cgshome_resolution).
    label, explanation = _VERIFY_ANSWERS[report.state]
    print(f"status={label}")
    print(f"findings={len(report.findings)}")
    print(explanation)
    for seq, finding, detail in report.findings:
        print(f"seq={seq} finding={finding.name} detail={detail}")
    if repair and report.state is not HistoryState.NO_HISTORY:
        print("repair=attempted (HEAD cache only; entries are never rewritten or deleted)")
    return _verify_exit_code(report.state)


def _verify_exit_code(state: HistoryState) -> int:
    """``0`` only when the answer is yes, or when there was nothing to ask.

    ``legacy`` exits non-zero on purpose. The question is "is this history
    intact?", and "I cannot tell" is not a yes — a build gating on
    ``verify`` must not pass because the evidence is in a format that cannot
    be checked.

    ``time-inconsistent`` exits non-zero for the neighbouring reason: the
    history holds, so it is not ``corrupt``, but something is wrong that a
    caller gating on this command should not sail past. Which of the two it
    is changes what the reader should go and look at, which is exactly why
    it is its own answer rather than folded into the other.
    """
    if state in (HistoryState.VERIFIED, HistoryState.NO_HISTORY):
        return EXIT_OK
    return EXIT_REFUSED


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


def _execute_pull_private(client: ComplexGitSyncClient, source_path: Path) -> int:
    _load_ready_registry_source(client, source_path)
    scope = _resolve_write_scope(client, private=True, command="pull")
    print(f"git_command=git fetch && git merge (scope={scope.value})")
    refreshed = client.refresh_private()
    for repo_name, source in refreshed:
        print(f"merged {repo_name} <- {source}")
    if not refreshed:
        print("merged nothing: every writable configuration repo is already current")
    print(_format_tree_state_line(client.get_tree_state()))
    _print_repo_tree_result(client)
    return 0


def _execute_pull(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    commit_gitignore: bool = False,
    force_gitignore_sync: bool = False,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
    force_access_protocol: str | None = None,
) -> int:
    registry = client.pull(
        source_path,
        commit_gitignore=commit_gitignore,
        force_gitignore_sync=force_gitignore_sync,
        git_user_name=git_user_name,
        git_user_email=git_user_email,
        force_access_protocol=force_access_protocol,
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
    *,
    force_access_protocol: str | None = None,
    private: bool = False,
) -> int:
    print("git_command=git fetch && git checkout -B <branch> FETCH_HEAD && git clean -fd (executed per repo)")
    registry = client.pull_force(
        source_path, force_access_protocol=force_access_protocol, private=private
    )
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    _print_repo_tree_result(client)
    return 0


def _execute_autofix(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    error: str | None,
    repo_name: str | None,
) -> int:
    _load_ready_registry_source(client, source_path)
    outcome = client.autofix(error=error, repo_name=repo_name)
    print(f"repaired={outcome.repaired} detail={outcome.detail}")
    return EXIT_OK if outcome.repaired else EXIT_REFUSED


def _execute_checkout(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    branch: str,
    ref_kind: RefKind,
    private: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git checkout {branch}")
    client.checkout(branch, ref_kind=ref_kind, private=private)
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
    private: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git branch {branch}")
    client.branch(branch, private=private)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"branch={branch}"
    )
    _print_repo_tree_result(client)
    return 0


def _execute_close_branch(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    branch: str,
    private: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git push <remote> <branch>:refs/heads/closed/{branch} "
          f"&& git push <remote> --delete {branch} && git branch -m {branch} closed/{branch}")
    client.close_branch(branch, private=private)
    _print_write_outcomes(
        client,
        verb="closed",
        nothing_note=(
            "no repository in scope had a branch named "
            f"'{branch}' to close."
        ),
    )
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
    private: bool = False,
    all_writable: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    scope = _resolve_write_scope(
        client, private=private, command="commit", all_writable=all_writable
    )
    print(f"git_command=git commit -m {message!r}")
    if dry_run:
        _print_dry_run_plan(
            client,
            command_name="commit",
            actions=(
                "git add --all" if stage_all else "skip git add --all (--no-stage)",
                f"git commit -m {message!r}",
            ),
            scope=scope,
        )
    else:
        client.commit(message, stage_all=stage_all, private=private, all_writable=all_writable)
        _print_write_outcomes(
            client,
            verb="committed",
            nothing_note=(
                "no repository in scope had staged changes. Check "
                "'cgitsync status' for where your changes actually live, and "
                "add --private if they are in a private/local repository."
            ),
        )
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"message={message!r}"
    )
    if not dry_run:
        _print_repo_tree_result(client)
    return 0


def _execute_merge(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    project_branch: str,
    into: str | None = None,
    private: bool = False,
    all_writable: bool = False,
    ff_only: bool = False,
    no_ff: bool = False,
    dry_run: bool = False,
    resolve: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    scope = _resolve_write_scope(
        client, private=private, command="merge", all_writable=all_writable
    )
    flag = " --ff-only" if ff_only else (" --no-ff" if no_ff else "")
    if into:
        if resolve:
            raise GitSyncError(
                "--into and --resolve cannot be combined: --resolve stops at the "
                "first conflict with a worktree to fix, and --into promises the "
                "opposite, that a conflict anywhere changes nothing."
            )
        print(f"git_command=git checkout {into} && git merge{flag} {project_branch}")
        return _execute_merge_into(
            client,
            scope_value=scope.value,
            source_branch=project_branch,
            target_branch=into,
            private=private,
            all_writable=all_writable,
            ff_only=ff_only,
            no_ff=no_ff,
            dry_run=dry_run,
        )
    print(f"git_command=git merge{flag} {project_branch}")
    if dry_run:
        _print_merge_plan(
            client,
            scope_value=scope.value,
            project_branch=project_branch,
            private=private,
            all_writable=all_writable,
        )
        print(_format_tree_state_line(client.get_tree_state()))
        return 0
    if resolve:
        return _execute_merge_resolve(
            client,
            project_branch=project_branch,
            private=private,
            all_writable=all_writable,
            ff_only=ff_only,
            no_ff=no_ff,
        )
    merged = client.merge(
        project_branch,
        private=private,
        all_writable=all_writable,
        ff_only=ff_only,
        no_ff=no_ff,
    )
    for repo_name, source in merged:
        print(f"merged {repo_name} <- {source}")
    if not merged:
        print(f"merged nothing: every repository in scope already has {project_branch!r}")
    print(_format_tree_state_line(client.get_tree_state()))
    _print_repo_tree_result(client)
    return 0


def _execute_merge_into(
    client: ComplexGitSyncClient,
    *,
    scope_value: str,
    source_branch: str,
    target_branch: str,
    private: bool,
    all_writable: bool,
    ff_only: bool,
    no_ff: bool,
    dry_run: bool,
) -> int:
    """``merge <source> --into <target>``: one checkout, one merge, one command."""
    labels = {
        "fast-forward": " (fast-forward — the branch just moves)",
        "merge": "",
        "already-merged": " (already has it — nothing to merge)",
        "no-source": " (no such branch here — skipped)",
        "no-target": " (no such branch here — would refuse)",
        "conflicts": " (conflicts — would block the merge)",
    }
    plan = client.merge_into_plan(
        source_branch, target_branch, private=private, all_writable=all_writable
    )
    print(f"dry_run={'true' if dry_run else 'false'} command=merge-into scope={scope_value}")
    print(
        "plan_order="
        + (
            " -> ".join(
                f"{row.name}: {row.target} <- {row.source}{labels.get(row.status, '')}"
                for row in plan
            )
            or "(no repository in scope)"
        )
    )

    if dry_run:
        blocked = [row for row in plan if row.status in ("conflicts", "no-target")]
        if blocked:
            print("refused=true")
            for row in blocked:
                listed = ", ".join(str(path) for path in row.conflicting_paths)
                print(f"  {row.name}: {listed or f'no branch {row.target!r}'}")
            print("note: nothing would be checked out and nothing would be merged.")
        print(_format_tree_state_line(client.get_tree_state()))
        return EXIT_OK

    outcomes = client.merge_into(
        source_branch,
        target_branch,
        private=private,
        all_writable=all_writable,
        ff_only=ff_only,
        no_ff=no_ff,
    )
    for row in outcomes:
        if row.status == "fast-forward":
            print(f"fast-forwarded {row.name}: {row.target} <- {row.source}")
        elif row.status == "merge":
            print(f"merged {row.name}: {row.target} <- {row.source}")
    if not any(row.status in ("fast-forward", "merge") for row in outcomes):
        print(f"merged nothing: every repository in scope already has {source_branch!r}")
    print(_format_tree_state_line(client.get_tree_state()))
    _print_repo_tree_result(client)
    return EXIT_OK


def _execute_merge_resolve(
    client: ComplexGitSyncClient,
    *,
    project_branch: str,
    private: bool,
    all_writable: bool,
    ff_only: bool,
    no_ff: bool,
) -> int:
    # The warning prints before the writes, not after: this is the one merge
    # mode that can leave the tree half-merged.
    print(
        "note: --resolve merges one repository at a time and stops at the "
        "first conflict. Repositories merged before it stay merged, so the "
        "tree can be left partly merged. Plain 'cgitsync merge' merges "
        "nothing when any repository conflicts."
    )
    outcome = client.merge_resolve(
        project_branch,
        private=private,
        all_writable=all_writable,
        ff_only=ff_only,
        no_ff=no_ff,
    )
    for repo_name, source in outcome.merged:
        print(f"merged {repo_name} <- {source}")

    if outcome.stopped_at is None:
        if not outcome.merged:
            print(
                f"merged nothing: every repository in scope already has "
                f"{project_branch!r}"
            )
        print(_format_tree_state_line(client.get_tree_state()))
        _print_repo_tree_result(client)
        return 0

    listed = ", ".join(str(path) for path in outcome.stopped_paths)
    print(f"stopped at {outcome.stopped_at}: {listed or '(no file named)'}")
    if outcome.not_reached:
        print(f"not reached: {', '.join(outcome.not_reached)}")

    manual = client.open_merge_tool(outcome.stopped_at_id)
    if manual is None:
        print(f"merge tool closed. Review {outcome.stopped_at}, then commit.")
    else:
        print(f"no merge tool available. Resolve by hand:\n  {manual}")
    print(_format_tree_state_line(client.get_tree_state()))
    return 1


def _print_merge_plan(
    client: ComplexGitSyncClient,
    *,
    scope_value: str,
    project_branch: str,
    private: bool,
    all_writable: bool = False,
) -> None:
    """Show which branch each repository would actually merge.

    The whole point of the dry run: the argument names the project's branch,
    and each repository translates it. Seeing that translation before it runs
    is what stops somebody merging a configuration repository from the wrong
    place.
    """
    plan = client.merge_plan(project_branch, private=private, all_writable=all_writable)
    # RepoScope.WRITABLE is the internal name; --all is the word the user typed.
    scope_value = "all" if all_writable else scope_value
    labels = {
        "merge": "",
        "already-on-it": " (already on it — nothing to merge into)",
        "no-branch": " (no such branch here — skipped)",
        "conflicts": " (conflicts — would block the merge)",
    }
    rows = [f"{name} <- {source}{labels[status]}" for name, source, status, _ in plan]
    print(f"dry_run=true command=merge scope={scope_value}")
    print("plan_order=" + (" -> ".join(rows) if rows else "(no repository in scope)"))

    blocked = [(name, paths) for name, _, status, paths in plan if status == "conflicts"]
    if blocked:
        print("conflicts=true")
        for name, paths in blocked:
            listed = ", ".join(str(path) for path in paths) or "(no file named)"
            print(f"  {name}: {listed}")
        print(
            "note: merge would refuse and merge nothing. Resolve these files, "
            "or run 'cgitsync merge --resolve' to merge one repository at a time."
        )
    elif plan and all(status != "merge" for _, _, status, _ in plan):
        print(
            f"note: nothing would be merged. Check out the branch you want to merge "
            f"*into* first — 'cgitsync checkout <target>' — then merge {project_branch}."
        )


def _execute_add(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    paths: list[str] | None = None,
    dry_run: bool = False,
    private: bool = False,
    all_writable: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    scope = _resolve_write_scope(client, private=private, command="add", all_writable=all_writable)
    action = f"git add -- {' '.join(paths)}" if paths else "git add --all"
    print(f"git_command={action}")
    if dry_run:
        _print_dry_run_plan(client, command_name="add", actions=(action,), scope=scope)
    else:
        client.add(paths=paths, private=private, all_writable=all_writable)
        _print_write_outcomes(
            client,
            verb="staged",
            nothing_note=(
                "no repository in scope had anything to stage. Check "
                "'cgitsync status' for where your changes actually live, and "
                "add --private if they are in a private/local repository."
            ),
        )
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
    private: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    scope = _resolve_write_scope(
        client, private=private, command="rm", default=RepoScope.ALL
    )
    action = f"git rm -- {' '.join(paths)}"
    print(f"git_command={action}")
    _warn_paths_reaching_configuration_repos(client, paths, private=private)
    if dry_run:
        # The real run refuses an out-of-scope path inside client.remove(),
        # which a dry run never reaches. Ask the same question here, so the
        # preview cannot show a plan the command would then decline to run.
        refusals = client.removals_outside_scope(paths, private=private)
        if refusals:
            raise GitSyncError(refusals[0])
        _print_dry_run_plan(client, command_name="rm", actions=(action,), scope=scope)
    else:
        client.remove(paths, private=private)
        _print_write_outcomes(
            client,
            verb="removed",
            nothing_note="no path resolved to a repository in scope.",
        )
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
    force_access_protocol: str | None = None,
    private: bool = False,
    all_writable: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    scope = _resolve_write_scope(client, private=private, command="push", all_writable=all_writable)
    print("git_command=git push (-u origin <branch> when upstream is missing)")
    if dry_run:
        actions = ("git push", "git push -u origin <branch> when upstream is missing")
        if _memory_declared_for_dry_run(client):
            actions = ("cgitsync memory push", *actions)
        _print_dry_run_plan(
            client,
            command_name="push",
            actions=actions,
            scope=scope,
        )
    else:
        client.push(
            force_access_protocol=force_access_protocol,
            private=private,
            all_writable=all_writable,
        )
        _print_memory_fold_outcome(client)
        _print_write_outcomes(
            client,
            verb="pushed",
            nothing_note=(
                "every repository in scope was already level with its "
                "upstream. Commit first, or add --private to reach the "
                "private/local repositories."
            ),
        )
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
    private: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git tag {name} && git push origin {name}")
    client.tag(name, private=private)
    _print_memory_fold_outcome(client)
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
    private: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    scope = _resolve_write_scope(
        client, private=private, command="freeze", default=RepoScope.WRITABLE
    )
    print(f"git_command=git add --all && git commit -m {name!r} && git tag {name} && git push")
    if dry_run:
        actions = ("git add --all", f"git commit -m {name!r}", f"git tag {name}", "git push")
        if _memory_declared_for_dry_run(client):
            actions = ("cgitsync memory push", *actions)
        _print_dry_run_plan(
            client,
            command_name="freeze",
            actions=actions,
            scope=scope,
        )
    else:
        client.freeze(name, private=private)
        _print_memory_fold_outcome(client)
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
    recursive: bool = False,
) -> int:
    """Execute the import-submodules command and print a human-readable report."""
    report = client.import_submodules(source, apply=apply, recursive=recursive)

    if not report.submodules:
        print(f"No .gitmodules found at {source} — nothing to import.")
        return 0

    # Every path is printed from *source*, and each submodule names the
    # .gitmodules that declared it. With --recursive the report spans
    # several repositories, and a bare .gitmodules path means nothing
    # without saying which repository it was read from.
    if not apply:
        print(f"Dry run — {len(report.submodules)} submodule(s) under {source}")
        print("Pass --apply to perform the conversion.\n")
        for sub in report.submodules:
            print(f"  submodule: {sub.name}")
            print(f"    path:        {report.path_from_scan_root(sub)}")
            print(f"    declared in: {report.gitmodules_from_scan_root(sub)}")
            print(f"    url:         {sub.url}")
            print(f"    branch:      {sub.branch}")
            print()
        return 0

    print(f"Converted {len(report.converted)} submodule(s) under {source}:")
    # apply=True converts every submodule the report found (an all-or-raise
    # preflight per level), so report.submodules and report.converted are
    # always in 1:1 order — zip rather than look up by name, which could
    # otherwise match the wrong entry if two levels share a submodule name.
    for sub in report.submodules:
        print(f"  ✓ {sub.name}  ({report.path_from_scan_root(sub)})")
    return 0


def _execute_init_from_submodules(
    client: ComplexGitSyncClient,
    source: Path,
    *,
    cgs_path: str | None = None,
    max_depth: int | None = None,
    dry_run: bool = False,
    force: bool = False,
    force_access_protocol: str | None = None,
) -> int:
    """Execute init-from-submodules and print a human-readable report."""
    report = client.init_from_submodules(
        source,
        cgs_path=cgs_path,
        max_depth=max_depth,
        dry_run=dry_run,
        force=force,
        force_access_protocol=force_access_protocol,
    )

    print(
        f"Found {len(report.discover.repos)} git repository(ies) under {report.root}\n"
        f"project name: {report.discover.project_name}"
    )
    for warning in report.discover.warnings:
        print(f"  ! {warning}")

    submodules = report.import_report.submodules if report.import_report else ()
    if dry_run:
        print("\nDry run — nothing written, cloned, or converted.")
        print(f"  would write:  {report.cgs_path}")
        print(f"  would adopt:  {report.root} (CGSHOME)")
        print(f"  would convert {len(submodules)} submodule(s):")
        for sub in submodules:
            print(
                f"    - {report.import_report.path_from_scan_root(sub)}"
                f"  (declared in {report.import_report.gitmodules_from_scan_root(sub)})"
            )
        print("\nRe-run without --dry-run to perform it.")
        return 0

    print(f"\n.cgs {'written to' if report.cgs_written else 'reused'}: {report.cgs_path}")
    print(f"CGSHOME: {report.root}")
    print(f"Converted {len(submodules)} submodule(s) to plain nested clones:")
    for sub in submodules:
        print(f"  ✓ {sub.name}  ({report.import_report.path_from_scan_root(sub)})")

    _print_repo_tree_result(client)
    # The conversion is staged, never committed: it touches every repository
    # that held a submodule, and some of those belong to other people.
    print(
        "\nThe conversion is staged but not committed. Review it, then:\n"
        f"  export CGSHOME={report.root}\n"
        "  cgitsync branch <name> && cgitsync checkout <name>\n"
        '  cgitsync add && cgitsync commit "<message>"'
    )
    return 0


#: Memory subcommands with no argument beyond `cgshome` and one call/print
#: each — pulled out of `_execute_memory`'s if-chain as a single branch so
#: that chain stays under the C90 complexity ceiling as new read-only
#: subcommands (like `self-history`) are added. Defined last: every
#: `_print_memory_*` function it references must already exist.
_SIMPLE_MEMORY_SUBCOMMANDS: dict[str, tuple[Callable, Callable]] = {
    "status": (lambda client, cgshome: client.memory_status(cgshome), _print_memory_status),
    "list": (lambda client, cgshome: client.memory_list(cgshome), _print_memory_list),
    "self-history": (
        lambda client, cgshome: client.memory_self_history(cgshome),
        _print_memory_self_history,
    ),
}

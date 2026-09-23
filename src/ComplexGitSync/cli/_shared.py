"""cli._shared — helpers shared across every cgitsync command group.

Ring: 4 (CLI adapter — the same ring cli.py itself occupies)
Contract: dispatch a command handler under structured run-logging (with the
    two hard-coded error hints), resolve/load a .cgs or .gts source —
    announcing which workspace and snapshot auto-discovery picked, and
    warning on stderr when that workspace is not the one the user is
    standing in — and format/print the plan, tree-state, and
    .gitignore-sync reports every command group's _execute_* functions
    reuse — no group-specific handler logic.
Imports: cgs_format, errors, git_repo, git_tree, orchestre, snapshot_resolver
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from ..cgs_format import CgsDocument
from ..errors import GitSyncError
from ..git_repo import RepoScope
from ..git_tree import ProjectTreeState, iter_tree_leaf_first, resolve_repo_for_path
from ..orchestre import (
    ComplexGitSyncClient,
    _scope_for,
    create_run_logger,
    resolve_command_scope,
)
from ..settings import other_workspaces, resolve_use_case
from ..snapshot_resolver import (
    CGSHOME_ORIGIN_CWD,
    CGSHOME_ORIGIN_DEFAULT,
    CGSHOME_ORIGIN_ENVIRONMENT,
    CGSHOME_ORIGIN_SEARCH_DIR,
    SNAPSHOT_ORIGIN_EXPLICIT,
    CgshomeResolution,
    SnapshotResolution,
    describe_cgshome,
    describe_workspace_source,
)


def _add_json_argument(subparser: argparse.ArgumentParser) -> None:
    """Register ``--json`` on a command that can answer a machine."""
    subparser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Print one JSON object on stdout and nothing else; every "
            "human-facing line goes to stderr instead. The exit code is the "
            "same as without it."
        ),
    )


@contextlib.contextmanager
def _json_stdout():
    """Keep stdout clear for the one JSON object a caller is parsing.

    Everything the command would have printed for a person — the workspace
    it resolved, the log file it wrote, any warning — goes to stderr for the
    duration. That is what makes ``cgitsync status --json | jq`` work
    without a caller having to strip a banner first, and it costs nothing:
    the same lines are still there, on the stream meant for them.
    """
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _add_gitignore_sync_arguments(subparser: argparse.ArgumentParser) -> None:
    """Register the DevPlanTicket Milestone 2/3 ``.gitignore``-sync flags.

    Shared by ``initialise``/``clean-init``/``pull`` (the commands that run
    discovery and can trigger the sync) so the three subparsers stay
    identical rather than drifting. Not registered on any other command —
    a global/top-level flag would silently no-op on commands where it has
    no meaning (``view-tree``, ``status``, ...).
    """
    subparser.add_argument(
        "--commit-gitignore",
        action="store_true",
        help=(
            "Explicit approval to stage, commit, and push any .gitignore "
            "the .gitignore lifecycle sync updates. Without this flag, the "
            "sync only writes the file and reports what changed."
        ),
    )
    subparser.add_argument(
        "--force-gitignore-sync",
        action="store_true",
        help=(
            "If a repo's safe pull fails before its .gitignore is synced, "
            "fall back to pull-force semantics (fetch, checkout -B <branch> "
            "FETCH_HEAD, clean -fd) for that repo instead of erroring out. "
            "Never force-pushes."
        ),
    )
    subparser.add_argument(
        "--git-user-name",
        dest="git_user_name",
        metavar="NAME",
        help=(
            "Override the Git author name used for ComplexGitSync-authored "
            "commits (e.g. the --commit-gitignore step). Persisted to "
            "CGSHOME/.cgitsync/master.toml so later invocations on this "
            "workspace pick it up without repeating the flag. Defaults to "
            "the local git config when never set."
        ),
    )
    subparser.add_argument(
        "--git-user-email",
        dest="git_user_email",
        metavar="EMAIL",
        help=(
            "Override the Git author email used for ComplexGitSync-authored "
            "commits. Persisted to CGSHOME/.cgitsync/master.toml alongside "
            "--git-user-name; see that flag for details."
        ),
    )


def _pull_force_risk_hint(client: ComplexGitSyncClient) -> str:
    """The hint printed when a `pull` fails, naming exactly what
    `pull-force` would discard for each repository that has local-only
    commits — the archived Autofix ticket (.agent/.local/.localSpec/DevTickets/archive/20260923_Autofix_DevPlanTicket.md) §3/WP6.

    Best-effort: a repository whose tracking state cannot be read (no
    remote configured, or the git query itself fails) is silently
    skipped rather than letting a diagnostic query mask the original
    failure this hint is printed alongside.
    """
    ahead: list[tuple[str, int]] = []
    registry = getattr(client, "registry", None)
    if registry is not None:
        for repo in registry.values():
            try:
                counts = client.git_runner.branch_tracking_counts(repo.absolute_path)
            except Exception:  # noqa: BLE001 — a diagnostic hint must not mask the real error
                continue
            if counts is not None and counts[0] > 0:
                ahead.append((repo.name or repo.repo_id, counts[0]))

    base = (
        "You can try cgitsync autofix (diagnoses first) or, to discard "
        "local-only commits unconditionally, cgitsync pull-force"
    )
    if not ahead:
        return base
    discards = ", ".join(f"{name} ahead(+{count})" for name, count in ahead)
    return f"{base} — this would discard: {discards}"


def _run_with_logging(
    *,
    command_name: str,
    source: Path,
    runner,
    client: ComplexGitSyncClient | None = None,
    project_root: Path | None = None,
) -> int:
    resolved_source = source.resolve()
    active_client = client or ComplexGitSyncClient()
    active_client.run_logger = _create_command_logger(
        command_name,
        resolved_source,
        project_root=project_root,
    )
    active_client.run_logger.log_event(
        "command_start",
        command=command_name,
        source_path=resolved_source,
        project_root=project_root,
    )
    try:
        exit_code = runner(active_client, resolved_source)
    except Exception as exc:
        if active_client.run_logger is not None:
            active_client.run_logger.log_event(
                "command_end",
                level=logging.ERROR,
                command=command_name,
                status="error",
                error=str(exc),
                tree_lifecycle_state=(
                    active_client.registry.lifecycle_state
                    if getattr(active_client, "registry", None) is not None
                    else None
                ),
            )
            if active_client.run_logger.log_path is not None:
                print(f"log_file={active_client.run_logger.log_path}")
        if command_name == "initialise":
            print("Try clean-init method", file=sys.stderr, flush=True)
        if command_name == "pull":
            # `pull-force` is a hard reset to the remote's tip — safe for a
            # repository whose content is prose, but it discards local-only
            # commits outright for one whose content is not (the archived
            # Autofix ticket,
            # .agent/.local/.localSpec/DevTickets/archive/20260923_Autofix_DevPlanTicket.md,
            # §3). `autofix` diagnoses first and only ever repairs a
            # situation a registered repair recognises, so it is offered
            # first; `pull-force` remains available for when the answer really
            # is "the remote wins, unconditionally" — named here with exactly
            # what it would discard, the same count `status` itself would
            # print, so the risk is visible before it happens rather than
            # only in `--help`.
            print(
                _pull_force_risk_hint(active_client),
                file=sys.stderr,
                flush=True,
            )
        raise

    if active_client.run_logger is not None:
        tree_state = active_client.get_tree_state() if getattr(active_client, "registry", None) is not None else None
        active_client.run_logger.log_event(
            "command_end",
            command=command_name,
            status="ok",
            tree_lifecycle_state=(tree_state.lifecycle_state if tree_state else None),
        )
        if active_client.run_logger.log_path is not None:
            print(f"log_file={active_client.run_logger.log_path}")
    return exit_code


def _create_command_logger(
    command_name: str,
    source_path: Path,
    *,
    project_root: Path | None,
):
    profile = "quiet"
    project_log_dir = None
    if source_path.suffix == ".cgs" and source_path.is_file():
        try:
            document = CgsDocument.from_toml(source_path)
        except Exception:
            document = None
        if document is not None:
            profile = str(document.runtime_setting("profile") or "quiet")
            project_log_dir = document.read("project.log_dir")
    return create_run_logger(
        command_name,
        profile=profile,
        source_path=source_path,
        project_root=project_root,
        project_log_dir=project_log_dir,
    )


def _load_ready_registry_source(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> None:
    client.load_gts(source_path)


def _load_visualization_source(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> None:
    if source_path.suffix == ".gts":
        client.load_gts(source_path)
    else:
        client.load_runtime_or_cgs(source_path, discover_nested=discover_nested)


def _cgshome_warnings(cgshome: CgshomeResolution) -> list[str]:
    """Every reason the user might not have meant this workspace.

    The loud case is a resolved CGSHOME that does not contain the current
    directory: the command then reads and writes a tree elsewhere on disk
    while its output — repository names, branches, clean/dirty — looks
    exactly like the tree the user is standing in. Each returned string is
    one warning line, already phrased for the input that caused it, and
    includes the way out.
    """
    if cgshome.contains_cwd:
        return []
    if cgshome.origin == CGSHOME_ORIGIN_DEFAULT:
        # Not a surprise: nothing was found, the default was used, and the
        # line above already said so. Warning here would tell the user their
        # workspace is somewhere unexpected when the truth is that they have
        # no workspace yet.
        return []
    cwd = Path.cwd().resolve()
    warnings = [
        f"CGSHOME ({cgshome.path}) does not contain the current directory ({cwd})."
    ]
    if cgshome.origin == CGSHOME_ORIGIN_ENVIRONMENT:
        warnings.append(
            "$CGSHOME is set and outranks the current directory, so this "
            "command reads and writes that workspace, not this one. Run "
            f"'unset CGSHOME', or pass --search-dir {cwd}, to work here instead."
        )
    elif cgshome.origin == CGSHOME_ORIGIN_SEARCH_DIR:
        warnings.append(
            "--search-dir points outside the current directory, so this "
            "command reads and writes that workspace, not this one."
        )
    else:
        warnings.append(
            f"no workspace was found at or above {cwd}; CGSHOME was resolved "
            f"from the {CGSHOME_ORIGIN_CWD} anyway."
        )
    return warnings


def _print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr, flush=True)


def _announce_cgshome_resolution(cgshome: CgshomeResolution) -> None:
    """Say which workspace was discovered, on whose say-so, and warn if surprising."""
    _print_cgshome_line(cgshome)
    _print_workspace_hint(cgshome)
    _print_warnings(_cgshome_warnings(cgshome))


def _print_cgshome_line(cgshome: CgshomeResolution) -> None:
    """The ``cgshome=`` line, with the use case that workspace puts us in.

    README §2 names two ways to run — standalone and nested — and the choice
    decides how the tree is laid out. Until this line carried it, no command
    ever said which one was in force, so a user who believed they were in
    one and were in the other had nothing to correct them.
    """
    print(
        f"cgshome={cgshome.path} (from {cgshome.origin}) "
        f"use_case={resolve_use_case(cgshome.path).value}"
    )


def _print_workspace_hint(cgshome: CgshomeResolution) -> None:
    """List the workspaces the user may have meant — never pick one.

    Only when the default workspace was used, because that is the one case
    where nothing the user typed chose a workspace. "The tool never fails"
    and "the user probably meant one of these seven" are different problems,
    and merging them is how a command acts on the wrong tree. So: printed,
    with the line that would select each one, and never selected here.
    """
    if cgshome.origin != CGSHOME_ORIGIN_DEFAULT:
        return
    existing = other_workspaces(exclude=cgshome.path)
    if not existing:
        return
    print(f"{len(existing)} other workspace(s) exist. To use one, export it:")
    for workspace in existing:
        print(f"  export CGSHOME={workspace}")


def _announce_source_resolution(resolution: SnapshotResolution) -> None:
    """Say which workspace and snapshot an auto-discovered command will act on.

    A command given an explicit path says nothing — the user already named
    the file. Everything else was discovered, and discovery has three
    possible inputs of which only ``--search-dir`` appears in the typed
    command. Naming the workspace, the snapshot, and the input that chose
    them turns "nothing happened" into a fact the user can check.
    """
    if resolution.origin == SNAPSHOT_ORIGIN_EXPLICIT:
        return
    cgshome = resolution.cgshome
    if cgshome is not None:
        _print_cgshome_line(cgshome)
        _print_workspace_hint(cgshome)
    print(f"source={resolution.path} (from {resolution.origin})")
    if cgshome is None:
        return
    warnings = []
    if not resolution.inside_cgshome:
        warnings.append(
            f"this snapshot is not under {cgshome.path}; its register names "
            "a snapshot belonging to another workspace."
        )
    warnings.extend(_cgshome_warnings(cgshome))
    _print_warnings(warnings)


def _resolve_cgshome(search_dir: str | None) -> Path:
    """Return the CGSHOME a workspace-wide command acts on, announced first."""
    resolution = describe_cgshome(search_dir)
    _announce_cgshome_resolution(resolution)
    return resolution.path


def _resolve_gts_path(gts: str | None, search_dir: str | None) -> Path:
    """Return the resolved .gts path, auto-discovering when *gts* is ``None``."""
    resolution = describe_workspace_source(gts, search_dir)
    _announce_source_resolution(resolution)
    return resolution.path


def _resolve_workspace_source(source: str | None, search_dir: str | None) -> Path:
    """Return a workspace source path for commands that accept optional input.

    The explicit source path, or the latest workspace snapshot under
    ``CGSHOME/.cgitsync`` when *source* is omitted (see
    :func:`~ComplexGitSync.snapshot_resolver.describe_workspace_source`).
    An auto-discovered one is announced first (see
    :func:`_announce_source_resolution`).
    """
    resolution = describe_workspace_source(source, search_dir)
    _announce_source_resolution(resolution)
    return resolution.path


def _resolve_visualization_source(source: str | None, search_dir: str | None) -> Path:
    """Return the resolved source path for visualization commands.

    When *source* is provided it is returned as-is (may be .cgs or .gts).
    When *source* is ``None`` the latest .gts snapshot is discovered
    automatically, and announced.
    """
    return _resolve_workspace_source(source, search_dir)


def _non_negative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("depth must be >= 0")
    return value


def _print_dry_run_plan(
    client: ComplexGitSyncClient,
    *,
    command_name: str,
    actions: tuple[str, ...],
    scope: RepoScope = RepoScope.ALL,
) -> None:
    print(f"dry_run=true command={command_name}")
    print(f"plan_actions={' -> '.join(actions)}")
    print(f"plan_order={_format_leaf_first_repo_order(client, scope)}")
    _print_scope_note(client, scope)


def _memory_declared_for_dry_run(client: ComplexGitSyncClient) -> bool:
    """Whether ``--dry-run`` should mention the memory fold the real run
    would attempt. ``False`` for anything that is not the real client
    (a CLI test double, most often) — same defensive shape as
    :func:`_format_leaf_first_repo_order`."""
    try:
        return client.memory_declared()
    except AttributeError:
        return False


def _print_memory_fold_outcome(client: ComplexGitSyncClient) -> None:
    """Say what the command just folded into this project's own memory, if any.

    Printed ahead of the command's own outcome lines
    (`main_1-1_PushFoldsMemory_DevPlanTicket.md` WP-3): `push`/`tag`/
    `freeze` now cross the `.cgitsync` → `.cgitsync/.memory` frontier
    before doing their own work, and a person reading the output should
    see that happen before whatever they actually ran. Prints nothing
    when the tree declares no memory (`last_memory_fold` stays `None`) —
    the common case, and not worth a line on every ordinary push — or when
    *client* is a test double that carries no such attribute at all, the
    same defensive shape :func:`_print_write_outcomes` already uses.
    """
    try:
        fold = client.last_memory_fold
    except AttributeError:
        return
    if fold is None:
        return
    print(
        f"memory_fold branch={fold['branch']} committed={fold['committed']} "
        f"recorded={fold['recorded']}"
    )


def _format_leaf_first_repo_order(
    client: ComplexGitSyncClient,
    scope: RepoScope = RepoScope.ALL,
) -> str:
    try:
        registry = client.get_dependency_registry()
    except (AttributeError, RuntimeError):
        return "leaf -> parent -> root"
    repo_names = [entry.name for entry in iter_tree_leaf_first(registry, scope)]
    return " -> ".join(repo_names) if repo_names else "leaf -> parent -> root"


def _resolve_write_scope(
    client: ComplexGitSyncClient,
    *,
    private: bool,
    command: str,
    all_writable: bool = False,
    default: RepoScope | None = None,
) -> RepoScope:
    """The scope a write command will run at, validated the same way a real run is.

    Calls the one owner of that rule
    (:func:`~ComplexGitSync.orchestre.resolve_command_scope`) rather than
    re-deriving it, so ``--dry-run`` fails on an empty ``--private`` exactly
    as the real command would instead of printing a plan that could never
    execute.

    *default* is for the commands whose bare form is not ``PROJECT`` —
    ``rm`` reaches every repository and ``freeze`` every writable one. It
    picks the other owner of the same rule
    (:func:`~ComplexGitSync.orchestre._scope_for`), which the client method
    behind those commands calls too, so the printed plan and the real run
    cannot disagree about what the bare command means.
    """
    try:
        registry = client.get_dependency_registry()
    except (AttributeError, RuntimeError):
        # Same tolerance the other helpers here already have: a client with
        # no loaded registry still gets a usable scope, and the real check
        # runs inside the client call itself.
        if private:
            return RepoScope.PRIVATE
        if all_writable:
            return RepoScope.WRITABLE
        return default if default is not None else RepoScope.PROJECT
    if default is not None:
        return _scope_for(registry, private=private, command=command, default=default)
    return resolve_command_scope(
        registry, private=private, command=command, all_writable=all_writable
    )


def _print_scope_note(client: ComplexGitSyncClient, scope: RepoScope) -> None:
    """Say which configuration repos a write command left alone, and why.

    Silence here is what used to make a tree-wide sweep dangerous: the
    command that wrote somewhere you did not mean looked exactly like the
    one that did not. Every scoped command says what it skipped.

    ``--all`` skips nothing writable, so the usual "run it again with
    --private" hint would be wrong there. It gets its own line instead,
    which also answers the question a user of ``--all`` actually has: was
    there a second half, and did it have anything in it?
    """
    if scope is RepoScope.ALL:
        return
    try:
        registry = client.get_dependency_registry()
    except (AttributeError, RuntimeError):
        return
    if scope is RepoScope.WRITABLE:
        _print_all_scope_note(registry)
        return
    skipped = [entry for entry in registry.values() if not scope.includes(entry)]
    if not skipped:
        return
    writable = sorted(
        entry.name for entry in skipped if entry.effective_private and entry.effective_writable
    )
    hint = f" ({', '.join(writable)} with --private)" if writable else ""
    print(f"scope={scope.value} skipped={len(skipped)} configuration repo(s){hint}")


def _warn_paths_reaching_configuration_repos(
    client: ComplexGitSyncClient, paths: Sequence[str], *, private: bool
) -> None:
    """Warn when a path-addressed command is about to write to a shared repo.

    ``rm`` is handed its paths, so it has never been scoped: a path inside
    a configuration repository is removed from there, no flag needed. That
    stays true — a path typed in full is not the tree-wide sweep the scope
    rail was built for — but it stops being silent. The warning names the
    repository, so a user who meant a file of their own can see they hit a
    repository shared with other projects, and names ``--private``, which
    turns the same reach into a rule the command enforces.

    Says nothing when ``--private`` was passed (the scope is already doing
    this job), when no path lands in a configuration repository, or when
    the tree cannot be read — a warning is never worth an exception.
    """
    if private:
        return
    try:
        registry = client.get_dependency_registry()
    except (AttributeError, RuntimeError):
        return
    reached: dict[str, list[str]] = {}
    for path in paths:
        try:
            repo, relative = resolve_repo_for_path(registry, path)
        except GitSyncError:
            continue  # The command itself reports an unusable path, in full.
        if repo.effective_private:
            reached.setdefault(repo.name, []).append(relative)
    for name in sorted(reached):
        _print_warnings([
            f"{' '.join(sorted(reached[name]))} belongs to '{name}', a "
            f"configuration repository shared with other projects. It is being "
            f"written to without --private; pass --private to act on the "
            f"configuration repositories alone, and to be refused anywhere else."
        ])


def _print_all_scope_note(registry) -> None:
    """What ``--all`` reached, named in both halves.

    A user who stops typing the project/private distinction should not also
    stop seeing it, so the two groups are always named separately even
    though one command wrote both. A tree with no writable configuration
    repository is the common case, not an error: say the half was empty and
    carry on.
    """
    private = sorted(
        entry.name for entry in registry.values() if RepoScope.PRIVATE.includes(entry)
    )
    project = sorted(
        entry.name for entry in registry.values() if RepoScope.PROJECT.includes(entry)
    )
    read_only = sorted(
        entry.name
        for entry in registry.values()
        if entry.effective_private and not entry.effective_writable
    )
    half = ", ".join(private) if private else "(none in this tree)"
    print(f"scope=all project={', '.join(project) or '(none)'} private={half}")
    if read_only:
        print(f"scope=all never_written={len(read_only)} read-only repo(s) ({', '.join(read_only)})")


def _format_tree_state_line(tree_state: ProjectTreeState) -> str:
    lifecycle = tree_state.lifecycle_state.value
    git_tree_created = lifecycle != "UNLOADED"
    git_tree_active = bool(tree_state.is_ready)
    return (
        f"{lifecycle} "
        f"ready={str(tree_state.is_ready).lower()} "
        f"complete={str(tree_state.registry_complete).lower()} "
        f"gittree_created={str(git_tree_created).lower()} "
        f"gittree_active={str(git_tree_active).lower()}"
    )


def _format_repo_tree_outline(client: ComplexGitSyncClient) -> str:
    try:
        return str(client.format_repo_tree())
    except AttributeError:
        return ""


def _print_repo_tree_result(client: ComplexGitSyncClient) -> None:
    try:
        tree = str(client.view_tree())
    except AttributeError:
        tree = ""
    if tree:
        print("repos:")
        print(tree)


def _print_write_outcomes(
    client: ComplexGitSyncClient, *, verb: str, nothing_note: str
) -> None:
    """Say what the write that just ran did to each repository, and to none.

    A tree-wide write used to print only the tree's lifecycle state, which
    is the same line whether every repository was written or none was. That
    is what makes a no-op indistinguishable from a wrong workspace, a wrong
    scope, or an empty diff. One line per repository, then a summary — and,
    when nothing was written, *nothing_note* saying so in words rather than
    leaving the user to infer it from silence.

    *verb* is the past-tense word each acted-on line starts with
    (``committed``, ``pushed``, ``staged``); repositories the command
    visited without changing anything are marked ``skipped`` and carry the
    reason the operation recorded.
    """
    try:
        outcomes = client.last_write_outcomes
    except AttributeError:
        return
    if not outcomes:
        print(f"{verb} nothing: no repository was in scope")
        return
    for outcome in outcomes:
        marker = verb if outcome.acted else "skipped"
        print(f"{marker} {outcome.name}: {outcome.detail}")
    acted = sum(1 for outcome in outcomes if outcome.acted)
    print(f"{verb}={acted} skipped={len(outcomes) - acted}")
    if not acted:
        print(f"note: {nothing_note}")


def _print_gitignore_sync_report(client: ComplexGitSyncClient) -> None:
    """Print what ``.gitignore`` sync changed (DevPlanTicket Milestones 1-2).

    Always verbose, never silent. By default nothing is staged, committed,
    or pushed — this is purely informational. With ``--commit-gitignore``
    each entry was also committed and pushed; the report reflects that
    instead, so the flag changes what happened, not whether the user is
    told about it.
    """
    try:
        synced_entries = client.last_gitignore_sync
    except AttributeError:
        return
    for entry in synced_entries:
        status = "committed and pushed" if entry.committed else "not committed"
        print(f".gitignore updated ({status}): {entry.name} ({entry.absolute_path})")
        for relative_path in entry.added_paths:
            print(f"  + {relative_path}")

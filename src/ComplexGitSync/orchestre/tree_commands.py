"""tree_commands — The tree-wide Git commands: pull, checkout, commit, merge, push and their kin.

Ring: 3
Contract: The tree-wide Git commands: pull, checkout, commit, merge, push and their kin.
Imports: __version__, auth_hints, autofix, client, commit_message, errors, git_probes, git_repo, git_tree, git_tree_branch, operations, snapshot_resolver
"""

from __future__ import annotations

import os
import shutil
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import __version__
from ..commit_message import CommitMessagePolicy
from ..errors import (
    GitSyncError,
)

if TYPE_CHECKING:
    from ..autofix import RepairOutcome
from ..git_repo import (
    AccessProtocol,
    RefKind,
    RepoScope,
)
from ..git_tree import (
    ROOT_REPO_ID,
    TreeLifecycleState,
    WorkingGitTree,
    iter_tree_leaf_first,
)
from ..git_tree_branch import GitTreeBranches, tree_project_name
from ..operations import (
    MERGE_INTO_ACTS,
    RepoBranches,
    ResolveOutcome,
    paths_outside_scope,
)
from ..snapshot_resolver import discover_cgshome
from .auth_hints import AuthFailureHints
from .git_probes import GitProbes

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient


class TreeCommands:
    """The tree-wide Git commands: pull, checkout, commit, merge, push and their kin.

    A collaborator of :class:`~ComplexGitSync.orchestre.client.ComplexGitSyncClient`,
    which keeps the state and exposes every method here unchanged. Everything
    that is not this class's own private helper is reached through the
    client, so a caller that patches a client method is still obeyed.
    """

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def pull(
        self,
        source_path: str | Path,
        *,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Resynchronize from a ``.cgs`` spec or restore from a ``.gts`` snapshot.

        ``commit_gitignore``/``force_gitignore_sync``/``git_user_name``/
        ``git_user_email`` only apply to ``.cgs`` sources (dispatched to
        :meth:`restart`) — a ``.gts`` source runs no discovery, so there is
        nothing new for the ``.gitignore`` lifecycle sync to find.
        ``force_access_protocol`` applies to both — see :meth:`push`.
        """
        resolved_source = Path(source_path).resolve()
        if resolved_source.suffix == ".cgs":
            return self.client.restart(
                resolved_source,
                commit_gitignore=commit_gitignore,
                force_gitignore_sync=force_gitignore_sync,
                git_user_name=git_user_name,
                git_user_email=git_user_email,
                force_access_protocol=force_access_protocol,
            )
        if resolved_source.suffix == ".gts":
            previous_tree_state = (
                self.client.registry.lifecycle_state if self.client.registry else TreeLifecycleState.UNLOADED
            )
            self.client._log_event("pull_start", snapshot_path=resolved_source)
            registry = self.client.load_gts(resolved_source)
            registry_values = registry.values() if hasattr(registry, "values") else ()
            if any(not entry.absolute_path.exists() for entry in registry_values):
                registry = self.client._restore_gts_snapshot(resolved_source)
            else:
                protocol = AccessProtocol(force_access_protocol) if force_access_protocol else None
                try:
                    self.client.orchestre.git_tree.git.pull(self.client.git_runner, force_access_protocol=protocol)
                except GitSyncError as exc:
                    hint = AuthFailureHints.protocol_switch_hint(str(exc), command="pull")
                    if hint:
                        raise GitSyncError(f"{exc}\n{hint}") from exc
                    raise
            if not registry.is_ready():
                raise GitSyncError("pull did not produce a READY tree.")
            snapshot_path = self.client.write_gts_snapshot(command_origin="pull")
            self.client.state_store.record_snapshot(resolved_source, snapshot_path)
            self.client._log_tree_transition(previous_tree_state, registry.lifecycle_state, reason="pull")
            self.client._log_event("pull_end", snapshot_path=resolved_source, output_gts=snapshot_path)
            self.client._warn_environment_drift()
            return registry
        raise ValueError(
            f"Unsupported source format '{resolved_source.suffix}' for {resolved_source!s}; expected .cgs or .gts."
        )

    def pull_force(
        self,
        source_path: str | Path,
        *,
        force_access_protocol: str | None = None,
        private: bool = False,
    ) -> WorkingGitTree:
        """Destructively resynchronize from a ``.cgs`` spec or ``.gts`` snapshot.

        ``force_access_protocol`` — see :meth:`push`.

        ``private`` limits the resynchronisation to the writable
        configuration repositories. It matters more here than anywhere
        else: this discards local work (``checkout -B FETCH_HEAD``, then
        ``clean -fd``), so a user asking for their configuration
        repositories alone must not get the whole tree.
        """
        resolved_source = Path(source_path).resolve()
        previous_tree_state = self.client.registry.lifecycle_state if self.client.registry else TreeLifecycleState.UNLOADED
        self.client._log_event("pull_force_start", source_path=resolved_source)
        if resolved_source.suffix == ".cgs":
            # Same reasoning as restart(): this re-syncs a tree already on
            # disk, so the root is discovered, never guessed from the .cgs
            # file's own directory.
            try:
                established_root: Path | None = discover_cgshome()
            except FileNotFoundError:
                established_root = None
            registry = self.client.load_cgs(
                resolved_source, discover_nested=True, project_root=established_root
            )
        elif resolved_source.suffix == ".gts":
            registry = self.client.load_gts(resolved_source)
        else:
            raise ValueError(
                f"Unsupported source format '{resolved_source.suffix}' for {resolved_source!s}; expected .cgs or .gts."
            )
        protocol = AccessProtocol(force_access_protocol) if force_access_protocol else None
        scope = GitProbes.scope_for(
            registry, private=private, command="pull-force", default=RepoScope.ALL
        )
        try:
            self.client.orchestre.git_tree.git.pull_force(
                self.client.git_runner, force_access_protocol=protocol, scope=scope
            )
        except GitSyncError as exc:
            hint = AuthFailureHints.protocol_switch_hint(str(exc), command="pull-force")
            if hint:
                raise GitSyncError(f"{exc}\n{hint}") from exc
            raise
        if not registry.is_ready():
            if scope is not RepoScope.ALL:
                raise GitSyncError(
                    f"pull-force --private did not produce a READY tree: the "
                    f"repositories outside the {scope.value} scope were not "
                    f"resynchronised, and {resolved_source.name} describes them "
                    f"too. Resynchronise from a .gts snapshot of a tree that is "
                    f"already checked out, or drop --private to do the whole tree."
                )
            raise GitSyncError("pull-force did not produce a READY tree.")
        snapshot_path = self.client.write_gts_snapshot(command_origin="pull-force")
        self.client.state_store.record_snapshot(resolved_source, snapshot_path)
        self.client._log_tree_transition(previous_tree_state, registry.lifecycle_state, reason="pull-force")
        self.client._log_event("pull_force_end", source_path=resolved_source, output_gts=snapshot_path)
        return registry

    def autofix(
        self,
        *,
        error: str | None = None,
        repo_name: str | None = None,
    ) -> "RepairOutcome":
        """Read "the former error" — or *error*, if given directly — and
        run whichever registered repair in :mod:`ComplexGitSync.autofix`
        matches it.

        With *error* omitted, reads the most recent
        ``.cgitsync/logs/*.log``'s failing command, the same one the
        owner just saw fail — see ``.agent/.local/.localSpec/DevTickets/archive/20260923_Autofix_DevPlanTicket.md``.
        *repo_name* narrows which mounted repository is diagnosed;
        omitted, it is guessed from the error text (a chain-shaped
        repository's name is normally visible in its own remote URL)
        before falling back to every repository in the tree.
        """
        from ..autofix import FromCliRepair

        if self.client.registry is None:
            raise GitSyncError("autofix: no workspace loaded.")
        root_entry = self.client.registry.get("root")
        logs_dir = root_entry.absolute_path / ".cgitsync" / "logs"
        self.client._log_event("autofix_start", error=error, repo_name=repo_name)
        outcome = FromCliRepair().run(
            self.client.registry,
            self.client.git_runner,
            logs_dir=logs_dir,
            error=error,
            repo_name=repo_name,
        )
        self.client._log_event("autofix_end", repaired=outcome.repaired, detail=outcome.detail)
        return outcome

    def checkout(
        self,
        branch_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
        private: bool = False,
    ) -> WorkingGitTree:
        """Check out *branch_name* across the full tree from a READY ``.gts`` state.

        Requires a ``READY`` registry.  After a successful execution the
        registry remains ``READY`` and a ``.gts`` snapshot is written.

        Steps delegated to :meth:`~ComplexGitSync.git_tree.GitTreeGitCommands.checkout`:

        1. :func:`~ComplexGitSync.operations.propagate_global_branch` — set
           the target ref on every entry.
        2. :func:`~ComplexGitSync.operations.create_global_branch` — create
           the branch locally where missing.
        3. ``git checkout`` on every repo, parent-first.
        """
        registry = self.client.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self.client._log_event("checkout_start", branch_name=branch_name, ref_kind=ref_kind)
        # Said before the tree moves, because afterwards the build that
        # would say it is gone.
        self._warn_if_build_changes(branch_name)
        self.client.orchestre.git_tree.git.checkout(
            self.client.git_runner,
            branch_name,
            ref_kind=ref_kind,
            scope=GitProbes.scope_for(registry, private=private, command="checkout"),
        )
        snapshot_path = self.client.write_gts_snapshot(command_origin="checkout")
        if self.client.source_path is not None:
            self.client.state_store.record_snapshot(self.client.source_path, snapshot_path)
        self.client._log_tree_transition(previous_state, registry.lifecycle_state, reason="checkout")
        self.client._log_event("checkout_end", branch_name=branch_name, ref_kind=ref_kind)
        return registry

    def branch(
        self,
        branch_name: str,
        *,
        private: bool = False,
    ) -> WorkingGitTree:
        """Create *branch_name* across the full tree without checkout."""
        registry = self.client.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self.client._log_event("branch_start", branch_name=branch_name)
        scope = GitProbes.scope_for(registry, private=private, command="branch")
        self.client.orchestre.git_tree.git.branch(self.client.git_runner, branch_name, scope=scope)
        if ROOT_REPO_ID in registry.repos:
            snapshot_path = self.client.write_gts_snapshot(command_origin="branch")
            if self.client.source_path is not None:
                self.client.state_store.record_snapshot(self.client.source_path, snapshot_path)
        self.client._log_tree_transition(previous_state, registry.lifecycle_state, reason="branch")
        self.client._log_event("branch_end", branch_name=branch_name)
        return registry

    def list_branches(self, *, private: bool = False) -> tuple[RepoBranches, ...]:
        """Every local branch of every repository in the tree, read-only.

        Without ``private`` that is the whole tree — a private/local
        repository carries branches of its own too, and they are part of
        what a branch of this project means. ``private`` narrows it to the
        writable configuration repositories, as it does for ``branch``.
        """
        registry = self.client.get_dependency_registry()
        scope = GitProbes.scope_for(registry, private=private, command="branch --list")
        return self.client.orchestre.git_tree.git.list_branches(self.client.git_runner, scope=scope)

    def close_branch(self, branch_name: str, *, private: bool = False) -> WorkingGitTree:
        """Rename *branch_name* to its closed name across the full tree, leaf-first.

        Renames, never deletes
        (`main_1-1_BranchClosing_DevPlanTicket.md` D1) —
        :func:`~ComplexGitSync.git_branch.closed_branch_name` names the
        target, and :func:`~ComplexGitSync.operations.close_branch` performs
        it. Refuses before touching any repository when *branch_name* is
        the project's own default branch, or when any repository in scope
        is currently checked out on it (D5) — see that function's own
        docstring for the full contract. ``--private`` selects the writable
        configuration repositories instead of the project's own, the same
        as ``branch`` (create).
        """
        registry = self.client.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self.client._log_event("close_branch_start", branch_name=branch_name)
        scope = GitProbes.scope_for(registry, private=private, command="branch close")
        self.client.last_write_outcomes = self.client.orchestre.git_tree.git.close_branch(
            self.client.git_runner, branch_name, scope=scope
        )
        if ROOT_REPO_ID in registry.repos:
            snapshot_path = self.client.write_gts_snapshot(command_origin="close_branch")
            if self.client.source_path is not None:
                self.client.state_store.record_snapshot(self.client.source_path, snapshot_path)
        self.client._log_tree_transition(previous_state, registry.lifecycle_state, reason="close_branch")
        self.client._log_event(
            "close_branch_end",
            branch_name=branch_name,
            closed=sum(1 for o in self.client.last_write_outcomes if o.acted),
        )
        return registry

    def commit(
        self,
        message: str,
        *,
        stage_all: bool = True,
        private: bool = False,
        all_writable: bool = False,
    ) -> WorkingGitTree:
        """Commit changes across the full tree, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  Repos with
        no staged changes are silently skipped.  After a successful execution
        the registry remains ``READY``.

        In a tree that has adopted DevSpec, *message* is checked against
        ``AgentConduct.md`` §2 first and a message that breaks it raises
        :exc:`~ComplexGitSync.errors.GitSyncError` naming the rule, before
        anything is staged or committed.  Any other tree is not checked.
        """
        registry = self.client.get_dependency_registry()
        policy = CommitMessagePolicy.for_tree(registry.get(ROOT_REPO_ID).absolute_path)
        if policy is not None:
            policy.require(message)
        previous_state = registry.lifecycle_state
        scope = self.client._write_scope(registry, "commit", private, all_writable)
        self.client._log_event("commit_start", message=message, stage_all=stage_all, scope=scope.value)
        self.client.last_write_outcomes = GitProbes.as_write_outcomes(
            self.client.orchestre.git_tree.git.commit(
                self.client.git_runner,
                message,
                stage_all=stage_all,
                scope=scope,
            )
        )
        self.client._log_tree_transition(previous_state, registry.lifecycle_state, reason="commit")
        committed = self.client._memory_commands._collect_commit_records(registry, scope, message)
        if committed:
            # A commit changes every repository's HEAD, so the tree is in a
            # state nobody has recorded yet. Writing it here is what gives
            # the messages a State to be filed under — and what stops the
            # memory skipping every commit until the next push.
            self.client.write_gts_snapshot(command_origin="commit", commits=committed)
        self.client._log_event(
            "commit_end",
            message=message,
            committed=sum(1 for o in self.client.last_write_outcomes if o.acted),
        )
        return registry

    def merge(
        self,
        project_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
        ff_only: bool = False,
        no_ff: bool = False,
    ) -> tuple[tuple[str, str], ...]:
        """Merge *project_branch* into the tree's current branch, leaf-first.

        *project_branch* is always the **project's** branch name. Each
        repository resolves what that means for itself: a project-owned repo
        merges that branch, and a private/local repo merges the branch
        derived from it (``<base>_<project_branch>``), because that is where
        its settings for that project branch live. ``private=True`` selects
        the writable configuration repositories instead of the project's own.

        Every repository in scope is checked before any is merged, so a
        conflict anywhere leaves the whole tree untouched. Returns one
        ``(repo_name, merged_ref)`` pair per repository a merge moved.

        **This merge knows what it merged into.** ``merge b`` is
        ``merge b --into <the branch the tree is on>``, and that target is
        read once, up front, instead of staying implicit in whatever each
        repository's ``HEAD`` happens to be — it names the branch in
        ``merge_start``/``merge_end``, and a merge that cannot say what it
        merged into cannot be recorded as more than "something moved". The
        branch comes from `git_tree_branch.py`, which owns the question.

        A State *is* written, unlike before: a merge moves ``HEAD``, which is
        what a State records, and skipping the write left `status`'s
        ``RECORDED`` column stale about commits this command had just made.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.
        """
        registry = self.client.get_dependency_registry()
        previous_state = registry.lifecycle_state
        scope = self.client._write_scope(registry, "merge", private, all_writable)
        into_branch = GitTreeBranches(registry, self.client.git_runner).tree_branch
        self.client._log_event(
            "merge_start",
            project_branch=project_branch,
            into_branch=into_branch,
            scope=scope.value,
        )
        merged = self.client.orchestre.git_tree.git.merge(
            self.client.git_runner,
            project_branch,
            scope=scope,
            ff_only=ff_only,
            no_ff=no_ff,
        )
        self.client._log_tree_transition(previous_state, registry.lifecycle_state, reason="merge")
        self.client.write_gts_snapshot(command_origin="merge")
        self.client._log_event(
            "merge_end",
            project_branch=project_branch,
            into_branch=into_branch,
            merged=len(merged),
        )
        return merged

    def merge_into(
        self,
        source_branch: str,
        target_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
        ff_only: bool = False,
        no_ff: bool = False,
    ) -> tuple[Any, ...]:
        """Check out *target_branch* and merge *source_branch* into it.

        What `merge` does after you have already run `checkout`, except that
        it does both — and doing both in one call is the entire point, not a
        convenience. This project manages a tree containing this project,
        installed editable, so a tree-wide checkout replaces the code that
        runs the next command: `checkout` followed by `merge` makes the
        older branch merge itself. One process cannot be caught that way,
        because its modules are already loaded.

        Both names are the **project's** branches; each repository
        translates them, so a private/local repository merges
        ``<base>_<source>`` into ``<base>``.

        Every repository is checked before any is touched — a conflict or a
        missing target leaves the whole tree on the source branch, with
        nothing checked out and nothing merged.

        A State is written, as `checkout` writes one: the tree is on a
        different branch afterwards and nothing else would record it.
        """
        registry = self.client.get_dependency_registry()
        previous_state = registry.lifecycle_state
        scope = self.client._write_scope(registry, "merge", private, all_writable)
        self.client._log_event(
            "merge_into_start",
            source_branch=source_branch,
            target_branch=target_branch,
            scope=scope.value,
        )
        self._warn_if_build_changes(target_branch, offer_remedy=False)
        outcomes = self.client.orchestre.git_tree.git.merge_into(
            self.client.git_runner,
            source_branch,
            target_branch,
            scope=scope,
            ff_only=ff_only,
            no_ff=no_ff,
        )
        self.client._log_tree_transition(
            previous_state, registry.lifecycle_state, reason="merge_into"
        )
        self.client.write_gts_snapshot(command_origin="merge-into")
        self.client._log_event(
            "merge_into_end",
            source_branch=source_branch,
            target_branch=target_branch,
            acted=sum(1 for plan in outcomes if plan.status in MERGE_INTO_ACTS),
        )
        return outcomes

    def merge_into_plan(
        self,
        source_branch: str,
        target_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
    ) -> tuple[Any, ...]:
        """What :meth:`merge_into` would do, in order, without doing it.

        Decided by the same function the merge uses, so a dry run cannot
        promise something the merge then refuses.
        """
        from ..operations import merge_into_status

        self._warn_if_build_changes(target_branch, offer_remedy=False)
        registry = self.client.get_dependency_registry()
        scope = self.client._write_scope(registry, "merge", private, all_writable)
        project_name = tree_project_name(registry)
        return tuple(
            merge_into_status(
                repo,
                self.client.git_runner,
                source_branch,
                target_branch,
                project_name=project_name,
            )
            for repo in iter_tree_leaf_first(registry, scope)
        )

    def _warn_if_build_changes(self, branch: str, *, offer_remedy: bool = True) -> None:
        """Warn when moving to *branch* replaces the ComplexGitSync running.

        Warned rather than printed, so a Python caller hears it too — the
        CLI is not the only way this happens. Warned rather than refused,
        because checking out an older branch to read it is legitimate;
        `main_1-4_SnapshotVersionGuard` is what makes the older build fail
        honestly if it is then pointed at a newer workspace.
        """
        installed = self.client.build_installed_from(branch)
        if installed is None or installed == __version__:
            return
        older = installed < __version__
        # `merge --into` is already the remedy, so it does not offer itself.
        remedy = (
            f" To merge into {branch!r} instead of stranding yourself there, run "
            f"'cgitsync merge <source> --into {branch}', which checks out and "
            f"merges in one command."
            if older and offer_remedy
            else ""
        )
        warnings.warn(
            f"this tree holds the ComplexGitSync you are running: {branch!r} carries "
            f"{installed} and this is {__version__}, so the next command runs "
            f"{'an older' if older else 'a different'} build.{remedy}",
            stacklevel=4,
        )

    def merge_resolve(
        self,
        project_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
        ff_only: bool = False,
        no_ff: bool = False,
    ) -> ResolveOutcome:
        """Merge one repository at a time, stopping at the first conflict.

        Decision recorded: :meth:`merge` stays the default. It merges nothing
        when any repository conflicts, so it can never leave the conflicted
        worktree a merge tool needs. This gives that guarantee up on purpose,
        which is why it is opt-in. Requires a ``READY`` registry.
        """
        registry = self.client.get_dependency_registry()
        previous_state = registry.lifecycle_state
        scope = self.client._write_scope(registry, "merge", private, all_writable)
        self.client._log_event(
            "merge_resolve_start", project_branch=project_branch, scope=scope.value
        )
        outcome = self.client.orchestre.git_tree.git.merge_one_at_a_time(
            self.client.git_runner,
            project_branch,
            scope=scope,
            ff_only=ff_only,
            no_ff=no_ff,
        )
        self.client._log_tree_transition(
            previous_state, registry.lifecycle_state, reason="merge --resolve"
        )
        self.client._log_event(
            "merge_resolve_end",
            project_branch=project_branch,
            merged=len(outcome.merged),
            stopped_at=outcome.stopped_at,
        )
        return outcome

    def merge_resolve_all(
        self,
        project_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
        ff_only: bool = False,
        no_ff: bool = False,
    ) -> ResolveOutcome:
        """Merge all repositories, resolving conflicts one at a time until done.

        Repeatedly calls :meth:`merge_resolve`, continuing past conflicts after
        either opening a merge tool (for human-editable files) or regenerating
        (for binary/generated files). Stops only when all repositories are merged
        or when a human-editable conflict requires manual resolution.

        Returns the outcome of the last merge_resolve call. If all conflicts were
        resolved, ``stopped_at`` will be None.
        """
        outcome = None
        while True:
            outcome = self.client.merge_resolve(
                project_branch,
                private=private,
                all_writable=all_writable,
                ff_only=ff_only,
                no_ff=no_ff,
            )
            # If nothing is left to merge, we're done
            if outcome.stopped_at is None:
                break

            stopped_repo_path = None
            try:
                registry = self.client.get_dependency_registry()
                repo = registry.get(outcome.stopped_at_id)
                stopped_repo_path = repo.absolute_path
            except (KeyError, AttributeError):
                # If we can't find the repo, break to avoid infinite loop
                break

            # Check if conflicting files are all binary or generated
            # If so, regenerate them and continue; otherwise, let user resolve
            should_continue = self._handle_conflicted_files(
                outcome.stopped_paths, stopped_repo_path
            )
            if not should_continue:
                # User needs to resolve manually; open merge tool and stop
                if outcome.stopped_at_id:
                    self.client.open_merge_tool(outcome.stopped_at_id)
                break

        return outcome

    def _handle_conflicted_files(self, paths: tuple[Path, ...], repo_path: Path) -> bool:
        """Check if conflicted files are binary/generated; regenerate if so.

        Returns True if all conflicts were auto-resolved (regenerated), False if
        human resolution is needed.
        """
        if not paths:
            return True  # No paths means unmergeable (no shared history), skip

        # Patterns for files that should be regenerated rather than merged
        generated_patterns = {
            "scripts/ceiling_baseline.json",
            "docs/MASTER.pdf",
            "docs/c_*.pdf",
        }

        binary_extensions = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico"}

        def is_generated(path: Path) -> bool:
            path_str = str(path).replace("\\", "/")
            # Check exact matches
            if path_str in generated_patterns:
                return True
            # Check pattern matches (c_*.pdf)
            if "docs/" in path_str and path_str.endswith(".pdf"):
                return True
            return False

        def is_binary(path: Path) -> bool:
            return path.suffix.lower() in binary_extensions

        # Check if all conflicts are binary/generated
        all_regenerable = all(is_generated(p) or is_binary(p) for p in paths)

        if not all_regenerable:
            return False  # Has human-editable files; need merge tool

        # Regenerate all binary/generated files
        for path in paths:
            if is_generated(path):
                self._regenerate_file(path, repo_path)
            # For other binary files, just mark as resolved (user will regenerate)
            # Add the file to mark conflict as resolved
            self.client.git_runner._run("add", str(path), cwd=repo_path)

        return True

    def _regenerate_file(self, path: Path, repo_path: Path) -> None:
        """Regenerate a known generated file.

        Prints the regeneration command and executes it.
        """
        path_str = str(path).replace("\\", "/")

        if path_str == "scripts/ceiling_baseline.json":
            print(
                f"Regenerating {path_str}...\n"
                "  pixi run python scripts/check_module_ceilings.py --write-baseline"
            )
            self.client.git_runner._run(
                "python",
                "scripts/check_module_ceilings.py",
                "--write-baseline",
                cwd=repo_path,
            )
        elif path_str.endswith(".pdf") and "docs/" in path_str:
            print(
                f"Regenerating {path_str}...\n"
                f"  cd {repo_path}/docs && latexmk -pdf {path.stem}.tex"
            )
            docs_path = repo_path / "docs"
            self.client.git_runner._run(
                "latexmk", "-pdf", f"{path.stem}.tex", cwd=docs_path
            )

    def open_merge_tool(self, repo_id: str) -> str | None:
        """Open one repository's conflicts in a merge tool.

        *repo_id* is the registry key (:class:`ResolveOutcome`'s
        ``stopped_at_id``), never the display name (``stopped_at``): two
        repositories in a tree may share a name, and a name is not always
        its own id (`.memory`'s never is) — passing the name here used to
        raise a bare ``KeyError`` instead of finding the repository.

        Returns ``None`` once the tool has run, or the command to run by hand
        when there is no tool to open — a missing editor is a normal outcome
        here, not an error.
        """
        registry = self.client.get_dependency_registry()
        try:
            repo = registry.get(repo_id)
        except KeyError as exc:
            raise GitSyncError(
                f"{repo_id!r} is not a repository in this tree — expected a repo_id "
                "(ResolveOutcome.stopped_at_id), not a display name."
            ) from exc
        tool, command = self.client._resolve_merge_tool(repo.absolute_path)
        if tool is None:
            return (
                f"cd {repo.absolute_path} && git mergetool  "
                f"# then: cgitsync add && cgitsync commit"
            )
        self.client.git_runner.mergetool(
            repo.absolute_path, tool=tool, tool_command=command
        )
        return None

    def _resolve_merge_tool(self, repo_path: Path) -> tuple[str | None, str | None]:
        # The user's own merge.tool always wins; VS Code is only a suggestion
        # when they configured nothing. Argument order is git's, not VS Code's
        # docs': $REMOTE is theirs and $LOCAL ours.
        configured = self.client.git_runner.configured_merge_tool(repo_path)
        if configured:
            return configured, None
        if shutil.which("code"):
            # VS Code is available. Check if we can reach it:
            # - $DISPLAY for X11 sessions
            # - $WAYLAND_DISPLAY for Wayland sessions
            # - $TERM_PROGRAM=="vscode" for integrated terminal or Remote-SSH/Tunnel
            if (
                os.environ.get("DISPLAY")
                or os.environ.get("WAYLAND_DISPLAY")
                or os.environ.get("TERM_PROGRAM") == "vscode"
            ):
                return "vscode", "code --wait --merge $REMOTE $LOCAL $BASE $MERGED"
        return None, None

    def refresh_private(self) -> tuple[tuple[str, str], ...]:
        """Bring each private/local repository up to date with its base branch.

        What ``pull --private`` runs. A private/local repository records this
        project's settings per project branch; those branches drift while a
        feature branch is open. This fetches and merges the base branch into
        each one, using the same merge primitive :meth:`merge` uses.

        Returns one ``(repo_name, merged_ref)`` pair per repository a merge
        moved. A repository already on its base branch has nothing to take
        and is skipped.
        """
        registry = self.client.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self.client._log_event("refresh_private_start")
        refreshed = self.client.orchestre.git_tree.git.refresh_private(self.client.git_runner)
        self.client._log_tree_transition(
            previous_state, registry.lifecycle_state, reason="pull --private"
        )
        self.client._log_event("refresh_private_end", refreshed=len(refreshed))
        return refreshed

    def merge_plan(
        self,
        project_branch: str,
        *,
        private: bool = False,
        all_writable: bool = False,
    ) -> tuple[tuple[str, str, str, tuple[Path, ...]], ...]:
        """What :meth:`merge` would do, in order, without doing it.

        One ``(repo_name, source_ref, status, conflicting_paths)`` row per
        in-scope repository, leaf-first. ``source_ref`` is the branch that
        repository would actually merge, which for a private/local repository
        is derived from *project_branch* rather than equal to it — seeing that
        translation before it runs is the point of a merge dry run.

        ``status`` is ``"merge"``, ``"already-on-it"``, ``"no-branch"`` or
        ``"conflicts"``, decided by the same function :meth:`merge` uses, so a
        dry run cannot promise something the merge then refuses.
        """
        from ..operations import merge_status

        registry = self.client.get_dependency_registry()
        scope = self.client._write_scope(registry, "merge", private, all_writable)
        project_name = tree_project_name(registry)
        return tuple(
            (
                repo.name,
                *merge_status(
                    repo, self.client.git_runner, project_branch, project_name=project_name
                ),
            )
            for repo in iter_tree_leaf_first(registry, scope)
        )

    def add(
        self,
        paths: Sequence[str | Path] | None = None,
        *,
        private: bool = False,
        all_writable: bool = False,
    ) -> WorkingGitTree:
        """Stage changes across the full tree, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  After a
        successful execution the registry remains ``READY``.

        With *paths* omitted (the default), every repo is staged in full —
        today's exact behaviour. With *paths* given, each one is resolved to
        its owning repo (see :func:`~.git_tree.resolve_repo_for_path`) and
        staged there individually, leaving every other repo untouched.
        """
        registry = self.client.get_dependency_registry()
        previous_state = registry.lifecycle_state
        scope = self.client._write_scope(registry, "add", private, all_writable)
        self.client._log_event(
            "add_start",
            paths=[str(p) for p in paths] if paths else None,
            scope=scope.value,
        )
        self.client.last_write_outcomes = GitProbes.as_write_outcomes(
            self.client.orchestre.git_tree.git.add(self.client.git_runner, paths=paths, scope=scope)
        )
        self.client._log_tree_transition(previous_state, registry.lifecycle_state, reason="add")
        self.client._log_event("add_end", staged=sum(1 for o in self.client.last_write_outcomes if o.acted))
        return registry

    def removals_outside_scope(
        self, paths: Sequence[str | Path], *, private: bool = False
    ) -> tuple[str, ...]:
        """Why :meth:`remove` would refuse these paths, without removing any.

        One finished sentence per path whose owning repository falls outside
        the scope ``private`` selects; empty when the removal would go ahead.
        Read-only, so ``rm --dry-run`` can ask the same question the real
        run answers and never print a plan that could not execute.
        """
        registry = self.client.get_dependency_registry()
        scope = GitProbes.scope_for(registry, private=private, command="rm", default=RepoScope.ALL)
        return paths_outside_scope(registry, paths, scope=scope)

    def remove(
        self, paths: Sequence[str | Path], *, private: bool = False
    ) -> WorkingGitTree:
        """Remove one or more tracked files, each from the repo that owns it.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise. Each path
        is resolved to its owning repo (see
        :func:`~.git_tree.resolve_repo_for_path`), removed from disk there,
        and the removal staged — a plain ``git rm``, distinct from
        :meth:`GitRunner.rm_cached` (index-only, built for the
        submodule-to-plain-clone conversion; this does not replace it).

        ``private`` narrows the removal to the writable configuration
        repositories, and is a **filter** here rather than a sweep: this
        command is handed its paths instead of finding them, so the scope
        is checked against the repository each path resolves to, and a path
        owned by a repository outside it is refused by name before anything
        is removed. Without it the reach is every repository, which is what
        this command has always done — see
        ``.agent/.local/.localSpec/DevTickets/archive/20260912_DeadScopeFlags_DevPlanTicket.md`` §2.1.

        Each repository actually removed from is reported in
        :attr:`last_write_outcomes`.
        """
        registry = self.client.get_dependency_registry()
        previous_state = registry.lifecycle_state
        scope = GitProbes.scope_for(registry, private=private, command="rm", default=RepoScope.ALL)
        self.client._log_event("rm_start", paths=[str(p) for p in paths], scope=scope.value)
        self.client.last_write_outcomes = GitProbes.as_write_outcomes(
            self.client.orchestre.git_tree.git.rm(self.client.git_runner, paths, scope=scope)
        )
        self.client._log_tree_transition(previous_state, registry.lifecycle_state, reason="rm")
        self.client._log_event("rm_end")
        return registry

    def push(
        self,
        *,
        force_access_protocol: str | None = None,
        private: bool = False,
        all_writable: bool = False,
    ) -> WorkingGitTree:
        """Push all repos to their remotes, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  After a
        successful execution the registry remains ``READY`` and refreshes the
        stored commit hashes in the runtime tree state.

        ``force_access_protocol`` (``"ssh"`` or ``"https"``,
        ``--force-protocol``), when given, rewrites each repo's remote to
        that protocol before pushing, persisting the change (``git remote
        set-url``) rather than a one-off override — see
        ``.agent/.local/.localSpec/DevTickets/archive/20260903_ProtocolSwitchOnPush_DevPlanTicket.md``. On a failure
        that looks like an auth problem, the error gains an actionable
        hint naming ``--force-protocol <the other one>``.
        """
        registry = self.client.get_dependency_registry()
        previous_state = registry.lifecycle_state
        scope = self.client._write_scope(registry, "push", private, all_writable)
        self.client._log_event("push_start", scope=scope.value)
        self.client._memory_commands._fold_memory_before_push()
        protocol = AccessProtocol(force_access_protocol) if force_access_protocol else None
        try:
            self.client.last_write_outcomes = GitProbes.as_write_outcomes(
                self.client.orchestre.git_tree.git.push(
                    self.client.git_runner, force_access_protocol=protocol, scope=scope
                )
            )
        except GitSyncError as exc:
            hint = AuthFailureHints.protocol_switch_hint(str(exc), command="push")
            if hint:
                raise GitSyncError(f"{exc}\n{hint}") from exc
            raise
        snapshot_path = self.client.write_gts_snapshot(
            command_origin="push",
            publications=self.client._memory_commands._collect_publication_records(registry, scope),
        )
        if self.client.source_path is not None:
            self.client.state_store.record_snapshot(self.client.source_path, snapshot_path)
        self.client._log_tree_transition(previous_state, registry.lifecycle_state, reason="push")
        self.client._log_event("push_end", pushed=sum(1 for o in self.client.last_write_outcomes if o.acted))
        return registry

    def tag(self, tag_name: str, *, private: bool = False) -> WorkingGitTree:
        """Create and push *tag_name* across the full tree, leaf-first.

        The runtime tree state is refreshed so the recorded tag target remains
        aligned with the synchronized repositories.
        """
        registry = self.client.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self.client._log_event("tag_start", tag_name=tag_name)
        self.client._memory_commands._fold_memory_before_push()
        scope = (
            RepoScope.PRIVATE
            if private
            else GitProbes.scope_for(registry, private=False, command="tag", default=RepoScope.WRITABLE)
        )
        self.client.orchestre.git_tree.git.tag(self.client.git_runner, tag_name, scope=scope)
        self.client._log_tree_transition(previous_state, registry.lifecycle_state, reason="tag")
        self.client._log_event("tag_end", tag_name=tag_name)
        return registry

    def git(
        self,
        gittree: WorkingGitTree | None,
        command: str,
        *args: str,
    ) -> WorkingGitTree:
        """Dispatch a git command across the full tree (lifecycle step 5).

        This is the unified git interface.  It dispatches *command* to the
        appropriate tree-wide operation and returns the updated registry.
        Ordering is command-specific (for example, ``pull``/``branch``/``checkout``
        run parent-first while ``push`` runs leaf-first).

        Parameters
        ----------
        gittree:
            The :class:`~.git_tree.WorkingGitTree` to operate on.
            Pass ``None`` to use the currently loaded registry.  Passing a
            registry replaces the active registry for the duration of the call.
        command:
            One of ``"pull"``, ``"checkout"``, ``"branch"``, ``"add"``,
            ``"commit"``, ``"push"``, ``"tag"``, or ``"freeze"``.
        *args:
            Command-specific positional arguments:

            - ``"pull"``: one argument — path to ``.cgs`` or ``.gts`` source.
            - ``"checkout"``: one argument — branch/tag name to switch to.
            - ``"branch"``: one argument — branch name to create (no checkout).
            - ``"add"``: no arguments.  Stages all changes tree-wide.
            - ``"commit"``: one argument — the commit message.  The message
              conventionally ends with ``CGS#VERSION``.
            - ``"push"``: no arguments.  Updates the stored hash in the
              ``GitTree`` for each repository.
            - ``"tag"``: one argument — the tag name.  Updates the stored tag
              in the ``GitTree`` for each repository.
            - ``"freeze"``: one argument — state/release tag name.

        Examples
        --------
        ::

            client.git(registry, "commit", "release: v1.0 CGS#1")
            client.git(registry, "push")
            client.git(registry, "tag", "v1.0")
        """
        if isinstance(gittree, WorkingGitTree):
            self.client.registry = gittree
            self.client.orchestre.git_tree.git.bind_tree(gittree)
        command = command.lower()

        def _required_arg(index: int, label: str) -> str:
            if len(args) <= index or not args[index]:
                raise ValueError(f"{command} requires {label} argument.")
            return args[index]

        if command == "pull":
            source = _required_arg(0, "source path")
            return self.client.pull(source)
        if command == "checkout":
            branch_name = _required_arg(0, "branch name")
            return self.client.checkout(branch_name)
        if command == "branch":
            branch_name = _required_arg(0, "branch name")
            return self.client.branch(branch_name)
        if command == "add":
            return self.client.add()
        if command == "commit":
            message = _required_arg(0, "message")
            return self.client.commit(message)
        if command == "push":
            return self.client.push()
        if command == "tag":
            tag_name = _required_arg(0, "tag name")
            return self.client.tag(tag_name)
        if command == "freeze":
            name = _required_arg(0, "tag name")
            return self.client.freeze(name)
        raise ValueError(
            f"Unknown git command '{command}'. Supported commands: 'pull', 'checkout', "
            "'branch', 'add', 'commit', 'push', 'tag', 'freeze'."
        )


__all__ = ["TreeCommands"]

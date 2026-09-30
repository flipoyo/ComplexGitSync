"""installer — Install, initialise, clone and bootstrap a tree, and work out where it lives.

Ring: 3
Contract: Install, initialise, clone and bootstrap a tree, and work out where it lives.
Imports: auth_hints, cgs_format, client, errors, git_repo, git_tree, master, paths, registry, snapshot_resolver
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..cgs_format import CgsDocument
from ..errors import (
    GitSyncError,
)

if TYPE_CHECKING:
    pass
from ..git_repo import (
    AccessProtocol,
)
from ..git_tree import (
    ROOT_REPO_ID,
    TreeLifecycleState,
    WorkingGitTree,
)
from ..master import MasterConfig
from ..paths import PathResolver
from ..registry import (
    RegistryTranslator,
)
from ..snapshot_resolver import discover_cgshome
from .auth_hints import AuthFailureHints

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient


class Installer:
    """Install, initialise, clone and bootstrap a tree, and work out where it lives.

    A collaborator of :class:`~ComplexGitSync.orchestre.client.ComplexGitSyncClient`,
    which keeps the state and exposes every method here unchanged. Everything
    that is not this class's own private helper is reached through the
    client, so a caller that patches a client method is still obeyed.
    """

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def configure(
        self,
        project: str | dict[str, Any],
        repositories: Sequence[str | dict[str, Any]],
        *,
        output_path: str | Path | None = None,
    ) -> CgsDocument:
        """Create a canonical ``.cgs`` document without interactive input.

        This public Python facade accepts the same authoring values collected
        by the CLI. Parsing, default normalization, and static validation are
        delegated to :class:`CgsDocument`; optional serialization is delegated
        to its ``to_toml()`` method. No Git or network operation is performed.

        Parameters
        ----------
        project:
            A project-name string or an authoring project table.
        repositories:
            Repository identifiers or advanced authoring tables.
        output_path:
            Optional destination for concise ``.cgs`` TOML. When omitted, the
            validated document is returned without writing a file.

        A lone repository is always the root — `_is_root_repo_spec`'s own
        rule (DiscoverRoundTrip F2/D2): with nothing else in the tree,
        there is no other repository it could be, whatever its own
        identifier's name happens to be.
        """
        document = CgsDocument.from_dict(
            {
                "project": project,
                "repos": list(repositories),
            }
        )
        if output_path is not None:
            document.to_toml(Path(output_path))
        return document

    def initialise(
        self,
        source: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> WorkingGitTree:
        """Unified initialisation entry point (lifecycle step 1).

        Dispatches based on source file extension:

        - ``.cgs`` source: initialises the workspace using CGSPATH/CGSHOME
          semantics (calls :meth:`initialise_cgs`).  The output path is
          CGSPATH, and CGSHOME is derived as ``CGSPATH/<project_name>`` after
          reading the ``.cgs``.  The root repository at CGSHOME is treated as
          already existing and is never recloned; **every dependency below it
          is deleted and cloned again**, which is not the same promise. See
          :meth:`initialise_cgs`.  All ComplexGitSync state is
          written under ``CGSHOME/.cgitsync/state(<hash>)_n/``.
        - ``.gts`` source: restores from a saved snapshot (calls
          :meth:`load_gts`).  Use this for existing projects that already have
          a ``.gts`` state file.

        Both paths end in a ``READY`` tree or raise explicitly.

        Parameters
        ----------
        source:
            Path to a ``.cgs`` authoring spec (clone mode) or a ``.gts``
            snapshot (restore mode).
        output_path:
            CGSPATH — parent directory used to derive CGSHOME as
            ``CGSPATH/<project_name>`` after the ``.cgs`` is read.  Defaults to
            ``../..`` relative to the current working directory
            (``CWD=$CGSHOME/ComplexGitSync``).
        """
        resolved = Path(source).resolve()
        if resolved.suffix == ".cgs":
            return self.client.initialise_cgs(resolved, output_path=output_path)
        if resolved.suffix == ".gts":
            return self.client.load_gts(resolved)
        raise ValueError(
            f"Unsupported source format '{resolved.suffix}' for {resolved!s}; expected .cgs or .gts."
        )

    def initialise_cgs(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
        clean_before_clone: bool = False,
        force_reclone: bool = False,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Initialise a workspace using CGSPATH/CGSHOME semantics.

        ``output_path`` is CGSPATH.  The ``.cgs`` file is read first, CGSHOME
        is derived as ``CGSPATH/<project_name>``, and that root repository is
        treated as already existing.  The clone sequence runs only for the
        dependencies declared in the ``.cgs`` document.

        **Dependencies are re-cloned, not adopted.** Only the root survives a
        second run: every dependency whose destination already holds files is
        deleted and cloned again. Before deleting anything, this checks each
        destination and refuses the whole run -- naming every repository, and
        deleting none -- when one holds work that exists nowhere else:
        uncommitted changes, commits not pushed to its upstream, or a branch
        with no upstream at all. A destination that is not a Git checkout (a
        clone interrupted mid-run) is still cleared without a flag.
        *force_reclone* (``--force-reclone``) skips that check and destroys
        the work.

        All ComplexGitSync state is stored under
        ``CGSHOME/.cgitsync/state(<hash>)_n/``.

        Parameters
        ----------
        config_path:
            Path to the ``.cgs`` authoring spec.
        force_reclone:
            Skip the unpushed-work check described above and clear every
            populated destination, reproducing the pre-guard behaviour.
            Destructive and unrecoverable: the old ``.git`` goes with the
            directory. ``clean_before_clone`` implies it, since ``clean-init``
            purges the workspace itself.
        output_path:
            CGSPATH — parent directory used to derive CGSHOME as
            ``CGSPATH/<project_name>``.  When *None*, defaults to ``../..``
            relative to the current working directory
            (``CWD=$CGSHOME/ComplexGitSync``), unless ``CGSHOME`` is set.
        commit_gitignore:
            Explicit approval (``--commit-gitignore``) to stage, commit, and
            push any ``.gitignore`` the lifecycle sync updates. Default
            ``False``: the sync only writes the file and reports it.
        force_gitignore_sync:
            Opt-in (``--force-gitignore-sync``) fallback to pull-force
            semantics for a repo whose safe pull fails before its
            ``.gitignore`` is synced, instead of raising. Never force-pushes.
        git_user_name, git_user_email:
            Override the Git identity used for ComplexGitSync-authored
            commits (``--git-user-name``/``--git-user-email``). Persisted to
            ``CGSHOME/.cgitsync/master.toml`` via :class:`~.master.MasterConfig`
            so later invocations on this workspace pick it up without
            repeating the flag. ``None`` (the default) leaves whatever is
            already configured/persisted, or local git config, untouched.
        force_access_protocol:
            ``"ssh"`` or ``"https"`` (``--force-protocol``). Overrides every
            cloned repo's ``access_protocol`` in memory only — nothing on
            disk is read or written differently. Applies to every entry the
            clone loop touches, including ones discovered later from a
            nested ``.cgs`` in a different, separately-cloned repo. ``None``
            (the default) leaves each entry's own ``.cgs``-declared protocol
            untouched, exactly as today.
        """
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        return self.client.initialise_cgs_document(
            document,
            source_path=source_path,
            output_path=output_path,
            clean_before_clone=clean_before_clone,
            force_reclone=force_reclone,
            commit_gitignore=commit_gitignore,
            force_gitignore_sync=force_gitignore_sync,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
            force_access_protocol=force_access_protocol,
        )

    def initialise_cgs_document(
        self,
        document: CgsDocument,
        *,
        source_path: str | Path,
        output_path: str | Path | None = None,
        clean_before_clone: bool = False,
        force_reclone: bool = False,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Initialise from an already-normalized, validated ``CgsDocument``.

        ``source_path`` is the logical origin used for relative paths, state
        metadata, and logging. It need not exist for direct CLI authoring.
        See :meth:`initialise_cgs` for ``commit_gitignore``/
        ``force_gitignore_sync``/``git_user_name``/``git_user_email``/
        ``force_access_protocol``.
        """
        document.validate()
        previous_tree_state = (
            self.client.registry.lifecycle_state if self.client.registry else TreeLifecycleState.UNLOADED
        )
        source_path = Path(source_path).resolve()
        cgshome = self.client.resolve_cgshome(document, source_path, output_path=output_path)
        MasterConfig.load(cgshome)
        if git_user_name is not None or git_user_email is not None:
            MasterConfig.persist(cgshome, user_name=git_user_name, user_email=git_user_email)
        self.client._forced_access_protocol = (
            AccessProtocol(force_access_protocol) if force_access_protocol else None
        )
        # clean-init purges the workspace itself, so its destinations are
        # already gone by the time the guard would look: it means
        # --force-reclone and says so in its own name.
        self.client._force_reclone = force_reclone or clean_before_clone
        project_root = cgshome

        self.client.registry = RegistryTranslator.from_cgs_document(document, source_path, project_root=project_root)
        self.client.orchestre.git_tree.git.bind_tree(self.client.registry)
        self.client.source_path = source_path

        root_entry = self.client.registry.get(ROOT_REPO_ID)
        self.client._attach_existing_root(root_entry, project_root)

        if clean_before_clone:
            self.client._purge_registry_workspace(self.client.registry)

        # Root is already checked out at CGSHOME; initialise clones only the
        # dependencies declared by the .cgs.
        sync_stack: set[Path] = {project_root}

        while True:
            cloned_any = False
            pending = self.client._pending_clone_entries(sync_stack)
            self.client._guard_clone_destinations(pending)
            for entry in pending:
                sync_stack.add(entry.absolute_path)
                self.client._clone_registry_entry(entry)
                cloned_any = True

            discovered = self.client.discover_nested_configs()
            self.client._log_nested_discovery(discovered)
            if not cloned_any and not discovered:
                break

        fixed = self.client.fix_circularities()
        if fixed:
            self.client._log_circularity_fixes(fixed)
        self.client._assert_nested_discovery_complete()
        self.client._gitignore_sync._sync_gitignore_lifecycle(
            force_pull_fallback=force_gitignore_sync,
            commit=commit_gitignore,
        )
        self.client.registry.recompute_tree_state()
        if not self.client.registry.is_ready():
            raise GitSyncError("Initialise did not produce a READY tree.")

        # Write the snapshot under CGSHOME.
        snapshot_name = f"{self.client.source_path.stem if self.client.source_path else root_entry.name}.gts"
        snapshot_output = cgshome / ".cgitsync" / "state" / snapshot_name
        snapshot_path = self.client.write_gts_snapshot(
            command_origin="clone", output_path=snapshot_output
        )
        self.client.state_store.record_snapshot(source_path, snapshot_path)
        self.client._log_tree_transition(
            previous_tree_state, self.client.registry.lifecycle_state, reason="initialise_cgs"
        )
        self.client._warn_environment_drift()
        return self.client.registry

    def clean_initialise_cgs(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Initialise a .cgs workspace after purging generated clone state."""
        return self.client.initialise_cgs(
            config_path,
            output_path=output_path,
            clean_before_clone=True,
            commit_gitignore=commit_gitignore,
            force_gitignore_sync=force_gitignore_sync,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
            force_access_protocol=force_access_protocol,
        )

    def clean_init(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Initialise a .cgs workspace after purging generated clone state."""
        return self.client.clean_initialise_cgs(
            config_path,
            output_path=output_path,
            commit_gitignore=commit_gitignore,
            force_gitignore_sync=force_gitignore_sync,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
            force_access_protocol=force_access_protocol,
        )

    def purge_cgs(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> tuple[Path, ...]:
        """Remove immediate child repos and project ledgers from CGSHOME."""
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        cgshome = self.client.resolve_cgshome(document, source_path, output_path=output_path)
        self.client.registry = RegistryTranslator.from_cgs_document(document, source_path, project_root=cgshome)
        self.client.orchestre.git_tree.git.bind_tree(self.client.registry)
        self.client.source_path = source_path
        return self.client._purge_registry_workspace(self.client.registry)

    def purge(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> tuple[Path, ...]:
        """Remove generated clone state for a .cgs workspace."""
        return self.client.purge_cgs(config_path, output_path=output_path)

    def resolve_cgshome(
        self,
        document: CgsDocument,
        source_path: Path,
        *,
        output_path: str | Path | None = None,
    ) -> Path:
        """Resolve CGSHOME from CGSPATH, the environment, or CWD."""
        return PathResolver.resolve_cgshome(document, source_path, output_path=output_path)

    def resolve_initialise_cgshome(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
    ) -> Path:
        """Read a .cgs file and resolve the CGSHOME initialise will use."""
        return PathResolver.resolve_initialise_cgshome(config_path, output_path=output_path)

    def resolve_clone_root(
        self,
        config_path: str | Path,
        *,
        target_dir: str | Path | None = None,
        output_path: str | Path | None = None,
    ) -> Path:
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        return PathResolver.resolve_project_root(document, source_path, target_dir, output_path)

    def clone_cgs(
        self,
        config_path: str | Path,
        *,
        force_reclone: bool = False,
        target_dir: str | Path | None = None,
        output_path: str | Path | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        previous_tree_state = self.client.registry.lifecycle_state if self.client.registry else TreeLifecycleState.UNLOADED
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        project_root = PathResolver.resolve_project_root(document, source_path, target_dir, output_path)
        self.client._forced_access_protocol = (
            AccessProtocol(force_access_protocol) if force_access_protocol else None
        )
        self.client._force_reclone = force_reclone

        self.client.registry = RegistryTranslator.from_cgs_document(document, source_path, project_root=project_root)
        self.client.orchestre.git_tree.git.bind_tree(self.client.registry)
        self.client.source_path = source_path

        # Sync stack: tracks absolute paths that have already entered the clone
        # pipeline.  If a repository's path appears in the stack, any subsequent
        # reference to it (created by nested-config discovery during the same
        # run) is treated as a mount point and skipped rather than cloned again.
        # This provides defence-in-depth against infinite-recursion edge cases
        # that may arise before fix_circularities() has had a chance to clean up
        # the registry.
        sync_stack: set[Path] = set()

        while True:
            cloned_any = False
            pending = self.client._pending_clone_entries(sync_stack)
            self.client._guard_clone_destinations(pending)
            for entry in pending:
                sync_stack.add(entry.absolute_path)
                self.client._clone_registry_entry(entry)
                cloned_any = True

            discovered = self.client.discover_nested_configs()
            self.client._log_nested_discovery(discovered)
            if not cloned_any and not discovered:
                break

        fixed = self.client.fix_circularities()
        if fixed:
            self.client._log_circularity_fixes(fixed)
        self.client._assert_nested_discovery_complete()
        # Every repo was just freshly cloned, so a safe-pull preflight (as
        # initialise_cgs_document runs before its own sync) can only be a
        # no-op fast-forward here -- skip it. See BootstrapGitignoreSync
        # DevPlanTicket: without this call, bootstrap/clone left every
        # parent-bearing repo's .gitignore missing its immediate children,
        # so plain `git status` saw each child as an embedded repository
        # (gitlink-shaped) instead of the plain independent clone it is.
        self.client._gitignore_sync._sync_gitignore_lifecycle(pre_pull=False, commit=False)
        self.client.registry.recompute_tree_state()
        if not self.client.registry.is_ready():
            raise GitSyncError("Clone did not produce a READY tree.")
        snapshot_path = self.client.write_gts_snapshot(command_origin="clone")
        self.client.state_store.record_snapshot(source_path, snapshot_path)
        self.client._log_tree_transition(previous_tree_state, self.client.registry.lifecycle_state, reason="clone_cgs")
        return self.client.registry

    def clone(
        self,
        config_path: str | Path,
        *,
        target_dir: str | Path | None = None,
        output_path: str | Path | None = None,
    ) -> WorkingGitTree:
        """Clone a project tree from a ``.cgs`` source."""
        return self.client.clone_cgs(config_path, target_dir=target_dir, output_path=output_path)

    def resolve_bootstrap_root(
        self,
        project_name: str,
        *,
        cgs_path: str | Path | None = None,
    ) -> Path:
        """Resolve the isolated CGSHOME a :meth:`bootstrap` run will clone into.

        ``project_name`` always forms the final path segment, regardless of
        the ``.cgs`` document's own ``project_name`` field, so the
        destination is explicit rather than inferred. When *cgs_path* is
        omitted, it defaults to a fresh ``$HOME/.cgs/CGS<timestamp>/``
        directory (``$HOME/.cgs`` is created if missing) so a bootstrapped
        project never lands inside the ComplexGitSync clone itself — running
        ComplexGitSync standalone must never mix its own repo with the
        project state it manages.
        """
        return PathResolver.resolve_bootstrap_root(project_name, cgs_path=cgs_path)

    def bootstrap(
        self,
        config_path: str | Path,
        project_name: str,
        *,
        cgs_path: str | Path | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Bootstrap a brand-new workspace tree from a standalone ComplexGitSync clone.

        Unlike :meth:`initialise_cgs` (which assumes CGSHOME already exists,
        with ComplexGitSync itself cloned inside it), this clones the full
        tree — including the root — from scratch, so ComplexGitSync can be
        run from its own clone (e.g. installed once, used across many
        projects) without ever writing project state into it. See
        :meth:`resolve_bootstrap_root` for how the destination is derived
        from *project_name* and *cgs_path*.

        Parameters
        ----------
        config_path:
            Path to the ``.cgs`` authoring spec.
        project_name:
            Required name for the workspace; forms the last path segment of
            CGSHOME regardless of the ``.cgs`` document's own project name.
        cgs_path:
            CGSPATH override. When *None*, defaults to a fresh
            ``$HOME/.cgs/CGS<timestamp>/`` directory.
        force_access_protocol:
            ``"ssh"`` or ``"https"`` (``--force-protocol``). See
            :meth:`initialise_cgs` for the full description — applies here
            identically, including to the root repo this command (unlike
            ``initialise``) also clones from scratch.
        """
        source_path = Path(config_path).resolve()
        if source_path.suffix != ".cgs":
            raise ValueError(
                f"bootstrap requires a .cgs source, got '{source_path.suffix}' for {source_path!s}."
            )
        target_dir = self.client.resolve_bootstrap_root(project_name, cgs_path=cgs_path)
        return self.client.clone_cgs(
            source_path, target_dir=target_dir, force_access_protocol=force_access_protocol
        )

    def restart(
        self,
        config_path: str | Path,
        *,
        commit_gitignore: bool = False,
        force_gitignore_sync: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Resynchronize an already-cloned tree from a ``.cgs`` file.

        Loads the ``.cgs`` configuration, discovers nested configs, then
        checks out the root repository's current branch across the whole tree
        parent-first.  Ends in ``READY`` or raises
        :exc:`~ComplexGitSync.errors.GitSyncError`. See
        :meth:`ComplexGitSyncClient.initialise_cgs` for
        ``commit_gitignore``/``force_gitignore_sync``/``git_user_name``/
        ``git_user_email``, and :meth:`push` for ``force_access_protocol``.
        """
        previous_tree_state = self.client.registry.lifecycle_state if self.client.registry else TreeLifecycleState.UNLOADED
        resolved_path = Path(config_path).resolve()
        self.client._log_event("restart_start", config_path=resolved_path)
        # The tree this re-syncs is already on disk somewhere; find that
        # somewhere by the same walk every other command uses (cwd,
        # $CGSHOME, the default workspace), never by guessing at the .cgs
        # file's own directory. A developer spec that sits under examples/
        # — this project's own — describes a tree rooted well above it.
        try:
            established_root: Path | None = discover_cgshome()
        except FileNotFoundError:
            established_root = None
        restart_cgshome = (
            established_root
            if established_root is not None
            else self.client.resolve_initialise_cgshome(resolved_path)
        )
        MasterConfig.load(restart_cgshome)
        if git_user_name is not None or git_user_email is not None:
            MasterConfig.persist(restart_cgshome, user_name=git_user_name, user_email=git_user_email)
        registry = self.client.load_cgs(
            resolved_path, discover_nested=True, project_root=established_root
        )
        protocol = AccessProtocol(force_access_protocol) if force_access_protocol else None
        try:
            self.client.orchestre.git_tree.git.pull(self.client.git_runner, force_access_protocol=protocol)
        except GitSyncError as exc:
            hint = AuthFailureHints.protocol_switch_hint(str(exc), command="pull")
            if hint:
                raise GitSyncError(f"{exc}\n{hint}") from exc
            raise
        self.client._gitignore_sync._sync_gitignore_lifecycle(
            pre_pull=False,
            force_pull_fallback=force_gitignore_sync,
            commit=commit_gitignore,
        )
        if not registry.is_ready():
            raise GitSyncError("restart did not produce a READY tree.")
        snapshot_path = self.client.write_gts_snapshot(command_origin="restart")
        self.client.state_store.record_snapshot(resolved_path, snapshot_path)
        self.client._log_tree_transition(previous_tree_state, registry.lifecycle_state, reason="restart")
        self.client._log_event("restart_end", config_path=resolved_path)
        self.client._warn_environment_drift()
        return registry


__all__ = ["Installer"]

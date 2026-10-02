"""installer — Install, initialise, clone and bootstrap a tree, and work out where it lives.

Ring: 3
Contract: Install, initialise, clone and bootstrap a tree, and work out where it lives.
Imports: auth_hints, cgs_format, client, errors, git_repo, git_tree, gts_document, master, paths, registry, settings, snapshot_resolver
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..cgs_format import CgsDocument
from ..errors import (
    GitSyncError,
    InstallFrontierError,
)

if TYPE_CHECKING:
    pass
from ..git_repo import (
    AccessProtocol,
    RefKind,
    RepoLifecycleState,
    SyncState,
    WorkingRepo,
)
from ..git_tree import (
    ROOT_REPO_ID,
    TreeLifecycleState,
    WorkingGitTree,
)
from ..gts_document import GtsDocument
from ..master import MasterConfig
from ..paths import PathResolver
from ..registry import (
    RegistryTranslator,
)
from ..settings import Settings, UseCase
from ..snapshot_resolver import discover_cgshome
from .auth_hints import AuthFailureHints

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient


@dataclass(frozen=True, slots=True)
class _Pin:
    """What a ``.gts`` recorded about one repository, kept while it is cloned.

    A snapshot describes a tree *as it was*: the commit each repository sat
    on, and the branch it sat on it under. Cloning has to reproduce that, so
    the clone is aimed at the recorded branch and the entry is put back to
    what the snapshot said once the clone is done — the State name is
    computed from those fields, and a rebuilt tree must carry the same one.
    """

    commit: str
    current_kind: RefKind | None
    current_name: str | None
    resolved_kind: RefKind | None
    resolved_name: str | None
    target_kind: RefKind | None
    target_name: str | None
    fallback_applied: bool
    fallback_reason: str | None


class Installer:
    """Install, initialise, clone and bootstrap a tree, and work out where it lives.

    A collaborator of :class:`~ComplexGitSync.orchestre.client.ComplexGitSyncClient`,
    which keeps the state and exposes every method here unchanged. Everything
    that is not this class's own private helper is reached through the
    client, so a caller that patches a client method is still obeyed.
    """

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client
        #: Where the running ComplexGitSync sits; ``None`` reads it from the
        #: package's own location. Injected by a test that has to stand an
        #: installation inside a workspace — never a flag a user can pass.
        self.installation: Path | None = None

    def _use_case_of(self, cgshome: Path) -> UseCase:
        """Whether the running installation is nested in *cgshome* or standalone to it."""
        return Settings.resolve_use_case(cgshome, installation=self.installation)

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
        """The nested install (lifecycle step 1): build a workspace around a checked-out root.

        ``initialise`` is the nested install and nothing else — ``bootstrap``
        is the standalone one (``AdditionalSpecs.md``, *The install
        frontier*). It builds a workspace whose root repository is already
        checked out at CGSHOME, with the running ComplexGitSync inside it, and
        it refuses — before touching the disk, naming ``bootstrap`` — when
        CGSHOME is not a Git checkout or the running installation is not inside
        it.

        Dispatches on the source's extension:

        - ``.cgs`` source: clones the workspace's dependencies at the branch
          the ``.cgs``'s fallback chain names (calls :meth:`initialise_cgs`).
          The output path is CGSPATH, and CGSHOME is derived as
          ``CGSPATH/<project_name>`` after reading the ``.cgs``.  The root
          repository at CGSHOME is never recloned; **every dependency below it
          is deleted and cloned again**, which is not the same promise. See
          :meth:`initialise_cgs`.  All ComplexGitSync state is
          written under ``CGSHOME/.cgitsync/state(<hash>)_n/``.
        - ``.gts`` source: clones the same dependencies and checks each out at
          the commit the snapshot recorded — the tree as it was, not as a
          ``.cgs`` would rebuild it today (calls :meth:`initialise_gts`).

        Both paths end in a ``READY`` tree or raise explicitly.

        Parameters
        ----------
        source:
            Path to a ``.cgs`` authoring spec or a ``.gts`` snapshot.
        output_path:
            CGSPATH — parent directory used to derive CGSHOME as
            ``CGSPATH/<project_name>`` after the source is read.  Defaults to
            ``../..`` relative to the current working directory
            (``CWD=$CGSHOME/ComplexGitSync``).
        """
        resolved = Path(source).resolve()
        if resolved.suffix == ".cgs":
            return self.client.initialise_cgs(resolved, output_path=output_path)
        if resolved.suffix == ".gts":
            return self.client.initialise_gts(resolved, output_path=output_path)
        raise ValueError(
            f"Unsupported source format '{resolved.suffix}' for {resolved!s}; expected .cgs or .gts."
        )

    def initialise_cgs(
        self,
        config_path: str | Path,
        *,
        output_path: str | Path | None = None,
        commit_gitignore: bool = False,
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
        with no upstream at all. There is no flag that skips the check
        (ComplexGitSync rewrites nothing); commit and push, or move the
        directory aside. A destination that is not a Git checkout (a clone
        interrupted mid-run) is still cleared.

        All ComplexGitSync state is stored under
        ``CGSHOME/.cgitsync/state(<hash>)_n/``.

        Parameters
        ----------
        config_path:
            Path to the ``.cgs`` authoring spec.
        output_path:
            CGSPATH — parent directory used to derive CGSHOME as
            ``CGSPATH/<project_name>``.  When *None*, defaults to ``../..``
            relative to the current working directory
            (``CWD=$CGSHOME/ComplexGitSync``), unless ``CGSHOME`` is set.
        commit_gitignore:
            Explicit approval (``--commit-gitignore``) to stage, commit, and
            push any ``.gitignore`` the lifecycle sync updates. Default
            ``False``: the sync only writes the file and reports it.
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
            commit_gitignore=commit_gitignore,
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
        commit_gitignore: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Initialise from an already-normalized, validated ``CgsDocument``.

        ``source_path`` is the logical origin used for relative paths, state
        metadata, and logging. It need not exist for direct CLI authoring.
        See :meth:`initialise_cgs` for ``commit_gitignore``/
        ``git_user_name``/``git_user_email``/
        ``force_access_protocol``.
        """
        document.validate()
        previous_tree_state = (
            self.client.registry.lifecycle_state if self.client.registry else TreeLifecycleState.UNLOADED
        )
        source_path = Path(source_path).resolve()
        cgshome = self.client.resolve_cgshome(document, source_path, output_path=output_path)
        self._require_nested_install(cgshome)
        MasterConfig.load(cgshome)
        if git_user_name is not None or git_user_email is not None:
            MasterConfig.persist(cgshome, user_name=git_user_name, user_email=git_user_email)
        self.client._forced_access_protocol = (
            AccessProtocol(force_access_protocol) if force_access_protocol else None
        )
        project_root = cgshome

        self.client.registry = RegistryTranslator.from_cgs_document(document, source_path, project_root=project_root)
        self.client.orchestre.git_tree.git.bind_tree(self.client.registry)
        self.client.source_path = source_path

        root_entry = self.client.registry.get(ROOT_REPO_ID)
        self.client._attach_existing_root(root_entry, project_root)

        # Root is already checked out at CGSHOME; initialise clones only the
        # dependencies declared by the .cgs.
        self._clone_pending(sync_stack={project_root})

        fixed = self.client.fix_circularities()
        if fixed:
            self.client._log_circularity_fixes(fixed)
        self.client._assert_nested_discovery_complete()
        self.client._gitignore_sync._sync_gitignore_lifecycle(
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

    def clone_cgs(
        self,
        config_path: str | Path,
        *,
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
        self._clone_pending(sync_stack=set())

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
        if source_path.suffix not in {".cgs", ".gts"}:
            raise ValueError(
                f"bootstrap requires a .cgs or .gts source, got '{source_path.suffix}' for {source_path!s}."
            )
        target_dir = self.client.resolve_bootstrap_root(project_name, cgs_path=cgs_path)
        self._require_fresh_target(target_dir)
        if source_path.suffix == ".gts":
            return self._clone_gts(
                source_path, target_dir=target_dir, force_access_protocol=force_access_protocol
            )
        return self.client.clone_cgs(
            source_path, target_dir=target_dir, force_access_protocol=force_access_protocol
        )

    def initialise_gts(
        self,
        snapshot_path: str | Path,
        *,
        output_path: str | Path | None = None,
        commit_gitignore: bool = False,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """The nested install, from a ``.gts`` snapshot: the tree as it was.

        Same frontier as :meth:`initialise_cgs` — CGSHOME must already be a
        checkout with the running installation inside it, and the root is
        never cloned or moved — but each dependency is checked out at the
        commit the snapshot recorded, not at the tip of the branch a ``.cgs``
        would name today. The snapshot is read with the hash algorithm it
        declares; one newer than this build knows is refused by name before
        anything is computed, and a commit no remote holds any more is
        refused by name, listing every repository, before anything is cloned.

        A dependency is deleted and cloned again exactly as
        :meth:`initialise_cgs` does, under the same unpushed-work guard
        (there is no flag that skips it). The root stays where it is: if it is on a
        different commit than the snapshot recorded, that is logged, not
        changed — it is the user's checkout.

        *commit_gitignore* is accepted so the two sources take one set of
        options; a pinned repository is never pulled, and ``.gitignore`` files
        are written but not committed.
        """
        resolved = Path(snapshot_path).resolve()
        document = GtsDocument.from_toml(resolved)
        cgshome = PathResolver.resolve_initialise_cgshome(resolved, output_path=output_path)
        self._require_nested_install(cgshome)
        MasterConfig.load(cgshome)
        if git_user_name is not None or git_user_email is not None:
            MasterConfig.persist(cgshome, user_name=git_user_name, user_email=git_user_email)
        return self._install_from_snapshot(
            document,
            resolved,
            cgshome,
            root_is_checkout=True,
            force_access_protocol=force_access_protocol,
            reason="initialise_gts",
        )

    def _clone_gts(
        self,
        snapshot_path: Path,
        *,
        target_dir: Path,
        force_access_protocol: str | None,
    ) -> WorkingGitTree:
        """The standalone install, from a ``.gts`` snapshot: root cloned too."""
        document = GtsDocument.from_toml(snapshot_path)
        return self._install_from_snapshot(
            document,
            snapshot_path,
            target_dir,
            root_is_checkout=False,
            force_access_protocol=force_access_protocol,
            reason="clone_gts",
        )

    def _install_from_snapshot(
        self,
        document: GtsDocument,
        snapshot_path: Path,
        cgshome: Path,
        *,
        root_is_checkout: bool,
        force_access_protocol: str | None,
        reason: str,
    ) -> WorkingGitTree:
        """Clone every repository a snapshot records, each at its recorded commit.

        The one ``.gts`` path both install commands share; they differ only in
        *root_is_checkout* — whether the root is already there and left alone
        (nested), or is one more repository to clone (standalone).
        """
        client = self.client
        previous_tree_state = (
            client.registry.lifecycle_state if client.registry else TreeLifecycleState.UNLOADED
        )
        client._forced_access_protocol = (
            AccessProtocol(force_access_protocol) if force_access_protocol else None
        )
        registry = RegistryTranslator.from_gts_document(document, tree_root=cgshome)
        client.registry = registry
        client.orchestre.git_tree.git.bind_tree(registry)
        client.source_path = snapshot_path
        root_entry = registry.get(ROOT_REPO_ID)

        pins = self._pins_for(registry, skip_root=root_is_checkout)
        self._require_commits_held(registry, pins)
        if root_is_checkout:
            recorded_root = root_entry.commit_sha
            client._attach_existing_root(root_entry, cgshome)
            if recorded_root and root_entry.commit_sha != recorded_root:
                client._log_event(
                    "root_commit_differs",
                    recorded=recorded_root,
                    actual=root_entry.commit_sha,
                )
        self._clone_pending(
            sync_stack={cgshome} if root_is_checkout else set(), pins=pins, discover=False
        )

        client._assert_nested_discovery_complete()
        # Never pre-pull: a pinned repository is exactly where the snapshot
        # put it, and a pull would move it.
        client._gitignore_sync._sync_gitignore_lifecycle(pre_pull=False, commit=False)
        registry.recompute_tree_state()
        if not registry.is_ready():
            raise GitSyncError(f"{reason} did not produce a READY tree.")
        written = client.write_gts_snapshot(command_origin="clone")
        client.state_store.record_snapshot(snapshot_path, written)
        client._log_tree_transition(previous_tree_state, registry.lifecycle_state, reason=reason)
        client._warn_environment_drift()
        return registry

    @staticmethod
    def _pins_for(registry: WorkingGitTree, *, skip_root: bool) -> dict[str, _Pin]:
        """Note what the snapshot recorded, then reset every repository to be cloned.

        A snapshot's entries arrive already ``READY``, which would make the
        clone loop skip them. Each is put back to ``DECLARED`` and aimed at the
        branch it was resolved on; :meth:`_apply_pin` restores what was
        recorded once the clone has landed.
        """
        pins: dict[str, _Pin] = {}
        for entry in registry.values():
            if skip_root and entry.repo_id == ROOT_REPO_ID:
                continue
            if entry.commit_sha:
                pins[entry.repo_id] = _Pin(
                    commit=entry.commit_sha,
                    current_kind=entry.current_ref_kind,
                    current_name=entry.current_ref_name,
                    resolved_kind=entry.resolved_ref_kind,
                    resolved_name=entry.resolved_ref_name,
                    target_kind=entry.target_ref_kind,
                    target_name=entry.target_ref_name,
                    fallback_applied=entry.fallback_applied,
                    fallback_reason=entry.fallback_reason,
                )
            if entry.resolved_ref_name:
                entry.target_ref_kind = entry.resolved_ref_kind or RefKind.BRANCH
                entry.target_ref_name = entry.resolved_ref_name
            entry.repo_lifecycle_state = RepoLifecycleState.DECLARED
            entry.sync_state = SyncState.PENDING
            entry.worktree_state = None
            entry.commit_sha = None
        return pins

    def _require_commits_held(self, registry: WorkingGitTree, pins: Mapping[str, _Pin]) -> None:
        """Refuse, before cloning anything, when a recorded commit is gone.

        Falling back to the branch tip would build a tree that carries a
        different State name than the one asked for, so it is refused instead,
        naming every repository at once.
        """
        missing: list[str] = []
        for entry in registry.values():
            pin = pins.get(entry.repo_id)
            if pin is None:
                continue
            remote_url = self.client._build_remote_url(entry)
            if not self.client.git_runner.remote_holds_commit(remote_url, pin.commit):
                missing.append(f"  {entry.name}: {pin.commit} on {remote_url}")
        if missing:
            raise InstallFrontierError(
                "The snapshot records commits its remotes no longer hold, so the tree it "
                "describes cannot be rebuilt. Nothing was cloned. Repositories:\n"
                + "\n".join(missing)
            )

    def _clone_pending(
        self,
        *,
        sync_stack: set[Path],
        pins: Mapping[str, _Pin] | None = None,
        discover: bool = True,
    ) -> None:
        """Clone every declared repository, parents first, until none is left.

        The one clone path all four install entry points share — a ``.cgs`` or
        a ``.gts``, nested or standalone. *sync_stack* holds the paths that
        already entered the pipeline: a later reference to one is a mount
        point, skipped rather than cloned again, which is defence in depth
        against infinite-recursion edge cases before ``fix_circularities()``
        has cleaned the registry. *discover* is false for a snapshot, which
        already names every repository, so there is nothing left to find.
        """
        client = self.client
        while True:
            cloned_any = False
            pending = client._pending_clone_entries(sync_stack)
            client._guard_clone_destinations(pending)
            for entry in pending:
                sync_stack.add(entry.absolute_path)
                client._clone_registry_entry(entry)
                pin = pins.get(entry.repo_id) if pins else None
                if pin is not None:
                    self._apply_pin(entry, pin)
                cloned_any = True

            if not discover:
                if not cloned_any:
                    return
                continue
            discovered = client.discover_nested_configs()
            client._log_nested_discovery(discovered)
            if not cloned_any and not discovered:
                return

    def _apply_pin(self, entry: WorkingRepo, pin: _Pin) -> None:
        """Put a freshly cloned repository on its recorded commit, and back to its record."""
        runner = self.client.git_runner
        if runner.head_commit_sha_or_none(entry.absolute_path) != pin.commit:
            # Stay on the branch the clone landed on, pointed at the recorded
            # commit; a tag has no branch to stay on, so it is detached.
            branch = runner.current_branch(entry.absolute_path)
            try:
                runner.checkout_commit(entry.absolute_path, pin.commit, branch=branch)
            except GitSyncError:
                # A single-branch clone holds one branch's history; the
                # commit may sit on another. Ask the remote for it by name.
                runner.fetch(entry.absolute_path, ref_name=pin.commit)
                runner.checkout_commit(entry.absolute_path, pin.commit, branch=branch)
        entry.commit_sha = pin.commit
        entry.current_ref_kind, entry.current_ref_name = pin.current_kind, pin.current_name
        entry.resolved_ref_kind, entry.resolved_ref_name = pin.resolved_kind, pin.resolved_name
        entry.target_ref_kind, entry.target_ref_name = pin.target_kind, pin.target_name
        entry.fallback_applied = pin.fallback_applied
        entry.fallback_reason = pin.fallback_reason
        entry.repo_lifecycle_state = (
            RepoLifecycleState.FALLBACK_READY if pin.fallback_applied else RepoLifecycleState.READY
        )
        if pin.fallback_applied:
            entry.sync_state = SyncState.FALLBACK_APPLIED
        elif runner.current_branch(entry.absolute_path) is None:
            entry.sync_state = SyncState.DETACHED_EXACT
        else:
            entry.sync_state = SyncState.ALIGNED

    def _require_nested_install(self, cgshome: Path) -> None:
        """Refuse ``initialise``, before touching the disk, when it is not the nested case.

        ``initialise`` builds the dependencies of a project whose root is
        already checked out at CGSHOME, with this ComplexGitSync inside it.
        Anything else is ``bootstrap``'s job, and cloning the root here would
        blur the two commands into one. A detached ``HEAD`` is still a
        checkout.
        """
        if self._use_case_of(cgshome) is UseCase.STANDALONE:
            raise InstallFrontierError(
                f"`initialise` is the nested install: it builds a workspace around the "
                f"ComplexGitSync running it, and this one is not inside {cgshome}. To "
                f"build a workspace from outside it, run `cgitsync bootstrap <spec> <name>`."
            )
        if not self.client.git_runner.is_repository_root(cgshome):
            raise InstallFrontierError(
                f"{cgshome} is not a git repository. `initialise` builds the dependencies of "
                f"a project whose root is already checked out here. To clone the whole tree, "
                f"root included, run `cgitsync bootstrap <spec> <name>`."
            )

    def _require_fresh_target(self, target_dir: Path) -> None:
        """Refuse ``bootstrap``, before touching the disk, into a directory that holds something.

        ``bootstrap`` clones the root as well, so its target starts empty or
        absent. A checkout already there is the nested case — ``initialise``'s
        — and adopting it here would blur the two commands.
        """
        if not target_dir.exists():
            return
        if target_dir.is_dir() and not any(target_dir.iterdir()):
            return
        if self.client.git_runner.is_repository_root(target_dir):
            raise InstallFrontierError(
                f"{target_dir} already holds a checkout. `bootstrap` clones the root as well, "
                f"so it needs an empty target. To build the dependencies around a root that "
                f"is already checked out, run `cgitsync initialise <spec>` from its "
                f"ComplexGitSync."
            )
        raise InstallFrontierError(
            f"{target_dir} already exists and is not empty. `bootstrap` clones the whole tree, "
            f"root included, into an empty directory: choose another <name> or --cgs-path, or "
            f"empty this one."
        )

    def restart(
        self,
        config_path: str | Path,
        *,
        commit_gitignore: bool = False,
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
        ``commit_gitignore``/``git_user_name``/
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

"""document_loader — Load, validate and snapshot the `.cgs`/`.gts` documents a tree is built from.

Ring: 3
Contract: Load, validate and snapshot the `.cgs`/`.gts` documents a tree is built from.
Imports: cgs_format, client, command_run_logger, git_tree, gts_document, memory, memory_facts, paths, registry
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..cgs_format import CgsDocument

if TYPE_CHECKING:
    pass
from ..git_tree import (
    ProjectTreeState,
    TreeLifecycleState,
    WorkingGitTree,
    build_tree_state,
    normalize_node_types,
    propagate_privacy,
)
from ..git_tree import (
    fix_circularities as _fix_circularities,
)
from ..gts_document import GtsDocument
from ..memory import Relocation
from ..memory.commit_log import (
    CommitLog,
)
from ..memory.pending import (
    PendingMemory,
)
from ..memory.states import (
    MemoryStates,
)
from ..paths import PathResolver
from ..registry import (
    RegistryTranslator,
)
from .command_run_logger import CommandRunLogger
from .memory_facts import MemoryFacts

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient


class DocumentLoader:
    """Load, validate and snapshot the `.cgs`/`.gts` documents a tree is built from.

    A collaborator of :class:`~ComplexGitSync.orchestre.client.ComplexGitSyncClient`,
    which keeps the state and exposes every method here unchanged. Everything
    that is not this class's own private helper is reached through the
    client, so a caller that patches a client method is still obeyed.
    """

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def load_cgs(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
        project_root: Path | None = None,
    ) -> WorkingGitTree:
        """Load a ``.cgs`` file, building the registry from it.

        *project_root* overrides where the tree's repositories are assumed
        to live. Every caller but :meth:`restart` leaves it unset, which
        keeps the long-standing default: the `.cgs` file's own directory —
        right for a spec that sits at the root it describes, which is the
        ordinary case. :meth:`restart` re-syncs a tree that is *already on
        disk*, possibly from a `.cgs` that sits elsewhere in it (a
        developer spec under ``examples/``, say) — for that caller, the
        `.cgs`'s own directory is not the tree's root and must not be
        guessed as one. See
        ``.agent/.local/.localSpec/DevTickets/archive/…_PullOutsideRoot_DevPlanTicket.md``.
        """
        previous_tree_state = self.client.registry.lifecycle_state if self.client.registry else TreeLifecycleState.UNLOADED
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        self.client.registry = RegistryTranslator.from_cgs_document(document, source_path, project_root=project_root)
        self.client.orchestre.git_tree.git.bind_tree(self.client.registry)
        self.client.source_path = source_path
        self.client.loaded_snapshot_path = None
        if discover_nested:
            discovered = self.client.discover_nested_configs()
            self.client._log_nested_discovery(discovered)
        self.client._log_tree_transition(previous_tree_state, self.client.registry.lifecycle_state, reason="load_cgs")
        return self.client.registry

    def load(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> WorkingGitTree:
        """Load a ``.cgs`` or ``.gts`` source into the registry.

        Accepts both file types:

        - ``.gts`` snapshot: loaded directly via :meth:`load_gts`.
        - ``.cgs`` specification: parsed via :meth:`load_cgs` and writes a
          ``.gts`` snapshot for later use with ``print`` and other commands.

        Parameters
        ----------
        source_path:
            Path to a ``.cgs`` authoring file or a ``.gts`` snapshot.
        discover_nested:
            When ``True``, run nested ``.cgs`` discovery for ``.cgs`` sources.
        """
        resolved = Path(source_path).resolve()
        if resolved.suffix == ".gts":
            return self.client.load_gts(resolved)
        registry = self.client.load_cgs(resolved, discover_nested=discover_nested)
        snapshot_path = self.client.write_gts_snapshot(command_origin="load")
        self.client.state_store.record_snapshot(resolved, snapshot_path)
        return registry

    def expand(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = True,
    ) -> str:
        """Expand the dependency tree (lifecycle step 2: LOADED → PENDING).

        Loads the source (``.cgs`` or ``.gts``), runs nested ``.cgs``
        discovery from parents to leaves (recursive), resolves any circularities
        that arise when leaves reference repos already registered as parents, and
        returns a formatted text rendering of the dependency tree.

        Parameters
        ----------
        source_path:
            Path to the ``.cgs`` specification or a previously-written
            ``.gts`` snapshot.
        discover_nested:
            When ``True`` (default) run nested ``.cgs`` discovery for child
            repositories that have not yet been resolved.
        """
        resolved = Path(source_path).resolve()
        if resolved.suffix == ".gts":
            self.client.load_gts(resolved)
        else:
            self.client.load_cgs(resolved, discover_nested=discover_nested)
            fixed = self.client.fix_circularities()
            if fixed:
                self.client._log_circularity_fixes(fixed)
            snapshot_path = self.client.write_gts_snapshot(command_origin="expand")
            self.client.state_store.record_snapshot(resolved, snapshot_path)
        return self.client.format_project_tree()

    def fix_circularities(self) -> tuple[str, ...]:
        """Resolve circularities in the loaded dependency tree (step 2.5).

        Detects and removes duplicate registry entries that arise when a leaf
        declared inside one parent's nested ``.cgs`` refers to the same physical
        repository as another parent already registered in the tree.  The
        canonical entry (the one sitting highest in the tree hierarchy, i.e. with
        the fewest ``:``-separated segments in its ``repo_id``) is kept; all
        lower-priority duplicates are removed.

        This method is called automatically inside :meth:`expand` (for ``.cgs``
        sources) and at the end of :meth:`clone_cgs`.  It can also be invoked
        manually between :meth:`expand` and :meth:`validate` when building a
        custom lifecycle pipeline.

        Returns
        -------
        tuple[str, ...]
            One entry per removed duplicate, each in the form
            ``"fixed_circularity:<removed_id>→<canonical_id>"``.
        """
        registry = self.client.get_dependency_registry()
        fixed = _fix_circularities(registry)
        normalize_node_types(registry)
        propagate_privacy(registry)
        registry.recompute_tree_state()
        return fixed

    def validate(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> ProjectTreeState:
        """Validate the dependency tree state (lifecycle step 3: PENDING → READY).

        Loads the source (``.cgs`` or ``.gts``), recomputes the tree lifecycle
        state, and returns a :class:`~.git_tree.ProjectTreeState` describing
        readiness.  Every :class:`~.git_repo.GitRepo` must be in ``READY``
        state for the tree to be considered ``READY``.

        Parameters
        ----------
        source_path:
            Path to the ``.cgs`` specification or a ``.gts`` snapshot.
        discover_nested:
            When ``True``, run nested ``.cgs`` discovery for ``.cgs`` sources.
        """
        resolved = Path(source_path).resolve()
        if resolved.suffix == ".gts":
            self.client.load_gts(resolved)
        else:
            self.client.load_cgs(resolved, discover_nested=discover_nested)
            snapshot_path = self.client.write_gts_snapshot(command_origin="validate")
            self.client.state_store.record_snapshot(resolved, snapshot_path)
        return self.client.get_tree_state()

    def load_gts(self, snapshot_path: str | Path) -> WorkingGitTree:
        previous_tree_state = self.client.registry.lifecycle_state if self.client.registry else TreeLifecycleState.UNLOADED
        resolved_snapshot_path = Path(snapshot_path).resolve()
        document = GtsDocument.from_toml(resolved_snapshot_path)
        # A snapshot records its paths against the tree, not against a
        # machine, so the reader supplies the tree: the workspace this
        # snapshot was found in. That is what lets a memory be cloned onto
        # another machine and still rebuild the right directories.
        tree_root = MemoryFacts.workspace_of_snapshot(resolved_snapshot_path)
        self.client.registry = RegistryTranslator.from_gts_document(document, tree_root=tree_root)
        self.client.orchestre.git_tree.git.bind_tree(self.client.registry)
        recorded_source = document.read("project.source_cgs_path")
        self.client.source_path = (
            PathResolver.from_tree(str(recorded_source), tree_root)
            if recorded_source
            else resolved_snapshot_path
        )
        self.client.loaded_snapshot_path = resolved_snapshot_path
        self.client._log_event(
            "gts_load",
            snapshot_path=resolved_snapshot_path,
            source_cgs_path=self.client.source_path if self.client.source_path.suffix == ".cgs" else None,
        )
        self.client._log_tree_transition(previous_tree_state, self.client.registry.lifecycle_state, reason="load_gts")
        return self.client.registry

    def load_runtime_or_cgs(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> WorkingGitTree:
        source_path = Path(config_path).resolve()
        snapshot_path = self.client.state_store.latest_snapshot_for(source_path)
        if snapshot_path is not None and snapshot_path.stat().st_mtime >= source_path.stat().st_mtime:
            return self.client.load_gts(snapshot_path)
        return self.client.load_cgs(source_path, discover_nested=discover_nested)

    def load_source(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
        prefer_runtime_for_cgs: bool = True,
    ) -> WorkingGitTree:
        resolved_source = Path(source_path).resolve()
        if resolved_source.suffix == ".gts":
            return self.client.load_gts(resolved_source)
        if resolved_source.suffix == ".cgs":
            if prefer_runtime_for_cgs:
                return self.client.load_runtime_or_cgs(resolved_source, discover_nested=discover_nested)
            return self.client.load_cgs(resolved_source, discover_nested=discover_nested)
        raise ValueError(
            f"Unsupported source format for {resolved_source!s}; expected .cgs or .gts."
        )

    def describe_cgs(self) -> str:
        registry = self.client.get_dependency_registry()
        tree_state = build_tree_state(registry)
        summary = {
            "source_path": str(self.client.source_path) if self.client.source_path else None,
            "project_name": registry.get("root").name,
            "lifecycle_state": tree_state.lifecycle_state.value,
            "registry_complete": tree_state.registry_complete,
            "repo_count": len(registry.repos),
        }
        return json.dumps(summary, indent=2, sort_keys=True)

    def _recorded_source(self) -> Path | None:
        """The ``.cgs`` a snapshot names as its source — never a ``.gts``.

        A State is the tree, not the spec that built it: no copy of the spec is
        stored beside it. What is recorded is only *where* the spec was, and
        only when it was a ``.cgs``; a tree rebuilt from a snapshot has no spec.
        """
        source = self.client.source_path
        return source if source is not None and source.suffix == ".cgs" else None

    def write_gts_snapshot(
        self,
        *,
        command_origin: str,
        output_path: str | Path | None = None,
        freeze_name: str | None = None,
        commits: Sequence[Any] = (),
        publications: Mapping[str, Sequence[Any]] | None = None,
        release: tuple[tuple[str, str], ...] | None = None,
        relocations: Sequence[Relocation] = (),
    ) -> Path:
        registry = self.client.get_dependency_registry()
        root_entry = registry.get("root")
        self.client._memory_commands._refresh_memory_mount_state(registry)
        document = RegistryTranslator.to_gts_document(registry, command_origin=command_origin, source_cgs_path=self._recorded_source(), freeze_name=freeze_name)
        # The State's name is its content. Two machines holding the same
        # tree write the same file name, which is the whole point of a
        # memory that can travel; and writing the same workspace twice
        # produces one State, not two. The TIME-L0 anchor that used to name
        # this is a clock reading with entropy in it, and belongs to the
        # ledger, where *when* is the subject.
        canonical_state_hash = document.ensure_snapshot_hash()
        cgitsync_dir = root_entry.absolute_path / ".cgitsync"
        cgitsync_dir.mkdir(parents=True, exist_ok=True)
        final_output_path = MemoryStates(cgitsync_dir).write(canonical_state_hash, document.to_toml)

        self.client._log_event(
            "gts_write",
            snapshot_path=final_output_path,
            source_cgs_path=self.client.source_path,
            tree_lifecycle_state=registry.lifecycle_state,
        )

        # One register, at one path. It used to be copied into every state
        # directory before each write, so a workspace held one copy per
        # operation and the parent was picked by modification time. With a
        # flat state area there is nowhere to copy it to, and nothing to
        # gain: the register is a single growing file.
        register_filename = f"{root_entry.name}.lgr"
        final_register_path = cgitsync_dir / register_filename
        if not final_register_path.is_file():
            previous_register_path = MemoryStates(cgitsync_dir).latest_artifact(register_filename)
            legacy_register_path = root_entry.absolute_path / register_filename
            if previous_register_path is None and legacy_register_path.is_file():
                previous_register_path = legacy_register_path
            if previous_register_path is not None:
                shutil.copy2(previous_register_path, final_register_path)
        legacy_register_path = root_entry.absolute_path / register_filename

        # One ledger. The single-file register this used to rewrite whole on
        # every operation is still *read* — an existing workspace resolves
        # and replays exactly as it did — but nothing writes it any more.
        # Three records of the same events, one of them tamper-evident, was
        # two too many.
        # The commit log is written before the entry that vouches for it,
        # because the entry carries its digest: an entry can only commit to
        # rows that already exist.
        commit_log_digest = ""
        if commits or publications:
            # The rows name the entry that wrote them and the entry carries
            # their digest, so one of the two has to go first. The rows do,
            # asking the ledger which sequence number is next.
            pending_seq = PendingMemory(cgitsync_dir).next_ledger_seq()
            written: list[Any] = []
            if commits:
                rows = [replace(record, entry=pending_seq) for record in commits]
                CommitLog(cgitsync_dir).append_commits(canonical_state_hash, rows)
                written.extend(rows)
            # Publications go into the logs of the States whose commits they
            # publish, which are older States than this one — a push
            # publishes work that earlier commits recorded. Written in State
            # order so the digest can be recomputed from the files later.
            for state_hash in sorted(publications or {}):
                rows = [
                    replace(record, entry=pending_seq)
                    for record in (publications or {})[state_hash]
                ]
                CommitLog(cgitsync_dir).append_publications(state_hash, rows)
                written.extend(rows)
            commit_log_digest = CommitLog.digest_of(written)
        self.client._memory_commands._append_ledger_entry(
            cgitsync_dir,
            command_origin=command_origin,
            state_hash=canonical_state_hash,
            state_path=final_output_path,
            tree_root=root_entry.absolute_path,
            commit_log=commit_log_digest,
            release=release,
            relocations=relocations,
        )
        # The log is a record of a run, not of a State: two runs that leave
        # the tree identical produce one State and two logs, so it is named
        # for the run and kept out of the state area entirely.
        final_log_path = cgitsync_dir / "logs" / (
            f"{command_origin}-{self.client.clock.now():%Y%m%dT%H%M%S%fZ}.log"
        )
        final_log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.client.run_logger is None:
            final_log_path.write_text(
                json.dumps(
                    {
                        "event": "memory_state_finalized",
                        "command_origin": command_origin,
                        "state_id": MemoryStates.format_id(canonical_state_hash),
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            CommandRunLogger.prune_old_logs(final_log_path.parent, keep_path=final_log_path)

        if legacy_register_path.is_file() and final_register_path.is_file():
            legacy_register_path.unlink()
        self.client.loaded_snapshot_path = final_output_path
        if self.client.run_logger is not None:
            self.client.run_logger.bind_log_file(final_log_path)
        return final_output_path


__all__ = ["DocumentLoader"]

"""memory_commands — Everything the client does with a workspace's memory and its ledger.

Ring: 3
Contract: Everything the client does with a workspace's memory and its ledger; a
    release's own workflow is ``release_commands.py``'s.
Imports: cgs_format, client, errors, git_branch, git_repo, git_tree, git_tree_branch, gts_document, master, memory, memory_facts, memory_setup, operations, registry, snapshot_resolver, toolchain
"""

from __future__ import annotations

import logging
import sys
import tomllib
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..cgs_format import CgsDocument, repo_identifier
from ..errors import (
    ComplexGitSyncError,
    ConfigValidationError,
    GitSyncError,
)
from ..git_branch import DEFAULT_BRANCH
from ..operations import AncestorOperation, MemoryMergeOperation
from .memory_chapters import MemoryChapters

if TYPE_CHECKING:
    pass
from ..git_repo import (
    RefKind,
    RepoScope,
)
from ..git_tree import (
    ROOT_REPO_ID,
    WorkingGitTree,
    _update_gitignore_file,
    format_view_tree,
    iter_tree_leaf_first,
)
from ..git_tree_branch import GitTreeBranches
from ..gts_document import GtsDocument
from ..master import MasterConfig
from ..memory import (
    ChainVerifier,
    Finding,
    HistoryState,
    LocalGitRegister,
    Relocation,
    SyncLedger,
    VerificationReport,
)
from ..memory import self_history as self_history_store
from ..memory.agent_contract import AgentContractRecord
from ..memory.as_of import AsOf
from ..memory.commit_log import (
    COMMIT_LOG_DIR_NAME,
    SCOPE_PRIVATE,
    SCOPE_PROJECT,
    CommitRecord,
    PublicationRecord,
)
from ..memory.environment import EnvironmentStore
from ..memory.ledger_entry import LedgerEntry
from ..memory.ledger_store import ArgvScrubber, LedgerStore, LedgerStoreError
from ..memory.pending import (
    PendingMemory,
)
from ..memory.repository import (
    MOUNT_PATH,
    SELF_HISTORY_SUBDIR_NAME,
    CgsEntryEditor,
    MemoryRepository,
)
from ..memory.self_history import SelfHistoryRecord
from ..memory.states import (
    MemoryStates,
)
from ..registry import (
    RegistryTranslator,
)
from ..snapshot_resolver import discover_gts_path
from ..toolchain import Toolchain
from .default_memory import DefaultMemory
from .memory_facts import MemoryFacts
from .memory_setup import MemorySetup

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient


class MemoryCommands:
    """Everything the client does with a workspace's memory, its ledger and its releases.

    A collaborator of :class:`~ComplexGitSync.orchestre.client.ComplexGitSyncClient`,
    which keeps the state and exposes every method here unchanged. Everything
    that is not this class's own private helper is reached through the
    client, so a caller that patches a client method is still obeyed.
    """

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def _collect_commit_records(
        self,
        registry: WorkingGitTree,
        scope: RepoScope,
        message: str,
    ) -> list[CommitRecord]:
        """What the commit just made, one row per repository that committed.

        The outcomes come back in the order the repositories were visited,
        so they are zipped against the same walk rather than matched by
        name: two repositories may share a name, and a row attributed to the
        wrong one is worse than no row at all.
        """
        records: list[CommitRecord] = []
        visited = list(iter_tree_leaf_first(registry, scope))
        branches = GitTreeBranches(registry, self.client.git_runner)
        for entry, outcome in zip(visited, self.client.last_write_outcomes, strict=False):
            if not outcome.acted or not entry.commit_sha:
                continue
            records.append(
                CommitRecord(
                    entry=0,  # replaced with the real seq in write_gts_snapshot
                    repository=entry.name,
                    repo_id=entry.repo_id,
                    scope=SCOPE_PRIVATE if entry.effective_private else SCOPE_PROJECT,
                    branch=branches.observed(entry) or "",
                    sha=entry.commit_sha,
                    message=message,
                    authored_at=self.client.git_runner.commit_authored_at(
                        entry.absolute_path, entry.commit_sha
                    ),
                )
            )
        return records

    def _collect_publication_records(
        self,
        registry: WorkingGitTree,
        scope: RepoScope,
    ) -> dict[str, list[PublicationRecord]]:
        """What the push just made public, grouped by the State that holds it.

        A push publishes everything a repository has committed since the
        last one, not only the commit at its HEAD, so every remembered
        commit of that repository that carries no publication row yet gets
        one. A commit the memory never saw — made by hand, or before any of
        this existed — gets nothing: the memory speaks for what it watched.

        Repositories are matched by their `.cgs` identifier rather than by
        name, because two repositories in one tree may share a name and a
        publication filed against the wrong one is worse than none.
        """
        root_entry = registry.get("root")
        cgitsync_dir = root_entry.absolute_path / ".cgitsync"
        folded_dir, pending_dir = PendingMemory(cgitsync_dir).dirs()
        if not (folded_dir / COMMIT_LOG_DIR_NAME).is_dir() and not (pending_dir / COMMIT_LOG_DIR_NAME).is_dir():
            return {}
        moment = self.client.clock.now().isoformat(timespec="seconds")
        branches = GitTreeBranches(registry, self.client.git_runner)
        published: dict[str, list[PublicationRecord]] = {}
        for entry, outcome in zip(
            iter_tree_leaf_first(registry, scope), self.client.last_write_outcomes, strict=False
        ):
            if not outcome.acted or not entry.repo_id:
                continue
            branch = branches.observed(entry)
            for state_hash, sha in PendingMemory(cgitsync_dir).unpublished_commits(entry.repo_id):
                published.setdefault(state_hash, []).append(
                    PublicationRecord(
                        entry=0,  # replaced with the real seq in write_gts_snapshot
                        repository=entry.name,
                        sha=sha,
                        remote=repo_identifier(entry),
                        ref=f"refs/heads/{branch}" if branch else "",
                        at=moment,
                    )
                )
        return published

    def freeze_state(
        self,
        state_name: str,
        *,
        output_gts: str | Path | None = None,
        message: str | None = None,
        stage_all: bool = True,
    ) -> WorkingGitTree:
        """Freeze an internal development state from a ``READY`` tree.

        Parameters mirror :meth:`freeze`:

        - ``state_name``: shared tag name applied across all repositories.
        - ``output_gts``: optional snapshot path for the emitted ``.gts`` file.
        - ``message``: optional commit message override.
        - ``stage_all``: stage all changes before committing when ``True``.

        Behavior is identical to release freezing (commit/tag/push leaf-first),
        but intended for internal development states.
        """
        return self.client._freeze_tag(
            state_name,
            output_gts=output_gts,
            message=message,
            stage_all=stage_all,
        )

    def launch_state(self, snapshot_path: str | Path) -> WorkingGitTree:
        """Restore an internal ``.gts`` state."""
        return self.client._restore_gts_snapshot(snapshot_path)

    def freeze(
        self,
        name: str,
        *,
        output_gts: str | Path | None = None,
        message: str | None = None,
        stage_all: bool = True,
        private: bool = False,
        release: tuple[tuple[str, str], ...] | None = None,
    ) -> WorkingGitTree:
        """Freeze a tree state and emit the next ``.gts`` snapshot id.

        ``private`` freezes the writable configuration repositories alone.
        Without it every repository this project may write is frozen, which
        is what this command has always done. ``release`` is
        ``release freeze``'s own parameter, threaded through rather than
        duplicated; every other caller leaves it ``None``.
        """
        return self.client._freeze_tag(
            name,
            output_gts=output_gts,
            message=message,
            stage_all=stage_all,
            private=private,
            release=release,
        )

    def memory_init(self, cgshome: str | Path, *, owner: str | None = None) -> dict[str, Any]:
        """Propose the `.cgs` entry that mounts this workspace's memory.

        It proposes and stops. **Nothing here creates or changes anything**:
        it returns the entry to add, the branch the memory will live on, and
        the command that creates the repository, and waits.

        The three commands that act on what it proposes are
        :meth:`repo_create`, :meth:`add_memory_repo_cgs` and
        :meth:`memory_adopt`. None of them holds a credential: creating a
        repository runs the provider's own tool, and everything else is
        plain Git.
        """
        workspace = Path(cgshome)
        registry = self.client.registry
        if registry is None or ROOT_REPO_ID not in registry.repos:
            raise GitSyncError(
                "cgitsync memory init needs a loaded project: run it in a workspace "
                "with a .gts, or pass --gts."
            )
        root = registry.get(ROOT_REPO_ID)
        repository_owner = owner or root.project_owner_name
        if not repository_owner:
            raise GitSyncError(
                "the project's root repository declares no owner, so no memory "
                "repository name can be proposed. Pass one explicitly."
            )
        entry = MemoryRepository.mount_entry(repository_owner, root.name)
        branches = GitTreeBranches(registry, self.client.git_runner)
        return {
            "entry": entry,
            "line": CgsEntryEditor.format_entry(entry),
            "branch": MemoryRepository.branch(root.name, branches.tree_branch or DEFAULT_BRANCH),
            "mount_path": str(MemoryRepository(workspace).mount_path()),
            "create_with": MemoryRepository.creation_command(entry),
            "mounted": DefaultMemory(self.client).is_published(workspace),
        }

    def add_memory_repo_cgs(
        self,
        cgs_path: str | Path,
        *,
        cgshome: str | Path | None = None,
        owner: str | None = None,
        entry: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add this project's memory to a `.cgs` that already exists.

        `ComplexGitSyncClient.configure` writes a whole file from arguments
        and replaces; it never appends. This
        appends — one entry, in the file's own layout, with every comment
        left where it was. §4 of the MemoryOnboarding ticket says why that
        matters more here than anywhere else.

        The file is parsed and validated before it replaces anything, so a
        `.cgs` is never left in a state that will not load.

        Adding an entry that is already there changes nothing and says so:
        running this twice is what a person does when they are not sure
        whether they ran it once.
        """
        target = Path(cgs_path).resolve()
        if not target.is_file():
            raise GitSyncError(f"{target} is not a file.")
        # *entry*, when given, is one `memory setup` built for another provider or name.
        entry = dict(entry or self.client.memory_init(cgshome or target.parent, owner=owner)["entry"])
        line = CgsEntryEditor.format_entry(entry)
        original = target.read_text(encoding="utf-8")

        if CgsEntryEditor.already_present(original, str(entry["repository"]), str(entry["relative_path"])):
            return {"cgs": str(target), "line": line, "added": False, "entry": entry}

        try:
            updated = CgsEntryEditor.insert(original, line)
        except ValueError as exc:
            raise GitSyncError(f"{target} cannot take a repository entry: {exc}.") from exc

        # Validated before it replaces anything: a spec that will not load
        # is worse than one that lacks an entry.
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(updated, encoding="utf-8")
        try:
            CgsDocument.from_toml(temporary)
        except (ConfigValidationError, tomllib.TOMLDecodeError) as exc:
            temporary.unlink(missing_ok=True)
            raise GitSyncError(
                f"adding the memory entry would make {target.name} invalid: {exc}"
            ) from exc
        temporary.replace(target)
        self.client._log_event("memory_mount", cgs=target, repository=entry["repository"])
        return {"cgs": str(target), "line": line, "added": True, "entry": entry}

    def memory_clone(
        self,
        cgshome: str | Path,
        *,
        owner: str | None = None,
        branch: str | None = None,
        remote: str | None = None,
    ) -> Path:
        """Bring this project's memory onto a machine that does not have it.

        The case this exists for is a machine with **no memory at all**, so
        it asks nothing of a loaded project: give it an owner and a branch
        and it works on a bare clone. When a project *is* loaded it fills
        both in from it, which is the case that needs no arguments.

        It refuses rather than overwrites. A local memory nobody has pushed
        is the only copy of itself, and cloning another one over it would
        destroy exactly the thing this milestone exists to preserve.
        """
        workspace = Path(cgshome)
        destination = MemoryRepository(workspace).mount_path()
        if (destination / ".git").exists():
            raise GitSyncError(f"{destination} is already a repository; nothing to clone.")
        if destination.is_dir() and any(destination.iterdir()):
            raise GitSyncError(
                f"{destination} already holds a memory. Move it aside before cloning "
                "one over it — this command never overwrites a local memory."
            )

        target_branch, remote_url = self._memory_remote(
            workspace, owner=owner, branch=branch, remote=remote
        )
        if not self.client.git_runner.remote_branch_exists(remote_url, target_branch):
            raise GitSyncError(
                f"{remote_url} has no branch {target_branch!r}: this project's memory "
                "has never been pushed, so there is nothing to clone."
            )
        self.client.git_runner.clone(remote_url, destination, branch=target_branch)
        self.client._log_event("memory_clone", destination=destination, branch=target_branch)
        self._clone_self_history_if_declared(workspace, branch=target_branch)
        return destination

    def _clone_self_history_if_declared(
        self, workspace: Path, *, branch: str
    ) -> Path | None:
        """Bring self-history along, per D8 — a clone replicates both or neither.

        The signal is `config-memory.cgs` itself, just cloned down with
        `.memory`: if it is there, this machine's `.memory` was adopted with
        self-history, and D8's principle — *"a project state must be
        Replicable"* — means a second machine gets the same accounting
        record as the first, not a memory that merely looks complete.
        `_self_history_identity_from_config` reads which repository that
        is, from the file itself rather than re-derived.
        """
        identity = MemoryFacts.self_history_identity_from_config(MemoryRepository(workspace).config_path())
        if identity is None:
            return None
        _owner, remote_url = identity
        destination = MemoryRepository(workspace).self_history_mount_path()
        if (destination / ".git").exists():
            return None
        if destination.is_dir() and any(destination.iterdir()):
            raise GitSyncError(
                f"{destination} already holds content. Move it aside before cloning "
                "self-history over it — this command never overwrites local content."
            )
        if not self.client.git_runner.remote_branch_exists(remote_url, branch):
            # Adopted but never pushed — nothing to clone yet, and not an
            # error: the same case `.memory` itself refuses on, one level
            # deeper.
            return None
        self.client.git_runner.clone(remote_url, destination, branch=branch)
        self.client._log_event("self_history_clone", destination=destination, branch=branch)
        return destination

    def _memory_remote(
        self,
        workspace: Path,
        *,
        owner: str | None,
        branch: str | None,
        remote: str | None,
    ) -> tuple[str, str]:
        """Which branch of which repository this workspace's memory is.

        Answers from a loaded project when there is one, and from the
        arguments when there is not — which is the fresh-machine case, where
        by definition nothing is loaded yet.
        """
        if branch is None or remote is None:
            proposal = self.client.memory_init(workspace, owner=owner)
            branch = branch or str(proposal["branch"])
            remote = remote or MemoryFacts.remote_url(
                str(proposal["entry"]["repository"])
            )
        return branch, remote

    def memory_adopt(
        self,
        cgshome: str | Path,
        *,
        owner: str | None = None,
        branch: str | None = None,
        remote: str | None = None,
        reboot: bool = False,
    ) -> dict[str, Any]:
        """Make this workspace's memory mount *be* a repository.

        `.cgitsync/.memory` is created fresh, empty — WorkingTransitionState
        moved everything a memory used to adopt "as found" (States, ledger,
        commit logs, logs) one level up, to `.cgitsync` itself, where every
        command already writes it. There is nothing here to leave untracked
        and untouched any more: the first thing this mount ever holds is
        whatever the next `memory push` folds into it.

        Nothing is committed and nothing is pushed: `memory push` does both
        and already knows how. This only ends the state where there is
        nowhere to push *from*.

        *reboot* (`cgitsync memory adopt --reboot`,
        `memory-dev_1-4_MemoryReboot_DevPlanTicket.md` §4) skips the one
        step below that would otherwise carry history forward: starting
        *target_branch* from `fallback_branch`'s tip when that branch
        already exists on the remote. Append — inheriting that history — is
        still the default; `reboot=True` leaves the branch exactly as
        `init_repository` made it, with nothing to inherit from.
        """
        workspace = Path(cgshome)
        mount = MemoryRepository(workspace).mount_path()
        # Adopting is the opt-in: a defaulted memory is retired below and adopted as found.
        default_memory = DefaultMemory(self.client)
        if (mount / ".git").exists() and not default_memory.is_defaulted(workspace):
            raise GitSyncError(
                f"{mount} is already a repository. 'cgitsync memory push' sends what "
                "it has gained."
            )
        if not MemoryRepository(workspace).pending_path().is_dir():
            raise GitSyncError(
                f"{MemoryRepository(workspace).pending_path()} does not exist yet. Run any "
                "cgitsync command in this workspace first."
            )
        mount.mkdir(parents=True, exist_ok=True)

        target_branch, remote_url = self._memory_remote(
            workspace, owner=owner, branch=branch, remote=remote
        )
        if not self.client.git_runner.remote_reachable(remote_url):
            raise GitSyncError(
                f"{remote_url} is not there, or these credentials cannot see it. "
                f"Create it with 'cgitsync repo create {MemoryFacts.identifier_of(remote_url)}'."
            )

        base = self._memory_base_branch(workspace, owner=owner)
        git = self.client.git_runner
        kept_history = default_memory.retire(workspace)
        renamed_from: str | None = None
        hand_added_remote = git.remote_get_url(mount, "origin") if kept_history else None
        try:
            if kept_history:
                # The local memory's own commits are kept: adopting publishes them, it never discards them.
                current = git.current_branch(mount)
                if current is not None and current != target_branch:
                    git.rename_branch(mount, current, target_branch)
                    renamed_from = current
            else:
                git.init_repository(mount, branch=target_branch)
            git.configure_remote(mount, "origin", remote_url)
            git.fetch(mount)
            started_from = ""
            if kept_history and not reboot and base and git.remote_branch_exists(remote_url, base):
                self._join_remote_base(workspace, mount, base)
                started_from = base
            elif not reboot and base and git.remote_branch_exists(remote_url, base):
                # Started from the repository's own default branch so the branch
                # shares its history, which is what makes `fallback_branch` in
                # the mount entry mean something.
                git.create_branch(mount, target_branch, start_point=f"origin/{base}")
                git.checkout(mount, target_branch)
                started_from = base
        except GitSyncError:
            if kept_history:
                self._undo_adopt(workspace, mount, target_branch, renamed_from=renamed_from, hand_added_remote=hand_added_remote)
            raise
        self.client._log_event(
            "memory_adopt", mount=mount, branch=target_branch, started_from=started_from
        )
        self.client._adopt_self_history_if_declared(workspace, branch=target_branch)
        return {
            "mount": str(mount),
            "branch": target_branch,
            "remote": remote_url,
            "started_from": started_from,
            "pending": len(MemoryRepository.uncommitted_paths(self.client.git_runner.status_porcelain(mount))),
        }

    def _undo_adopt(
        self, workspace: Path, mount: Path, target_branch: str, *, renamed_from: str | None, hand_added_remote: str | None
    ) -> None:
        """Undo this command's own steps, so a refused adoption leaves the local memory as it was."""
        git = self.client.git_runner
        if hand_added_remote:
            git.configure_remote(mount, "origin", hand_added_remote)
        elif git.remote_get_url(mount, "origin"):
            git.remove_remote(mount, "origin")
        if renamed_from is not None:
            git.rename_branch(mount, target_branch, renamed_from)
        DefaultMemory(self.client).unretire(workspace)

    def _join_remote_base(self, workspace: Path, mount: Path, base: str) -> None:
        """Merge the remote memory's *base* into a kept local memory, adding one merge commit.

        The two histories share no commit, so the merge allows unrelated
        histories. If it does not apply cleanly it is aborted, so the local
        memory is left exactly as it was, and adopting is refused: which
        side's entries are right is not something to guess. With no Git
        identity configured, the merge signs as the tool, like the default
        memory's own first commit.
        """
        git = self.client.git_runner
        MasterConfig.load(workspace)
        user_name, user_email = MasterConfig.resolve_identity(mount, git)
        message = f"memory adopt: join origin/{base}"
        try:
            try:
                git.merge(mount, f"origin/{base}", no_ff=True, allow_unrelated=True, message=message, user_name=user_name, user_email=user_email)
            except GitSyncError as error:
                if "identity unknown" not in str(error):
                    raise
                self._abort_merge_if_any(mount)
                git.merge(mount, f"origin/{base}", no_ff=True, allow_unrelated=True, message=message, user_name="cgitsync", user_email="cgitsync@localhost")
        except GitSyncError as error:
            self._abort_merge_if_any(mount)
            first_line = (str(error).strip().splitlines() or [str(error)])[0]
            raise GitSyncError(
                f"{mount}: the local memory and origin/{base} cannot be merged cleanly, so nothing was "
                "adopted and the local memory is unchanged. Adopt with --reboot to publish the local "
                f"memory on a branch of its own, or run 'cgitsync autofix'. Git said: {first_line}"
            ) from error

    def _abort_merge_if_any(self, mount: Path) -> None:
        if (mount / ".git" / "MERGE_HEAD").exists():
            self.client.git_runner.merge_abort(mount)

    def self_history_adopt(
        self, cgshome: str | Path, *, owner: str | None = None, branch: str | None = None
    ) -> dict[str, Any]:
        """Bootstrap self-history for this project, or retrofit it onto a
        `.memory` that was adopted before self-history existed.

        This is the **one place self-history's identity is decided** — it
        writes `config-memory.cgs` when nothing has decided that identity
        yet. Every other reader (`_adopt_self_history_if_declared` on a
        second machine, `_clone_self_history_if_declared`) only ever reads
        that file back via `_self_history_identity_from_config`; none of
        them re-derive an owner or guess at whether self-history applies
        here. That split — one writer, several readers of the same
        committed fact — is deliberate: a `.gts` is a static snapshot of an
        already-discovered tree, not a place to keep re-asking "should I
        create this," and there is exactly one place that question is ever
        answered by *deciding*, not by reading.

        Unlike the automatic path `memory_adopt` runs on every adopt,
        asking for this explicitly means wanting to know why it did not
        work, not a silent no-op: every precondition below raises instead
        of returning `None`.

        *branch* defaults to `.memory`'s own actual current branch, read
        off the mount directly rather than re-derived — the retrofit case
        is exactly the one where re-deriving it from the project's own
        name could, in principle, disagree with what is really checked
        out.
        """
        workspace = Path(cgshome)
        memory_mount = MemoryRepository(workspace).mount_path()
        DefaultMemory(self.client).require_published(workspace, "self-history adopt")
        mount = MemoryRepository(workspace).self_history_mount_path()
        if (mount / ".git").exists():
            raise GitSyncError(
                f"{mount} is already a repository. 'cgitsync memory push' sends what "
                "it has gained."
            )
        resolved_branch = branch or self.client.git_runner.current_branch(memory_mount)
        registry = self.client.registry
        root = registry.get(ROOT_REPO_ID) if registry is not None else None
        repository_owner = owner or (root.project_owner_name if root is not None else None)
        if not repository_owner:
            raise GitSyncError("self-history needs an owner to adopt under: pass one explicitly.")
        remote_url = MemoryFacts.remote_url(MemoryRepository.self_history_repository_id(repository_owner))
        if not self.client.git_runner.remote_reachable(remote_url):
            raise GitSyncError(
                f"{remote_url} is not there, or these credentials cannot see it. "
                f"Create it with 'cgitsync repo create {MemoryFacts.identifier_of(remote_url)}'."
            )
        config_path = MemoryRepository(workspace).config_path()
        if not config_path.is_file():
            config_path.write_text(
                MemoryRepository.config_document(repository_owner, resolved_branch), encoding="utf-8"
            )
        return self._finish_self_history_adopt(workspace, branch=resolved_branch, remote_url=remote_url)

    def _adopt_self_history_if_declared(
        self, workspace: Path, *, branch: str
    ) -> dict[str, Any] | None:
        """Follow what `.memory`'s own already-fetched content already
        decided, if anything — never bootstraps, never probes a remote
        that has no reason to exist.

        The signal is `config-memory.cgs` **as `.memory`'s own adopt just
        fetched it** — not a `nested_config` flag on the registry, and not
        a blind reachability probe on every adopt. Both of those were the
        wrong question: `.gts` is a static, already-discovered snapshot —
        a READY tree does not re-run discovery, and the fact that a `.gts`
        can be loaded at all is downstream of it having been generated
        from a tree that already went through discovery once. Asking the
        registry "does `.memory` declare `nested_config`" therefore asks a
        `.cgs`-only question a `.gts`-loaded registry was never going to be
        able to answer — not a bug to route around with a probe, just the
        wrong layer to ask at all. The question that actually matters is
        simpler and does not touch the registry at all: has *this
        project's `.memory`, as actually committed*, already decided to
        use self-history. `config-memory.cgs`'s presence in what `.memory`
        just fetched answers that directly, the same way `verify` trusts
        the ledger over a runtime guess about what happened.

        For a project that has never bootstrapped self-history at all,
        `config-memory.cgs` does not exist anywhere to fetch, so this is a
        silent no-op — additive by construction. For one that has, an
        unreachable remote here is unexpected (the ledger side already
        says this repository should exist) and warns rather than raising,
        the same "a second repository's trouble is not a reason to fail
        the first one's operation" stance `_fold_memory_before_push`
        already takes.
        """
        mount = MemoryRepository(workspace).self_history_mount_path()
        if (mount / ".git").exists():
            return None
        identity = MemoryFacts.self_history_identity_from_config(MemoryRepository(workspace).config_path())
        if identity is None:
            return None
        _owner, remote_url = identity
        if not self.client.git_runner.remote_reachable(remote_url):
            warnings.warn(
                f"self-history is declared for this project but {remote_url} is not "
                "there, or these credentials cannot see it. Adopt it by hand once it "
                "is, with 'cgitsync self-history adopt'.",
                stacklevel=3,
            )
            return None
        return self._finish_self_history_adopt(workspace, branch=branch, remote_url=remote_url)

    def _finish_self_history_adopt(
        self, workspace: Path, *, branch: str, remote_url: str
    ) -> dict[str, Any]:
        """The git-level mechanics both adopt paths share, once each has
        decided (bootstrap) or confirmed (follow) that self-history
        applies here and found a reachable remote for it."""
        mount = MemoryRepository(workspace).self_history_mount_path()
        mount.mkdir(parents=True, exist_ok=True)
        self.client.git_runner.init_repository(mount, branch=branch)
        # An unborn branch (no commit at all) is a shape `current_branch`
        # degrades gracefully for, but `is_ready()` (`git_tree.py`) still
        # requires every repo's `commit_sha` to be real — self-history is
        # "empty but initiated" (AgentReport WP2's own phrase for it), not
        # unborn, so it gets exactly one real, contentless commit right
        # here, the moment it exists, rather than waiting for the first
        # `self-history add` to give it one implicitly.
        MasterConfig.load(workspace)
        user_name, user_email = MasterConfig.resolve_identity(mount, self.client.git_runner)
        self.client.git_runner.commit(
            mount,
            MemoryRepository.self_history_commit_message(workspace.name, 0, clock=self.client.clock),
            user_name=user_name,
            user_email=user_email,
            allow_empty=True,
        )
        self.client.git_runner.configure_remote(mount, "origin", remote_url)
        self.client.git_runner.fetch(mount)
        # `.memory`'s own worktree now holds a *second* repository nested
        # inside it — an empty one, with no commit `git add` could even
        # make a gitlink entry out of, so `.memory`'s next ordinary
        # `stage_all` (inside `memory_push`) would fail outright on
        # ".self-history/ does not have a commit checked out" until this is
        # written. `sync_gitignore` would write the same line once a full
        # discovery pass registers `.self-history` as `.memory`'s child;
        # this does not wait for that pass, because a `.memory` push can
        # run before it ever does.
        _update_gitignore_file(MemoryRepository(workspace).mount_path(), [SELF_HISTORY_SUBDIR_NAME])
        self.client._log_event("self_history_adopt", mount=mount, branch=branch)
        return {"mount": str(mount), "branch": branch, "remote": remote_url}

    def memory_migrate(self, cgshome: str | Path, cgs_path: str | Path) -> dict[str, Any]:
        """Move a memory mounted before WorkingTransitionState onto its new layout.

        A memory adopted before this milestone is mounted directly at
        `.cgitsync` — sharing it with States, the ledger, commit logs and
        run logs, the exact arrangement WorkingTransitionState exists to
        end (`.agent/.local/.dev/DevTickets/archive/20260917_WorkingTransitionState_DevPlanTicket.md`).
        This is the one-time move: `.git` and every file `git ls-files`
        names travel down into `.cgitsync/.memory`, untouched — no re-clone,
        no rewritten history — and whatever was never tracked (this
        workspace's own pending States, ledger entries, logs) stays exactly
        where it already was, which is where the new layout wants it
        anyway. The `.cgs` entry that declares the mount is then updated to
        match.

        `memory adopt` never needs this: a fresh adopt already creates the
        mount at the new path. This is only for a `.cgitsync` that is
        *already* a memory's own git repository, at the old path.
        """
        workspace = Path(cgshome)
        old_mount = workspace / self.client._OLD_MOUNT_RELATIVE_PATH
        new_mount = MemoryRepository(workspace).mount_path()
        if (new_mount / ".git").exists():
            raise GitSyncError(f"{new_mount} is already a repository; nothing to migrate.")
        if not (old_mount / ".git").is_dir():
            raise GitSyncError(
                f"{old_mount} is not a repository — there is no old-layout memory here "
                "to migrate. 'cgitsync memory adopt' mounts a fresh one at the new layout "
                "directly."
            )

        tracked = self.client.git_runner.tracked_files(old_mount)
        new_mount.mkdir(parents=True, exist_ok=True)
        (old_mount / ".git").rename(new_mount / ".git")
        moved = 0
        for relative in tracked:
            source = old_mount / relative
            if not source.is_file():
                continue
            destination = new_mount / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            moved += 1

        target = Path(cgs_path).resolve()
        original = target.read_text(encoding="utf-8")
        old_needle = f'relative_path = "{self.client._OLD_MOUNT_RELATIVE_PATH}"'
        new_value = f'relative_path = "{MOUNT_PATH}"'
        if old_needle not in original:
            raise GitSyncError(
                f"{target} does not declare {old_needle!r} — the mount was moved on disk, "
                "but its .cgs entry needs updating by hand."
            )
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(original.replace(old_needle, new_value, 1), encoding="utf-8")
        try:
            CgsDocument.from_toml(temporary)
        except (ConfigValidationError, tomllib.TOMLDecodeError) as exc:
            temporary.unlink(missing_ok=True)
            raise GitSyncError(
                f"migrating the memory entry would make {target.name} invalid: {exc}"
            ) from exc
        temporary.replace(target)

        self.client._log_event(
            "memory_migrate", old_mount=old_mount, new_mount=new_mount, files_moved=moved
        )
        return {
            "old_mount": str(old_mount),
            "new_mount": str(new_mount),
            "files_moved": moved,
            "cgs": str(target),
        }

    def memory_branch(
        self,
        cgshome: str | Path,
        project_branch: str,
        *,
        push: bool = True,
    ) -> dict[str, Any]:
        """Create the memory branch another project branch will need.

        A memory born on a feature branch has never had a branch for the
        branch it is about to merge into: merging ``memory-dev`` into
        ``main`` asks for ``<project>`` where only ``<project>_memory-dev``
        has ever existed. `merge` reports that and names this command rather
        than creating the branch itself — a merge that makes its own target
        cannot tell a new project branch from a mistyped one.

        The new branch starts at the memory's current head and is pushed, so
        the merge has something to merge into on both sides.
        """
        workspace = Path(cgshome)
        mount = MemoryRepository(workspace).mount_path()
        DefaultMemory(self.client).require_published(workspace, "memory branch")
        registry = self.client.get_dependency_registry()
        target = MemoryRepository.branch(registry.get(ROOT_REPO_ID).name, project_branch)
        existed = self.client.git_runner.local_branch_exists(mount, target)
        if not existed:
            self.client.git_runner.create_branch(mount, target)
        if push:
            self.client.git_runner.push(mount, ref_name=target)
        self.client._log_event("memory_branch", mount=mount, branch=target, created=not existed)
        return {
            "mount": str(mount),
            "project_branch": project_branch,
            "branch": target,
            "created": not existed,
            "pushed": push,
        }

    def memory_merge(
        self,
        cgshome: str | Path,
        source_branch: str,
        *,
        into: str | None = None,
        keep: str,
    ) -> dict[str, Any]:
        """Merge one project branch's memory into another's by keeping one side whole.

        *source_branch* and *into* name **project** branches (*into* defaults
        to the one checked out); the memories merged are their derived
        branches. A memory is never merged file by file — a ledger is a hash
        chain, and two of them collide or interleave — so one commit takes
        one side's files whole and keeps the other reachable as its second
        parent. *keep* is Git's meaning for merging *source* into *into*:
        ``"ours"`` keeps *into*'s memory, ``"theirs"`` keeps *source_branch*'s.
        Nothing is rewritten, and nothing is spliced.

        What is pending in `.cgitsync` is folded and committed first, so the
        merge loses nothing recorded. The result is pushed, without force,
        unless the memory is a defaulted one or has no remote.
        """
        if keep not in MemoryMergeOperation.KEEPS:
            raise GitSyncError("memory merge: say which memory to keep, --ours (the one merged into) or --theirs (the one merged from).")
        workspace = Path(cgshome)
        git = self.client.git_runner
        registry = self.client.get_dependency_registry()
        project = registry.get(ROOT_REPO_ID).name
        target_project = into or GitTreeBranches(registry, git).tree_branch or DEFAULT_BRANCH
        source, target = MemoryRepository.branch(project, source_branch), MemoryRepository.branch(project, target_project)
        recorded = self.memory_push(workspace, message=f"{project} memory merge: recording before {source_branch} into {target_project}")
        mount = Path(str(recorded["mount"]))
        plan = MemoryMergeOperation.plan(git, mount, source, target, keep)
        if plan.status == "no-source":
            raise GitSyncError(f"project branch '{source_branch}' has no memory branch '{source}' here or on its remote; nothing was merged.")
        if plan.status == "no-target":
            raise GitSyncError(f"project branch '{target_project}' has no memory branch '{target}'; 'cgitsync memory branch --project-branch {target_project}' creates it. Nothing was merged.")
        MasterConfig.load(workspace)
        user_name, user_email = MasterConfig.resolve_identity(mount, git)
        commit = MemoryMergeOperation.apply(git, mount, plan, user_name=user_name, user_email=user_email)
        pushed = bool(commit) and not DefaultMemory(self.client).is_defaulted(workspace) and bool(git.remote_get_url(mount, "origin"))
        if pushed:
            try:
                git.push(mount, ref_name=target, set_upstream=True)
            except GitSyncError as error:
                first_line = (str(error).strip().splitlines() or [str(error)])[0]
                raise GitSyncError(
                    f"the memory of '{target_project}' was merged here, but pushing '{target}' was refused ({first_line}). "
                    "Nothing was forced. Fetch and look at what the remote holds, then run 'cgitsync memory push'."
                ) from error
        self.client._log_event("memory_merge", mount=mount, source=source, target=target, keep=keep, status=plan.status, pushed=pushed)
        return {
            "mount": str(mount), "source": source, "target": target, "source_project_branch": source_branch,
            "target_project_branch": target_project, "keep": keep, "status": plan.status, "commit": commit,
            "pushed": pushed, "detail": MemoryMergeOperation.describe(plan),
        }

    def _memory_base_branch(self, workspace: Path, *, owner: str | None) -> str:
        """The branch a new memory branch starts from — the entry's fallback."""
        proposal = self.client.memory_init(workspace, owner=owner)
        return str(proposal["entry"].get("fallback_branch") or DEFAULT_BRANCH)

    def _memory_declared(self, registry: WorkingGitTree) -> bool:
        """Whether *registry* declares a memory mount at all; says nothing about adoption."""
        return DefaultMemory.declared(registry)

    def memory_declared(self) -> bool:
        """Whether the loaded tree declares a memory mount at all.

        The public form of :meth:`_memory_declared`, used by the CLI to decide
        whether a ``--dry-run`` plan mentions the fold (PushFoldsMemory D4).
        """
        return self._memory_declared(self.client.get_dependency_registry())

    def _fold_memory_before_push(self) -> dict[str, Any] | None:
        """Fold this project's own memory and send it, before publishing anything else.

        Unconditional, once a memory is mounted — `.cgitsync/.memory` is
        this project's own record of itself, not a `--private`-scoped
        configuration repository, so no scope flag decides whether this
        runs (`main_1-1_PushFoldsMemory_DevPlanTicket.md` D1). Called first,
        by `push()`, `tag()`, and `_freeze_tag()` (covering `freeze`/
        `freeze_state`), so `.cgitsync` never carries more than what has
        accumulated since the command that is about to publish something
        else — `freeze_release` folding twice in one run, once via its own
        `push()` call and once via its own `freeze()` call, is a harmless
        consequence of that rather than a special case.

        Returns `None`, without doing anything, when the tree declares no
        memory mount (D2, most trees). When one is declared, returns
        `memory_push`'s own result — or warns and returns `None` when
        `memory_push` raises, whether because `memory adopt`/`memory clone`
        was never run or for any other reason (D2/D3): an otherwise
        successful push, tag, or freeze must never be blocked by the
        memory's own trouble reaching its remote. Warned rather than
        printed, so a Python caller hears it too, the same reasoning
        `_warn_if_build_changes` already follows. Also recorded on
        `self.last_memory_fold`, so the CLI can report what was folded
        (count, branch, whether anything was committed) without asking
        `memory_push` to run a second time.
        """
        registry = self.client.get_dependency_registry()
        self.client.last_memory_fold = None
        if not self._memory_declared(registry):
            return None
        try:
            self.client.last_memory_fold = self.client.memory_push(self.client._workspace_root())
        except GitSyncError as exc:
            warnings.warn(
                f"memory not folded: {exc} Run 'cgitsync memory push' by hand "
                "once this is resolved.",
                stacklevel=4,
            )
            return None
        return self.client.last_memory_fold

    def _push_self_history(self, workspace: Path) -> dict[str, Any] | None:
        """Fold self-history's pending records into its own mount, and push.

        A plain move, unlike :meth:`_fold_memory_pending`'s per-kind
        handling: every self-history record is named by its own content
        hash (AgentReport §2: "the same reason it is safe for States"), so
        a name that repeats is identical content and a plain
        ``Path.replace`` can never lose anything. Returns ``None`` without
        touching anything when self-history has not been adopted — the
        mount's own ``.git`` is the only thing asked, so a workspace that
        never opted in behaves exactly as it did before this existed.
        """
        mount = MemoryRepository(workspace).self_history_mount_path()
        if not (mount / ".git").exists():
            return None
        pending_dir = workspace / ".cgitsync" / self_history_store.SELF_HISTORY_PENDING_DIR_NAME
        moved = 0
        if pending_dir.is_dir():
            for item in sorted(pending_dir.iterdir()):
                item.replace(mount / item.name)
                moved += 1
            pending_dir.rmdir()
        pending_paths = MemoryRepository.uncommitted_paths(self.client.git_runner.status_porcelain(mount))
        committed = False
        if pending_paths:
            self.client.git_runner.stage_all(mount)
            MasterConfig.load(workspace)
            user_name, user_email = MasterConfig.resolve_identity(mount, self.client.git_runner)
            self.client.git_runner.commit(
                mount,
                MemoryRepository.self_history_commit_message(workspace.name, moved, clock=self.client.clock),
                user_name=user_name,
                user_email=user_email,
            )
            committed = True
        if not committed:
            try:
                self.client.git_runner.rev_parse_head(mount)
            except GitSyncError:
                # Adopted, and nothing has ever been committed here yet —
                # `current_branch`/`push` below both need a real commit to
                # resolve HEAD against, and `git rev-parse --abbrev-ref
                # HEAD` raises outright on an unborn branch rather than
                # answering "none". A `memory push` (or `memory reboot`,
                # which folds via this same method) before the first
                # `self-history add` must be a no-op here, not a crash —
                # additive, the same stance a workspace that never adopted
                # self-history at all already gets from the check above.
                return None
        branch = self.client.git_runner.current_branch(mount)
        self.client.git_runner.push(mount, ref_name=branch, set_upstream=True)
        self.client._log_event("self_history_push", mount=mount, branch=branch, committed=committed)
        return {
            "mount": str(mount),
            "branch": branch,
            "committed": committed,
            "recorded": moved,
        }

    def memory_push(self, cgshome: str | Path, *, message: str | None = None) -> dict[str, Any]:
        """Fold what has accumulated since the last push, commit it, and send it.

        Offline is not a failure mode, it is the normal case: everything a
        memory records is written locally first and pushed when somebody
        asks. So this is a command, never automatic, and a machine with no
        network keeps a complete, valid, verifiable memory without it.

        Folding (:meth:`_fold_memory_pending`) is part of what "send what
        the memory gained" already means, not a step the caller has to
        remember to run first — `.cgitsync`'s pending content only ever
        moves into the mount here, and only here.

        When self-history has been adopted (AgentReport WP2), its own fold
        and push run **first** — leaf before parent, as every tree-wide
        operation does — and are a no-op when its mount has no `.git`.

        A defaulted memory (`default_memory.py`) is folded and committed
        here but never pushed, and never has self-history: both are a
        developer's privilege, stated in a `.cgs`.
        """
        workspace = Path(cgshome)
        defaulted = DefaultMemory(self.client).is_defaulted(workspace)
        if not defaulted:
            self._push_self_history(workspace)
        mount = MemoryRepository(workspace).mount_path()
        if not (mount / ".git").exists():
            raise GitSyncError(
                f"{mount} is not a repository yet. Run 'cgitsync memory init' for the "
                "entry that mounts one, then 'cgitsync memory clone'."
            )
        status = self.client.memory_status(workspace)
        self.client._fold_memory_pending(MemoryRepository(workspace).pending_path(), mount)
        pending = MemoryRepository.uncommitted_paths(self.client.git_runner.status_porcelain(mount))
        committed = False
        if pending:
            self.client.git_runner.stage_all(mount)
            MasterConfig.load(workspace)
            user_name, user_email = MasterConfig.resolve_identity(mount, self.client.git_runner)
            self.client.git_runner.commit(
                mount,
                message
                or MemoryRepository.commit_message(Path(str(status["cgshome"])).name, int(status["states"]), int(status["entries"]), clock=self.client.clock),
                user_name=user_name,
                user_email=user_email,
            )
            committed = True
        branch = self.client.git_runner.current_branch(mount)
        if not defaulted:  # a defaulted memory is never published
            self.client.git_runner.push(mount, ref_name=branch, set_upstream=True)
        self.client._log_event("memory_push", mount=mount, branch=branch, committed=committed, pushed=not defaulted)
        return {
            "mount": str(mount),
            "branch": branch,
            "committed": committed,
            "pushed": not defaulted,
            "recorded": len(pending),
            "states": status["states"],
            "entries": status["entries"],
        }

    def self_history_add(
        self,
        cgshome: str | Path,
        *,
        ticket: str,
        goal: str,
        action: str,
        worker: self_history_store.AgentInfo,
        orchestrator: self_history_store.AgentInfo,
        conformity: self_history_store.ConformityScore,
        state_before: str = "",
        state_after: str = "",
        repos_written: Sequence[tuple[str, str]] = (),
        lint_passed: bool | None = None,
        tests_passed: bool | None = None,
        pushed: bool = False,
        pushed_reason: str = "",
    ) -> Path:
        """Write one self-history record to the pending half (AgentReport WP1).

        ``ticket``/``goal``/``action``/``worker``/``orchestrator``/
        ``conformity``/``state_before``/``pushed``/``pushed_reason`` are the
        orchestrator's own account of the work — declared, not observed,
        per the ticket's §1 split. Three facts this method fills in
        itself, because the tool can check them directly and an agent
        should not have to (or be trusted to) type them by hand:

        - ``contract`` — the current signed
          :class:`~ComplexGitSync.memory.agent_contract.AgentContractRecord`'s
          own hash, from ``.agent/.distant/dev-sync/agent-contracts/current``;
          empty when nothing is signed (AgentContract D4: absent, not fatal).
        - ``checks.status_errors`` — this workspace's own ``errors=`` count,
          from the same view ``status`` prints, when a tree is loaded.
        - ``repos_written`` (AgentReport WP4/D5) — when both ``state_before``
          and ``state_after`` resolve, **observed** by diffing the two
          States' own per-repository ``commit_sha`` (`_repos_written_between`):
          any repository whose commit changed between them was written, and
          its scope is read from the same ``private``/``writable`` flags
          `cgitsync status` labels a row with. The *argument* still exists
          for the one case this cannot cover — no ``state_before`` to diff
          from — where the orchestrator's own account is recorded as given,
          same as before.

        ``lint_passed``/``tests_passed`` stay caller-supplied, permanently,
        by design rather than by omission: whether ``pixi run lint``/``pixi
        run test`` passed is a fact about a process outside this tool's own
        reach, and Ring confinement keeps ``subprocess`` inside
        ``git_runner.py`` alone, which runs Git, not Pixi. No future version
        of this method can observe it without breaking that confinement, so
        this is not open work — see the AgentReport ticket's own note.

        ``state_after`` is observed too, when not given: the ledger's own
        most recent entry, folded and pending merged — "the state after"
        is exactly what the ledger says is current at the moment this
        method runs. ``state_before``/``state_after`` are otherwise
        **verified against the ledger** (WP3), not merely shape-checked:
        each, if given, must name a real ledger entry whose State is on
        disk and still hashes to its own name (`_resolve_ledger_state` —
        the same three questions `verify`'s own `MISSING_STATE`/
        `STATE_DIGEST_MISMATCH` findings ask). A citation that does not
        resolve raises rather than being recorded as though it were fact —
        AgentReport's own acceptance criterion is that both States *name*
        real history, not merely look like a state id.
        """
        workspace = Path(cgshome)
        DefaultMemory(self.client).refuse_self_history(workspace)
        cgitsync_dir = workspace / ".cgitsync"
        pending_dir = cgitsync_dir / self_history_store.SELF_HISTORY_PENDING_DIR_NAME
        contract = ""
        try:
            dev_sync_dir = workspace / ".agent" / ".distant" / "dev-sync"
            current_contract = AgentContractRecord.read_current(dev_sync_dir)
            if current_contract is not None:
                contract = current_contract.digest()
        except (OSError, ValueError):
            contract = ""
        status_errors: int | None = None
        try:
            view = self.client._reporting._collect_status()
            if not view.is_empty:
                status_errors = view.counts.errors
        except (GitSyncError, RuntimeError):
            status_errors = None
        ledger_entries = PendingMemory(cgitsync_dir).read_ledger_entries()
        if not state_after and ledger_entries:
            state_after = ledger_entries[-1].state_id
        for label, value in (("state_before", state_before), ("state_after", state_after)):
            if value and MemoryFacts.resolve_ledger_state(cgitsync_dir, value) is None:
                raise GitSyncError(
                    f"{label}={value!r} does not resolve in the ledger: no entry "
                    "names it, its State is not on disk, or its content no longer "
                    "hashes to its own name. self-history only cites States the "
                    "ledger can actually verify."
                )
        observed_repos_written = None
        if state_before and state_after:
            before_hash = MemoryStates.parse_hash(state_before)
            after_hash = MemoryStates.parse_hash(state_after)
            if before_hash is not None and after_hash is not None:
                observed_repos_written = MemoryFacts.repos_written_between(
                    cgitsync_dir, workspace, before_hash, after_hash
                )
        resolved_repos_written = (
            tuple(observed_repos_written)
            if observed_repos_written is not None
            else tuple(repos_written)
        )
        record = self_history_store.SelfHistoryRecord(
            ticket=ticket,
            goal=goal,
            action=action,
            worker=worker,
            orchestrator=orchestrator,
            conformity=conformity,
            recorded_at=self.client.clock.now().isoformat(),
            state_before=state_before,
            state_after=state_after,
            contract=contract,
            repos_written=resolved_repos_written,
            lint_passed=lint_passed,
            tests_passed=tests_passed,
            status_errors=status_errors,
            pushed=pushed,
            pushed_reason=pushed_reason,
        )
        path = record.write(pending_dir)
        self.client._log_event("self_history_add", path=path, ticket=ticket, contract=contract)
        return path

    def memory_reboot(self, cgshome: str | Path) -> dict[str, Any]:
        """Close this memory's current chapter and open a fresh one, keeping the old.

        Four steps (`memory-dev_1-4_MemoryReboot_DevPlanTicket.md` §1), in
        this order, touching nothing but the memory itself:

        1. Whatever `.cgitsync` is holding pending is folded in and pushed
           under the branch's current name — `memory_push`'s own fold and
           push, reused rather than duplicated, so nothing recorded since
           the last push is lost to the reboot (§5 D6).
        2. The tree's current shape is exported — `to_cgs()` against the
           loaded `.gts`, never a hand-authored file — to a permanent,
           versioned `.cgitsync/.memory/.cgs/<project>-v<N>.cgs`, committed
           and pushed by the same call as step 1.
        3. The branch is archived: pushed to origin under
           `<branch>.archived-<YYYYMMDD>` *before* the old name is removed
           from origin, never the reverse, so the commits are always
           reachable under some name on the remote — then renamed locally
           to match.
        4. A fresh branch is created under the original name; its States,
           ledger, commit logs and run logs are cleared, so the new
           branch's first commit is a true beginning. `.cgs/`'s versioned
           exports (step 2, and every export before it) are the one thing
           *not* cleared — §2 calls that directory a permanent, ordered
           record this reboot is not exempt from. One fresh State is then
           written, committed, and **pushed**: step 3 already removed
           *current_branch* from origin, so a second machine bootstrapping
           before this push lands on a branch (`fallback_branch`) that
           never held this project's `.cgs` at all — the field failure a
           reboot done right before switching machines actually produced.
           Pushing here, rather than waiting for the next ordinary
           `memory push`, is what bounds that window to this method.

        Raises `GitSyncError` when the mount is not a repository yet
        (`memory adopt` first), and when today's archived name already
        exists — a second reboot the same day needs the owner to say what
        to call it, rather than silently colliding with the first.
        """
        workspace = Path(cgshome)
        mount = MemoryRepository(workspace).mount_path()
        DefaultMemory(self.client).require_published(workspace, "memory reboot")

        registry = self.client._document_loader.load_gts(discover_gts_path(str(workspace)), unmeasured=True)
        project_name = registry.get(ROOT_REPO_ID).name

        folded = self.client._fold_memory_pending(MemoryRepository(workspace).pending_path(), mount)

        cgs_dir = mount / ".cgs"
        cgs_dir.mkdir(parents=True, exist_ok=True)
        next_version = MemoryFacts.next_reboot_cgs_version(cgs_dir, project_name)
        exported_path = cgs_dir / f"{project_name}-v{next_version}.cgs"
        MemoryStates.write_atomically(exported_path, registry.to_cgs().to_toml)

        pushed = self.client.memory_push(
            workspace,
            message=f"{project_name} memory reboot: exporting v{next_version} before archiving",
        )
        current_branch = str(pushed["branch"])

        archived_branch = f"{current_branch}.archived-{self.client.clock.now():%Y%m%d}"
        remote_url = self.client.git_runner.remote_get_url(mount, "origin") or ""
        if self.client.git_runner.local_branch_exists(mount, archived_branch) or (
            remote_url and self.client.git_runner.remote_branch_exists(remote_url, archived_branch)
        ):
            raise GitSyncError(
                f"{archived_branch} already exists — this memory was already rebooted "
                "today. Archive it under another name yourself first, or wait for tomorrow."
            )

        self.client.git_runner.push_ref_as(mount, current_branch, archived_branch, remote="origin")
        self.client.git_runner.delete_remote_branch(mount, current_branch, remote="origin")
        self.client.git_runner.rename_branch(mount, current_branch, archived_branch)

        self.client.git_runner.create_orphan_branch(mount, current_branch)
        for cleared in (*self.client._FOLD_SUBDIRS, COMMIT_LOG_DIR_NAME, "logs"):
            # Every fold subdirectory, and the `logs/` older folds left behind (never
            # folded any more, but still tracked on this branch). `.cgs` is not one
            # of them: the export above lives there, and a reboot must not erase it.
            self.client.git_runner.remove_tracked_path(mount, cleared)

        # Clearing `state/` leaves nothing anywhere `discover_gts_path()`
        # can find — the pending half was already empty (step 1 folded
        # it), so a workspace rebooted this way could not even run
        # `cgitsync status` afterward. `self.registry` is still the tree
        # this method loaded at the top, so writing it now gives the
        # workspace a State to resume from immediately — one State, dated
        # now, not the history just archived.
        self.client.write_gts_snapshot(command_origin="memory_reboot")

        # An orphan branch with nothing committed has no HEAD to read —
        # `git rev-parse HEAD` fails, and `cgitsync status` reported that
        # as `error`/`error` rather than as the healthy, just-rebooted
        # branch it actually was. Folding and committing the one State
        # just written gives the branch a real HEAD before this method
        # returns.
        self.client._fold_memory_pending(MemoryRepository(workspace).pending_path(), mount)
        if MemoryRepository.uncommitted_paths(self.client.git_runner.status_porcelain(mount)):
            self.client.git_runner.stage_all(mount)
            MasterConfig.load(workspace)
            user_name, user_email = MasterConfig.resolve_identity(mount, self.client.git_runner)
            self.client.git_runner.commit(
                mount,
                f"{project_name} memory reboot: first State of a fresh chapter",
                user_name=user_name,
                user_email=user_email,
            )

        # Step 3 already deleted *current_branch* from origin as half of
        # the archive rename — from that moment until this push, origin
        # has no ref under the memory's own name at all. Pushing this
        # commit now, rather than leaving it to whenever the next ordinary
        # `memory push` happens to run, is what keeps that window to the
        # width of this method rather than to however long the owner goes
        # before their next push — the field failure a bootstrap on a
        # second machine hit when that window was left open across a
        # machine switch. `set_upstream=True` mirrors `memory_push`'s own
        # push exactly, so the branch is `synced`, not merely `ahead`, the
        # moment this method returns.
        self.client.git_runner.push(mount, ref_name=current_branch, set_upstream=True)

        self.client._log_event(
            "memory_reboot",
            mount=mount,
            archived=archived_branch,
            exported=exported_path,
            branch=current_branch,
        )
        return {
            "mount": str(mount),
            "folded": folded,
            "exported": str(exported_path),
            "archived_from": current_branch,
            "archived_to": archived_branch,
            "branch": current_branch,
        }

    def memory_status(self, cgshome: str | Path) -> dict[str, Any]:
        """What this workspace remembers, in one answer.

        How many States it holds, how long its chain is, when it was last
        written, which of the four verification answers it is in, and the
        toolchain its first and last entries record — the interesting
        question being whether those two differ.
        """
        workspace = Path(cgshome)
        entries = PendingMemory(workspace / ".cgitsync").read_ledger_entries()
        report = self.client.verify(workspace)
        states = PendingMemory(workspace / ".cgitsync").state_files()
        return {
            "cgshome": str(workspace.resolve()),
            "notice": MemorySetup(self.client).notice(workspace),
            "verification": report.state.name.lower().replace("_", "-"),
            "findings": len(report.findings),
            "states": len(states),
            "entries": len(entries),
            "last_recorded_at": entries[-1].recorded_at if entries else None,
            "genesis_toolchain": dict(entries[0].toolchain) if entries else {},
            "latest_toolchain": dict(entries[-1].toolchain) if entries else {},
        }

    def _verify_relocations(self, cgitsync_dir: Path, entries: Sequence[Any]) -> list[tuple[int, Finding, str]]:
        """`UNRESOLVED_RELOCATION` for each recorded move whose asset is not where it says.

        A relocation names its repository, not a path, so the tree is
        needed to ask Git. When none is loaded, the State the ledger last
        recorded is loaded for it; when even that fails, nothing is
        checked, because "could not ask" is not "asked, and it was missing".
        """
        if not any(entry.relocations for entry in entries):
            return []
        if self.client.registry is None:
            latest = PendingMemory(cgitsync_dir).current_state_from_ledger()
            try:
                if latest is not None:
                    self.client.load_gts(latest)
            except (ComplexGitSyncError, OSError, ValueError):
                return []
        if self.client.registry is None:
            return []
        repos = {repo.name: repo for repo in self.client.registry.values()}

        def resolves(relocation: Relocation) -> bool:
            repo = repos.get(relocation.to.partition(":")[0])
            if repo is None or not repo.absolute_path.is_dir():
                return False
            return AncestorOperation.resolves(
                self.client.git_runner, repo.absolute_path, relocation, remote=repo.remote_name or "origin"
            )

        return ChainVerifier.check_relocations(entries, resolves)

    def memory_as_of(self, cgshome: str | Path, moment: str, *, branch: str | None = None) -> dict[str, Any]:
        """What was this tree at *moment*? The State the chain recorded at or before it.

        Read-only and local: it reads the same folded-and-pending ledger every
        `memory` command reads. ``entry`` is the last entry, in chain order, recorded at
        or before *moment* (see `AsOf`), or ``None`` when nothing was recorded yet.

        ``reliable`` is false whenever the chain does not verify cleanly, and
        ``history`` says why — notably ``time-inconsistent``, where a clock moved
        backwards and "at or before" can name an entry the workspace did not hold at
        that moment. The answer is still returned, flagged, never silently confident.

        *branch* reads another chapter instead, from Git: its branch, its
        closed name, or the copy ``ancestors`` keeps once it is deleted;
        ``read_from`` says which.
        """
        read_from = "this workspace"
        if branch is None:
            entries = PendingMemory(Path(cgshome) / ".cgitsync").read_ledger_entries()
        else:
            entries, source = MemoryChapters(self.client).entries(Path(cgshome), branch)
            read_from = source.read_from
        resolved = AsOf.parse_moment(moment)
        report = ChainVerifier.verify(entries)
        chosen = AsOf.select(entries, resolved)
        return {
            "moment": resolved,
            "entry": None if chosen is None else {"seq": chosen.seq, "recorded_at": chosen.recorded_at, "command": chosen.command, "state": MemoryStates.parse_hash(chosen.state_id) or ""},
            "first_recorded_at": AsOf.first_recorded_at(entries),
            "reliable": report.is_verified,
            "history": report.state.name.lower().replace("_", "-"),
            "findings": [f"seq={seq} {finding.name}: {detail}" for seq, finding, detail in report.findings[:5]],
            "read_from": read_from,
        }

    def memory_list(self, cgshome: str | Path, *, branch: str | None = None) -> list[dict[str, Any]]:
        """Every State this workspace holds, with what the ledger says about it.

        One row per State on disk, newest recording first. ``recorded_at``
        and ``commands`` come from the entries that name it: a State seen
        three times has one row and three commands, because being seen twice
        is two ledger entries pointing at one name.

        A State no entry records still appears, with no timestamp. It is
        there, and saying so is more useful than hiding it — ``verify``
        reports it as an orphan.

        *branch* lists another chapter's States, read from Git as
        :meth:`memory_as_of` reads it; each row then says where.
        """
        if branch is not None:
            return MemoryChapters(self.client).list_rows(Path(cgshome), branch)
        workspace = Path(cgshome)
        cgitsync_dir = workspace / ".cgitsync"
        entries = PendingMemory(cgitsync_dir).read_ledger_entries()
        seen: dict[str, list[Any]] = {}
        for entry in entries:
            state_hash = MemoryStates.parse_hash(entry.state_id)
            if state_hash is not None:
                seen.setdefault(state_hash, []).append(entry)

        rows: list[dict[str, Any]] = []
        for snapshot in PendingMemory(cgitsync_dir).state_files():
            recorded = seen.pop(snapshot.stem, [])
            rows.append(
                {
                    "state": snapshot.stem,
                    "path": str(snapshot),
                    "recorded_at": recorded[-1].recorded_at if recorded else None,
                    "commands": [entry.command for entry in recorded],
                }
            )
        for state_hash, recorded in seen.items():
            # Recorded, and not on disk. `verify` calls this MISSING_STATE;
            # listing it is how a reader finds out which one.
            rows.append(
                {
                    "state": state_hash,
                    "path": None,
                    "recorded_at": recorded[-1].recorded_at,
                    "commands": [entry.command for entry in recorded],
                }
            )
        rows.sort(key=lambda row: (row["recorded_at"] or "", row["state"]), reverse=True)
        return rows

    def memory_self_history(self, cgshome: str | Path) -> list[dict[str, Any]]:
        """Every self-history record this workspace holds, oldest first.

        Consultation only, folded and pending merged — see
        :meth:`self_history_add` for how a record gets here, and
        ``self_history.read_records`` for the merge itself. A workspace
        that has never adopted self-history, or that has adopted it but
        written nothing yet, answers with an empty list rather than an
        error: this command is additive, like everything else about it.
        """
        cgitsync_dir = Path(cgshome) / ".cgitsync"
        return [record.to_dict() for record in SelfHistoryRecord.read_all(cgitsync_dir)]

    def memory_show(self, cgshome: str | Path, state: str) -> dict[str, Any]:
        """One State: what it recorded, every entry that names it, and what
        was committed.

        *state* may be the full content hash or any unambiguous prefix of
        one — a 64-character name is not something anybody retypes. It may
        also be pasted verbatim from wherever a State's name was printed
        rather than typed by hand: a bare hash the way `memory show` itself
        wants it, the full ``state(<hash>)`` id the way a ledger entry or
        ``self-history add --state-before`` prints it, or the ``state=``
        label `memory explore --timeline` prints it under — a real incident
        this exact copy-paste produced, since that label reads exactly like
        the argument to give back. `_normalise_state_argument` strips
        whichever of those two decorations is present; a bare prefix with
        neither passes through unchanged, as before.

        The commit messages come back whole. Deciding that a long one should
        be shown as a single line is the printer's business, not this
        method's: a caller reading the memory from Python wants the message
        that was written, not the one that fitted.

        ``tree`` is this State's own topology, rendered exactly the way
        ``cgitsync view-tree`` renders the live one (`format_view_tree`) —
        but built from *this* `.gts` document, not whatever is currently
        loaded, so it shows the tree as it was at this State, not as it is
        now. A full Environment's own machine/tools/credentials/manifests
        detail is `memory show env=<ref>`'s job (:meth:`memory_show_environment`),
        not this method's — ``environments`` here stays the bare reference
        it always was.
        """
        workspace = Path(cgshome)
        cgitsync_dir = workspace / ".cgitsync"
        normalised_state = MemoryFacts.normalise_state_argument(state)
        matches = sorted(
            snapshot
            for snapshot in PendingMemory(cgitsync_dir).state_files()
            if snapshot.stem.startswith(normalised_state)
        )
        if len(matches) > 1:
            names = ", ".join(snapshot.stem[:12] for snapshot in matches)
            raise GitSyncError(f"{state!r} matches more than one State: {names}.")
        chapters = MemoryChapters(self.client)
        if matches:
            stem, document, read_from, shown_path = matches[0].stem, GtsDocument.from_toml(matches[0]), "this workspace", str(matches[0])
        else:
            # Not on disk: a chapter whose branch was closed or deleted may
            # still hold it, on its branch or through `ancestors`.
            found = chapters.find_state(workspace, normalised_state) if (MemoryRepository(workspace).mount_path() / ".git").exists() else None
            if found is None:
                raise GitSyncError(
                    f"no State under {cgitsync_dir} (folded or pending), nor in any chapter of the memory "
                    f"repository, begins with {state!r}. "
                    "'cgitsync memory explore --timeline' lists every State's real hash prefix."
                )
            stem, document, read_from = found
            shown_path = f"(in Git: {read_from})"
        recorded = [
            entry
            for entry in PendingMemory(cgitsync_dir).read_ledger_entries()
            if MemoryStates.parse_hash(entry.state_id) == stem
        ]
        log = PendingMemory(cgitsync_dir).read_commit_log(stem)
        committed: dict[int, list[dict[str, Any]]] = {}
        for row in log["commit"]:
            committed.setdefault(int(row.get("entry", 0)), []).append(row)
        published_shas = {str(row.get("sha", "")) for row in log["published"]}
        environments = EnvironmentStore.resolve_references(PendingMemory(cgitsync_dir).dirs(), (entry.environment for entry in recorded))
        kept_on_ancestors: list[str] = []
        try:
            state_tree = RegistryTranslator.from_gts_document(document, tree_root=workspace)
            tree = format_view_tree(state_tree)
            kept_on_ancestors = chapters.commits_only_on_ancestors(workspace, list(state_tree.values()))
        except (ValueError, KeyError, TypeError):
            # A snapshot old enough to carry its own absolute paths, taken
            # on a different machine, can name a repository this one never
            # had — the same "read what was there" spirit `verify` already
            # applies to a State's own content: showing it is not
            # conditional on this machine being able to rebuild it.
            tree = ""
        return {
            "state": stem,
            "path": shown_path,
            "read_from": read_from,
            "commits_on_ancestors": kept_on_ancestors,
            "project": document.read("project.name"),
            "lifecycle_state": document.read("tree_state.lifecycle_state"),
            "repos": len(document.repo_states),
            "integrity_schema": document.integrity_schema, "gittree_root": document.gittree_root,
            "tree": tree,
            "environments": environments,
            "entries": [
                {
                    "seq": entry.seq,
                    "recorded_at": entry.recorded_at,
                    "command": entry.command,
                    "outcome": entry.outcome,
                    "toolchain": dict(entry.toolchain),
                    "commits": [
                        {**row, "published": str(row.get("sha", "")) in published_shas}
                        for row in committed.get(entry.seq, [])
                    ],
                }
                for entry in recorded
            ],
            "published": list(log["published"]),
        }

    def memory_show_environment(self, cgshome: str | Path, env_ref: str) -> dict[str, Any]:
        """One Environment record, in full — ``memory show env=<ref>``.

        *env_ref* may be the full content hash, any unambiguous prefix of
        one, or that hash pasted back decorated the way `memory show`
        itself prints it (``env(<hash>)``) or the way its own CLI argument
        is spelled (``env=<hash>``) — `_normalise_environment_argument`
        strips whichever is present, the same accommodation `memory_show`
        already makes for a State reference.

        This is the one place the full machine/tools/credentials/manifests
        detail lives: `memory_show` (a State) only ever cites an
        Environment by its bare reference, because a State's own topology —
        `format_view_tree`'s job there — is the thing that answers "what
        was this tree", not "what ran it".
        """
        workspace = Path(cgshome)
        cgitsync_dir = workspace / ".cgitsync"
        normalised_ref = MemoryFacts.normalise_environment_argument(env_ref)
        matches = sorted(
            path
            for path in PendingMemory(cgitsync_dir).environment_files()
            if path.stem.startswith(normalised_ref)
        )
        if not matches:
            raise GitSyncError(
                f"no Environment under {cgitsync_dir} (folded or pending) begins with "
                f"{env_ref!r}. 'cgitsync memory show state=<ref>' names the Environment "
                "each State cites."
            )
        if len(matches) > 1:
            names = ", ".join(path.stem[:12] for path in matches)
            raise GitSyncError(f"{env_ref!r} matches more than one Environment: {names}.")

        path = matches[0]
        record = EnvironmentStore.read(path)
        return {
            "id": EnvironmentStore.format_id(path.stem),
            "path": str(path),
            "record": record.to_dict(),
        }

    def memory_explore(
        self,
        cgshome: str | Path,
        *,
        branch: str | None = None,
        timeline: bool = False,
    ) -> dict[str, Any]:
        """A memory a person can actually read, by branch or in ledger order.

        The default view answers *what a colleague pulling this branch
        would see*: one row per commit this memory recorded as published,
        newest push first. ``timeline=True`` answers instead *everything
        that happened, in the order it did*: one row per ledger entry —
        `checkout`, `merge`, `push` included, which the commit-only view
        drops.

        Both read the same two sources every other `memory` command does —
        `.cgitsync/.memory` (folded) and `.cgitsync` itself (pending) — so
        a memory that has never been pushed still explores; nothing here
        needs a mount to exist.

        *branch* names a memory branch other than the one checked out on
        this disk. MemoryExplore §D1 keeps this to local reads only for
        now, so a name that does not match what is actually checked out at
        the mount is refused by name, naming `memory clone --branch` as
        the way to bring that branch here first — silently answering for
        the wrong branch would be worse than saying so.
        """
        workspace = Path(cgshome)
        cgitsync_dir = workspace / ".cgitsync"
        mount = MemoryRepository(workspace).mount_path()
        current_branch = (
            self.client.git_runner.current_branch(mount) if (mount / ".git").exists() else None
        )
        if branch is not None and branch != current_branch:
            # Another chapter is read from Git when this clone reaches it —
            # its branch, its closed name, or `ancestors` — as a timeline of
            # its entries; its commit messages need it checked out.
            try:
                entries, source = MemoryChapters(self.client).entries(workspace, branch)
            except GitSyncError:
                raise GitSyncError(
                    f"branch {branch!r} is not checked out at {mount}, and no branch, closed branch or "
                    f"ancestors copy here holds it. Bring it onto this disk first with "
                    f"'cgitsync memory clone --branch {branch}'."
                ) from None
            rows = [{"seq": e.seq, "recorded_at": e.recorded_at, "command": e.command, "outcome": e.outcome, "state": MemoryStates.parse_hash(e.state_id) or "", "commits": [], "published": []} for e in entries]
            return {"branch": branch, "read_from": source.read_from, "entries": rows}
        resolved_branch = branch or current_branch
        if timeline:
            return {"branch": resolved_branch, "entries": PendingMemory(cgitsync_dir).timeline()}
        return {"branch": resolved_branch, "commits": PendingMemory(cgitsync_dir).published_commits()}

    def verify(self, cgshome: str | Path, *, repair: bool = False) -> VerificationReport:
        """Say which of the four answers this workspace's history deserves.

        ``report.state`` is the answer — **verified**, **no history**,
        **legacy** or **corrupt** — and ``report.findings`` says why when it
        is the last one. The four are fixed by
        ``.agent/.local/.localSpec/AdditionalSpecs.md``, *The hash-chained ledger*.

        The distinction this method exists to make: an empty
        ``.cgitsync/lgr`` used to be reported as a clean chain, so the
        command answered "yes" for every workspace on earth, a tampered one
        included. Nothing writes that directory yet, which made the answer
        worthless everywhere. Now "I read a chain and it held" and "there was
        no chain to read" are different answers, and a workspace whose only
        history is the single-file ``.lgr`` register is told that its
        history is readable but not verifiable.

        What is checked when there *is* a chain: linkage (``BROKEN_LINK``),
        entry-hash integrity (``BAD_ENTRY_HASH``), sequence gaps and
        duplicates (``SEQ_GAP``/``SEQ_DUPLICATE``), and whether the cached
        ``HEAD`` agrees with the recomputed head (``HEAD_STALE``).

        Store-level checks (``MISSING_STATE``, ``ORPHAN_STATE``,
        ``STATE_DIGEST_MISMATCH`` — cross-referencing entries against the
        state directories on disk) became possible once a State was named by
        its content. ``COMMIT_LOG_MISMATCH`` and ``ORPHAN_COMMIT_LOG`` check
        the commit messages the same way: an entry carries the digest of the
        rows it wrote, so an edited log is caught by arithmetic rather than
        by trust.

        With ``repair=True``, a stale ``HEAD`` cache is corrected in place.
        Entries themselves are never rewritten or deleted — a broken chain
        is reported, not silently healed. A ledger that can be edited back
        into looking clean is evidence of nothing.
        """
        workspace = Path(cgshome)
        cgitsync_dir = workspace / ".cgitsync"
        entries = PendingMemory(cgitsync_dir).read_ledger_entries()
        report = ChainVerifier.verify(entries)

        if entries:
            # Whichever half currently holds the highest-seq entry is where
            # the HEAD cache that matters lives — `write_entry` always
            # updates it in the same directory it just wrote to, and a
            # fold moves both together, so this is never split across the
            # two halves.
            active_lgr_dir = PendingMemory(cgitsync_dir).current_ledger_dir()
            cached_head = LedgerStore(active_lgr_dir).read_head()
            true_head = LedgerStore(active_lgr_dir).recompute_head()
            if cached_head != true_head:
                report.findings.append((
                    entries[-1].seq,
                    Finding.HEAD_STALE,
                    f"cached HEAD={cached_head}, recomputed HEAD={true_head}",
                ))
            report.findings.extend(MemoryFacts.verify_states_on_disk(workspace, entries))
            report.findings.extend(MemoryFacts.verify_commit_logs(workspace, entries))
            report.findings.extend(self._verify_relocations(cgitsync_dir, entries))
            # The store checks run after verify_chain, so the verdict is
            # recomputed here rather than left at the chain's own — through
            # `resolve_state`, the same function the chain pass uses, so a
            # finding cannot mean one thing to one pass and something else
            # to the other. Which findings are fatal, which get their own
            # verdict, and which are reported without changing it (an
            # orphan State, for one) is decided there and only there.
            report.state = ChainVerifier.resolve_state(report.findings)
            if repair:
                LedgerStore(active_lgr_dir).verify_and_repair_head()
        elif LocalGitRegister.exists_in(workspace):
            report.state = HistoryState.LEGACY

        return report

    def _append_ledger_entry(
        self,
        cgitsync_dir: Path,
        *,
        command_origin: str,
        state_hash: str,
        state_path: Path,
        tree_root: Path,
        commit_log: str = "",
        release: tuple[tuple[str, str], ...] | None = None,
        relocations: Sequence[Relocation] = (),
    ) -> None:
        """Record in the chain that this State was seen, now, by these tools.

        This writes the chain that makes ``cgitsync verify`` meaningful.

        **Recording must never cost the command its work.** A snapshot that
        stays written if the ledger append fails. The failure is logged and
        the next `verify` reports the gap.

        Always writes into the *pending* half (``cgitsync_dir / "lgr"``,
        never ``.memory/lgr`` — that only ever gains content through
        ``memory push``'s own fold), but chains from whichever entry is
        actually last, folded or pending — `append_entry`'s own
        single-directory read would otherwise treat a workspace that just
        folded as having no history at all, and start a new genesis entry
        over real, already-folded history.
        """
        try:
            existing_entries = PendingMemory(cgitsync_dir).read_ledger_entries()
            environment_id = ""
            try:
                observed = self.client.environment()
                EnvironmentStore(cgitsync_dir).write(observed)
                environment_id = EnvironmentStore.format_id(observed.digest())
            except (ComplexGitSyncError, OSError, ValueError) as exc:
                self.client._log_event(
                    "environment_record_failed",
                    level=logging.WARNING,
                    error=str(exc),
                )
            entry = LedgerEntry.build_next(existing_entries[-1] if existing_entries else None, command=command_origin, argv=ArgvScrubber.scrub(sys.argv[1:], tree_root=tree_root), state_id=MemoryStates.format_id(state_hash), state_dir=str(state_path.parent.name), outcome="ok", clock=self.client.clock, toolchain=tuple(sorted(Toolchain.read(self.client.git_runner).items())), commit_log=commit_log, environment=environment_id, release=release or (), relocations=relocations)
            LedgerStore(cgitsync_dir / "lgr").write_entry(entry)
        except (LedgerStoreError, OSError) as exc:
            self.client._log_event(
                "ledger_append_failed",
                level=logging.WARNING,
                register_path=cgitsync_dir / "lgr",
                error=str(exc),
            )
            return
        self.client._log_event(
            "ledger_append",
            register_path=cgitsync_dir / "lgr",
            seq=entry.seq,
            state_id=entry.state_id,
            command=command_origin,
        )

    def _refresh_memory_mount_state(self, registry: WorkingGitTree) -> None:
        """Read the memory mount's *actual* branch and HEAD, in place.

        Every other repository's recorded `commit_sha` is kept fresh by the
        action that touched it — `checkout`, `commit`, `push` each refresh
        the repos they visited before a State is written. `memory push`
        touches the mount too, but through `git_runner` calls of its own,
        not through `commit_tree`/`push_tree` — so nothing else ever
        refreshes the *registry's* record of it, and a State whose recorded
        commit for the memory never moves would disagree with `status`'s
        own live reading of it the moment a fold first moves it
        (`memory-dev_MemoryRecordedRefresh`). Since
        `memory-dev_WorkingTransitionState`, the mount is an ordinary
        private/local repository everywhere else — `merge`/`checkout`/
        `pull` all reach it normally, and their own per-repo refresh is
        what marks it `READY`; this call only keeps its `commit_sha`/branch
        honest between one `memory push` and the next State write.

        Read-only: `git rev-parse`/`current branch`, the same questions
        `status` already asks. Silently does nothing when the mount does
        not exist yet, is not a repository yet (`memory adopt` not run),
        or — freshly adopted, nothing committed — has no HEAD to read.
        """
        MemorySetup(self.client).before_recording(registry)  # runs before every State is written
        for entry in registry.values():
            if entry.relative_path != Path(MOUNT_PATH):
                continue
            if not (entry.absolute_path / ".git").is_dir():
                continue
            try:
                entry.commit_sha = self.client.git_runner.rev_parse_head(entry.absolute_path)
            except GitSyncError:
                continue
            branch = self.client.git_runner.current_branch(entry.absolute_path)
            if branch:
                entry.current_ref_kind = RefKind.BRANCH
                entry.current_ref_name = branch
                entry.resolved_ref_kind = RefKind.BRANCH
                entry.resolved_ref_name = branch

    def get_ledger_history(self, register_path: str | Path) -> list[dict[str, Any]]:
        """Return all ledger events for *register_path* in topological DAG order.

        Parameters
        ----------
        register_path:
            Path to the project-local ``.lgr`` register file (e.g.
            ``<project-root>/demo.lgr``).

        Returns
        -------
        list[dict[str, Any]]
            Ledger events ordered parents-first.  Each event contains the
            fields defined by the ``.lgr`` ledger schema: ``sync_id``,
            ``parent_sync_ids``, ``operation``, ``timestamp``, ``actor``,
            ``workspace_hash``, ``gts_snapshot_id``, and ``affected_repos``.
        """
        return SyncLedger(register_path).history()

    def replay_ledger(self, register_path: str | Path) -> list[dict[str, Any]]:
        """Return ledger events in topological order for deterministic replay.

        Reconstructs the workspace evolution history from the first recorded
        sync operation to the last.  Alias for :meth:`get_ledger_history`.

        Parameters
        ----------
        register_path:
            Path to the project-local ``.lgr`` register file.
        """
        return SyncLedger(register_path).replay()


__all__ = ["MemoryCommands"]

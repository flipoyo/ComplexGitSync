"""omniscience_control — `cgitsync omniscience register`: validate one registration, append it, extend the index.

Ring: 3
Contract: the private ``IntegrityControl``. Register a State of a developer
    tree in the shared register: build its record, refuse one that does not
    validate, commit it to the project's branch and push it fast-forward only
    (retrying when somebody else appended first, never forcing), then extend
    the global checkpoint. Observations are `OmniscienceObserver`'s.
Imports: errors, git_tree, tree, omniscience_appender, omniscience_checkpoints, omniscience_commands, omniscience_reader, omniscience_record, omniscience_repository, operating_space

What is checked before anything is written: the record's hash is its file's
name (by construction), its ``os_id`` is the id of its project, its ``H_STATE``
and ``H_GITTREE`` are what the State on disk recomputes to, and its memory
commit is held by the memory's remote. Whether the contributor may push to the
project's branch is the provider's to answer, by refusing the push.

Running it again after a crash between the commit and the push finds the
record's file not on the remote and writes it once; running it again after a
success finds the file there and does nothing. The same registration is the
same file, so two contributors registering one State do not make two records.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomli_w

from ..errors import ConfigValidationError, GitSyncError
from ..operating_space.operating_space import OperatingSpace
from ..tree.git_tree import TreeProfile
from ..tree.memory.ledger_store import LedgerStore
from ..tree.memory.repository import MemoryRepository
from .omniscience_appender import RegisterAppender
from .omniscience_checkpoints import GlobalCheckpoints
from .omniscience_commands import OmniscienceCommands
from .omniscience_reader import REGISTER_DIR
from .omniscience_record import OmniscienceRecord
from .omniscience_repository import GLOBAL_BRANCH, OMNISCIENCE_MOUNT_PATH, OmniscienceRepository

if TYPE_CHECKING:
    from ..orchestre.client import ComplexGitSyncClient


class IntegrityControl:
    """Registers a State. Holds the client; everything else is read from the tree each time."""

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def mounted(self, command: str, cgshome: str | Path, *, writes: bool = True) -> Path:
        """The mounted register of this developer tree, or a refusal by name before anything is touched.

        Refused in a USER tree, with no ``.omniscience`` mount, with the mount
        declared read-only (when the command writes), or with uncommitted
        changes in it, which an append would have to carry or lose.
        """
        registry = self.client.get_dependency_registry()
        if registry.profile is not TreeProfile.DEV:
            raise GitSyncError(
                f"{command} was refused: this tree holds no private repository, so it is a user's tree, "
                "and Omniscience is for developer trees. Nothing was changed."
            )
        mount = Path(cgshome) / OMNISCIENCE_MOUNT_PATH
        entry = next((e for e in registry.values() if e.relative_path == Path(OMNISCIENCE_MOUNT_PATH)), None)
        if entry is None or not (mount / ".git").exists():
            raise GitSyncError(f"{command} was refused: the register is not mounted at {mount}. Run 'cgitsync omniscience mount --contribute'.")
        if writes and not entry.writable:
            raise GitSyncError(f"{command} was refused: the register is mounted read-only. Run 'cgitsync omniscience mount --contribute' (and make the entry writable in the .cgs).")
        if self.client.git_runner.has_uncommitted_changes(mount):
            raise GitSyncError(f"{command} was refused: the register at {mount} has uncommitted changes. Commit or discard them first.")
        return mount

    def register(self, cgshome: str | Path, state: str | None = None) -> dict[str, Any]:
        """Register *state* (default: the memory's newest) and extend the global checkpoint.

        Returns the record's hash and file, whether a commit was pushed
        (``appended``, false when the record was already there), the commit
        it is on, ``retries``, and the ``checkpoint`` step's own answer, which
        carries ``status`` (``rewritten`` when the remote's index was rewritten:
        reported and never repaired), the files left ``unaccepted``, and why.
        """
        mount = self.mounted("omniscience register", cgshome)
        state = state or self._newest_state(Path(cgshome))
        record = OmniscienceRecord.from_dict(self.client.omniscience_record(cgshome, state)["record"])
        self.validate(record, Path(cgshome))
        branch = OmniscienceRepository.branch(record.project, record.project_branch)
        if branch == GLOBAL_BRANCH or record.project == GLOBAL_BRANCH:
            raise GitSyncError(f"{GLOBAL_BRANCH!r} is reserved for the register's index, so a project cannot be registered under it.")
        git = self.client.git_runner

        def build(tip: str | None) -> dict[str, str] | None:
            if tip and record.filename in git.tree_blobs(mount, tip, REGISTER_DIR):
                return None  # already registered: the same registration is the same file
            return {record.filename: tomli_w.dumps(record.to_dict())}

        appender = RegisterAppender(self.client, mount)
        appended = appender.append(branch, build, f"register {record.project} {record.project_branch} {record.state_id}")
        checkpoint = GlobalCheckpoints(self.client, mount).extend(verified=frozenset({record.record_hash}))
        appender.settle(branch)
        self.client._log_event("omniscience_register", record=record.record_hash, branch=branch, appended=appended["appended"], checkpoint=checkpoint["status"])
        return {
            "record_hash": record.record_hash,
            "filename": record.filename,
            "branch": branch,
            "appended": appended["appended"],
            "commit": appended["commit"],
            "retries": appended["retries"],
            "checkpoint": checkpoint,
        }

    def validate(self, record: OmniscienceRecord, cgshome: Path) -> None:
        """Refuse, naming the first thing wrong, a record that does not match the State, the id or the memory's remote."""
        if record.os_id != OperatingSpace.id_of(record.project):
            raise GitSyncError("this record was refused: its os_id is not the id of its project's name.")
        mount = MemoryRepository(cgshome).mount_path()
        document, state_hash = OmniscienceCommands._committed_state(mount, record.state_id.removeprefix("state(").removesuffix(")"))
        try:
            problems = document.integrity_mismatches()
        except ConfigValidationError as exc:
            raise GitSyncError(f"this record was refused: the State on disk cannot be hashed ({exc}).") from exc
        if problems or document.compute_snapshot_hash() != state_hash:
            raise GitSyncError(f"this record was refused: the State on disk no longer hashes to its name ({', '.join(kind for kind, _ in problems) or 'snapshot_hash'}).")
        if document.compute_gittree_root() != record.gittree_root:
            raise GitSyncError("this record was refused: its H_GITTREE is not the one the State on disk recomputes to.")
        git = self.client.git_runner
        url = git.remote_get_url(mount, "origin")
        if not url or not git.remote_holds_commit(url, record.memory_commit):
            raise GitSyncError(f"this record was refused: the memory's remote does not hold commit {record.memory_commit[:12]}. Run 'cgitsync memory push'.")

    def _newest_state(self, cgshome: Path) -> str:
        """The State the memory's last ledger entry recorded, as its hash."""
        entries = LedgerStore(MemoryRepository(cgshome).mount_path() / "lgr").read_all_entries()
        if not entries:
            raise GitSyncError("the memory has no ledger entry, so there is no State to register. Pass --state, or run 'cgitsync memory push'.")
        return entries[-1].state_id.removeprefix("state(").removesuffix(")")


__all__ = ["IntegrityControl"]

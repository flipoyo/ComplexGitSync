"""omniscience_memory — read a registered ``.memory`` at its immutable commit, and check it is the one the record names.

Ring: 3
Contract: the private half of ``Omniscience.memory`` and ``get_state``. From
    one accepted record, clone the memory repository it names into a
    temporary directory under the tree's ``.cgitsync/``, read the ledger and
    the State at the registered commit from Git's objects, check them against
    the record, remove the clone, and answer. Writes nothing to the source
    memory or to the register.
Imports: errors, gts_document, memory, merkle, memory_facts, omniscience_answer, omniscience_observation, omniscience_record, universal_clock

The order is the one the design fixed: the address, the clone (a refusal is
``unauthorized`` when the provider said so, else ``remote_unavailable``), the
commit (``commit_unreachable`` when the ref does not lead to it), the ledger
(``ledger_corrupt``), then the State and its links to the record
(``invalid_proof``). Nothing a record names reaches a command unchecked: only
a ``provider:owner/repo`` identifier is turned into an address, and a ref or
address starting with ``-`` is refused.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import ConfigValidationError, GitSyncError
from ..gts_document import GtsDocument
from ..memory.integrity import ChainVerifier, HistoryState
from ..memory.ledger_store import LedgerStore, LedgerStoreError
from ..memory.states import STATE_DIR_NAME, MemoryStates
from ..merkle import MerkleRoot
from ..omniscience_answer import Answer, MemoryDetail, OmniscienceStatus
from ..omniscience_observation import ObservationStatus
from ..omniscience_record import OmniscienceRecord
from ..universal_clock import SystemClock
from .memory_facts import MemoryFacts

if TYPE_CHECKING:
    from .client import ComplexGitSyncClient

_LEDGER_DIR = "lgr/"
_ENTRY_FILE = re.compile(r"\d{6}\.toml")
_HEADS = "refs/heads/"


class MemoryReading:
    """Reads registered memories for one tree. Holds the client and where the temporary clones go."""

    def __init__(self, client: ComplexGitSyncClient, cgshome: str | Path) -> None:
        self.client = client
        self.cgitsync = Path(cgshome) / ".cgitsync"

    def read(self, record: OmniscienceRecord, *, with_state: bool = False) -> Answer:
        """The memory *record* names, verified at its commit; ``data["state_toml"]`` holds the State's text when *with_state*."""
        git = self.client.git_runner
        branch = record.memory_ref[len(_HEADS):] if record.memory_ref.startswith(_HEADS) else ""
        if not branch or branch.startswith("-"):
            return self._fail(OmniscienceStatus.INVALID, MemoryDetail.INVALID_PROOF, f"the record's memory ref {record.memory_ref!r} is not a branch this build will clone.")
        try:
            url = MemoryFacts.remote_url(record.memory_repository)
        except (GitSyncError, ValueError, KeyError) as exc:
            return self._fail(OmniscienceStatus.UNAVAILABLE, MemoryDetail.REMOTE_UNAVAILABLE, f"{record.memory_repository!r} is not an address this build can read ({exc}).")
        if url.startswith("-"):
            return self._fail(OmniscienceStatus.UNAVAILABLE, MemoryDetail.REMOTE_UNAVAILABLE, "the memory's address is not one this build will hand to Git.")
        self.cgitsync.mkdir(parents=True, exist_ok=True)
        with SystemClock.scratch_directory("omniscience-read-", str(self.cgitsync)) as scratch:
            clone = Path(scratch) / "memory"
            try:
                git.clone(url, clone, branch=branch)
            except GitSyncError as exc:
                status = ObservationStatus.of_git_failure(str(exc))
                if status is ObservationStatus.ACCESS_DENIED:
                    return Answer(OmniscienceStatus.UNAUTHORIZED, reason="the provider refused to serve the registered .memory to this reader; nothing of it was read.")
                return self._fail(OmniscienceStatus.UNAVAILABLE, MemoryDetail.REMOTE_UNAVAILABLE, f"the registered .memory could not be read ({status.value}); that is not a finding that it is gone.", observed=status.value)
            if not git.is_ancestor(clone, record.memory_commit, f"refs/heads/{branch}"):  # a local clone copies every object, so presence proves nothing
                return self._fail(OmniscienceStatus.UNAVAILABLE, MemoryDetail.COMMIT_UNREACHABLE, f"commit {record.memory_commit[:12]} is not reachable from {record.memory_ref} on the registered .memory.")
            return self._verify(clone, record, with_state)

    # -- checking what was fetched -------------------------------------------

    def _verify(self, clone: Path, record: OmniscienceRecord, with_state: bool) -> Answer:
        git = self.client.git_runner
        commit = record.memory_commit
        entries = []
        for path in sorted(git.tree_blobs(clone, commit, _LEDGER_DIR)):
            if not _ENTRY_FILE.fullmatch(path.rsplit("/", 1)[-1]):
                continue
            try:
                entries.append(LedgerStore.entry_from_text(git.show_file(clone, commit, path) or ""))
            except (LedgerStoreError, tomllib.TOMLDecodeError, KeyError, ValueError, TypeError) as exc:
                return self._fail(OmniscienceStatus.INVALID, MemoryDetail.LEDGER_CORRUPT, f"ledger entry {path} cannot be read ({exc}).")
        entries.sort(key=lambda entry: entry.seq)
        report = ChainVerifier.verify(entries)
        # INCOMPLETE holds as a chain (CommitLogLoss D2); a chain-only pass never yields it today.
        if report.state not in (HistoryState.VERIFIED, HistoryState.INCOMPLETE):
            return self._fail(OmniscienceStatus.INVALID, MemoryDetail.LEDGER_CORRUPT, f"the ledger at commit {commit[:12]} is {report.state.name.lower().replace('_', ' ')}: it does not hold.", ledger_findings=len(report.findings))
        if entries[-1].entry_hash != record.local_ledger_tip:
            return self._fail(OmniscienceStatus.INVALID, MemoryDetail.INVALID_PROOF, "the ledger tip at the registered commit is not the tip the record names.")
        if not any(entry.state_id == record.state_id for entry in entries):
            return self._fail(OmniscienceStatus.INVALID, MemoryDetail.INVALID_PROOF, f"no ledger entry records {record.state_id}.")
        state_hash = MemoryStates.parse_hash(record.state_id) or ""
        text = git.show_file(clone, commit, f"{STATE_DIR_NAME}/{state_hash}.gts")
        if text is None:
            return self._fail(OmniscienceStatus.INVALID, MemoryDetail.INVALID_PROOF, f"the memory holds no State {state_hash[:12]} at the registered commit, so the damage cannot be located: diagnosis unavailable.", diagnosis="unavailable", located=[])
        problem, document, located = self._state_problem(clone, text, state_hash, record)
        if problem is not None or document is None:
            return self._fail(OmniscienceStatus.INVALID, MemoryDetail.INVALID_PROOF, problem or "the State cannot be read.", diagnosis="located" if located else "unavailable", located=located or [])
        states = sorted(Path(p).stem for p in git.tree_blobs(clone, commit, f"{STATE_DIR_NAME}/") if p.endswith(".gts"))
        data: dict[str, Any] = {
            "memory": {"repository": record.memory_repository, "ref": record.memory_ref, "commit": commit},
            "states": states,
            "ledger": {"entries": len(entries), "tip": entries[-1].entry_hash, "state": "verified"},
            "state": {
                "state_id": record.state_id,
                "gittree_root": record.gittree_root,
                "repositories": [
                    {"relative_path": repo.get("relative_path"), "ref": repo.get("ref"), "commit": repo.get("commit_sha")}
                    for repo in document.repo_states
                ],
            },
        }
        if with_state:
            data["state_toml"] = text
        return Answer(OmniscienceStatus.VERIFIED, reason="the memory at the registered commit holds the ledger and the State the record names.", data=data)

    @staticmethod
    def _state_problem(clone: Path, text: str, state_hash: str, record: OmniscienceRecord) -> tuple[str | None, GtsDocument | None, list[str] | None]:
        """What is wrong with the State read from the memory, else the document; ``located`` names the repositories whose own hash no longer holds (``None`` when the evidence cannot be read)."""
        copy = clone.parent / "state.gts"  # beside the clone, so the memory's worktree is never written
        copy.write_text(text, encoding="utf-8")
        try:
            document = GtsDocument.from_toml(copy)
            mismatches = document.integrity_mismatches()
            if mismatches or document.compute_snapshot_hash() != state_hash:
                located = sorted(str(where) for kind, where in mismatches if kind == "repo_hash" and where is not None)
                where = f"; located at {', '.join(located)}" if located else ""
                return f"the State on the memory no longer hashes to its name ({', '.join(kind for kind, _ in mismatches) or 'snapshot_hash'}){where}.", None, located
            if document.compute_gittree_root() != record.gittree_root:
                return "the State's H_GITTREE is not the one the record names.", None, []
        except (ConfigValidationError, GitSyncError, tomllib.TOMLDecodeError) as exc:
            located = MemoryReading._damaged_repositories(text)
            if located:
                return f"the State on the memory no longer hashes to its name; located at {', '.join(located)}.", None, located
            return f"the State on the memory cannot be read or hashed ({str(exc).splitlines()[0]}); diagnosis unavailable.", None, None
        return None, document, []

    @staticmethod
    def _damaged_repositories(text: str) -> list[str]:
        """The repositories of a State whose own ``repo_hash`` is no longer the hash of what the State says of them; empty when the text cannot be read that far.

        A State that does not hash cannot be loaded as a document, so this reads the table directly and hashes each
        repository with the one leaf hash there is (``MerkleRoot``).
        """
        try:
            rows = tomllib.loads(text).get("repo_state", [])
            return sorted(str(row["relative_path"]) for row in rows if row.get("repo_hash") != MerkleRoot.repo_leaf_hash(GtsDocument.repo_leaf(row)))
        except (tomllib.TOMLDecodeError, KeyError, TypeError, ValueError, ConfigValidationError):
            return []

    @staticmethod
    def _fail(status: OmniscienceStatus, detail: MemoryDetail, reason: str, **data: Any) -> Answer:
        return Answer(status, detail.value, reason, data)


__all__ = ["MemoryReading"]

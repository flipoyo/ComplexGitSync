"""omniscience_view — the mounted register as a reader sees it: fetched, its chain walked, its records checked against a checkpoint.

Ring: 3
Contract: the private half of the query side. Fetch the mounted register
    (touching only remote-tracking refs, never the worktree), read its
    checkpoint chain and its records from Git's objects, and answer what a
    query needs: which checkpoint to use, which records it accepts, the
    inclusion proof of one of them, the observations beside them, and whether
    the register itself is ``intact``, ``attested``, ``rewritten`` or
    ``contradicted``. Writes nothing, remembers nothing.
Imports: errors, global_index, merkle, omniscience_answer, omniscience_observation, omniscience_record, omniscience_repository, operating_space, omniscience_checkpoints, omniscience_reader

A reader trusts a checkpoint, never a file lying beside it: a record is
registered at a checkpoint only if that checkpoint's body names it, and the
checkpoint is sound only if the root recomputed over exactly the records it
names is the root it states. Anything else is ``invalid``, with the reason.
Whether the register was rewritten is asked of the tip this tree last found
sound (kept by ``register`` and ``observe``); a tree that has never written
cannot say, and the answer says so rather than guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import ConfigValidationError, GitSyncError
from ..merkle import MerkleRoot
from ..operating_space.operating_space import OperatingSpace
from .global_index import GlobalIndex
from .omniscience_answer import Answer, OmniscienceStatus, Stop
from .omniscience_checkpoints import GlobalCheckpoints
from .omniscience_observation import Observation, ObservationStatus
from .omniscience_reader import RegisterFile, RegisterReader
from .omniscience_record import OmniscienceRecord
from .omniscience_repository import GLOBAL_BRANCH, OMNISCIENCE_MOUNT_PATH, OmniscienceRepository

if TYPE_CHECKING:
    from ..orchestre.client import ComplexGitSyncClient


@dataclass(frozen=True, slots=True)
class Registered:
    """A record a checkpoint accepts, the register branch it is filed on, and the first checkpoint that covered it (0 is the oldest)."""

    record: OmniscienceRecord
    branch: str
    first_checkpoint: int


class RegisterView:
    """One fetched register. Built by :meth:`open`; every method after it reads memory already loaded."""

    def __init__(self, client: ComplexGitSyncClient, cgshome: str | Path) -> None:
        self.client = client
        self.mount = Path(cgshome) / OMNISCIENCE_MOUNT_PATH
        self.git = client.git_runner
        self.reader = RegisterReader(self.git, self.mount)
        self.checkpoints = GlobalCheckpoints(client, self.mount)
        self.branches: dict[str, str] = {}
        self.tip: str | None = None
        self.head: str | None = None
        self.chain: dict[str, dict[str, Any]] = {}
        self.order: list[str] = []
        self.chain_problem: str | None = None
        self.files: list[RegisterFile] = []

    # -- opening ------------------------------------------------------------

    def open(self) -> RegisterView:
        """Fetch the register and read what it holds; ends the query (``Stop``) when it cannot be read at all."""
        if not (self.mount / ".git").exists():
            raise Stop(Answer(OmniscienceStatus.UNAVAILABLE, reason=f"the register is not mounted at {self.mount}. Run 'cgitsync omniscience mount'."))
        try:
            self.git.fetch(self.mount)
        except GitSyncError as exc:
            denied = ObservationStatus.of_git_failure(str(exc)) is ObservationStatus.ACCESS_DENIED
            raise Stop(Answer(
                OmniscienceStatus.UNAUTHORIZED if denied else OmniscienceStatus.UNAVAILABLE,
                reason=("the provider refused to serve the register to this reader" if denied else f"the register's remote could not be read: {str(exc).splitlines()[0] if str(exc) else 'no answer'}"),
            )) from exc
        self.branches = self.reader.remote_branches()
        self.tip = self.branches.get(GLOBAL_BRANCH)
        try:
            self.chain, self.head = self.checkpoints.chain(self.tip)
        except ConfigValidationError as exc:
            self.chain, self.head, self.chain_problem = {}, None, f"the checkpoint chain is not sound: {exc}"
        self.order = self._walk(self.head)
        self.files = [f for branch, sha in sorted(self.branches.items()) if branch != GLOBAL_BRANCH for f in self.reader.records(branch, sha)]
        return self

    def _walk(self, head: str | None) -> list[str]:
        """The chain's names, oldest first, following ``parent`` from *head* (a name met twice ends the walk)."""
        names: list[str] = []
        while head and head not in names:
            names.append(head)
            head = str(self.chain[head].get("parent") or "") or None
        return names[::-1]

    # -- checkpoints and what they accept -----------------------------------

    def checkpoint(self, prefix: str | None = None) -> str:
        """The checkpoint to answer from: the head, or the one *prefix* names."""
        if self.chain_problem:
            raise Stop(Answer(OmniscienceStatus.INVALID, reason=self.chain_problem))
        if prefix is None:
            if self.head is None:
                raise Stop(Answer(OmniscienceStatus.NOT_REGISTERED, reason="the register holds no checkpoint yet, so nothing in it is registered."))
            return self.head
        found = [name for name in self.order if prefix and name.startswith(prefix)]
        if len(found) != 1:
            raise Stop(Answer(
                OmniscienceStatus.NOT_REGISTERED if not found else OmniscienceStatus.INVALID,
                reason=f"no checkpoint begins with {prefix!r}." if not found else f"{prefix!r} begins {len(found)} checkpoints; give more of the name.",
            ))
        return found[0]

    def registered(self, checkpoint: str) -> list[Registered]:
        """Every record *checkpoint* accepts, in index order, after checking the checkpoint is sound.

        Ends the query as ``invalid`` when it names a record the register no
        longer holds, or its stated root is not the root over the records it names.
        """
        body = self.chain[checkpoint]
        valid = {f.value.record_hash: f for f in self.files if isinstance(f.value, OmniscienceRecord)}
        missing = [name for name in body["records"] if name not in valid]
        if missing:
            raise Stop(Answer(OmniscienceStatus.INVALID, reason=f"checkpoint {checkpoint[:12]} names {len(missing)} record(s) the register no longer holds ({', '.join(m[:12] for m in missing[:3])})."))
        records = [valid[name].value for name in body["records"]]
        if GlobalIndex.of(records).root != body["global_root"]:  # type: ignore[arg-type]
            raise Stop(Answer(OmniscienceStatus.INVALID, reason=f"checkpoint {checkpoint[:12]} states a root that is not the root over the records it names."))
        first = {name: next(i for i, step in enumerate(self.order) if name in self.chain[step]["records"]) for name in body["records"]}
        accepted = [Registered(valid[r.record_hash].value, valid[r.record_hash].branch, first[r.record_hash]) for r in GlobalIndex.of(records).records]  # type: ignore[arg-type]
        for item in accepted:
            if item.record.os_id != OperatingSpace.id_of(item.record.project):
                raise Stop(Answer(OmniscienceStatus.INVALID, reason=f"record {item.record.record_hash[:12]} carries an os_id that is not the id of its project's name."))
            if item.branch != OmniscienceRepository.branch(item.record.project, item.record.project_branch):
                raise Stop(Answer(OmniscienceStatus.INVALID, reason=f"record {item.record.record_hash[:12]} is filed on {item.branch!r}, not on the branch its project and branch name."))
        return accepted

    def proposed(self, checkpoint: str | None) -> list[RegisterFile]:
        """Files the register holds that *checkpoint* does not accept: valid-looking but not covered, or not a record at all."""
        covered = set(self.chain[checkpoint]["records"]) if checkpoint else set()
        return [f for f in self.files if f.value is None or f.value.record_hash not in covered]  # type: ignore[union-attr]

    def proof(self, checkpoint: str, accepted: list[Registered], record: OmniscienceRecord) -> dict[str, Any]:
        """The inclusion proof of *record* in *checkpoint*, checked against the checkpoint's stated root and size."""
        digests = [item.record.leaf_digest for item in accepted]
        proof = MerkleRoot.global_inclusion_proof(digests, digests.index(record.leaf_digest))
        body = self.chain[checkpoint]
        return {
            "checkpoint": checkpoint,
            "global_root": body["global_root"],
            "leaves": len(digests),
            "proof": proof.summary(),
            "proof_verified": proof.verify(record.leaf_digest.hex(), str(body["global_root"]), len(digests)),
            "scope": "this registration is in the register's index; it says nothing of whether the repositories it names are still there",
        }

    # -- observations ---------------------------------------------------------

    def observations(self, project: str) -> list[Observation]:
        """Every observation any project branch holds about *project*, unordered; a file that is not one is skipped."""
        found: list[Observation] = []
        for branch, sha in sorted(self.branches.items()):
            if branch != GLOBAL_BRANCH:
                found.extend(f.value for f in self.reader.observations(branch, sha) if isinstance(f.value, Observation) and f.value.project == project)
        return found

    # -- the register itself --------------------------------------------------

    def register_state(self) -> dict[str, Any]:
        """``intact``, ``attested``, ``rewritten`` or ``contradicted``, with the reason and the protections that were checked.

        ``contradicted``: the register disagrees with itself (a forked or broken chain,
        a checkpoint naming a record that is gone, a stated root that does not
        recompute). ``rewritten``: the remote's index no longer contains the tip this
        tree last found sound. ``attested``: neither, and the index's tip commit
        carries a good signature. ``intact``: neither, unsigned.
        """
        signed = bool(self.tip) and self.git.commit_signature(self.mount, str(self.tip)) == "G"
        remembered = self.checkpoints.last_seen()
        protections = {"branch_protection": "not checked", "signed_head_commit": signed, "last_seen_tip_known": bool(remembered)}
        state, reason = "intact", "no checkpoint has been appended yet." if self.tip is None else ""
        if self.chain_problem:
            state, reason = "contradicted", self.chain_problem
        elif remembered and self.tip and remembered != self.tip and not self.git.is_ancestor(self.mount, remembered, self.tip):
            state, reason = "rewritten", f"the remote's {GLOBAL_BRANCH!r} no longer contains the checkpoint this tree last saw ({remembered[:12]})."
        elif self.head:
            try:
                self.registered(self.head)
            except Stop as stop:
                state, reason = "contradicted", stop.answer.reason
            else:
                state = "attested" if signed else "intact"
        return {"state": state, "reason": reason, "protections": protections, "checkpoints": len(self.order), "head": self.head}


__all__ = ["RegisterView", "Registered"]

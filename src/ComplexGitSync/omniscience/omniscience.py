"""omniscience — the public, read-only API of the global memory: find a project, prove a State, read a registered ``.memory``.

Ring: 3
Contract: ``Omniscience`` answers questions about what the shared register
    holds, and only reads. Every answer is an ``Answer`` carrying one of
    ``verified``, ``invalid``, ``unavailable``, ``unauthorized`` or
    ``not_registered``. It mirrors ``cgitsync omniscience get|branches|history|
    state|proof|verify|status|report|memory`` one to one, and the client
    mirrors it as ``ComplexGitSyncClient.omniscience_*``. The private classes
    it delegates to are never exposed.
Imports: errors, omniscience_answer, omniscience_completeness, omniscience_memory, omniscience_observation, omniscience_view

Anyone the provider lets read the ``omniscience`` repository may ask; this
module adds no check of its own (owner, D3). Nothing here appends, pushes,
registers or probes: the register is fetched into the mount's remote-tracking
refs, a registered ``.memory`` is cloned into a temporary directory and
removed, and nothing is written to either. A proof is only ever a proof of
*registration*: a verified answer never says a repository is available, and an
unobserved or missing one is ``unavailable``, never "deleted".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import GitSyncError
from .omniscience_answer import Answer, OmniscienceStatus, Stop
from .omniscience_completeness import CompletenessReport
from .omniscience_memory import MemoryReading
from .omniscience_observation import Observation, ObservationStatus
from .omniscience_view import Registered, RegisterView

if TYPE_CHECKING:
    from ..orchestre.client import ComplexGitSyncClient

_HEX = re.compile(r"[0-9a-f]{1,64}")


class Omniscience:
    """The questions one tree can ask of the shared register. Built per call: it holds the client and the workspace."""

    def __init__(self, client: ComplexGitSyncClient, cgshome: str | Path) -> None:
        self.client = client
        self.cgshome = Path(cgshome)

    # -- the project ----------------------------------------------------------

    def get(self, project_name: str, checkpoint: str | None = None) -> Answer:
        """Whether *project_name* is registered, under which ``os_id``, on which branches, with how many States."""
        try:
            view, name, accepted, mine = self._project(project_name, checkpoint)
            self._require_proofs(view, name, accepted, mine)
            return Answer(OmniscienceStatus.VERIFIED, reason="registered: every record is in the checkpoint, and the checkpoint's root recomputes.", data={
                "project": project_name,
                "os_id": mine[0].record.os_id,
                "checkpoint": name,
                "global_root": view.chain[name]["global_root"],
                "branches": sorted({item.record.project_branch for item in mine}),
                "states": len({item.record.state_id for item in mine}),
                "records": len(mine),
                "proposed": len(self._proposed_for(view, name, project_name)),
            })
        except Stop as stop:
            return stop.answer

    def branches(self, project_name: str, checkpoint: str | None = None) -> Answer:
        """Each registered branch of *project_name*, its register branch, and its newest registered State."""
        try:
            view, name, accepted, mine = self._project(project_name, checkpoint)
            self._require_proofs(view, name, accepted, mine)
            rows = []
            for branch in sorted({item.record.project_branch for item in mine}):
                items = [item for item in mine if item.record.project_branch == branch]
                newest = max(items, key=self._order)
                rows.append({
                    "project_branch": branch,
                    "register_branch": newest.branch,
                    "states": len({item.record.state_id for item in items}),
                    "latest_state_id": newest.record.state_id,
                    "latest_record": newest.record.record_hash,
                    "latest_recorded_at": newest.record.recorded_at,
                })
            return Answer(OmniscienceStatus.VERIFIED, reason="the project's branches, from records the checkpoint accepts.", data={"project": project_name, "checkpoint": name, "branches": rows})
        except Stop as stop:
            return stop.answer

    def history(self, project_name: str, branch: str | None = None, checkpoint: str | None = None) -> Answer:
        """Every registration of *project_name* (of one *branch*, when given), oldest first, in the order the checkpoints took them."""
        try:
            view, name, accepted, mine = self._project(project_name, checkpoint)
            self._require_proofs(view, name, accepted, mine)
            chosen = [item for item in mine if branch is None or item.record.project_branch == branch]
            if not chosen:
                raise Stop(Answer(OmniscienceStatus.NOT_REGISTERED, reason=f"project {project_name!r} has no registered branch {branch!r}."))
            rows = [
                {
                    "project_branch": item.record.project_branch,
                    "state_id": item.record.state_id,
                    "record": item.record.record_hash,
                    "checkpoint_number": item.first_checkpoint + 1,
                    "recorded_at": item.record.recorded_at,
                    "contributor": item.record.contributor,
                    "memory_commit": item.record.memory_commit,
                }
                for item in sorted(chosen, key=self._order)
            ]
            return Answer(OmniscienceStatus.VERIFIED, reason="in the order the checkpoints accepted them; recorded_at and contributor are the contributor's claims.", data={"project": project_name, "branch": branch, "checkpoint": name, "history": rows})
        except Stop as stop:
            return stop.answer

    # -- a State and its proof -------------------------------------------------

    def get_state(self, project_name: str, state_id: str, checkpoint: str | None = None, *, destination: str | Path | None = None) -> Answer:
        """The registration of a State with its proof, and the State itself read from the registered ``.memory``.

        With *destination*, the State's ``.gts`` is written there once the
        read verified, so ``cgitsync bootstrap --gts`` can restore it; an
        existing file is never overwritten. Without access to the memory the
        answer is ``unauthorized`` and carries the registration and proof but
        none of the memory's content.
        """
        try:
            view, name, accepted, mine = self._project(project_name, checkpoint)
            item = self._state(mine, state_id)
            proof = view.proof(name, accepted, item.record)
            registration = {"registration": item.record.to_dict(), "record": item.record.record_hash, **proof}
            if not proof["proof_verified"]:
                return Answer(OmniscienceStatus.INVALID, reason="the registration's inclusion proof does not hold against its checkpoint.", data=registration)
            read = MemoryReading(self.client, self.cgshome).read(item.record, with_state=destination is not None)
            text = read.data.get("state_toml")
            answer = Answer(read.status, read.detail, read.reason, {k: v for k, v in read.data.items() if k != "state_toml"}).with_data(project=project_name, **registration)
            if read.verified and destination is not None and text is not None:
                target = Path(destination)
                if target.exists():
                    raise GitSyncError(f"{target} already exists; Omniscience never overwrites a file. Nothing was written.")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
                answer = answer.with_data(written=str(target))
            return answer
        except Stop as stop:
            return stop.answer

    def get_proof(self, project_name: str, state_id: str, checkpoint: str | None = None) -> Answer:
        """The inclusion proof of a State's registration in a checkpoint, checked. Proves registration, not availability."""
        try:
            view, name, accepted, mine = self._project(project_name, checkpoint)
            item = self._state(mine, state_id)
            proof = view.proof(name, accepted, item.record)
            data = {"project": project_name, "state_id": item.record.state_id, "record": item.record.record_hash, "os_state_hash": item.record.os_state_hash, "leaf": item.record.leaf_digest.hex(), **proof}
            if proof["proof_verified"]:
                return Answer(OmniscienceStatus.VERIFIED, reason="the registration is in the checkpoint's index.", data=data)
            return Answer(OmniscienceStatus.INVALID, reason="the inclusion proof does not hold against the checkpoint's root and size.", data=data)
        except Stop as stop:
            return stop.answer

    def verify(self, project_name: str, state_id: str, checkpoint: str | None = None) -> Answer:
        """The proof of a State's registration, and what is known of the register itself: ``intact``, ``attested``, ``rewritten`` or ``contradicted``."""
        view: RegisterView | None = None
        try:
            view = RegisterView(self.client, self.cgshome).open()
            state = view.register_state()
            if state["state"] in ("rewritten", "contradicted"):
                return Answer(OmniscienceStatus.INVALID, reason=f"the register is {state['state']}: {state['reason']}", data={"project": project_name, "register": state})
            proof = self._proof_in(view, project_name, state_id, checkpoint)
            return Answer(proof.status, proof.detail, proof.reason, {**proof.data, "register": state})
        except Stop as stop:
            return stop.answer.with_data(register=view.register_state()) if view is not None else stop.answer

    def _proof_in(self, view: RegisterView, project_name: str, state_id: str, checkpoint: str | None) -> Answer:
        """:meth:`get_proof` over a register already fetched, so one command fetches once."""
        name = view.checkpoint(checkpoint)
        accepted = view.registered(name)
        item = self._state(self._mine(accepted, project_name, view, name), state_id)
        proof = view.proof(name, accepted, item.record)
        data = {"project": project_name, "state_id": item.record.state_id, "record": item.record.record_hash, **proof}
        status = OmniscienceStatus.VERIFIED if proof["proof_verified"] else OmniscienceStatus.INVALID
        return Answer(status, reason="the registration is in the checkpoint's index." if proof["proof_verified"] else "the inclusion proof does not hold.", data=data)

    # -- the registered .memory -------------------------------------------------

    def memory(self, project_name: str, branch: str, checkpoint: str | None = None) -> Answer:
        """The ``.memory`` registered for *branch*'s newest State, read at its immutable commit and checked against the record."""
        try:
            view, name, accepted, mine = self._project(project_name, checkpoint)
            items = [item for item in mine if item.record.project_branch == branch]
            if not items:
                raise Stop(Answer(OmniscienceStatus.NOT_REGISTERED, reason=f"project {project_name!r} has no registered branch {branch!r}."))
            newest = max(items, key=self._order)
            proof = view.proof(name, accepted, newest.record)
            if not proof["proof_verified"]:
                return Answer(OmniscienceStatus.INVALID, reason="the registration's inclusion proof does not hold against its checkpoint.", data={"project": project_name, "record": newest.record.record_hash, "proof": proof})
            read = MemoryReading(self.client, self.cgshome).read(newest.record)
            return read.with_data(project=project_name, project_branch=branch, record=newest.record.record_hash, checkpoint=name)
        except Stop as stop:
            return stop.answer

    # -- availability and the whole project ------------------------------------

    def repository_status(self, project_name: str, repo_id: str, checkpoint: str | None = None) -> Answer:
        """A registered repository (``memory`` for the project's ``.memory``): its registration, and the newest observation of it.

        ``verified`` only when the registration holds *and* the newest
        observation found the repository available. Never observed, or
        observed missing, closed, denied or unreachable, is ``unavailable``
        with the observation; a proof alone never says a repository is there.
        """
        try:
            view, name, accepted, mine = self._project(project_name, checkpoint)
            latest = max(mine, key=self._order)
            target = latest.record.memory_repository if repo_id == "memory" else repo_id
            naming = [item for item in mine if target == item.record.memory_repository or any(str(row.get("repository")) == target for row in item.record.published_rows)]
            if not naming:
                raise Stop(Answer(OmniscienceStatus.NOT_REGISTERED, reason=f"no record of project {project_name!r} names the repository {target!r}."))
            first = min(naming, key=self._order)
            proof = view.proof(name, accepted, first.record)
            registration = {"project": project_name, "repository": target, "record": first.record.record_hash, "registration_proof_verified": proof["proof_verified"], "checkpoint": name}
            if not proof["proof_verified"]:
                return Answer(OmniscienceStatus.INVALID, reason="the registration naming this repository does not verify.", data=registration)
            seen = sorted((o for o in view.observations(project_name) if o.target == target), key=lambda o: (o.observed_at, o.observation_hash))
            report = CompletenessReport.of(seen)
            if not seen:
                return Answer(OmniscienceStatus.UNAVAILABLE, "not_observed", "registered and verified, but nobody has observed this repository: run 'cgitsync omniscience observe'. A proof of registration is not evidence that it is there.", {**registration, "availability": "not_observed", "completeness": report.verdict})
            last = seen[-1]
            data = {**registration, "availability": last.status.value, "observation": self._observation(last), "observations": len(seen), "completeness": report.verdict}
            if last.status is ObservationStatus.AVAILABLE:
                return Answer(OmniscienceStatus.VERIFIED, reason=f"registered, and observed available at {last.observed_at}.", data=data)
            return Answer(OmniscienceStatus.UNAVAILABLE, last.status.value.lower(), f"registered, and the newest observation ({last.observed_at}) is {last.status.value}; the registration still verifies.", data)
        except Stop as stop:
            return stop.answer

    def integrity_report(self, project_name: str) -> Answer:
        """The register's state, whether every record of *project_name* is accepted and proved, what is only proposed, and completeness."""
        view: RegisterView | None = None
        try:
            view = RegisterView(self.client, self.cgshome).open()
            state = view.register_state()
            if state["state"] in ("rewritten", "contradicted"):
                return Answer(OmniscienceStatus.INVALID, reason=f"the register is {state['state']}: {state['reason']}", data={"project": project_name, "register": state})
            name = view.checkpoint(None)
            accepted = view.registered(name)
            mine = self._mine(accepted, project_name, view, name)
            proofs = [view.proof(name, accepted, item.record)["proof_verified"] for item in mine]
            report = CompletenessReport.of(view.observations(project_name))
            proposed = [{"branch": f.branch, "path": f.path, "reason": f.problem or "no checkpoint accepts it yet"} for f in self._proposed_for(view, name, project_name)]
            data = {"project": project_name, "checkpoint": name, "global_root": view.chain[name]["global_root"], "records": len(mine), "proofs_verified": sum(proofs), "proposed": proposed, "register": state, "completeness": report.to_dict()}
            if not all(proofs):
                return Answer(OmniscienceStatus.INVALID, reason="an inclusion proof does not hold.", data=data)
            return Answer(OmniscienceStatus.VERIFIED, reason=f"register {state['state']}, {len(mine)} record(s) accepted and proved; completeness {report.verdict}.", data=data)
        except Stop as stop:
            return stop.answer.with_data(register=view.register_state()) if view is not None else stop.answer

    # -- shared steps -----------------------------------------------------------

    def _project(self, project_name: str, checkpoint: str | None) -> tuple[RegisterView, str, list[Registered], list[Registered]]:
        view = RegisterView(self.client, self.cgshome).open()
        name = view.checkpoint(checkpoint)
        accepted = view.registered(name)
        return view, name, accepted, self._mine(accepted, project_name, view, name)

    def _mine(self, accepted: list[Registered], project_name: str, view: RegisterView, checkpoint: str) -> list[Registered]:
        mine = [item for item in accepted if item.record.project == project_name]
        if not mine:
            held = len(self._proposed_for(view, checkpoint, project_name))
            raise Stop(Answer(
                OmniscienceStatus.NOT_REGISTERED,
                reason=f"no project named {project_name!r} is registered at checkpoint {checkpoint[:12]}." + (f" {held} file(s) for it are only proposed: no checkpoint accepts them." if held else ""),
                data={"project": project_name, "checkpoint": checkpoint, "proposed": held},
            ))
        return mine

    @staticmethod
    def _proposed_for(view: RegisterView, checkpoint: str, project_name: str) -> list[Any]:
        return [f for f in view.proposed(checkpoint) if f.value is None or getattr(f.value, "project", None) == project_name]

    @staticmethod
    def _order(item: Registered) -> tuple[int, str, str]:
        """Causal first: the checkpoint that first covered the record, then the claimed time, then the hash."""
        return (item.first_checkpoint, item.record.recorded_at, item.record.record_hash)

    @staticmethod
    def _state(mine: list[Registered], state_id: str) -> Registered:
        """The registration of the State *state_id* names (``state(<hex>)`` or a hex prefix); the first when several records share it."""
        wanted = state_id.strip().removeprefix("state(").removesuffix(")")
        if not _HEX.fullmatch(wanted):
            raise Stop(Answer(OmniscienceStatus.NOT_REGISTERED, reason=f"{state_id!r} is not a State id or a prefix of one."))
        found = [item for item in mine if item.record.state_id.removeprefix("state(").startswith(wanted)]
        if not found:
            raise Stop(Answer(OmniscienceStatus.NOT_REGISTERED, reason=f"no registered State of this project begins with {wanted!r}."))
        if len({item.record.state_id for item in found}) > 1:
            raise Stop(Answer(OmniscienceStatus.INVALID, reason=f"{wanted!r} begins more than one registered State; give more of it.", data={"candidates": sorted({item.record.state_id for item in found})}))
        return min(found, key=Omniscience._order)

    @staticmethod
    def _require_proofs(view: RegisterView, checkpoint: str, accepted: list[Registered], mine: list[Registered]) -> None:
        bad = [item.record.record_hash[:12] for item in mine if not view.proof(checkpoint, accepted, item.record)["proof_verified"]]
        if bad:
            raise Stop(Answer(OmniscienceStatus.INVALID, reason=f"inclusion proof(s) do not hold for record(s) {', '.join(bad[:3])}."))

    @staticmethod
    def _observation(item: Observation) -> dict[str, Any]:
        return {"status": item.status.value, "observed_at": item.observed_at, "method": item.method, "kind": item.target_kind, "observer": item.observer, "evidence": dict(item.evidence)}


__all__ = ["Omniscience"]

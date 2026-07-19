from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .drivers import EchoValueDriver, ValueDriver, _driver_accepts
from .errors import CGSError, ErrorCode
from .gateway import Gateway
from .graph import Graph
from .L0 import _anchor_occurrence, _new_l0
from .serialization import FrozenJson, canonical_json, freeze_json, thaw_json
from .state import CandidateState, State, _new_state, _state_digest
from .state_id import StateId, _validate_digest


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """Immutable canonical Memory of one complete authoritative State."""

    graph_name: str
    graph_binding: str
    gateway_binding: str
    state_id: str
    left: FrozenJson
    right: FrozenJson
    payload: FrozenJson
    validated: bool
    state_digest: str
    record_digest: str

    def state_data(self) -> dict[str, Any]:
        return {
            "graph_name": self.graph_name,
            "graph_binding": self.graph_binding,
            "gateway_binding": self.gateway_binding,
            "state_id": self.state_id,
            "left": thaw_json(self.left),
            "right": thaw_json(self.right),
            "payload": thaw_json(self.payload),
            "validated": self.validated,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.state_data(),
            "state_digest": self.state_digest,
            "record_digest": self.record_digest,
        }


@dataclass(frozen=True, slots=True)
class MemoryResult:
    record: MemoryRecord | None = None
    error: CGSError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.record is not None


@dataclass(frozen=True, slots=True)
class MemoryRecoveryResult:
    state: State | None = None
    record: MemoryRecord | None = None
    error: CGSError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.state is not None and self.record is not None


@dataclass(frozen=True, slots=True)
class _MemorySnapshot:
    record_blobs: tuple[str, ...]
    ms_state_blob: str | None


def _record_digest(data: dict[str, Any], state_digest: str) -> str:
    return hashlib.sha256(
        b"CGS-MEMORY-RECORD-v1\x00"
        + canonical_json({**data, "state_digest": state_digest}).encode("utf-8")
    ).hexdigest()


def _record_for_state(state: object) -> MemoryResult:
    if type(state) is not State or not state.validated:
        return MemoryResult(
            error=CGSError(
                ErrorCode.MEMORY_REJECTED,
                "Memory accepts only a validated authoritative State",
                "memory.prepare",
            )
        )
    data = state.to_memory_data()
    if _state_digest(data) != state.state_digest:
        return MemoryResult(
            error=CGSError(
                ErrorCode.MEMORY_REJECTED,
                "authoritative State binding is invalid",
                "memory.prepare",
            )
        )
    return MemoryResult(
        record=MemoryRecord(
            graph_name=state.graph_name,
            graph_binding=state.graph_binding,
            gateway_binding=state.gateway_binding,
            state_id=state.state_id.value,
            left=freeze_json(thaw_json(state.left)),
            right=freeze_json(thaw_json(state.right)),
            payload=freeze_json(thaw_json(state.payload)),
            validated=True,
            state_digest=state.state_digest,
            record_digest=_record_digest(data, state.state_digest),
        )
    )


def _record_from_blob(blob: str) -> MemoryRecord:
    value = json.loads(blob)
    data = {
        "graph_name": value["graph_name"],
        "graph_binding": value["graph_binding"],
        "gateway_binding": value["gateway_binding"],
        "state_id": value["state_id"],
        "left": value["left"],
        "right": value["right"],
        "payload": value["payload"],
        "validated": value["validated"],
    }
    state_digest = value["state_digest"]
    if _state_digest(data) != state_digest:
        raise ValueError("invalid sealed State digest")
    if _record_digest(data, state_digest) != value["record_digest"]:
        raise ValueError("invalid sealed Memory record digest")
    record = MemoryRecord(
        graph_name=data["graph_name"],
        graph_binding=data["graph_binding"],
        gateway_binding=data["gateway_binding"],
        state_id=data["state_id"],
        left=freeze_json(data["left"]),
        right=freeze_json(data["right"]),
        payload=freeze_json(data["payload"]),
        validated=data["validated"],
        state_digest=state_digest,
        record_digest=value["record_digest"],
    )
    if canonical_json(record.to_dict()) != blob:
        raise ValueError("Memory record blob is not canonical")
    return record


def _record_blob(record: MemoryRecord) -> str:
    return canonical_json(record.to_dict())


def _state_from_blob(blob: str) -> State:
    value = json.loads(blob)
    expected_digest = value.pop("state_digest")
    if _state_digest(value) != expected_digest:
        raise ValueError("invalid sealed Memory State")
    state = _new_state(
        graph_name=value["graph_name"],
        graph_binding=value["graph_binding"],
        gateway_binding=value["gateway_binding"],
        state_id=value["state_id"],
        left=value["left"],
        right=value["right"],
        payload=value["payload"],
    )
    if state.state_digest != expected_digest:
        raise ValueError("invalid sealed Memory State")
    return state


class MemorySystem:
    """Sealed @MS wrapper with a detached-value storage driver."""

    __slots__ = (
        "name",
        "_driver",
        "_record_blobs",
        "_ms_graph",
        "_ms_gateway",
        "_ms_state_blob",
    )

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("MemorySystem is sealed; compose a ValueDriver")

    def __init__(
        self,
        name: str = "MemorySystem",
        *,
        driver: ValueDriver | None = None,
    ) -> None:
        if not name:
            raise ValueError("MemorySystem.name must be non-empty")
        self.name = name
        self._driver = driver or EchoValueDriver()
        self._record_blobs: tuple[str, ...] = ()
        self._ms_graph = Graph("MemorySystem", node="G", edge="Storage", op="Persist")
        self._ms_gateway = Gateway(self._ms_graph)
        self._ms_state_blob: str | None = None

    @property
    def graph(self) -> Graph:
        return self._ms_graph.detached_copy()

    @property
    def gateway(self) -> Gateway:
        return Gateway(self._ms_graph)

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        return tuple(_record_from_blob(blob) for blob in self._record_blobs)

    @property
    def memory_state(self) -> State | None:
        return _state_from_blob(self._ms_state_blob) if self._ms_state_blob is not None else None

    def prepare(self, state: object) -> MemoryResult:
        canonical = _record_for_state(state)
        if not canonical.ok:
            return canonical
        assert canonical.record is not None
        if not _driver_accepts(
            self._driver,
            "memory.prepare",
            canonical_json(canonical.record.to_dict()),
        ):
            return MemoryResult(
                error=CGSError(
                    ErrorCode.MEMORY_REJECTED,
                    "physical Memory preparation rejected",
                    "memory.prepare",
                )
            )
        return canonical

    def persist(self, state: object) -> MemoryResult:
        canonical = _record_for_state(state)
        if not canonical.ok:
            return canonical
        assert canonical.record is not None
        record_blob = canonical_json(canonical.record.to_dict())
        if not _driver_accepts(self._driver, "memory.persist", record_blob):
            return MemoryResult(
                error=CGSError(
                    ErrorCode.MEMORY_REJECTED,
                    "physical Memory persistence rejected",
                    "memory.persist",
                )
            )
        if record_blob in self._record_blobs:
            return canonical

        next_records = self._record_blobs + (record_blob,)
        journal_digest = hashlib.sha256(
            b"CGS-MS-JOURNAL-v1\x00" + canonical_json(list(next_records)).encode("utf-8")
        ).hexdigest()
        source_binding = {
            "source_state_id": canonical.record.state_id,
            "source_graph_binding": canonical.record.graph_binding,
            "source_gateway_binding": canonical.record.gateway_binding,
            "source_state_digest": canonical.record.state_digest,
            "record_digest": canonical.record.record_digest,
        }
        candidate = CandidateState(
            "MemorySystem",
            source_binding,
            source_binding,
            {"journal_digest": journal_digest},
        )
        listened = Gateway.listen(self._ms_gateway, candidate)
        interpreted = Gateway.interpret(self._ms_gateway, listened)
        validated = Gateway.validate(self._ms_gateway, interpreted)
        if not validated.ok:
            return MemoryResult(
                error=CGSError(
                    ErrorCode.MEMORY_REJECTED,
                    "internal Memory State validation failed",
                    "memory.persist",
                )
            )
        l0 = _new_l0()
        _anchor, ms_state_id = _anchor_occurrence(l0)
        ms_state = _new_state(
            graph_name="MemorySystem",
            graph_binding=self._ms_graph.digest,
            gateway_binding=self._ms_gateway.binding,
            state_id=ms_state_id,
            left=candidate.left,
            right=candidate.right,
            payload=candidate.payload,
        )
        transferred = Gateway.transfer(self._ms_gateway, validated, ms_state)
        if not transferred.ok:
            return MemoryResult(
                error=CGSError(
                    ErrorCode.MEMORY_REJECTED,
                    "internal Memory State transfer failed",
                    "memory.persist",
                )
            )
        ms_state_blob = canonical_json(
            {**ms_state.to_memory_data(), "state_digest": ms_state.state_digest}
        )
        self._record_blobs, self._ms_state_blob = next_records, ms_state_blob
        return canonical

    def recover(self, state_id: StateId | str) -> MemoryRecoveryResult:
        value = state_id.value if type(state_id) is StateId else state_id
        try:
            _validate_digest(value, "StateId")
        except (TypeError, ValueError):
            return MemoryRecoveryResult(
                error=CGSError(
                    ErrorCode.MEMORY_REJECTED,
                    "physical Memory recovery rejected",
                    "memory.recover",
                )
            )
        if not _driver_accepts(self._driver, "memory.recover", canonical_json({"state_id": value})):
            return MemoryRecoveryResult(
                error=CGSError(
                    ErrorCode.MEMORY_REJECTED,
                    "physical Memory recovery rejected",
                    "memory.recover",
                )
            )
        try:
            records = tuple(_record_from_blob(blob) for blob in self._record_blobs)
            if len({record.record_digest for record in records}) != len(records) or len(
                {record.state_id for record in records}
            ) != len(records):
                raise ValueError("Memory journal contains duplicate identities")
            matches = tuple(record for record in records if record.state_id == value)
            if not matches:
                return MemoryRecoveryResult(
                    error=CGSError(
                        ErrorCode.MEMORY_NOT_FOUND,
                        "authoritative State was not found in Memory",
                        "memory.recover",
                    )
                )
            if len(matches) != 1:
                raise ValueError("Memory journal contains duplicate StateId")
            record = matches[0]
            state = _new_state(
                graph_name=record.graph_name,
                graph_binding=record.graph_binding,
                gateway_binding=record.gateway_binding,
                state_id=record.state_id,
                left=record.left,
                right=record.right,
                payload=record.payload,
            )
            canonical = _record_for_state(state)
            if not canonical.ok or canonical.record != record or state.state_id.value != value:
                raise ValueError("recovery verification failed")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return MemoryRecoveryResult(
                error=CGSError(
                    ErrorCode.MEMORY_CORRUPT,
                    "authoritative State Memory verification failed",
                    "memory.recover",
                )
            )
        return MemoryRecoveryResult(state=state, record=record)

    def _journal_snapshot(self) -> _MemorySnapshot:
        return _MemorySnapshot(tuple(self._record_blobs), self._ms_state_blob)

    def _restore_journal(self, snapshot: _MemorySnapshot) -> None:
        self._record_blobs = tuple(snapshot.record_blobs)
        self._ms_state_blob = snapshot.ms_state_blob


def _verify_commit(
    memory_system: MemorySystem,
    before: _MemorySnapshot,
    record: MemoryRecord,
) -> bool:
    after = memory_system._journal_snapshot()
    blob = _record_blob(record)
    if blob in before.record_blobs:
        return after == before
    expected_records = before.record_blobs + (blob,)
    if after.record_blobs != expected_records or after.ms_state_blob is None:
        return False
    try:
        ms_state = _state_from_blob(after.ms_state_blob)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    journal_digest = hashlib.sha256(
        b"CGS-MS-JOURNAL-v1\x00" + canonical_json(list(expected_records)).encode("utf-8")
    ).hexdigest()
    expected_source = freeze_json(
        {
            "source_state_id": record.state_id,
            "source_graph_binding": record.graph_binding,
            "source_gateway_binding": record.gateway_binding,
            "source_state_digest": record.state_digest,
            "record_digest": record.record_digest,
        }
    )
    return (
        ms_state.graph_name == "MemorySystem"
        and ms_state.graph_binding == memory_system._ms_graph.digest
        and ms_state.gateway_binding == memory_system._ms_gateway.binding
        and ms_state.left == expected_source
        and ms_state.right == expected_source
        and ms_state.payload == freeze_json({"journal_digest": journal_digest})
        and (before.ms_state_blob is None or after.ms_state_blob != before.ms_state_blob)
    )

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from ._authority import CGS_AUTHORITY, require_cgs_authority
from .errors import CGSError, ErrorCode
from .serialization import FrozenJson, canonical_json, thaw_json
from .state import State
from .state_id import StateId


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """Immutable canonical Memory of one complete authoritative State."""

    graph_name: str
    state_id: str
    left: FrozenJson
    right: FrozenJson
    payload: FrozenJson
    validated: bool
    state_digest: str

    def state_data(self) -> dict[str, Any]:
        return {
            "graph_name": self.graph_name,
            "state_id": self.state_id,
            "left": thaw_json(self.left),
            "right": thaw_json(self.right),
            "payload": thaw_json(self.payload),
            "validated": self.validated,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.state_data(), "state_digest": self.state_digest}


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


class MemorySystem:
    """CGS-owned persistence of complete validated authoritative State."""

    __slots__ = ("name", "_records")

    def __init__(self, name: str = "MemorySystem") -> None:
        if not name:
            raise ValueError("MemorySystem.name must be non-empty")
        self.name = name
        self._records: tuple[MemoryRecord, ...] = ()

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        return self._records

    def prepare(self, state: object) -> MemoryResult:
        if not isinstance(state, State) or not state.validated:
            return MemoryResult(
                error=CGSError(
                    ErrorCode.MEMORY_REJECTED,
                    "Memory accepts only a validated authoritative State",
                    "memory.prepare",
                )
            )
        state_data = {
            "graph_name": state.graph_name,
            "state_id": state.state_id.value,
            "left": thaw_json(state.left),
            "right": thaw_json(state.right),
            "payload": thaw_json(state.payload),
            "validated": state.validated,
        }
        digest = hashlib.sha256(canonical_json(state_data).encode("utf-8")).hexdigest()
        return MemoryResult(
            record=MemoryRecord(
                graph_name=state.graph_name,
                state_id=state.state_id.value,
                left=state.left,
                right=state.right,
                payload=state.payload,
                validated=state.validated,
                state_digest=digest,
            )
        )

    def persist(self, state: object, *, _authority: object | None = None) -> MemoryResult:
        prepared = self.prepare(state)
        if not prepared.ok:
            return prepared
        require_cgs_authority(_authority)
        return self._commit(prepared, _authority=_authority)

    def _commit(self, prepared: MemoryResult, *, _authority: object | None = None) -> MemoryResult:
        require_cgs_authority(_authority)
        if not prepared.ok or prepared.record is None:
            return MemoryResult(
                error=CGSError(
                    ErrorCode.MEMORY_REJECTED,
                    "prepared Memory record is invalid",
                    "memory.persist",
                )
            )
        if prepared.record not in self._records:
            self._records = self._records + (prepared.record,)
        return prepared

    def recover(
        self, state_id: StateId | str, *, _authority: object | None = None
    ) -> MemoryRecoveryResult:
        require_cgs_authority(_authority)
        value = state_id.value if isinstance(state_id, StateId) else state_id
        record = next((item for item in reversed(self._records) if item.state_id == value), None)
        if record is None:
            return MemoryRecoveryResult(
                error=CGSError(
                    ErrorCode.MEMORY_NOT_FOUND,
                    "authoritative State was not found in Memory",
                    "memory.recover",
                )
            )
        actual_digest = hashlib.sha256(
            canonical_json(record.state_data()).encode("utf-8")
        ).hexdigest()
        if actual_digest != record.state_digest:
            return MemoryRecoveryResult(
                error=CGSError(
                    ErrorCode.MEMORY_CORRUPT,
                    "authoritative State Memory verification failed",
                    "memory.recover",
                )
            )
        try:
            recovered_id = StateId(record.state_id, _authority=CGS_AUTHORITY)
            state = State(
                graph_name=record.graph_name,
                state_id=recovered_id,
                left=record.left,
                right=record.right,
                payload=record.payload,
                validated=record.validated,
                _authority=CGS_AUTHORITY,
            )
        except (TypeError, ValueError):
            return MemoryRecoveryResult(
                error=CGSError(
                    ErrorCode.MEMORY_CORRUPT,
                    "authoritative State Memory verification failed",
                    "memory.recover",
                )
            )
        return MemoryRecoveryResult(state=state, record=record)

    def _snapshot(self, *, _authority: object) -> tuple[MemoryRecord, ...]:
        require_cgs_authority(_authority)
        return self._records

    def _restore(self, records: tuple[MemoryRecord, ...], *, _authority: object) -> None:
        require_cgs_authority(_authority)
        self._records = records

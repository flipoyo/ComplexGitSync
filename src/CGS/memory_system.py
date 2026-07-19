from __future__ import annotations

from dataclasses import dataclass
import hashlib

from ._authority import _AuthorityScope, require_cgs_authority
from .errors import CGSError, ErrorCode
from .serialization import canonical_json
from .state import State


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    graph_name: str
    state_id: str
    public_digest: str

    def to_dict(self) -> dict[str, str]:
        return {
            "graph_name": self.graph_name,
            "state_id": self.state_id,
            "public_digest": self.public_digest,
        }


@dataclass(frozen=True, slots=True)
class MemoryResult:
    record: MemoryRecord | None = None
    error: CGSError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class MemorySystem:
    """CGS-owned validated public-record persistence boundary."""

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
        public = state.to_public_dict()
        digest = hashlib.sha256(canonical_json(public).encode("utf-8")).hexdigest()
        return MemoryResult(
            record=MemoryRecord(
                graph_name=state.graph_name,
                state_id=state.state_id.value,
                public_digest=digest,
            )
        )

    def persist(self, state: object, *, _authority: _AuthorityScope | None = None) -> MemoryResult:
        prepared = self.prepare(state)
        if not prepared.ok:
            return prepared
        require_cgs_authority(_authority)  # type: ignore[arg-type]
        return self._commit(prepared, _authority=_authority)

    def _commit(
        self, prepared: MemoryResult, *, _authority: _AuthorityScope | None = None
    ) -> MemoryResult:
        require_cgs_authority(_authority)  # type: ignore[arg-type]
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

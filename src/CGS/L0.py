from __future__ import annotations

from dataclasses import dataclass
import hashlib
import threading
import time

from ._authority import CGS_AUTHORITY, require_cgs_authority
from .state_id import StateId


@dataclass(frozen=True, slots=True, repr=False)
class _PrivateAnchor:
    occurrence_ns: int
    value: bytes

    def __repr__(self) -> str:
        return "<_PrivateAnchor redacted>"


class L0:
    """CGS-owned deterministic occurrence anchoring service."""

    __slots__ = ()
    _clock_lock = threading.Lock()
    _last_occurrence_ns = 0

    def __init__(self, *, _authority: object | None = None) -> None:
        require_cgs_authority(_authority)

    @classmethod
    def _next_occurrence_ns(cls, *, _authority: object) -> int:
        require_cgs_authority(_authority)
        with cls._clock_lock:
            occurrence_ns = max(time.time_ns(), cls._last_occurrence_ns + 1)
            cls._last_occurrence_ns = occurrence_ns
            return occurrence_ns

    @staticmethod
    def _encode_occurrence(occurrence_ns: int, *, _authority: object) -> bytes:
        require_cgs_authority(_authority)
        if occurrence_ns < 0:
            raise ValueError("L0 occurrence must be non-negative")
        return b"CGS-L0-v1\x00" + str(occurrence_ns).encode("ascii")

    @classmethod
    def _state_id_for_occurrence(cls, occurrence_ns: int, *, _authority: object) -> StateId:
        anchor_value = cls._encode_occurrence(occurrence_ns, _authority=_authority)
        return StateId(hashlib.sha256(anchor_value).hexdigest(), _authority=CGS_AUTHORITY)

    def _anchor(self, *, _authority: object) -> tuple[_PrivateAnchor, StateId]:
        require_cgs_authority(_authority)
        occurrence_ns = self._next_occurrence_ns(_authority=_authority)
        anchor_value = self._encode_occurrence(occurrence_ns, _authority=_authority)
        anchor = _PrivateAnchor(occurrence_ns=occurrence_ns, value=anchor_value)
        state_id = self._state_id_for_occurrence(occurrence_ns, _authority=_authority)
        return anchor, state_id

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import threading
import time

from .errors import ErrorCode, OwnershipError
from .state_id import StateId, _new_state_id


@dataclass(frozen=True, slots=True, repr=False)
class _PrivateAnchor:
    occurrence_ns: int
    value: bytes

    def __repr__(self) -> str:
        return "<_PrivateAnchor redacted>"


class L0:
    """Public marker for the CGS-owned Time Layer; construction is kernel-only."""

    __slots__ = ()

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("L0 is sealed")

    def __init__(self) -> None:
        raise OwnershipError(
            ErrorCode.OWNERSHIP_VIOLATION,
            "L0 can only be created by the CGS kernel",
        )


_CLOCK_LOCK = threading.Lock()
_LAST_OCCURRENCE_NS = 0


def _new_l0() -> L0:
    return object.__new__(L0)


def _next_occurrence_ns() -> int:
    global _LAST_OCCURRENCE_NS
    with _CLOCK_LOCK:
        occurrence_ns = max(time.time_ns(), _LAST_OCCURRENCE_NS + 1)
        _LAST_OCCURRENCE_NS = occurrence_ns
        return occurrence_ns


def _encode_occurrence(occurrence_ns: int) -> bytes:
    if occurrence_ns < 0:
        raise ValueError("L0 occurrence must be non-negative")
    return b"CGS-L0-v1\x00" + str(occurrence_ns).encode("ascii")


def _state_id_for_occurrence(occurrence_ns: int) -> StateId:
    return _new_state_id(hashlib.sha256(_encode_occurrence(occurrence_ns)).hexdigest())


def _anchor_occurrence(_l0: L0) -> tuple[_PrivateAnchor, StateId]:
    if type(_l0) is not L0:
        raise OwnershipError(
            ErrorCode.OWNERSHIP_VIOLATION,
            "only the CGS kernel Time Layer may anchor an occurrence",
        )
    occurrence_ns = _next_occurrence_ns()
    value = _encode_occurrence(occurrence_ns)
    return _PrivateAnchor(occurrence_ns, value), _state_id_for_occurrence(occurrence_ns)

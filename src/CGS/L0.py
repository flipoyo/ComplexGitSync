from __future__ import annotations

from dataclasses import dataclass
import hashlib

from ._authority import CGS_AUTHORITY, _AuthorityScope, require_cgs_authority
from .serialization import canonical_json
from .state import CandidateState
from .state_id import StateId


@dataclass(frozen=True, slots=True, repr=False)
class _PrivateAnchor:
    value: bytes

    def __repr__(self) -> str:
        return "<_PrivateAnchor redacted>"


class L0:
    """CGS-owned deterministic occurrence anchoring service."""

    __slots__ = ()

    def __init__(self, *, _authority: _AuthorityScope | None = None) -> None:
        require_cgs_authority(_authority)  # type: ignore[arg-type]

    def _anchor(
        self, candidate: CandidateState, *, _authority: _AuthorityScope
    ) -> tuple[_PrivateAnchor, StateId]:
        require_cgs_authority(_authority)
        material = canonical_json(candidate.to_dict()).encode("utf-8")
        anchor = _PrivateAnchor(hashlib.sha256(b"CGS-L0-v1\x00" + material).digest())
        state_id = StateId(hashlib.sha256(anchor.value).hexdigest(), _authority=CGS_AUTHORITY)
        return anchor, state_id

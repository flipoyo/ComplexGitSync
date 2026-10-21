"""omniscience_answer — what every Omniscience query answers with.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: the five outcomes a query can have, the finer reasons a memory read
    can give, and the one value that carries them with the data. Computing the
    answer is `orchestre/omniscience.py`'s; this module only names it.
Imports: (none)

The five outcomes are the whole vocabulary a caller branches on. ``verified``
is only ever said of what was checked: a registration is verified when its
inclusion proof holds against a sound checkpoint, never because a repository
was seen to be there. ``unavailable`` is the honest "cannot tell" (a remote
that did not answer, a commit nobody serves, nothing observed yet) and is never
turned into "deleted". ``unauthorized`` is what the provider said to a reader
it would not serve, and carries no content of the thing refused.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OmniscienceStatus(Enum):
    """The outcome of a query."""

    VERIFIED = "verified"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    NOT_REGISTERED = "not_registered"


class MemoryDetail(Enum):
    """Why a read of a registered ``.memory`` did not verify, finer than its outcome."""

    REMOTE_UNAVAILABLE = "remote_unavailable"
    COMMIT_UNREACHABLE = "commit_unreachable"
    INVALID_PROOF = "invalid_proof"
    LEDGER_CORRUPT = "ledger_corrupt"


#: Keys an answer's own data may not use, because the answer's table is flat.
RESERVED_KEYS = frozenset({"status", "detail", "reason"})


@dataclass(frozen=True, slots=True)
class Answer:
    """One query's answer: its outcome, a finer ``detail`` when there is one, a plain-English ``reason``, and the data."""

    status: OmniscienceStatus
    detail: str | None = None
    reason: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        clash = RESERVED_KEYS & set(self.data)
        if clash:
            raise ValueError(f"an answer's data cannot use the reserved keys {sorted(clash)}")

    @property
    def verified(self) -> bool:
        """Whether the outcome is ``verified``."""
        return self.status is OmniscienceStatus.VERIFIED

    def with_data(self, **more: Any) -> Answer:
        """The same answer with *more* added to its data."""
        return Answer(self.status, self.detail, self.reason, {**self.data, **more})

    def to_dict(self) -> dict[str, Any]:
        """The answer as one flat, JSON-ready table: ``status``, ``detail``, ``reason``, then the data."""
        return {"status": self.status.value, "detail": self.detail, "reason": self.reason, **self.data}


class Stop(Exception):
    """Raised inside a query to end it with *answer*, the way a refusal is raised elsewhere."""

    def __init__(self, answer: Answer) -> None:
        super().__init__(answer.reason)
        self.answer = answer


__all__ = ["RESERVED_KEYS", "Answer", "MemoryDetail", "OmniscienceStatus", "Stop"]

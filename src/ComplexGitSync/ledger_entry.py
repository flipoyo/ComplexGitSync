"""ledger_entry — hash-chained register entry construction.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: given the previous chain entry (or none, for genesis) and the
    facts of one operation, deterministically construct the next
    ``LedgerEntry`` — computing ``prev``/``entry_hash`` correctly. Chain
    *verification* across many entries is ``integrity.py``'s contract, not
    this module's.
Imports: none

Design reference: ``AgentSpecs/IsolationPlan.md`` §2.2 (hash-chained
register schema) and §3.3 (``ClockProtocol``). This module also absorbs the
responsibility of ``L0.py``'s ``new_time_l0_anchor()``/``hash_time_l0_anchor()``
(see ``AgentSpecs/IsolationPlan.md``'s feasibility review, and §3.3): the
same private TIME-L0 anchor generation, but driven through an injectable
:class:`ClockProtocol` instead of reading ``datetime.now(UTC)``,
``time.time_ns()``, ``os.getpid()``, and ``secrets.token_hex()`` directly,
so it is fully deterministic under test with a fake clock. ``L0.py`` itself
is not edited by this module — a later integration step wires the two
together.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, Sequence

# Genesis predecessor hash — the fixed all-zero sentinel a chain's first
# entry points at, per IsolationPlan.md §2.2 ("Genesis entry: prev =
# 'sha256:' + '0' * 64").
_GENESIS_PREV = "sha256:" + "0" * 64


class ClockProtocol(Protocol):
    """Everything a caller needs to inject to make entry creation and
    TIME-L0 anchor generation fully deterministic — no direct clock, PID,
    or entropy reads anywhere in this module.
    """

    def now(self) -> datetime:
        """Current instant. Must be timezone-aware; UTC is assumed."""
        ...

    def time_ns(self) -> int:
        """High-resolution nanosecond counter, for anchor entropy."""
        ...

    def pid(self) -> int:
        """Current process id, for anchor entropy."""
        ...

    def token_hex(self, nbytes: int) -> str:
        """Random hex token, for anchor entropy."""
        ...


@dataclass(frozen=True, slots=True)
class TimeL0State:
    """Public identity derived from a private TIME-L0 anchor.

    Equivalent in shape to ``L0.py``'s ``TimeL0State`` — reimplemented here
    (not imported; Ring-0 modules are self-contained, see module docstring)
    so this module owns the injectable-clock variant of anchor generation.
    """

    state_hash: str

    @property
    def state_id(self) -> str:
        return f"state({self.state_hash})"


def hash_time_l0_anchor(anchor: str) -> str:
    """Return ``HASH(.@)`` for a private TIME-L0 anchor.

    Pure hashing logic, carried over as-is from ``L0.py``.
    """

    return hashlib.sha256(f".{anchor}".encode()).hexdigest()


def new_time_l0_anchor(clock: ClockProtocol) -> TimeL0State:
    """Create a private TIME-L0 anchor for one generated State.

    Same construction as ``L0.py``'s ``new_time_l0_anchor()``, but every
    entropy source (clock, high-resolution counter, PID, random token) is
    read through ``clock`` instead of the real ``datetime``/``time``/``os``/
    ``secrets`` modules — so this is deterministic and testable with a fake.
    """

    instant = clock.now().astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    private_anchor = (
        f"TIME-L0:{instant}:{clock.time_ns()}:{clock.pid()}:{clock.token_hex(16)}"
    )
    return TimeL0State(state_hash=hash_time_l0_anchor(private_anchor))


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One hash-chained ``.lgr`` register entry.

    Schema fixed by ``AgentSpecs/IsolationPlan.md`` §2.2 — do not add or
    rename fields without updating that document first.
    """

    seq: int
    prev: str
    recorded_at: str
    command: str
    argv: tuple[str, ...]
    state_id: str
    state_dir: str
    outcome: str
    entry_hash: str


def _canonical_payload(
    *,
    seq: int,
    prev: str,
    recorded_at: str,
    command: str,
    argv: Sequence[str],
    state_id: str,
    state_dir: str,
    outcome: str,
) -> dict[str, Any]:
    """Every ``LedgerEntry`` field except ``entry_hash`` itself, as a plain
    dict ready for canonical serialisation.
    """

    return {
        "seq": seq,
        "prev": prev,
        "recorded_at": recorded_at,
        "command": command,
        "argv": list(argv),
        "state_id": state_id,
        "state_dir": state_dir,
        "outcome": outcome,
    }


def _canonical_json(payload: dict[str, Any]) -> str:
    """Same canonicalisation discipline ``GtsDocument.compute_snapshot_hash``
    already uses in ``orchestre.py`` — stable key ordering, compact
    separators, no ASCII escaping. One canonicalisation idea, two users
    (``IsolationPlan.md`` §2.2); reimplemented here rather than imported,
    since Ring 0 cannot depend on Ring 3.
    """

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_entry_hash(
    *,
    seq: int,
    prev: str,
    recorded_at: str,
    command: str,
    argv: Sequence[str],
    state_id: str,
    state_dir: str,
    outcome: str,
) -> str:
    """Compute ``entry_hash`` over the canonical serialisation of every
    other field, including ``prev`` — so editing any field, or splicing in
    a different predecessor, changes the hash.
    """

    payload = _canonical_payload(
        seq=seq,
        prev=prev,
        recorded_at=recorded_at,
        command=command,
        argv=argv,
        state_id=state_id,
        state_dir=state_dir,
        outcome=outcome,
    )
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_next_entry(
    prev: LedgerEntry | None,
    *,
    command: str,
    argv: Sequence[str],
    state_id: str,
    state_dir: str,
    outcome: str,
    clock: ClockProtocol,
) -> LedgerEntry:
    """Build the next entry in the chain following ``prev``.

    ``prev=None`` builds the genesis entry: ``seq=1`` and
    ``prev="sha256:" + "0" * 64``. Otherwise ``seq = prev.seq + 1`` and
    ``prev = prev.entry_hash`` — the new entry's ``prev`` field literally is
    its predecessor's ``entry_hash``, closing the chain link.
    """

    seq = 1 if prev is None else prev.seq + 1
    prev_hash = _GENESIS_PREV if prev is None else prev.entry_hash
    recorded_at = clock.now().astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    argv_tuple = tuple(argv)

    entry_hash = compute_entry_hash(
        seq=seq,
        prev=prev_hash,
        recorded_at=recorded_at,
        command=command,
        argv=argv_tuple,
        state_id=state_id,
        state_dir=state_dir,
        outcome=outcome,
    )

    return LedgerEntry(
        seq=seq,
        prev=prev_hash,
        recorded_at=recorded_at,
        command=command,
        argv=argv_tuple,
        state_id=state_id,
        state_dir=state_dir,
        outcome=outcome,
        entry_hash=entry_hash,
    )

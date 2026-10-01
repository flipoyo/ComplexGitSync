"""as_of — "what was this tree at time T?" answered from the ledger's own order.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: turn what a person typed into the one UTC form every ledger entry's
    ``recorded_at`` is written in, and pick, from entries in chain order, the last
    one recorded at or before it. Whether the chain deserves to be believed is
    `integrity.py`'s question; this module only selects.
Imports: errors

Why chain order and not the nearest time
----------------------------------------
The hash chain fixes the order of entries beyond dispute; each entry's
timestamp is only a claim. So the answer is the *last entry in the chain* whose
claim is at or before T — never "the closest in time", which on a chain whose
clock ran backwards would pick an entry the workspace did not hold at T. A chain
whose timestamps disagree with its own order is exactly what
`Finding.TIME_REGRESSION` reports, and the caller says so beside the answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from typing import Protocol

from ..errors import GitSyncError


class _Entry(Protocol):
    """What selection needs of a ledger entry; `LedgerEntry` satisfies it."""

    seq: int
    recorded_at: str


class AsOf:
    """Parse a moment and select the ledger entry that held at it."""

    #: The form every ledger entry writes: fixed-width, UTC, so strings compare chronologically.
    FORMAT = "%Y-%m-%dT%H:%M:%SZ"

    @staticmethod
    def parse_moment(text: str) -> str:
        """The UTC ``YYYY-MM-DDTHH:MM:SSZ`` *text* names.

        Accepts an ISO-8601 date or date-time. A time with no offset is **UTC** —
        the ledger records UTC, so what is typed and what is printed by
        ``memory explore --timeline`` agree; write ``+02:00`` for another zone. A
        bare date means the **end** of that day (23:59:59), so "what was the tree
        on 30 September?" includes the whole of the 30th. Fractions of a second are
        floored: entries have one-second resolution, so the entry at second *S* is
        at or before *T* exactly when *S* is at or before floor(*T*).
        """
        raw = text.strip().upper()  # 't' and 'z' are as good as 'T' and 'Z'
        try:
            if "T" not in raw and " " not in raw:
                moment = datetime.combine(date.fromisoformat(raw), time(23, 59, 59), tzinfo=UTC)
            else:
                moment = datetime.fromisoformat(raw.replace(" ", "T", 1))
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=UTC)
            return moment.astimezone(UTC).strftime(AsOf.FORMAT)
        except (ValueError, OverflowError) as exc:  # OverflowError: a year the UTC conversion cannot hold
            raise GitSyncError(
                f"cannot read {text!r} as a moment. Write an ISO-8601 date or time, in UTC "
                "unless it carries an offset: 2026-09-30, 2026-09-30T17:00, "
                "2026-09-30 17:00:00, 2026-09-30T17:00+02:00."
            ) from exc

    @staticmethod
    def select(entries: Sequence[_Entry], moment: str) -> _Entry | None:
        """The last entry, in chain order, whose ``recorded_at`` is at or before *moment*.

        *moment* is :meth:`parse_moment`'s output. ``None`` when nothing was
        recorded yet at *moment*: a time before the first entry answers "nothing
        recorded", never the genesis entry by accident. An entry with no
        ``recorded_at`` cannot be placed in time and is skipped.
        """
        chosen: _Entry | None = None
        for entry in sorted(entries, key=lambda item: item.seq):
            recorded = entry.recorded_at or ""
            if recorded and recorded <= moment:
                chosen = entry
        return chosen

    @staticmethod
    def first_recorded_at(entries: Sequence[_Entry]) -> str | None:
        """When the chain's first dated entry says it was written, to explain a "nothing yet"."""
        dated = [entry.recorded_at for entry in sorted(entries, key=lambda item: item.seq) if entry.recorded_at]
        return dated[0] if dated else None


__all__ = ["AsOf"]

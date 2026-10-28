"""conformity_scale — the scale a conformity score is read against.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: state the maximum of each conformity criterion and of the total,
    refuse a score outside its range, and write a score the way it is always
    shown: every number next to its maximum. Stores nothing.
Imports: none
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ConformityScale:
    """The owner's weighting of an orchestrator's quote: 33 + 33 + 34 = 100.

    Each criterion is scored from 0 up to its own maximum — spec respect 33,
    `.PUBLIC`/`.PRIVATE` gating 33, quality of production 34 — and the
    **total is the plain sum** of the three, out of 100. There is no other
    weighting, rounding or averaging. A score is never shown without its
    maximum, so "27" is never mistaken for a percentage.
    """

    MAXIMUM: Mapping[str, int] = {"spec_respect": 33, "gating": 33, "quality": 34}
    TOTAL: int = sum(MAXIMUM.values())

    @classmethod
    def check(cls, criterion: str, score: float) -> None:
        """Raise `ValueError` when *score* is outside 0 to the criterion's maximum."""
        if not 0 <= score <= cls.MAXIMUM[criterion]:
            raise ValueError(f"{criterion} score must be between 0 and {cls.MAXIMUM[criterion]}, got {score:g}")

    @classmethod
    def argument(cls, criterion: str):
        """A command-line type for *criterion*'s score: a number in range, or a clean usage error."""

        def parse(text: str) -> float:
            value = float(text)
            cls.check(criterion, value)
            return value

        parse.__name__ = f"score from 0 to {cls.MAXIMUM[criterion]}"
        return parse

    @classmethod
    def total(cls, conformity: Mapping[str, Any]) -> float:
        """The sum of the three scores in a record's ``conformity`` table."""
        return sum(float(conformity[name]["score"]) for name in cls.MAXIMUM)

    @classmethod
    def render(cls, conformity: Mapping[str, Any]) -> str:
        """One line, always with the maxima: ``spec_respect=26/33 (asserted) ... total=84/100``."""
        parts = [
            f"{name}={conformity[name]['score']:g}/{limit} ({conformity[name]['basis']})"
            for name, limit in cls.MAXIMUM.items()
        ]
        return f"{'  '.join(parts)}  total={cls.total(conformity):g}/{cls.TOTAL}"


__all__ = ["ConformityScale"]

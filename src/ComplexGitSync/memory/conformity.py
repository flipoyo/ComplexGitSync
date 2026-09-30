"""conformity — the score an orchestrator gives a piece of agent work.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: own the shape of the three-criterion conformity score a
    self-history record carries, and the validation that keeps an asserted
    judgement from being dressed up as a measured fact. Stores nothing.
Imports: none
"""

from __future__ import annotations

from dataclasses import dataclass

#: Whether a conformity criterion's score is a fact the tool checked, or a
#: judgement the orchestrator is asserting — see the ticket §3. A record
#: must say which; there is no third option that hides the distinction.
VALID_CONFORMITY_BASES = frozenset({"measured", "asserted"})


@dataclass(frozen=True, slots=True)
class ConformityCriterion:
    """One third of the score (ticket §3): a value, its basis, and why.

    ``basis`` is the load-bearing field — it is what keeps a criterion
    the tool could not check from being dressed up as one it did.
    """

    score: float
    basis: str
    reasoning: str

    def __post_init__(self) -> None:
        if self.basis not in VALID_CONFORMITY_BASES:
            raise ValueError(
                f"conformity basis must be one of {sorted(VALID_CONFORMITY_BASES)}, "
                f"got {self.basis!r}"
            )
        if not self.reasoning.strip():
            raise ValueError("conformity reasoning must not be empty")
        # Coerced to float *before* the first write: TOML distinguishes an
        # integer from a float, so an int written once and a float read
        # back afterwards (from_dict below) would digest differently for
        # the same score, breaking the round trip a content hash promises.
        object.__setattr__(self, "score", float(self.score))

    def to_dict(self) -> dict[str, object]:
        return {"score": self.score, "basis": self.basis, "reasoning": self.reasoning}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ConformityCriterion:
        return cls(
            score=float(value["score"]),  # type: ignore[arg-type]
            basis=str(value["basis"]),
            reasoning=str(value["reasoning"]),
        )


@dataclass(frozen=True, slots=True)
class ConformityScore:
    """The three criteria the owner's ticket weighs 33/33/34 (ticket §3)."""

    spec_respect: ConformityCriterion
    gating: ConformityCriterion
    quality: ConformityCriterion

    def to_dict(self) -> dict[str, object]:
        return {
            "spec_respect": self.spec_respect.to_dict(),
            "gating": self.gating.to_dict(),
            "quality": self.quality.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ConformityScore:
        return cls(
            spec_respect=ConformityCriterion.from_dict(value["spec_respect"]),  # type: ignore[arg-type]
            gating=ConformityCriterion.from_dict(value["gating"]),  # type: ignore[arg-type]
            quality=ConformityCriterion.from_dict(value["quality"]),  # type: ignore[arg-type]
        )


__all__ = [
    "VALID_CONFORMITY_BASES",
    "ConformityCriterion",
    "ConformityScore",
]

"""conformity — the score an orchestrator gives a piece of agent work.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: own the shape of the three-criterion conformity score a
    self-history record carries, and the validation that keeps an asserted
    judgement from being dressed up as a measured fact. Stores nothing.
Imports: conformity_scale
"""

from __future__ import annotations

from dataclasses import dataclass

from .conformity_scale import ConformityScale

#: Whether a criterion's score is a fact the tool checked or a judgement the
#: orchestrator asserts. A record must say which.
VALID_CONFORMITY_BASES = frozenset({"measured", "asserted"})


@dataclass(frozen=True, slots=True)
class ConformityCriterion:
    """One criterion: a score, its basis (``measured``/``asserted``), and why."""

    score: float
    basis: str
    reasoning: str

    def __post_init__(self) -> None:
        if self.basis not in VALID_CONFORMITY_BASES:
            raise ValueError(f"conformity basis must be one of {sorted(VALID_CONFORMITY_BASES)}, got {self.basis!r}")
        if not self.reasoning.strip():
            raise ValueError("conformity reasoning must not be empty")
        # A float from the start: TOML tells an int from a float, so an int written and a float read back would digest differently.
        object.__setattr__(self, "score", float(self.score))

    def to_dict(self) -> dict[str, object]:
        return {"score": self.score, "basis": self.basis, "reasoning": self.reasoning}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ConformityCriterion:
        return cls(score=float(value["score"]), basis=str(value["basis"]), reasoning=str(value["reasoning"]))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ConformityScore:
    """The three criteria, scored on `ConformityScale` (33 + 33 + 34 = 100).

    ``explanation`` is the orchestrator's short account of how the total came
    about; optional, and left out of the stored record when empty.
    """

    spec_respect: ConformityCriterion
    gating: ConformityCriterion
    quality: ConformityCriterion
    explanation: str = ""

    def __post_init__(self) -> None:
        for name in ConformityScale.MAXIMUM:
            ConformityScale.check(name, getattr(self, name).score)

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {name: getattr(self, name).to_dict() for name in ConformityScale.MAXIMUM}
        return {**data, "explanation": self.explanation} if self.explanation.strip() else data

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ConformityScore:
        criteria = {name: ConformityCriterion.from_dict(value[name]) for name in ConformityScale.MAXIMUM}  # type: ignore[arg-type]
        return cls(**criteria, explanation=str(value.get("explanation", "")))


__all__ = ["VALID_CONFORMITY_BASES", "ConformityCriterion", "ConformityScore"]

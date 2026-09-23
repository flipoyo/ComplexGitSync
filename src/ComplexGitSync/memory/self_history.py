"""self_history — one record per piece of agent work, content-addressed.

Ring: 1 (filesystem only, no subprocess, no clock)
Contract: own the ``SelfHistoryRecord`` shape and its atomic, content-addressed
    storage under a caller-given pending directory (``.cgitsync/.self-history/``)
    and the read-both-halves merge over a caller-given ``cgitsync_dir``, the
    same pattern ``pending.py`` already uses for the other five kinds of
    memory content. Decides nothing about *how* a record's facts were
    gathered, and nothing about Git — the pending half is a plain,
    gitignored directory today; folding it into a repository of its own is
    a separate work package (AgentReport ticket, WP2) this module does not
    assume has landed.
Imports: repository, states

See ``.agent/.local/.localSpec/DevTickets/openTickets/main_1-1_AgentReport_DevPlanTicket.md``
§1 for the record's field-by-field rationale (this module implements WP1
and WP6 only — see that ticket's Status column for what is not yet built)
and ``.agent/.local/.claude/CLAUDE.md``'s *Attribution* section for why this
content is private and never published.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from .repository import MEMORY_SUBDIR_NAME
from .states import _parse_state_hash

SELF_HISTORY_PENDING_DIR_NAME = ".self-history"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

#: This project's own narrowing of the six-role template — see
#: ``.agent/.local/.localSpec/AGENT.md``. A record names the role an agent
#: wore while doing the work, not a job title.
VALID_AGENT_ROLES = frozenset(
    {"Orchestration", "Dev", "CI/CD", "Editing", "Maths", "Scientific editing"}
)

#: Whether a conformity criterion's score is a fact the tool checked, or a
#: judgement the orchestrator is asserting — see the ticket §3. A record
#: must say which; there is no third option that hides the distinction.
VALID_CONFORMITY_BASES = frozenset({"measured", "asserted"})


@dataclass(frozen=True, slots=True)
class AgentInfo:
    """One agent's identity as it appears in a record: role, vendor, model.

    Two of these appear per record (``worker``, ``orchestrator``) — see
    the ticket §1.1. Neither is a signature; both are a provenance claim
    the orchestrator is making about who did what.
    """

    role: str
    vendor: str
    model: str

    def __post_init__(self) -> None:
        if self.role not in VALID_AGENT_ROLES:
            raise ValueError(
                f"agent role must be one of {sorted(VALID_AGENT_ROLES)}, got {self.role!r}"
            )
        if not self.vendor.strip():
            raise ValueError("agent vendor must not be empty")
        if not self.model.strip():
            raise ValueError("agent model must not be empty")

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "vendor": self.vendor, "model": self.model}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> AgentInfo:
        return cls(
            role=str(value["role"]), vendor=str(value["vendor"]), model=str(value["model"])
        )


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


def _validate_line_limit(field_name: str, value: str, *, max_lines: int = 3) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if len(value.splitlines()) > max_lines:
        raise ValueError(f"{field_name} must be at most {max_lines} lines of plain English")


@dataclass(frozen=True, slots=True)
class SelfHistoryRecord:
    """One record: a transition between two project states, and who made it.

    Every field here is either **declared** (the two agents' identity,
    ``ticket``/``goal``/``action``) or **observed** (everything else) —
    the ticket's own distinction (§1), kept explicit in this module by
    which fields the caller must supply outright versus which
    ``memory.self_history_add`` (WP1, in ``orchestre.py``) fills in from
    what the tool can itself check. This dataclass does not enforce that
    split — it is a plain, honest container; the split is a contract
    between the client method and its caller, stated in ``AdditionalSpecs.md``.
    """

    ticket: str
    goal: str
    action: str
    worker: AgentInfo
    orchestrator: AgentInfo
    conformity: ConformityScore
    recorded_at: str
    state_before: str = ""
    state_after: str = ""
    contract: str = ""
    repos_written: tuple[tuple[str, str], ...] = ()
    lint_passed: bool | None = None
    tests_passed: bool | None = None
    status_errors: int | None = None
    pushed: bool = False
    pushed_reason: str = ""

    def __post_init__(self) -> None:
        _validate_line_limit("goal", self.goal)
        _validate_line_limit("action", self.action)
        if not self.ticket.strip():
            raise ValueError("ticket must not be empty")
        for label, value in (("state_before", self.state_before), ("state_after", self.state_after)):
            if value and _parse_state_hash(value) is None:
                raise ValueError(f"{label} must be a state(<hash>) id or empty, got {value!r}")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ticket": self.ticket,
            "goal": self.goal,
            "action": self.action,
            "worker": self.worker.to_dict(),
            "orchestrator": self.orchestrator.to_dict(),
            "conformity": self.conformity.to_dict(),
            "recorded_at": self.recorded_at,
            "state_before": self.state_before,
            "state_after": self.state_after,
            "contract": self.contract,
            "repos_written": [{"repo": repo, "scope": scope} for repo, scope in self.repos_written],
            "pushed": self.pushed,
            "pushed_reason": self.pushed_reason,
        }
        checks: dict[str, object] = {}
        if self.lint_passed is not None:
            checks["lint_passed"] = self.lint_passed
        if self.tests_passed is not None:
            checks["tests_passed"] = self.tests_passed
        if self.status_errors is not None:
            checks["status_errors"] = self.status_errors
        if checks:
            payload["checks"] = checks
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> SelfHistoryRecord:
        checks = value.get("checks", {})
        assert isinstance(checks, dict)
        repos_written = tuple(
            (str(row["repo"]), str(row["scope"]))  # type: ignore[index]
            for row in value.get("repos_written", [])  # type: ignore[union-attr]
        )
        return cls(
            ticket=str(value["ticket"]),
            goal=str(value["goal"]),
            action=str(value["action"]),
            worker=AgentInfo.from_dict(value["worker"]),  # type: ignore[arg-type]
            orchestrator=AgentInfo.from_dict(value["orchestrator"]),  # type: ignore[arg-type]
            conformity=ConformityScore.from_dict(value["conformity"]),  # type: ignore[arg-type]
            recorded_at=str(value["recorded_at"]),
            state_before=str(value.get("state_before", "")),
            state_after=str(value.get("state_after", "")),
            contract=str(value.get("contract", "")),
            repos_written=repos_written,
            lint_passed=checks.get("lint_passed"),
            tests_passed=checks.get("tests_passed"),
            status_errors=checks.get("status_errors"),
            pushed=bool(value.get("pushed", False)),
            pushed_reason=str(value.get("pushed_reason", "")),
        )

    def digest(self) -> str:
        canonical = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_path(base_dir: Path, record_hash: str) -> Path:
    """Return the canonical path for *record_hash* under *base_dir*."""
    if not _HASH_RE.fullmatch(record_hash):
        raise ValueError("self-history record hash must be 64 lowercase hexadecimal characters")
    return base_dir / f"{record_hash}.toml"


def write_record(base_dir: Path, record: SelfHistoryRecord) -> Path:
    """Atomically persist *record* under the name of its own digest."""
    digest = record.digest()
    destination = record_path(base_dir, digest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = tomli_w.dumps(record.to_dict()).encode("utf-8")
    if destination.is_file():
        if destination.read_bytes() != content:
            raise ValueError(f"self-history record digest collision at {destination}")
        return destination

    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def read_record(path: Path) -> SelfHistoryRecord:
    """Load a self-history record and verify its name matches its content."""
    with path.open("rb") as handle:
        record = SelfHistoryRecord.from_dict(tomllib.load(handle))
    expected = path.stem
    if record.digest() != expected:
        raise ValueError(f"self-history record digest does not match its filename: {path}")
    return record


def self_history_dirs(cgitsync_dir: Path) -> tuple[Path, Path]:
    """``(folded, pending)`` for self-history, one level deeper than ``.memory``.

    The folded half (``.cgitsync/.memory/.self-history``) is a repository
    of its own once AgentReport WP2 lands; today it may not exist at all,
    which is exactly why every reader here treats a missing directory as
    "nothing folded yet" rather than an error — the same stance
    ``pending.py`` takes for every other kind of memory content.
    """
    return (
        cgitsync_dir / MEMORY_SUBDIR_NAME / SELF_HISTORY_PENDING_DIR_NAME,
        cgitsync_dir / SELF_HISTORY_PENDING_DIR_NAME,
    )


def read_records(cgitsync_dir: Path) -> list[SelfHistoryRecord]:
    """Every record this workspace holds, folded and pending, oldest first.

    Sorted by ``recorded_at`` rather than filename: a record's name is its
    content hash, which carries no order at all, unlike a ledger entry's
    ``seq``.
    """
    folded_dir, pending_dir = self_history_dirs(cgitsync_dir)
    records = [
        read_record(path)
        for directory in (folded_dir, pending_dir)
        if directory.is_dir()
        for path in sorted(directory.glob("*.toml"))
    ]
    records.sort(key=lambda record: record.recorded_at)
    return records

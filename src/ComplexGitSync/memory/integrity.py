"""integrity — register verification: Finding taxonomy and chain-arithmetic checks.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: given a sequence of entries, decide whether the chain is intact.
Imports: ledger_entry

One canonicalisation, shared
----------------------------
``recompute_entry_hash`` below delegates to
``ledger_entry.compute_entry_hash``. It used to reimplement the same
routine, deliberately, because the two modules were authored in parallel
and this one depends on a structural protocol rather than that module's
concrete class. Both have existed side by side for weeks, so the copy is
gone: one canonicalisation, and no way for a verifier to disagree with a
writer about what an entry hashes to.

The protocol below is still the contract — nothing here imports
``LedgerEntry`` itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Protocol, Sequence

from .ledger_entry import compute_entry_hash

HASH_ALGORITHM = "sha256"

#: The `prev` value carried by the genesis (first) entry of a register —
#: an all-zero digest. Schema: `.agent/.local/.localSpec/AdditionalSpecs.md`, *The
#: hash-chained register*.
GENESIS_PREV = f"{HASH_ALGORITHM}:" + "0" * 64


class LedgerEntryLike(Protocol):
    """Structural shape of one register entry.

    The nine fields are fixed by `.agent/.local/.localSpec/AdditionalSpecs.md`'s *The
    hash-chained register* section; adding or renaming one is a change to
    that section first.

    Any object with these attributes satisfies this protocol — including,
    once it exists, `ledger_entry.py`'s concrete entry class. Nothing in
    this module imports that class; this Protocol is the entire contract
    between the two work packages.
    """

    seq: int
    prev: str
    recorded_at: str
    command: str
    argv: Sequence[str]
    state_id: str
    state_dir: str
    outcome: str
    entry_hash: str


class Finding(Enum):
    """Taxonomy of register-integrity problems.

    Listed in `.agent/.local/.localSpec/AdditionalSpecs.md`, *The hash-chained register*.

    All eleven members are defined here because the type is shared with the
    later `verify_store()` work (Ring 1, filesystem-backed, out of scope for
    this module). `verify_chain()` below — pure arithmetic over the entry
    sequence — only ever produces the first four and `TIME_REGRESSION`.
    """

    BROKEN_LINK = auto()  # prev mismatch — history was rewritten
    BAD_ENTRY_HASH = auto()  # entry edited in place
    SEQ_GAP = auto()  # entries removed
    SEQ_DUPLICATE = auto()  # concurrent write slipped through
    MISSING_STATE = auto()  # entry references an absent state directory
    ORPHAN_STATE = auto()  # state directory with no register entry
    STATE_DIGEST_MISMATCH = auto()  # directory contents no longer hash to its name
    HEAD_STALE = auto()  # cached HEAD disagrees with recomputed chain
    ORPHAN_COMMIT_LOG = auto()  # commit messages kept for a State that is gone
    COMMIT_LOG_MISMATCH = auto()  # commit rows edited since the entry vouched for them
    TIME_REGRESSION = auto()  # recorded_at moved backwards along the chain


#: Findings that mean the history itself does not hold — the chain was
#: rewritten, truncated, edited, or no longer matches what it vouches for.
#: Anything in here makes a pass `CORRUPT`.
_STRUCTURAL_FINDINGS = frozenset({
    Finding.BROKEN_LINK,
    Finding.BAD_ENTRY_HASH,
    Finding.SEQ_GAP,
    Finding.SEQ_DUPLICATE,
    Finding.MISSING_STATE,
    Finding.STATE_DIGEST_MISMATCH,
    Finding.HEAD_STALE,
    Finding.ORPHAN_COMMIT_LOG,
    Finding.COMMIT_LOG_MISMATCH,
})


class HistoryState(Enum):
    """What a verification pass actually found, in one word.

    A check that cannot fail is not a check. Reading an empty directory and
    calling the chain clean answered "yes" for every workspace in
    existence, including a tampered one — so "I verified a chain" and "there
    was nothing to verify" are now different answers, and so is "there is
    history here that this format cannot verify".

    The five are fixed by `.agent/.local/.localSpec/AdditionalSpecs.md`, *The hash-chained
    register*.
    """

    VERIFIED = auto()  # a non-empty chain was read and every link held
    NO_HISTORY = auto()  # nothing recorded here yet — not a failure
    LEGACY = auto()  # single-file register: readable, not verifiable
    CORRUPT = auto()  # a chain was read and it does not hold
    TIME_INCONSISTENT = auto()  # the chain holds; its own timestamps do not


def resolve_state(findings: Sequence[tuple[int, Finding, str]]) -> HistoryState:
    """The verdict a non-empty chain's *findings* add up to.

    The single authority on which findings mean what, so a chain-level pass
    and a store-level one cannot drift into disagreeing about the same
    finding. Three tiers, in precedence order:

    1. **Structural** (:data:`_STRUCTURAL_FINDINGS`) — `CORRUPT`. The
       history does not hold.
    2. **`TIME_REGRESSION`** — `TIME_INCONSISTENT`. The history *does*
       hold: every link checked out, and only the clock that stamped it
       moved backwards. Calling that "corrupt" would be false, and this
       project has already paid once for reporting an intact artefact as
       corrupt (`.agent/.local/.localSpec/AdditionalSpecs.md`, *What a State's name is
       computed from* — the version-2 canonicalisation story): it is the
       worst answer available, because it invites deleting the one thing
       that was fine. Its own verdict, exiting non-zero, says "something is
       wrong here and it is not your history".
    3. **`ORPHAN_STATE`** — no verdict change. Every workspace used before
       the ledger existed holds States no entry records; they are history,
       not damage. Reported, never fatal.
    """
    kinds = {finding for _seq, finding, _detail in findings}
    if kinds & _STRUCTURAL_FINDINGS:
        return HistoryState.CORRUPT
    if Finding.TIME_REGRESSION in kinds:
        return HistoryState.TIME_INCONSISTENT
    return HistoryState.VERIFIED


@dataclass
class VerificationReport:
    """The result of a verification pass: what was found, and where."""

    findings: list[tuple[int, Finding, str]] = field(default_factory=list)
    state: HistoryState = HistoryState.NO_HISTORY

    @property
    def is_clean(self) -> bool:
        """True when no findings were recorded — nothing wrong was detected.

        Deliberately **not** the same question as "is this history
        verified". An empty register has no findings and is not evidence of
        anything; :attr:`state` is what says which of the four answers this
        pass reached.
        """
        return not self.findings

    @property
    def is_verified(self) -> bool:
        """True only when a real chain was read and it held."""
        return self.state is HistoryState.VERIFIED


def recompute_entry_hash(entry: LedgerEntryLike) -> str:
    """Recompute what `entry.entry_hash` should be, from its other fields.

    Pure function of `entry`'s own current field values — it never looks at
    any other entry. Comparing this against the entry's stored `entry_hash`
    is exactly `BAD_ENTRY_HASH` detection.

    ``ledger_entry.compute_entry_hash`` is the same function the writer uses; a verifier with its own copy of a hash rule is
    a verifier that can disagree with the writer and be wrong about it.
    """
    return compute_entry_hash(
        seq=entry.seq,
        prev=entry.prev,
        recorded_at=entry.recorded_at,
        command=entry.command,
        argv=entry.argv,
        state_id=entry.state_id,
        state_dir=entry.state_dir,
        outcome=entry.outcome,
        toolchain=getattr(entry, "toolchain", ()),
        # Both of these are read through getattr because this module's
        # contract is the Protocol above, not a concrete class — and an
        # entry written before either field existed has neither. The
        # Writer defaults preserve hashes of entries from before additive fields.
        commit_log=getattr(entry, "commit_log", ""),
        environment=getattr(entry, "environment", ""),
    )


def verify_chain(entries: Sequence[LedgerEntryLike]) -> VerificationReport:
    """Pure chain-arithmetic verification over `entries`.

    `entries` must be given in chain (append) order — the order entries were
    originally recorded in, e.g. ascending by `seq` for an uncorrupted
    register. An empty sequence produces no findings and
    `HistoryState.NO_HISTORY`: there was nothing to check, which is not the
    same answer as "checked, and it holds".

    Checks performed, each independent of the others:

    - **Sequential `seq`, no gaps or duplicates.** Computed once up front
      over the full (seq-sorted) set, so a deleted or doubled entry is
      reported regardless of where it falls in the hash-chain pass below.
    - **Monotonic time.** `recorded_at` must never decrease along the
      chain. See :func:`_check_time_monotonic`.
    - **Hash chain.** Walked once, in the given order, tracking an
      `expected_prev` cursor that starts at `GENESIS_PREV`. For each entry:
      `entry.prev` is compared against `expected_prev` (`BROKEN_LINK` on
      mismatch), then `entry.entry_hash` is compared against
      `recompute_entry_hash(entry)` (`BAD_ENTRY_HASH` on mismatch).

      Once a `BROKEN_LINK` is found, every entry from that point on is
      *also* reported `BROKEN_LINK`, without re-attempting to resynchronise
      against a later entry's own hash. This is a deliberate, conservative
      choice matching the threat model's tamper-*evidence* goal
      (`.agent/.local/.localSpec/AdditionalSpecs.md`, *The hash-chained register* —
      tamper-evidence): a single rewritten or deleted entry means
      nothing downstream of it can be trusted to still describe the real
      history, even if the raw bytes of later entries happen to still be
      self-consistent among themselves — so `verify` should say so, loudly,
      for the whole remainder of the chain rather than only at the seam.
    """
    findings: list[tuple[int, Finding, str]] = []
    if not entries:
        # No chain was read. Whether that means "nothing recorded yet" or
        # "history exists in the old format" is a filesystem question this
        # pure module cannot answer; the caller upgrades NO_HISTORY to
        # LEGACY when it finds a single-file register.
        return VerificationReport(findings=findings, state=HistoryState.NO_HISTORY)

    _check_seq_integrity(entries, findings)
    _check_hash_chain(entries, findings)
    _check_time_monotonic(entries, findings)

    return VerificationReport(findings=findings, state=resolve_state(findings))


def _check_seq_integrity(
    entries: Sequence[LedgerEntryLike], findings: list[tuple[int, Finding, str]]
) -> None:
    """Append `SEQ_DUPLICATE`/`SEQ_GAP` findings for `entries`'s `seq` values."""
    seen: set[int] = set()
    duplicates: set[int] = set()
    for entry in entries:
        if entry.seq in seen:
            duplicates.add(entry.seq)
        seen.add(entry.seq)

    for seq in sorted(duplicates):
        findings.append(
            (seq, Finding.SEQ_DUPLICATE, f"seq {seq} appears on more than one entry")
        )

    unique_seqs = sorted(seen)
    for previous_seq, current_seq in zip(unique_seqs, unique_seqs[1:]):
        missing = current_seq - previous_seq - 1
        if missing > 0:
            findings.append(
                (
                    current_seq,
                    Finding.SEQ_GAP,
                    f"missing {missing} seq(s) between {previous_seq} and {current_seq}",
                )
            )


def _check_time_monotonic(
    entries: Sequence[LedgerEntryLike], findings: list[tuple[int, Finding, str]]
) -> None:
    """Append `TIME_REGRESSION` wherever `recorded_at` moves backwards.

    The chain fixes the order of entries beyond dispute; each entry also
    carries the moment it claims to have been written. Put those together
    and each checks the other: entry *N+1* was written after entry *N* — the
    hash chain proves that — so a timestamp that decreases is not a
    difference of opinion, it is a detected fault. That is what turns a
    local clock reading from an unchecked claim into a checked one, without
    a network, a signature, or a third party.

    What it catches: an NTP correction or a manual `date` set mid-session, a
    VM or container snapshot restored to an earlier moment, a dual-boot
    machine with a different idea of the hour — and a **backdated entry**,
    which is the tampering case. A date cannot be forged downwards without
    contradicting the chain around it.

    What it is not: proof that the dates are *true*. A machine whose clock
    was wrong from the start, consistently, produces a perfectly monotonic
    chain of wrong timestamps. Absolute time needs a witness outside the
    machine — see the UniversalClock ticket, §4.3.

    Comparison is lexicographic on the stored strings, which is exactly
    chronological for the fixed-width UTC ISO-8601 form every chain entry is
    written in (`build_next_entry`, `timespec="seconds"`, `Z` suffix) — no
    parsing, so a malformed value cannot raise here. An entry missing the
    field, or carrying an empty one, is skipped rather than reported: this
    module's contract is a structural Protocol, and "not recorded" is not
    the same claim as "recorded, and earlier".
    """
    previous: tuple[int, str] | None = None
    for entry in entries:
        moment = getattr(entry, "recorded_at", "") or ""
        if not moment:
            continue
        if previous is not None and moment < previous[1]:
            findings.append((
                entry.seq,
                Finding.TIME_REGRESSION,
                f"recorded_at {moment} is earlier than seq {previous[0]}'s {previous[1]}",
            ))
        previous = (entry.seq, moment)


def _check_hash_chain(
    entries: Sequence[LedgerEntryLike], findings: list[tuple[int, Finding, str]]
) -> None:
    """Append `BROKEN_LINK`/`BAD_ENTRY_HASH` findings walking `entries` in order."""
    expected_prev = GENESIS_PREV
    chain_broken = False

    for entry in entries:
        if chain_broken:
            findings.append(
                (
                    entry.seq,
                    Finding.BROKEN_LINK,
                    "chain already broken upstream; link unverifiable",
                )
            )
        elif entry.prev != expected_prev:
            findings.append(
                (
                    entry.seq,
                    Finding.BROKEN_LINK,
                    f"prev {entry.prev!r} does not match expected {expected_prev!r}",
                )
            )
            chain_broken = True

        recomputed = recompute_entry_hash(entry)
        if recomputed != entry.entry_hash:
            findings.append(
                (
                    entry.seq,
                    Finding.BAD_ENTRY_HASH,
                    f"stored entry_hash {entry.entry_hash!r} != recomputed {recomputed!r}",
                )
            )

        expected_prev = recomputed

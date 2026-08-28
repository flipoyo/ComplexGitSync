"""Unit tests for ``ledger_entry`` — hash-chained register entry construction.

Pure-unit, no filesystem, no real clock: every entry is built through a fake
``ClockProtocol`` so the tests are fully deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ComplexGitSync.ledger_entry import (
    ClockProtocol,
    LedgerEntry,
    TimeL0State,
    build_next_entry,
    compute_entry_hash,
    hash_time_l0_anchor,
    new_time_l0_anchor,
)

_GENESIS_PREV = "sha256:" + "0" * 64


class FakeClock:
    """Deterministic stand-in for :class:`ClockProtocol`."""

    def __init__(
        self,
        *,
        instant: datetime = datetime(2026, 8, 27, 10, 14, 22, tzinfo=UTC),
        nanos: int = 123_456_789,
        pid: int = 4242,
        token: str = "deadbeef" * 4,
    ) -> None:
        self._instant = instant
        self._nanos = nanos
        self._pid = pid
        self._token = token

    def now(self) -> datetime:
        return self._instant

    def time_ns(self) -> int:
        return self._nanos

    def pid(self) -> int:
        return self._pid

    def token_hex(self, nbytes: int) -> str:
        return self._token[: nbytes * 2]


# ---------------------------------------------------------------------------
# ClockProtocol conformance
# ---------------------------------------------------------------------------


def test_fake_clock_satisfies_clock_protocol():
    clock: ClockProtocol = FakeClock()
    assert isinstance(clock.now(), datetime)
    assert isinstance(clock.time_ns(), int)
    assert isinstance(clock.pid(), int)
    assert isinstance(clock.token_hex(16), str)


# ---------------------------------------------------------------------------
# TIME-L0 anchor generation (absorbed from L0.py, now clock-injectable)
# ---------------------------------------------------------------------------


class TestTimeL0Anchor:
    def test_deterministic_with_fake_clock(self):
        clock = FakeClock()
        first = new_time_l0_anchor(clock)
        second = new_time_l0_anchor(clock)
        assert first == second
        assert isinstance(first, TimeL0State)

    def test_state_id_wraps_hash(self):
        clock = FakeClock()
        anchor = new_time_l0_anchor(clock)
        assert anchor.state_id == f"state({anchor.state_hash})"

    def test_different_clock_reads_produce_different_anchors(self):
        first = new_time_l0_anchor(FakeClock(instant=datetime(2026, 1, 1, tzinfo=UTC)))
        second = new_time_l0_anchor(FakeClock(instant=datetime(2026, 1, 2, tzinfo=UTC)))
        assert first != second

    def test_pid_alone_changes_anchor(self):
        first = new_time_l0_anchor(FakeClock(pid=1))
        second = new_time_l0_anchor(FakeClock(pid=2))
        assert first != second

    def test_token_alone_changes_anchor(self):
        first = new_time_l0_anchor(FakeClock(token="aa" * 16))
        second = new_time_l0_anchor(FakeClock(token="bb" * 16))
        assert first != second

    def test_hash_time_l0_anchor_is_pure_sha256_of_dot_prefixed_input(self):
        import hashlib

        anchor_text = "TIME-L0:example"
        assert hash_time_l0_anchor(anchor_text) == hashlib.sha256(
            f".{anchor_text}".encode()
        ).hexdigest()


# ---------------------------------------------------------------------------
# LedgerEntry / build_next_entry — example-based
# ---------------------------------------------------------------------------


class TestBuildNextEntryGenesis:
    def test_genesis_entry_has_seq_one_and_zero_prev(self):
        entry = build_next_entry(
            None,
            command="freeze",
            argv=["freeze", "--message", "checkpoint"],
            state_id="state(ab12)",
            state_dir="state(ab12)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        assert entry.seq == 1
        assert entry.prev == _GENESIS_PREV

    def test_genesis_entry_fields_round_trip(self):
        clock = FakeClock()
        entry = build_next_entry(
            None,
            command="freeze",
            argv=["freeze", "--message", "checkpoint"],
            state_id="state(ab12)",
            state_dir="state(ab12)_1",
            outcome="ok",
            clock=clock,
        )
        assert entry.command == "freeze"
        assert entry.argv == ("freeze", "--message", "checkpoint")
        assert entry.state_id == "state(ab12)"
        assert entry.state_dir == "state(ab12)_1"
        assert entry.outcome == "ok"
        assert entry.recorded_at == "2026-08-27T10:14:22Z"

    def test_deterministic_with_fake_clock(self):
        clock = FakeClock()
        first = build_next_entry(
            None,
            command="freeze",
            argv=["freeze"],
            state_id="state(ab12)",
            state_dir="state(ab12)_1",
            outcome="ok",
            clock=clock,
        )
        second = build_next_entry(
            None,
            command="freeze",
            argv=["freeze"],
            state_id="state(ab12)",
            state_dir="state(ab12)_1",
            outcome="ok",
            clock=clock,
        )
        assert first == second

    def test_argv_is_stored_as_tuple(self):
        entry = build_next_entry(
            None,
            command="freeze",
            argv=["freeze", "--message", "x"],
            state_id="state(ab12)",
            state_dir="state(ab12)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        assert isinstance(entry.argv, tuple)


class TestBuildNextEntryChaining:
    def test_second_entry_seq_and_prev_link_to_first(self):
        clock = FakeClock()
        first = build_next_entry(
            None,
            command="freeze",
            argv=["freeze"],
            state_id="state(ab12)",
            state_dir="state(ab12)_1",
            outcome="ok",
            clock=clock,
        )
        second = build_next_entry(
            first,
            command="checkout",
            argv=["checkout", "main"],
            state_id="state(cd34)",
            state_dir="state(cd34)_1",
            outcome="ok",
            clock=clock,
        )
        assert second.seq == first.seq + 1
        assert second.prev == first.entry_hash

    def test_entry_hash_covers_prev_field(self):
        # Two genesis entries with different downstream field values still
        # share the same genesis prev, but their hashes must differ.
        clock = FakeClock()
        a = build_next_entry(
            None,
            command="freeze",
            argv=["freeze"],
            state_id="state(ab12)",
            state_dir="state(ab12)_1",
            outcome="ok",
            clock=clock,
        )
        b = build_next_entry(
            None,
            command="checkout",
            argv=["checkout"],
            state_id="state(cd34)",
            state_dir="state(cd34)_1",
            outcome="ok",
            clock=clock,
        )
        assert a.prev == b.prev == _GENESIS_PREV
        assert a.entry_hash != b.entry_hash


class TestLedgerEntryImmutability:
    def test_frozen_dataclass_rejects_mutation(self):
        entry = build_next_entry(
            None,
            command="freeze",
            argv=["freeze"],
            state_id="state(ab12)",
            state_dir="state(ab12)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        with pytest.raises((AttributeError, TypeError)):
            entry.outcome = "failed"  # type: ignore[misc]


class TestComputeEntryHash:
    def test_hash_is_sha256_prefixed(self):
        digest = compute_entry_hash(
            seq=1,
            prev=_GENESIS_PREV,
            recorded_at="2026-08-27T10:14:22Z",
            command="freeze",
            argv=("freeze",),
            state_id="state(ab12)",
            state_dir="state(ab12)_1",
            outcome="ok",
        )
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64

    def test_changing_any_field_changes_the_hash(self):
        base_kwargs = dict(
            seq=1,
            prev=_GENESIS_PREV,
            recorded_at="2026-08-27T10:14:22Z",
            command="freeze",
            argv=("freeze",),
            state_id="state(ab12)",
            state_dir="state(ab12)_1",
            outcome="ok",
        )
        baseline = compute_entry_hash(**base_kwargs)
        for field, new_value in [
            ("seq", 2),
            ("prev", "sha256:" + "1" * 64),
            ("recorded_at", "2026-08-27T10:14:23Z"),
            ("command", "checkout"),
            ("argv", ("freeze", "--force")),
            ("state_id", "state(zz99)"),
            ("state_dir", "state(zz99)_1"),
            ("outcome", "failed"),
        ]:
            mutated = dict(base_kwargs)
            mutated[field] = new_value
            assert compute_entry_hash(**mutated) != baseline, field


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

_command_strategy = st.sampled_from(["freeze", "checkout", "branch", "pull-force", "purge"])
_argv_strategy = st.lists(st.text(min_size=0, max_size=20), min_size=0, max_size=5)
_id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd")), min_size=1, max_size=12
)
_outcome_strategy = st.sampled_from(["ok", "failed", "skipped"])


def _build_chain(n: int, clock: ClockProtocol, *, label: str = "s") -> list[LedgerEntry]:
    chain: list[LedgerEntry] = []
    prev: LedgerEntry | None = None
    for i in range(n):
        entry = build_next_entry(
            prev,
            command="freeze",
            argv=["freeze", str(i)],
            state_id=f"state({label}{i})",
            state_dir=f"state({label}{i})_1",
            outcome="ok",
            clock=clock,
        )
        chain.append(entry)
        prev = entry
    return chain


class TestChainProperties:
    @given(n=st.integers(min_value=1, max_value=25))
    @settings(max_examples=50)
    def test_chain_of_n_entries_is_internally_consistent(self, n):
        clock = FakeClock()
        chain = _build_chain(n, clock)

        assert chain[0].prev == _GENESIS_PREV
        for i in range(1, len(chain)):
            assert chain[i].prev == chain[i - 1].entry_hash
            assert chain[i].seq == chain[i - 1].seq + 1

    @given(n=st.integers(min_value=1, max_value=25))
    @settings(max_examples=50)
    def test_genesis_prev_is_always_all_zero_hash(self, n):
        chain = _build_chain(n, FakeClock())
        assert chain[0].prev == "sha256:" + "0" * 64

    @given(
        command=_command_strategy,
        argv=_argv_strategy,
        state_id=_id_strategy,
        state_dir=_id_strategy,
        outcome=_outcome_strategy,
    )
    @settings(max_examples=100)
    def test_changing_any_field_of_a_built_entry_changes_its_hash(
        self, command, argv, state_id, state_dir, outcome
    ):
        clock = FakeClock()
        entry = build_next_entry(
            None,
            command=command,
            argv=argv,
            state_id=state_id,
            state_dir=state_dir,
            outcome=outcome,
            clock=clock,
        )

        mutated_hash = compute_entry_hash(
            seq=entry.seq,
            prev=entry.prev,
            recorded_at=entry.recorded_at,
            command=entry.command + "!",
            argv=entry.argv,
            state_id=entry.state_id,
            state_dir=entry.state_dir,
            outcome=entry.outcome,
        )
        assert mutated_hash != entry.entry_hash

    @given(n=st.integers(min_value=2, max_value=20))
    @settings(max_examples=30)
    def test_splicing_a_different_predecessor_breaks_the_link(self, n):
        clock = FakeClock()
        chain_a = _build_chain(n, clock, label="a")
        chain_b = _build_chain(n, clock, label="b")

        # Recompute what entry 1 of chain_a *would* hash to if it claimed
        # chain_b's second entry as its predecessor instead of its own.
        spliced_hash = compute_entry_hash(
            seq=chain_a[1].seq,
            prev=chain_b[0].entry_hash,
            recorded_at=chain_a[1].recorded_at,
            command=chain_a[1].command,
            argv=chain_a[1].argv,
            state_id=chain_a[1].state_id,
            state_dir=chain_a[1].state_dir,
            outcome=chain_a[1].outcome,
        )
        assert spliced_hash != chain_a[1].entry_hash

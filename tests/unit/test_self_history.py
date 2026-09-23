"""Self-history records: content-addressed, atomic, and self-verifying.

Mirrors ``test_agent_contract.py`` — the same storage discipline, applied
to the AgentReport ticket's own record type.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from ComplexGitSync.memory.self_history import (
    AgentInfo,
    ConformityCriterion,
    ConformityScore,
    SelfHistoryRecord,
    read_record,
    read_records,
    record_path,
    self_history_dirs,
    write_record,
)

_WORKER = AgentInfo(role="Dev", vendor="Anthropic", model="claude-sonnet-5")
_ORCHESTRATOR = AgentInfo(role="Orchestration", vendor="Anthropic", model="claude-sonnet-5")
_CONFORMITY = ConformityScore(
    spec_respect=ConformityCriterion(score=33, basis="measured", reasoning="lint/test/status all pass"),
    gating=ConformityCriterion(score=33, basis="measured", reasoning="nothing private pushed"),
    quality=ConformityCriterion(score=30, basis="asserted", reasoning="a reasonable first pass"),
)
_RECORD = SelfHistoryRecord(
    ticket="AgentReport",
    goal="Implement WP1: the record format and the add command.",
    action="Wrote memory/self_history.py and ComplexGitSyncClient.self_history_add.",
    worker=_WORKER,
    orchestrator=_ORCHESTRATOR,
    conformity=_CONFORMITY,
    recorded_at="2026-09-24T00:00:00+00:00",
    lint_passed=True,
    tests_passed=True,
    status_errors=0,
)


def test_write_then_read_round_trips(tmp_path):
    path = write_record(tmp_path, _RECORD)

    assert path == record_path(tmp_path, _RECORD.digest())
    assert read_record(path) == _RECORD


def test_write_is_idempotent_for_identical_content(tmp_path):
    first = write_record(tmp_path, _RECORD)
    second = write_record(tmp_path, _RECORD)

    assert first == second
    assert read_record(first) == _RECORD


def test_write_rejects_a_hash_collision_with_different_content(tmp_path, monkeypatch):
    write_record(tmp_path, _RECORD)
    forced_digest = _RECORD.digest()
    colliding = replace(_RECORD, action="a completely different action")
    monkeypatch.setattr(SelfHistoryRecord, "digest", lambda self: forced_digest)

    with pytest.raises(ValueError, match="collision"):
        write_record(tmp_path, colliding)


def test_read_rejects_a_record_whose_content_does_not_match_its_filename(tmp_path):
    path = write_record(tmp_path, _RECORD)
    tampered = path.with_name(f"{'0' * 64}.toml")
    path.rename(tampered)

    with pytest.raises(ValueError, match="does not match its filename"):
        read_record(tampered)


def test_editing_the_record_changes_its_name(tmp_path):
    original_path = write_record(tmp_path, _RECORD)
    edited = replace(_RECORD, action="a superseding account of the work")
    edited_path = write_record(tmp_path, edited)

    assert original_path != edited_path
    assert read_record(original_path) == _RECORD
    assert read_record(edited_path) == edited


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("goal", "one\ntwo\nthree\nfour", "at most 3 lines"),
        ("action", "", "must not be empty"),
        ("ticket", "   ", "must not be empty"),
        ("state_before", "not-a-state-id", "state\\(<hash>\\) id"),
    ],
)
def test_record_validates_its_own_fields(field, value, match):
    kwargs = {
        "ticket": "AgentReport",
        "goal": "A goal.",
        "action": "An action.",
        "worker": _WORKER,
        "orchestrator": _ORCHESTRATOR,
        "conformity": _CONFORMITY,
        "recorded_at": "2026-09-24T00:00:00+00:00",
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=match):
        SelfHistoryRecord(**kwargs)


def test_agent_info_rejects_a_role_outside_the_roster():
    with pytest.raises(ValueError, match="role must be one of"):
        AgentInfo(role="Manager", vendor="Anthropic", model="claude-sonnet-5")


def test_conformity_criterion_rejects_an_unknown_basis():
    with pytest.raises(ValueError, match="basis must be one of"):
        ConformityCriterion(score=33, basis="claimed", reasoning="because")


def test_read_records_merges_folded_and_pending_and_sorts_by_recorded_at(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"
    folded_dir, pending_dir = self_history_dirs(cgitsync_dir)
    earlier = replace(_RECORD, recorded_at="2026-09-20T00:00:00+00:00")
    later = replace(_RECORD, action="a later action", recorded_at="2026-09-24T00:00:00+00:00")

    write_record(folded_dir, later)
    write_record(pending_dir, earlier)

    records = read_records(cgitsync_dir)

    assert [record.recorded_at for record in records] == [earlier.recorded_at, later.recorded_at]


def test_read_records_is_empty_when_nothing_has_been_recorded(tmp_path):
    assert read_records(tmp_path / ".cgitsync") == []

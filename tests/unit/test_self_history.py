"""Self-history records: content-addressed, atomic, and self-verifying.

Mirrors ``test_agent_contract.py`` — the same storage discipline, applied
to the AgentReport ticket's own record type.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from ComplexGitSync.memory.conformity import ConformityCriterion, ConformityScore
from ComplexGitSync.memory.self_history import AgentInfo, SelfHistoryRecord

_WORKER = AgentInfo(role="Dev", vendor="vendor-name", model="model-name")
_ORCHESTRATOR = AgentInfo(role="Orchestration", vendor="vendor-name", model="model-name")
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
    path = _RECORD.write(tmp_path)

    assert path == SelfHistoryRecord.path_in(tmp_path, _RECORD.digest())
    assert SelfHistoryRecord.read(path) == _RECORD


def test_write_is_idempotent_for_identical_content(tmp_path):
    first = _RECORD.write(tmp_path)
    second = _RECORD.write(tmp_path)

    assert first == second
    assert SelfHistoryRecord.read(first) == _RECORD


def test_write_rejects_a_hash_collision_with_different_content(tmp_path, monkeypatch):
    _RECORD.write(tmp_path)
    forced_digest = _RECORD.digest()
    colliding = replace(_RECORD, action="a completely different action")
    monkeypatch.setattr(SelfHistoryRecord, "digest", lambda self: forced_digest)

    with pytest.raises(ValueError, match="collision"):
        colliding.write(tmp_path)


def test_read_rejects_a_record_whose_content_does_not_match_its_filename(tmp_path):
    path = _RECORD.write(tmp_path)
    tampered = path.with_name(f"{'0' * 64}.toml")
    path.rename(tampered)

    with pytest.raises(ValueError, match="does not match its filename"):
        SelfHistoryRecord.read(tampered)


def test_editing_the_record_changes_its_name(tmp_path):
    original_path = _RECORD.write(tmp_path)
    edited = replace(_RECORD, action="a superseding account of the work")
    edited_path = edited.write(tmp_path)

    assert original_path != edited_path
    assert SelfHistoryRecord.read(original_path) == _RECORD
    assert SelfHistoryRecord.read(edited_path) == edited


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
        AgentInfo(role="Manager", vendor="vendor-name", model="model-name")


def test_conformity_criterion_rejects_an_unknown_basis():
    with pytest.raises(ValueError, match="basis must be one of"):
        ConformityCriterion(score=33, basis="claimed", reasoning="because")


def test_read_records_merges_folded_and_pending_and_sorts_by_recorded_at(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"
    folded_dir, pending_dir = SelfHistoryRecord.dirs(cgitsync_dir)
    earlier = replace(_RECORD, recorded_at="2026-09-20T00:00:00+00:00")
    later = replace(_RECORD, action="a later action", recorded_at="2026-09-24T00:00:00+00:00")

    later.write(folded_dir)
    earlier.write(pending_dir)

    records = SelfHistoryRecord.read_all(cgitsync_dir)

    assert [record.recorded_at for record in records] == [earlier.recorded_at, later.recorded_at]


def test_read_records_is_empty_when_nothing_has_been_recorded(tmp_path):
    assert SelfHistoryRecord.read_all(tmp_path / ".cgitsync") == []

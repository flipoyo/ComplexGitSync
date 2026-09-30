"""AgentContract records: content-addressed, atomic, and self-verifying."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ComplexGitSync.memory.agent_contract import (
    AgentContractRecord,
)

_RECORD = AgentContractRecord(
    provider="anthropic",
    terms_version="Anthropic Consumer Terms of Service, effective 2025-10-08 (consumer-subscription)",
    date="2026-09-23",
    legal_terms_sha256="a" * 64,
    attested_by="Claude (Anthropic), model claude-sonnet-5",
)


def test_write_then_read_round_trips(tmp_path):
    path = _RECORD.write(tmp_path)

    assert path == AgentContractRecord.path_in(tmp_path, _RECORD.digest())
    assert AgentContractRecord.read(path) == _RECORD


def test_write_is_idempotent_for_identical_content(tmp_path):
    first = _RECORD.write(tmp_path)
    second = _RECORD.write(tmp_path)

    assert first == second
    assert AgentContractRecord.read(first) == _RECORD


def test_write_rejects_a_hash_collision_with_different_content(tmp_path, monkeypatch):
    _RECORD.write(tmp_path)
    forced_digest = _RECORD.digest()
    colliding = replace(_RECORD, attested_by="a different attestation entirely")
    monkeypatch.setattr(AgentContractRecord, "digest", lambda self: forced_digest)

    with pytest.raises(ValueError, match="collision"):
        colliding.write(tmp_path)


def test_read_rejects_a_record_whose_content_does_not_match_its_filename(tmp_path):
    path = _RECORD.write(tmp_path)
    tampered = path.with_name(f"{'0' * 64}.toml")
    path.rename(tampered)

    with pytest.raises(ValueError, match="does not match its filename"):
        AgentContractRecord.read(tampered)


def test_editing_the_record_changes_its_name(tmp_path):
    original_path = _RECORD.write(tmp_path)
    edited = replace(_RECORD, terms_version="a superseding terms reference")
    edited_path = edited.write(tmp_path)

    assert original_path != edited_path
    assert AgentContractRecord.read(original_path) == _RECORD
    assert AgentContractRecord.read(edited_path) == edited


def test_current_pointer_follows_the_most_recently_written_record(tmp_path):
    _RECORD.write(tmp_path)
    superseding = replace(_RECORD, terms_version="a superseding terms reference")
    superseding.write(tmp_path)

    assert AgentContractRecord.read_current(tmp_path) == superseding


def test_read_current_contract_is_none_when_nothing_has_been_signed(tmp_path):
    assert AgentContractRecord.read_current(tmp_path) is None
    (tmp_path / "agent-contracts").mkdir()
    assert AgentContractRecord.read_current(tmp_path) is None

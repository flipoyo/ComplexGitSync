"""AgentContract records: content-addressed, atomic, and self-verifying."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ComplexGitSync.memory.agent_contract import (
    AgentContractRecord,
    contract_path,
    read_contract,
    read_current_contract,
    write_contract,
)

_RECORD = AgentContractRecord(
    provider="anthropic",
    terms_version="Anthropic Consumer Terms of Service, effective 2025-10-08 (consumer-subscription)",
    date="2026-09-23",
    legal_terms_sha256="a" * 64,
    attested_by="Claude (Anthropic), model claude-sonnet-5",
)


def test_write_then_read_round_trips(tmp_path):
    path = write_contract(tmp_path, _RECORD)

    assert path == contract_path(tmp_path, _RECORD.digest())
    assert read_contract(path) == _RECORD


def test_write_is_idempotent_for_identical_content(tmp_path):
    first = write_contract(tmp_path, _RECORD)
    second = write_contract(tmp_path, _RECORD)

    assert first == second
    assert read_contract(first) == _RECORD


def test_write_rejects_a_hash_collision_with_different_content(tmp_path, monkeypatch):
    write_contract(tmp_path, _RECORD)
    forced_digest = _RECORD.digest()
    colliding = replace(_RECORD, attested_by="a different attestation entirely")
    monkeypatch.setattr(AgentContractRecord, "digest", lambda self: forced_digest)

    with pytest.raises(ValueError, match="collision"):
        write_contract(tmp_path, colliding)


def test_read_rejects_a_record_whose_content_does_not_match_its_filename(tmp_path):
    path = write_contract(tmp_path, _RECORD)
    tampered = path.with_name(f"{'0' * 64}.toml")
    path.rename(tampered)

    with pytest.raises(ValueError, match="does not match its filename"):
        read_contract(tampered)


def test_editing_the_record_changes_its_name(tmp_path):
    original_path = write_contract(tmp_path, _RECORD)
    edited = replace(_RECORD, terms_version="a superseding terms reference")
    edited_path = write_contract(tmp_path, edited)

    assert original_path != edited_path
    assert read_contract(original_path) == _RECORD
    assert read_contract(edited_path) == edited


def test_current_pointer_follows_the_most_recently_written_record(tmp_path):
    write_contract(tmp_path, _RECORD)
    superseding = replace(_RECORD, terms_version="a superseding terms reference")
    write_contract(tmp_path, superseding)

    assert read_current_contract(tmp_path) == superseding


def test_read_current_contract_is_none_when_nothing_has_been_signed(tmp_path):
    assert read_current_contract(tmp_path) is None
    (tmp_path / "agent-contracts").mkdir()
    assert read_current_contract(tmp_path) is None

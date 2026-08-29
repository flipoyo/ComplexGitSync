"""Unit tests for ``state_store`` — content-addressed
``.cgitsync/state(<hash>)_n/`` directory allocation.

Pure filesystem tests (Ring 1): everything here runs against ``tmp_path``,
no Git, no subprocess. Ported/adapted from the coverage already exercising
this code (as module-level functions) inside ``orchestre.py`` via
``tests/unit/test_registry_client.py`` — that file's
``test_state_directory_suffix_is_scoped_to_exact_state_hash`` is left
untouched; this file re-derives equivalent coverage against the new
``state_store`` module.

Note on ``MemoryStateDirectory``/``_resolve_memory_state_directory``: despite
the name, this is unrelated to the deleted Memory SSH-Git transport
(``CleanupPass2_DevPlanTicket.md`` D1) — it is the general state-directory
allocator every lifecycle command uses. See ``state_store.py``'s module
docstring.
"""

from __future__ import annotations

import pytest

from ComplexGitSync.state_store import (
    MemoryStateDirectory,
    _format_state_id,
    _latest_state_artifact,
    _next_state_directory_order,
    _parse_state_hash,
    _resolve_memory_state_directory,
    _state_artifact_candidates,
    _state_directory_name,
    _state_order_from_directory_name,
    _state_snapshot_candidates,
    _state_snapshot_candidates_for_id,
    _temporary_state_directory_name,
)

_HASH_A = "a" * 64
_HASH_B = "b" * 64


# ---------------------------------------------------------------------------
# _format_state_id / _parse_state_hash
# ---------------------------------------------------------------------------


def test_format_state_id_wraps_a_valid_hash():
    assert _format_state_id(_HASH_A) == f"state({_HASH_A})"


@pytest.mark.parametrize(
    "bad_hash",
    ["", "not-hex", "A" * 64, "a" * 63, "a" * 65, "g" * 64],
)
def test_format_state_id_rejects_non_sha256_hex(bad_hash):
    with pytest.raises(ValueError):
        _format_state_id(bad_hash)


def test_parse_state_hash_round_trips_with_format_state_id():
    state_id = _format_state_id(_HASH_A)
    assert _parse_state_hash(state_id) == _HASH_A


@pytest.mark.parametrize(
    "bad_state_id",
    ["state(short)", f"state({_HASH_A})_0", _HASH_A, "", "state()"],
)
def test_parse_state_hash_returns_none_for_malformed_ids(bad_state_id):
    assert _parse_state_hash(bad_state_id) is None


# ---------------------------------------------------------------------------
# _state_directory_name / _temporary_state_directory_name
# ---------------------------------------------------------------------------


def test_state_directory_name_appends_order_suffix():
    assert _state_directory_name(_HASH_A, 0) == f"state({_HASH_A})_0"
    assert _state_directory_name(_HASH_A, 7) == f"state({_HASH_A})_7"


def test_temporary_state_directory_name_prefixes_dot_tmp():
    assert _temporary_state_directory_name(_HASH_A, 3) == f".tmp-state({_HASH_A})_3"


@pytest.mark.parametrize("bad_order", [-1, -100])
def test_state_directory_name_rejects_negative_order(bad_order):
    with pytest.raises(ValueError):
        _state_directory_name(_HASH_A, bad_order)


@pytest.mark.parametrize("bad_order", [-1, -100])
def test_temporary_state_directory_name_rejects_negative_order(bad_order):
    with pytest.raises(ValueError):
        _temporary_state_directory_name(_HASH_A, bad_order)


# ---------------------------------------------------------------------------
# _state_order_from_directory_name
# ---------------------------------------------------------------------------


def test_state_order_from_directory_name_parses_matching_names():
    assert _state_order_from_directory_name(f"state({_HASH_A})_0") == 0
    assert _state_order_from_directory_name(f"state({_HASH_A})_42") == 42


@pytest.mark.parametrize(
    "name",
    ["state", f"state({_HASH_A})", f".tmp-state({_HASH_A})_0", "state(short)_0", "unrelated"],
)
def test_state_order_from_directory_name_returns_none_for_non_matching_names(name):
    assert _state_order_from_directory_name(name) is None


# ---------------------------------------------------------------------------
# _next_state_directory_order
# ---------------------------------------------------------------------------


def test_next_state_directory_order_is_zero_when_directory_missing(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"
    assert _next_state_directory_order(cgitsync_dir, _HASH_A) == 0


def test_next_state_directory_order_is_zero_for_empty_directory(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"
    cgitsync_dir.mkdir()
    assert _next_state_directory_order(cgitsync_dir, _HASH_A) == 0


def test_next_state_directory_order_skips_past_existing_orders_for_same_hash(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"
    (cgitsync_dir / _state_directory_name(_HASH_A, 0)).mkdir(parents=True)
    (cgitsync_dir / _state_directory_name(_HASH_A, 1)).mkdir()

    assert _next_state_directory_order(cgitsync_dir, _HASH_A) == 2


def test_next_state_directory_order_ignores_other_hashes_and_files(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"
    (cgitsync_dir / _state_directory_name(_HASH_B, 5)).mkdir(parents=True)
    (cgitsync_dir / "state.txt").write_text("not a directory match")

    assert _next_state_directory_order(cgitsync_dir, _HASH_A) == 0
    assert _next_state_directory_order(cgitsync_dir, _HASH_B) == 6


# ---------------------------------------------------------------------------
# MemoryStateDirectory / _resolve_memory_state_directory
# ---------------------------------------------------------------------------


def test_state_directory_suffix_is_scoped_to_exact_state_hash(tmp_path):
    """Equivalent of test_registry_client.py's test of the same name, ported
    to the extracted module rather than orchestre.py.
    """

    cgitsync_dir = tmp_path / ".cgitsync"
    (cgitsync_dir / _state_directory_name(_HASH_A, 0)).mkdir(parents=True)
    (cgitsync_dir / _state_directory_name(_HASH_A, 1)).mkdir()

    same_hash_state = _resolve_memory_state_directory(cgitsync_dir, _HASH_A)
    other_hash_state = _resolve_memory_state_directory(cgitsync_dir, _HASH_B)

    assert same_hash_state.state_order == 2
    assert same_hash_state.final_path.name == _state_directory_name(_HASH_A, 2)
    assert other_hash_state.state_order == 0
    assert other_hash_state.final_path.name == _state_directory_name(_HASH_B, 0)


def test_resolve_memory_state_directory_returns_a_frozen_dataclass(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"

    resolved = _resolve_memory_state_directory(cgitsync_dir, _HASH_A)

    assert isinstance(resolved, MemoryStateDirectory)
    assert resolved.final_path == cgitsync_dir / f"state({_HASH_A})_0"
    assert resolved.temporary_path == cgitsync_dir / f".tmp-state({_HASH_A})_0"
    with pytest.raises(AttributeError):
        resolved.state_order = 99  # type: ignore[misc]


def test_resolve_memory_state_directory_skips_orders_with_a_stray_temporary_path(tmp_path):
    """A crashed-mid-write leftover .tmp- directory must not be reused, even
    if the final path for that order was never created.
    """

    cgitsync_dir = tmp_path / ".cgitsync"
    (cgitsync_dir / _temporary_state_directory_name(_HASH_A, 0)).mkdir(parents=True)

    resolved = _resolve_memory_state_directory(cgitsync_dir, _HASH_A)

    assert resolved.state_order == 1


# ---------------------------------------------------------------------------
# _state_snapshot_candidates / _state_snapshot_candidates_for_id
# ---------------------------------------------------------------------------


def test_state_snapshot_candidates_finds_gts_files_under_state_directories(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"
    state_dir = cgitsync_dir / _state_directory_name(_HASH_A, 0)
    state_dir.mkdir(parents=True)
    (state_dir / "snapshot.gts").write_text("x")
    (state_dir / "ignored.txt").write_text("x")

    candidates = _state_snapshot_candidates(cgitsync_dir)

    assert candidates == [state_dir / "snapshot.gts"]


def test_state_snapshot_candidates_includes_legacy_state_directory(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"
    legacy_dir = cgitsync_dir / "state"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "legacy.gts").write_text("x")

    candidates = _state_snapshot_candidates(cgitsync_dir)

    assert candidates == [legacy_dir / "legacy.gts"]


def test_state_snapshot_candidates_returns_empty_list_when_directory_missing(tmp_path):
    assert _state_snapshot_candidates(tmp_path / ".cgitsync") == []


def test_state_snapshot_candidates_for_id_matches_only_the_requested_hash(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"
    state_dir_a = cgitsync_dir / _state_directory_name(_HASH_A, 0)
    state_dir_a.mkdir(parents=True)
    (state_dir_a / "a.gts").write_text("x")
    state_dir_b = cgitsync_dir / _state_directory_name(_HASH_B, 0)
    state_dir_b.mkdir(parents=True)
    (state_dir_b / "b.gts").write_text("x")

    candidates = _state_snapshot_candidates_for_id(cgitsync_dir, _format_state_id(_HASH_A))

    assert candidates == [state_dir_a / "a.gts"]


def test_state_snapshot_candidates_for_id_returns_empty_for_malformed_id(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"
    cgitsync_dir.mkdir()

    assert _state_snapshot_candidates_for_id(cgitsync_dir, "not-a-state-id") == []


# ---------------------------------------------------------------------------
# _state_artifact_candidates / _latest_state_artifact
# ---------------------------------------------------------------------------


def test_state_artifact_candidates_finds_named_file_across_state_directories(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"
    state_dir_a = cgitsync_dir / _state_directory_name(_HASH_A, 0)
    state_dir_a.mkdir(parents=True)
    (state_dir_a / "register.lgr").write_text("x")
    state_dir_b = cgitsync_dir / _state_directory_name(_HASH_B, 0)
    state_dir_b.mkdir(parents=True)
    (state_dir_b / "register.lgr").write_text("x")
    (state_dir_b / "unrelated.txt").write_text("x")

    candidates = _state_artifact_candidates(cgitsync_dir, "register.lgr")

    assert set(candidates) == {state_dir_a / "register.lgr", state_dir_b / "register.lgr"}


def test_state_artifact_candidates_returns_empty_when_directory_missing(tmp_path):
    assert _state_artifact_candidates(tmp_path / ".cgitsync", "register.lgr") == []


def test_latest_state_artifact_returns_most_recently_modified(tmp_path):
    cgitsync_dir = tmp_path / ".cgitsync"
    state_dir_a = cgitsync_dir / _state_directory_name(_HASH_A, 0)
    state_dir_a.mkdir(parents=True)
    older = state_dir_a / "register.lgr"
    older.write_text("old")

    state_dir_b = cgitsync_dir / _state_directory_name(_HASH_B, 0)
    state_dir_b.mkdir(parents=True)
    newer = state_dir_b / "register.lgr"
    newer.write_text("new")

    import os
    import time

    now = time.time()
    os.utime(older, (now - 100, now - 100))
    os.utime(newer, (now, now))

    assert _latest_state_artifact(cgitsync_dir, "register.lgr") == newer


def test_latest_state_artifact_returns_none_when_no_candidates(tmp_path):
    assert _latest_state_artifact(tmp_path / ".cgitsync", "register.lgr") is None

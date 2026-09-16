"""A State is named by what it contains, so two machines agree on its name.

The test that matters here is cross-machine determinism, and it is not an
implementation detail: the same tree, materialised twice, in two directories
with different absolute paths, by two different users, must produce the same
`state/<hash>.gts`. If it does not, something machine-specific is still
leaking into the canonical form.

Before this milestone the name came from a TIME-L0 anchor — a timestamp with
entropy in it — so every command invented a new name for a tree that had not
changed, and no two machines ever agreed on anything.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from ComplexGitSync.gts_document import GtsDocument
from ComplexGitSync.orchestre import ComplexGitSyncClient

_CGS = """
project = "demo"

repos = [
  "github:owner/demo",
]
"""


def _workspace(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.cgs").write_text(_CGS, encoding="utf-8")
    return root / "project.cgs"


def _state_files(workspace: Path) -> list[Path]:
    return sorted((workspace / ".cgitsync" / "state").glob("*.gts"))


# ---------------------------------------------------------------------------
# Determinism — the criterion this ticket closes on
# ---------------------------------------------------------------------------


def test_the_same_tree_in_two_directories_gets_one_name(tmp_path):
    """Different absolute paths, same tree, same State name."""
    first = _workspace(tmp_path / "alice" / "work" / "demo")
    second = _workspace(tmp_path / "srv" / "elsewhere" / "demo")

    ComplexGitSyncClient().load(first)
    ComplexGitSyncClient().load(second)

    [first_state] = _state_files(first.parent)
    [second_state] = _state_files(second.parent)

    assert first_state.name == second_state.name


def test_two_writes_over_an_unchanged_workspace_produce_one_state(tmp_path):
    config = _workspace(tmp_path / "demo")

    client = ComplexGitSyncClient()
    client.load(config)
    client.load(config)

    # One name, not two. The `_n` occurrence counter had nothing left to
    # count: the same content is the same file, and being seen twice is two
    # ledger entries pointing at one name.
    assert len(_state_files(tmp_path / "demo")) == 1


def test_a_state_is_named_by_its_own_recorded_hash(tmp_path):
    config = _workspace(tmp_path / "demo")

    ComplexGitSyncClient().load(config)

    [state] = _state_files(tmp_path / "demo")
    document = GtsDocument.from_toml(state)
    assert state.stem == document.snapshot_hash
    assert state.stem == document.compute_snapshot_hash()


def test_a_changed_tree_gets_a_different_name(tmp_path):
    """Determinism is worth nothing if every tree hashes the same."""
    config = _workspace(tmp_path / "demo")
    ComplexGitSyncClient().load(config)
    [before] = _state_files(tmp_path / "demo")

    config.write_text(
        _CGS.replace('"github:owner/demo",', '"github:owner/demo",\n  "github:owner/second",'),
        encoding="utf-8",
    )
    ComplexGitSyncClient().load(config)

    names = {state.name for state in _state_files(tmp_path / "demo")}
    assert len(names) == 2
    assert before.name in names


# ---------------------------------------------------------------------------
# What the name is computed from
# ---------------------------------------------------------------------------


def test_the_canonical_payload_holds_no_absolute_path(tmp_path):
    config = _workspace(tmp_path / "demo")
    ComplexGitSyncClient().load(config)
    [state] = _state_files(tmp_path / "demo")

    document = GtsDocument.from_toml(state)
    payload = document._build_canonical_payload(document.hash_canonicalisation)

    rendered = repr(payload)
    assert str(tmp_path) not in rendered
    assert "absolute_path" not in rendered
    assert "source_cgs_path" not in rendered
    # The snapshot itself still records where it was written — that is
    # useful, and it is metadata, not identity.
    assert document.read("project.root_absolute_path") is not None


def test_a_toolchain_version_would_not_change_the_name(tmp_path):
    """Versions are provenance, and belong to the ledger, not to a name.

    Recording them in the State would give one tree two names on two
    machines running different git versions — exactly what this milestone
    exists to prevent. The memory workstream records them per ledger entry.
    """
    config = _workspace(tmp_path / "demo")
    ComplexGitSyncClient().load(config)
    [state] = _state_files(tmp_path / "demo")

    document = GtsDocument.from_toml(state)
    before = document.compute_snapshot_hash()
    document._data["document"]["cgitsync_version"] = "2026.99"
    document._data["document"]["git_version"] = "2.39.5"

    assert document.compute_snapshot_hash() == before


# ---------------------------------------------------------------------------
# The old layout keeps working
# ---------------------------------------------------------------------------


def test_a_version_one_snapshot_still_validates_under_version_one(tmp_path):
    """An existing snapshot is never re-measured with the new algorithm."""
    config = _workspace(tmp_path / "demo")
    ComplexGitSyncClient().load(config)
    [state] = _state_files(tmp_path / "demo")

    data = tomllib.loads(state.read_text(encoding="utf-8"))
    legacy = tmp_path / "legacy.gts"
    del data["document"]["hash_canonicalisation"]
    data["document"]["snapshot_hash"] = GtsDocument(dict(data)).compute_snapshot_hash(
        canonicalisation=1
    )
    import tomli_w

    legacy.write_text(tomli_w.dumps(data), encoding="utf-8")

    document = GtsDocument.from_toml(legacy)
    assert document.hash_canonicalisation == 1
    document.validate()  # raises if the old hash is measured the new way


def test_a_snapshot_in_the_old_directory_layout_is_still_found(tmp_path):
    """A workspace written before the flat layout keeps resolving."""
    from ComplexGitSync.snapshot_resolver import discover_gts_path

    config = _workspace(tmp_path / "demo")
    ComplexGitSyncClient().load(config)
    [state] = _state_files(tmp_path / "demo")

    old_dir = tmp_path / "demo" / ".cgitsync" / f"state({state.stem})_0"
    old_dir.mkdir()
    (old_dir / "project.gts").write_text(state.read_text(encoding="utf-8"), encoding="utf-8")
    state.unlink()

    assert discover_gts_path(str(tmp_path / "demo")) == (old_dir / "project.gts").resolve()

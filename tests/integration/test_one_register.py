"""One ledger, written by real operations, and able to fail.

Three attempts at remembering things used to sit in the tree at once: the
single-file register `orchestre.py` rewrote whole on every operation, a
half-wired extraction, and a hash-chained store that only `verify` read and
nothing ever wrote. This milestone leaves one — and these tests exercise it
through the client, not through `ledger_store` directly, because "nothing
writes it" was the bug.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import tomli_w

from ComplexGitSync.memory.integrity import Finding, HistoryState
from ComplexGitSync.memory.ledger_store import read_all_entries, read_head
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


def _entries(workspace: Path):
    return read_all_entries(workspace / ".cgitsync" / "lgr")


def _states(workspace: Path) -> list[Path]:
    return sorted((workspace / ".cgitsync" / "state").glob("*.gts"))


# ---------------------------------------------------------------------------
# The chain is written, by ordinary commands
# ---------------------------------------------------------------------------


def test_a_real_operation_writes_an_entry(tmp_path):
    config = _workspace(tmp_path / "demo")

    ComplexGitSyncClient().load(config)

    [entry] = _entries(tmp_path / "demo")
    assert entry.seq == 1
    assert entry.command == "load"
    assert entry.outcome == "ok"


def test_verify_now_reports_a_verified_chain_on_a_used_workspace(tmp_path):
    """The whole point of the milestone, in one assertion."""
    config = _workspace(tmp_path / "demo")
    ComplexGitSyncClient().load(config)

    report = ComplexGitSyncClient().verify(tmp_path / "demo")

    assert report.state is HistoryState.VERIFIED
    assert report.findings == []


def test_entries_chain_to_each_other(tmp_path):
    config = _workspace(tmp_path / "demo")
    client = ComplexGitSyncClient()
    client.load(config)
    client.expand(config)
    client.validate(config)

    entries = _entries(tmp_path / "demo")
    assert [entry.seq for entry in entries] == [1, 2, 3]
    assert entries[0].prev == "sha256:" + "0" * 64
    assert entries[1].prev == entries[0].entry_hash
    assert entries[2].prev == entries[1].entry_hash


def test_the_parent_comes_from_head_not_from_a_timestamp(tmp_path):
    """Two writes inside one filesystem tick still chain correctly.

    The register this replaced chose its parent with `max(..., key=st_mtime)`,
    so two operations in the same timestamp tick — or a restored backup —
    could fork from the wrong one, silently.
    """
    config = _workspace(tmp_path / "demo")
    client = ComplexGitSyncClient()
    client.load(config)
    client.load(config)

    entries = _entries(tmp_path / "demo")
    assert entries[1].prev == entries[0].entry_hash

    head = read_head(tmp_path / "demo" / ".cgitsync" / "lgr")
    assert head is not None
    assert head.seq == entries[-1].seq
    assert head.entry_hash == entries[-1].entry_hash


def test_one_ledger_and_no_copy_forward(tmp_path):
    config = _workspace(tmp_path / "demo")
    client = ComplexGitSyncClient()
    for _ in range(3):
        client.load(config)

    cgitsync = tmp_path / "demo" / ".cgitsync"
    # One ledger directory, one file per entry, and no register copied into
    # a state directory — the growth that was quadratic in operations.
    assert (cgitsync / "lgr").is_dir()
    assert sorted(path.name for path in (cgitsync / "lgr").glob("*.toml")) == [
        "000001.toml",
        "000002.toml",
        "000003.toml",
    ]
    assert list(cgitsync.glob("*.lgr")) == []
    assert list(cgitsync.glob("state(*)_*")) == []


# ---------------------------------------------------------------------------
# What each entry says about the tools that made it
# ---------------------------------------------------------------------------


def test_every_entry_records_all_five_tool_versions(tmp_path):
    config = _workspace(tmp_path / "demo")
    client = ComplexGitSyncClient()
    client.load(config)
    client.expand(config)

    for entry in _entries(tmp_path / "demo"):
        recorded = dict(entry.toolchain)
        assert set(recorded) == {"cgitsync", "git", "pixi", "dvc", "git-lfs"}
        assert recorded["cgitsync"]
        assert recorded["git"].startswith("git version")


def test_a_tool_that_is_not_installed_is_recorded_as_none(tmp_path):
    config = _workspace(tmp_path / "demo")
    ComplexGitSyncClient().load(config)

    [entry] = _entries(tmp_path / "demo")
    # No data backend was used, so neither is asked for its version — and
    # the answer recorded is the word, never a blank.
    assert dict(entry.toolchain)["dvc"] == "none"
    assert dict(entry.toolchain)["git-lfs"] == "none"


def test_editing_a_recorded_version_is_detected(tmp_path):
    """The toolchain is inside the entry hash, like every other field."""
    config = _workspace(tmp_path / "demo")
    ComplexGitSyncClient().load(config)

    entry_path = tmp_path / "demo" / ".cgitsync" / "lgr" / "000001.toml"
    data = tomllib.loads(entry_path.read_text(encoding="utf-8"))
    data["entry"]["toolchain"]["git"] = "git version 9.9.9"
    entry_path.write_text(tomli_w.dumps(data), encoding="utf-8")

    report = ComplexGitSyncClient().verify(tmp_path / "demo")

    assert report.state is HistoryState.CORRUPT
    assert any(finding is Finding.BAD_ENTRY_HASH for _s, finding, _d in report.findings)


# ---------------------------------------------------------------------------
# The three checks that became possible once a State was named by content
# ---------------------------------------------------------------------------


def test_an_entry_naming_a_state_that_is_gone_is_reported(tmp_path):
    config = _workspace(tmp_path / "demo")
    ComplexGitSyncClient().load(config)
    _states(tmp_path / "demo")[0].unlink()

    report = ComplexGitSyncClient().verify(tmp_path / "demo")

    assert report.state is HistoryState.CORRUPT
    assert any(finding is Finding.MISSING_STATE for _s, finding, _d in report.findings)


def test_an_edited_state_no_longer_hashes_to_its_name(tmp_path):
    """What content-addressing bought: a stored State cannot be edited quietly."""
    config = _workspace(tmp_path / "demo")
    ComplexGitSyncClient().load(config)

    state = _states(tmp_path / "demo")[0]
    state.write_text(state.read_text(encoding="utf-8").replace('name = "demo"', 'name = "stolen"', 1),
                     encoding="utf-8")

    report = ComplexGitSyncClient().verify(tmp_path / "demo")

    assert report.state is HistoryState.CORRUPT
    assert any(
        finding is Finding.STATE_DIGEST_MISMATCH for _s, finding, _d in report.findings
    )


def test_a_state_no_entry_recorded_is_reported_but_is_not_corruption(tmp_path):
    """An orphan is reported, and the chain still verifies.

    Every workspace used before the ledger existed holds States that no
    entry records. They are history, not damage — calling that "corrupt"
    would teach exactly the shrug this command was rebuilt to stop.
    """
    config = _workspace(tmp_path / "demo")
    ComplexGitSyncClient().load(config)
    (tmp_path / "demo" / ".cgitsync" / "state" / f"{'b' * 64}.gts").write_text(
        "orphan\n", encoding="utf-8"
    )

    report = ComplexGitSyncClient().verify(tmp_path / "demo")

    assert any(finding is Finding.ORPHAN_STATE for _s, finding, _d in report.findings)
    assert report.state is HistoryState.VERIFIED

    # …and a real problem alongside it still reads as corrupt.
    _states(tmp_path / "demo")[0].unlink()
    assert ComplexGitSyncClient().verify(tmp_path / "demo").state is HistoryState.CORRUPT


# ---------------------------------------------------------------------------
# Old workspaces
# ---------------------------------------------------------------------------


def test_a_workspace_with_only_a_legacy_register_still_resolves(tmp_path):
    """Nothing writes the single-file register, and everything still reads it."""
    from ComplexGitSync.snapshot_resolver import discover_gts_path

    workspace = tmp_path / "old"
    state_dir = workspace / ".cgitsync" / f"state({'a' * 64})_0"
    state_dir.mkdir(parents=True)
    snapshot = state_dir / "demo.gts"
    snapshot.write_text("[document]\n", encoding="utf-8")
    (workspace / ".cgitsync" / "demo.lgr").write_text("[register]\n", encoding="utf-8")

    assert discover_gts_path(str(workspace)) == snapshot.resolve()


def test_the_current_snapshot_comes_from_the_chain(tmp_path):
    """Resolution asks the ledger, not a filesystem timestamp."""
    from ComplexGitSync.snapshot_resolver import describe_gts_path

    config = _workspace(tmp_path / "demo")
    ComplexGitSyncClient().load(config)

    resolution = describe_gts_path(str(tmp_path / "demo"))

    assert resolution.origin == "ledger"
    assert resolution.path == _states(tmp_path / "demo")[0].resolve()

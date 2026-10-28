"""`omniscience mount` and `omniscience record`: join the register, see what a State would register.

Real Git throughout, with bare repositories standing in for the memory's remote
and for the shared ``omniscience`` repository, as in ``test_push_folds_memory.py``
and ``test_user_dev_profile.py``. Nothing here registers anything: the record is
only printed, and the mount only declares and clones.
"""

from __future__ import annotations

import json
import subprocess
import warnings
from pathlib import Path

import pytest
from test_push_folds_memory import _change, _git, _identify, _loaded, _memory_ready_workspace

from ComplexGitSync import orchestre
from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.omniscience.omniscience_record import OmniscienceRecord
from ComplexGitSync.operating_space.operating_space import OperatingSpace
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.orchestre.memory_setup import MemorySetupWarning
from ComplexGitSync.tree.memory.commit_log import CommitLog


def _committed_states(tree: dict[str, Path]) -> list[Path]:
    return sorted((tree["mount"] / "state").glob("*.gts"))


# -- record -----------------------------------------------------------------------


def test_a_pushed_state_makes_a_record_that_names_everything_it_commits_to(tmp_path):
    tree = _memory_ready_workspace(tmp_path)
    [state] = _committed_states(tree)
    client = _loaded(tree["snapshot"])

    result = client.omniscience_record(tree["root"], state.stem[:10])

    record = OmniscienceRecord.from_dict(result["record"])
    assert record.os_id == OperatingSpace.id_of("demo")
    assert (record.project, record.project_branch) == ("demo", "main")
    assert record.state_id == f"state({state.stem})"
    assert record.memory_commit == record.local_memory_git_commit == _git(tree["memory_remote"], "rev-parse", "demo")
    assert record.memory_ref == "refs/heads/demo" and record.memory_mount == ".cgitsync/.memory"
    assert record.local_ledger_tip.startswith("sha256:")
    assert record.recorded_at.endswith("Z") and record.contributor == "integration@complexgitsync.test"
    assert result["record_hash"] == record.record_hash and result["filename"] == f"register/{record.record_hash}.toml"
    assert result["os_state_hash"] == record.os_state_hash and result["leaf"] == record.leaf_digest.hex()


def test_recording_writes_nothing(tmp_path):
    tree = _memory_ready_workspace(tmp_path)
    [state] = _committed_states(tree)
    client = _loaded(tree["snapshot"])
    before = (_git(tree["mount"], "status", "--porcelain"), _git(tree["mount"], "rev-parse", "HEAD"), sorted(p.name for p in (tree["root"] / ".cgitsync").rglob("*") if p.is_file()))

    client.omniscience_record(tree["root"], state.stem)

    assert before == (_git(tree["mount"], "status", "--porcelain"), _git(tree["mount"], "rev-parse", "HEAD"), sorted(p.name for p in (tree["root"] / ".cgitsync").rglob("*") if p.is_file()))


def test_the_same_state_gives_the_same_record_hash_whenever_it_is_asked(tmp_path):
    tree = _memory_ready_workspace(tmp_path)
    [state] = _committed_states(tree)
    client = _loaded(tree["snapshot"])

    first = client.omniscience_record(tree["root"], state.stem)
    second = client.omniscience_record(tree["root"], state.stem)

    assert first["record_hash"] == second["record_hash"]


def test_publication_evidence_is_digested_into_the_record_and_its_rows_check_against_it(tmp_path):
    tree = _memory_ready_workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")
    client.commit("work to register")
    client.push()
    client.memory_push(tree["root"])  # the rows a push writes are pending until the next fold
    published = [path for path in _committed_states(tree) if CommitLog(tree["mount"]).read(path.stem)["published"]]
    assert len(published) == 1

    result = client.omniscience_record(tree["root"], published[0].stem)

    record = OmniscienceRecord.from_dict(result["record"])
    rows = CommitLog(tree["mount"]).read(published[0].stem)["published"]
    assert record.published_state_id == CommitLog.digest_of_rows([], rows)
    [evidence] = record.published_rows
    assert evidence["commit"] == rows[0]["sha"] and evidence["ref"] == "refs/heads/main"
    assert evidence["remote_commit"] == rows[0]["sha"]  # the remote's own word, from PushAnchor


def test_a_state_not_yet_in_the_committed_memory_is_refused_with_the_command_that_fixes_it(tmp_path):
    tree = _memory_ready_workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")
    client.commit("pending work")
    pending = [p for p in (tree["root"] / ".cgitsync" / "state").glob("*.gts")]
    assert pending

    with pytest.raises(GitSyncError, match="memory push"):
        client.omniscience_record(tree["root"], pending[0].stem)


def test_an_uncommitted_or_unpushed_memory_is_refused(tmp_path):
    tree = _memory_ready_workspace(tmp_path)
    [state] = _committed_states(tree)
    client = _loaded(tree["snapshot"])

    (tree["mount"] / "stray.txt").write_text("x", encoding="utf-8")
    with pytest.raises(GitSyncError, match="uncommitted changes"):
        client.omniscience_record(tree["root"], state.stem)

    _identify(tree["mount"])
    _git(tree["mount"], "add", "stray.txt")
    _git(tree["mount"], "commit", "-m", "not pushed")
    with pytest.raises(GitSyncError, match="not on its remote"):
        client.omniscience_record(tree["root"], state.stem)


def test_an_unknown_state_is_refused(tmp_path):
    tree = _memory_ready_workspace(tmp_path)

    with pytest.raises(GitSyncError, match="no State beginning with 'ffff'"):
        _loaded(tree["snapshot"]).omniscience_record(tree["root"], "ffff")


def test_record_is_on_the_command_line(tmp_path, capsys):
    tree = _memory_ready_workspace(tmp_path)
    [state] = _committed_states(tree)
    # The latest State is what the command loads, and it must declare the memory mount.
    client = _loaded(tree["snapshot"])
    client.write_gts_snapshot(command_origin="load")
    client.memory_push(tree["root"])

    code = cli_main(["omniscience", "record", state.stem[:8], "--json", "--search-dir", str(tree["root"])])

    out = capsys.readouterr().out
    printed = json.loads(out[out.index('{\n  "record"') :])  # after the run's own progress lines
    assert code == 0 and printed["record"]["record"]["project"] == "demo"
    assert printed["filename"] == f"register/{printed['record_hash']}.toml"


# -- mount ------------------------------------------------------------------------

_DEV_CGS = """project = "demo"

repos = [
  "github:flipoyo/demo",
  { repository = "github:someone/conf", relative_path = "conf", private = true },
]
"""

_USER_CGS = """project = "demo"

repos = [
  "github:flipoyo/demo",
]
"""


def _omniscience_remote(tmp_path: Path, *branches: str) -> Path:
    remote = tmp_path / "omniscience.git"
    subprocess.run(["git", "init", "--bare", "-b", branches[0], str(remote)], check=True, capture_output=True)
    seed = tmp_path / "omniscience-seed"
    seed.mkdir()
    _git(seed, "init", "-b", branches[0])
    _identify(seed)
    (seed / "README.md").write_text("register\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "start")
    _git(seed, "remote", "add", "origin", str(remote))
    for branch in branches:
        _git(seed, "push", "origin", f"HEAD:refs/heads/{branch}")
    return remote


def _tree(root: Path, cgs: str) -> ComplexGitSyncClient:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.cgs").write_text(cgs, encoding="utf-8")
    client = ComplexGitSyncClient()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MemorySetupWarning)
        client.load(root / "project.cgs")
    return client


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    for key, value in (("GIT_AUTHOR_NAME", "Test"), ("GIT_COMMITTER_NAME", "Test"), ("GIT_AUTHOR_EMAIL", "t@example.com"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(key, value)


def test_mount_declares_a_read_only_entry_and_clones_the_index_branch(tmp_path, monkeypatch):
    remote = _omniscience_remote(tmp_path, "_global")
    monkeypatch.setattr(orchestre, "_remote_url_for_identifier", lambda identifier: str(remote))
    workspace = tmp_path / "d"
    client = _tree(workspace, _DEV_CGS)

    result = client.omniscience_mount(None, workspace)

    assert result["added"] is True and result["cloned"] is True and result["branch"] == "_global"
    spec = (workspace / "project.cgs").read_text(encoding="utf-8")
    assert 'repository = "github:flipoyo/omniscience"' in spec and 'relative_path = ".omniscience"' in spec
    assert "writable" not in spec.split(".omniscience")[1].split("}")[0]  # an inspector: read-only
    assert (workspace / ".omniscience" / ".git").is_dir()
    assert _git(workspace / ".omniscience", "branch", "--show-current") == "_global"


def test_mount_takes_the_projects_own_branch_when_there_is_one_and_contribute_is_writable(tmp_path, monkeypatch):
    remote = _omniscience_remote(tmp_path, "demo", "_global")
    monkeypatch.setattr(orchestre, "_remote_url_for_identifier", lambda identifier: str(remote))
    workspace = tmp_path / "d"
    client = _tree(workspace, _DEV_CGS)

    result = client.omniscience_mount(None, workspace, contribute=True)

    assert result["branch"] == "demo"
    assert "writable = true" in (workspace / "project.cgs").read_text(encoding="utf-8").split(".omniscience")[1]


def test_mounting_twice_changes_nothing(tmp_path, monkeypatch):
    remote = _omniscience_remote(tmp_path, "_global")
    monkeypatch.setattr(orchestre, "_remote_url_for_identifier", lambda identifier: str(remote))
    workspace = tmp_path / "d"
    client = _tree(workspace, _DEV_CGS)
    client.omniscience_mount(None, workspace)
    spec = (workspace / "project.cgs").read_text(encoding="utf-8")

    again = client.omniscience_mount(None, workspace)

    assert again["added"] is False and again["cloned"] is False
    assert (workspace / "project.cgs").read_text(encoding="utf-8") == spec


def test_a_user_tree_is_refused_by_name_before_anything_is_touched(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestre, "_remote_url_for_identifier", lambda identifier: pytest.fail("the remote was reached"))
    workspace = tmp_path / "u"
    client = _tree(workspace, _USER_CGS)
    spec = (workspace / "project.cgs").read_text(encoding="utf-8")

    with pytest.raises(GitSyncError, match="omniscience mount was refused"):
        client.omniscience_mount(None, workspace)

    assert (workspace / "project.cgs").read_text(encoding="utf-8") == spec
    assert not (workspace / ".omniscience").exists()


def test_a_remote_with_nothing_registered_is_refused_but_the_entry_stays_declared(tmp_path, monkeypatch):
    remote = _omniscience_remote(tmp_path, "someone-elses")
    monkeypatch.setattr(orchestre, "_remote_url_for_identifier", lambda identifier: str(remote))
    workspace = tmp_path / "d"
    client = _tree(workspace, _DEV_CGS)

    with pytest.raises(GitSyncError, match="nothing has been registered"):
        client.omniscience_mount(None, workspace)

    assert 'relative_path = ".omniscience"' in (workspace / "project.cgs").read_text(encoding="utf-8")


def test_a_declared_entry_that_disagrees_about_writable_is_said_so_not_silently_kept(tmp_path, monkeypatch):
    remote = _omniscience_remote(tmp_path, "_global")
    monkeypatch.setattr(orchestre, "_remote_url_for_identifier", lambda identifier: str(remote))
    workspace = tmp_path / "d"
    client = _tree(workspace, _DEV_CGS)
    client.omniscience_mount(None, workspace)  # declared read-only
    client = _tree(workspace, (workspace / "project.cgs").read_text(encoding="utf-8"))  # a tree built from that .cgs

    again = client.omniscience_mount(None, workspace, contribute=True)

    assert again["added"] is False and "read-only" in again["mismatch"]
    assert client.omniscience_mount(None, workspace)["mismatch"] is None


def test_a_memory_with_no_ledger_entry_is_told_so_not_called_unpushed(tmp_path):
    tree = _memory_ready_workspace(tmp_path)
    [state] = _committed_states(tree)
    for entry in (tree["mount"] / "lgr").glob("*"):
        entry.unlink()
    _git(tree["mount"], "add", "-A")
    _git(tree["mount"], "commit", "-m", "emptied")
    _git(tree["mount"], "push", "origin", "HEAD")

    with pytest.raises(GitSyncError, match="no ledger entry"):
        _loaded(tree["snapshot"]).omniscience_record(tree["root"], state.stem)

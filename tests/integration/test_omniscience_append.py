"""`omniscience register` and `omniscience observe`: appending to the shared register, and watching what disappears.

Real Git throughout: a bare repository stands in for the shared ``omniscience``
repository and one for each workspace's memory, as in ``test_omniscience_register.py``.
Every identifier is mapped to a local path by patching the one function the
package resolves addresses through.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from test_omniscience_register import _USER_CGS, _omniscience_remote, _tree
from test_push_folds_memory import _git, _identify, _loaded, _memory_ready_workspace

from ComplexGitSync import orchestre
from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_runner import GitRunner
from ComplexGitSync.global_index import GlobalIndex
from ComplexGitSync.merkle import MerkleRoot
from ComplexGitSync.omniscience_observation import ObservationStatus
from ComplexGitSync.omniscience_record import OmniscienceRecord

_OMNISCIENCE = "github:flipoyo/omniscience"


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    for key, value in (("GIT_AUTHOR_NAME", "Test"), ("GIT_COMMITTER_NAME", "Test"), ("GIT_AUTHOR_EMAIL", "t@example.com"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(key, value)


@pytest.fixture
def addresses(monkeypatch) -> dict[str, str]:
    """The identifier-to-path map every address is resolved through."""
    mapping: dict[str, str] = {}
    monkeypatch.setattr(orchestre, "_remote_url_for_identifier", lambda identifier: mapping.get(identifier, str(Path("/nonexistent") / identifier.replace(":", "_").replace("/", "_"))))
    return mapping


def _workspace(tmp_path: Path, name: str, owner: str, omniscience: Path, addresses: dict[str, str]):
    """A DEV tree with a pushed memory (owned by *owner*) and the register mounted writable."""
    base = tmp_path / name
    base.mkdir()
    tree = _memory_ready_workspace(base)
    omni = tree["root"] / ".omniscience"
    _git(base, "clone", "-b", "_global", str(omniscience), str(omni))
    _identify(omni)
    addresses[_OMNISCIENCE] = str(omniscience)
    addresses[f"github:{owner}/memory"] = str(tree["memory_remote"])
    text = tree["snapshot"].read_text(encoding="utf-8").replace('project_owner_name = "owner"\nproject_name = "memory"', f'project_owner_name = "{owner}"\nproject_name = "memory"')
    text += f"""
[[repo_state]]
name = "omniscience"
node_type = "leaf"
absolute_path = "{omni.as_posix()}"
parent_absolute_path = "{tree['root'].as_posix()}"
relative_path = ".omniscience"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "_global"
target_ref_kind = "branch"
target_ref_name = "_global"
resolved_ref_kind = "branch"
resolved_ref_name = "_global"
commit_sha = "{_git(omni, 'rev-parse', 'HEAD')}"
project_owner_name = "flipoyo"
project_name = "omniscience"
gitprovider = "github"
private = true
writable = true
"""
    tree["snapshot"].write_text(text, encoding="utf-8")
    tree["omni"] = omni
    return tree, _loaded(tree["snapshot"])


def _blobs(remote: Path, branch: str, prefix: str) -> list[str]:
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", branch, "--", prefix], cwd=remote, capture_output=True, text=True, check=False).stdout
    return out.split()


def _checkpoint(remote: Path) -> dict:
    """The head of the chain: the checkpoint no other names as its parent."""
    bodies = [tomllib.loads(_git(remote, "show", f"_global:{path}")) for path in _blobs(remote, "_global", "checkpoints/")]
    parents = {body["parent"] for body in bodies}
    [head] = [body for body in bodies if MerkleRoot.checkpoint_hash(body) not in parents]
    return head


def _hand_push(tmp_path: Path, remote: Path, branch: str, files: dict[str, str], *, force: bool = False, orphan: bool = False) -> None:
    scratch = tmp_path / f"hand-{len(list(tmp_path.glob('hand-*')))}"
    _git(tmp_path, "clone", "-b", branch, str(remote), str(scratch))
    _identify(scratch)
    if orphan:
        _git(scratch, "checkout", "--orphan", "rewritten")
        _git(scratch, "rm", "-rf", ".")
    for relative, text in files.items():
        (scratch / relative).parent.mkdir(parents=True, exist_ok=True)
        (scratch / relative).write_text(text, encoding="utf-8")
        _git(scratch, "add", relative)
    _git(scratch, "commit", "-m", "by hand", "--allow-empty")
    _git(scratch, "push", *(["--force"] if force else []), "origin", f"HEAD:refs/heads/{branch}")


# -- register ---------------------------------------------------------------------


def test_register_appends_the_record_and_the_first_checkpoint_and_twice_does_nothing(tmp_path, addresses):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)

    first = client.omniscience_register(tree["root"])

    assert first["appended"] is True and first["branch"] == "demo" and first["retries"] == 0
    assert _blobs(omni, "demo", "register/") == [first["filename"]]
    record = OmniscienceRecord.from_dict(tomllib.loads(_git(omni, "show", f"demo:{first['filename']}")))
    assert first["checkpoint"]["status"] == "extended"
    body = _checkpoint(omni)
    assert body["records"] == [first["record_hash"]] and body["parent"] == ""
    assert body["global_root"] == GlobalIndex.of([record]).root == first["checkpoint"]["global_root"]
    assert _git(tree["omni"], "branch", "--show-current") == "demo"
    tips = (_git(omni, "rev-parse", "demo"), _git(omni, "rev-parse", "_global"))

    again = client.omniscience_register(tree["root"])

    assert again["appended"] is False and again["checkpoint"]["status"] == "unchanged"
    assert (_git(omni, "rev-parse", "demo"), _git(omni, "rev-parse", "_global")) == tips


def test_a_file_the_register_holds_with_other_content_is_never_rewritten(tmp_path, addresses):
    """Omniscience §10, item 8: the same content-addressed name with other bytes is refused, not overwritten."""
    from ComplexGitSync.orchestre.omniscience_appender import RegisterAppender

    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    first = client.omniscience_register(tree["root"])
    held = _git(omni, "show", f"demo:{first['filename']}")
    tip = _git(omni, "rev-parse", "demo")

    with pytest.raises(GitSyncError, match="never rewritten"):
        RegisterAppender(client, tree["omni"]).append("demo", lambda _tip: {first["filename"]: held + "# other bytes\n"}, "collide")

    assert _git(omni, "rev-parse", "demo") == tip
    assert _git(omni, "show", f"demo:{first['filename']}") == held
    # The same bytes again, as the runner reads them, are no change at all, not a refusal.
    exact = client.git_runner.show_file(tree["omni"], tip, first["filename"])
    assert RegisterAppender(client, tree["omni"]).append("demo", lambda _tip: {first["filename"]: exact}, "same")["appended"] is False


def test_a_push_the_provider_refuses_with_nobody_else_appending_is_the_callers_error_unretried(tmp_path, addresses, monkeypatch):
    """Omniscience §10, items 4 and 8: an unauthorized append reaches the caller unchanged, moves nothing and is never retried."""
    from ComplexGitSync.orchestre.omniscience_appender import RegisterAppender

    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    client.omniscience_register(tree["root"])
    tip = _git(omni, "rev-parse", "demo")
    pushes: list = []
    events: list = []

    def refused(self, repo, local_ref, remote_ref, **kwargs):
        pushes.append(remote_ref)
        raise GitSyncError("remote: Permission denied to alice.\nfatal: unable to access the register")

    monkeypatch.setattr(GitRunner, "push_ref_as", refused)
    monkeypatch.setattr(client, "_log_event", lambda name, **fields: events.append(name))

    with pytest.raises(GitSyncError, match="Permission denied to alice"):
        RegisterAppender(client, tree["omni"]).append("demo", lambda _tip: {"register/unauthorized.toml": "x = 1\n"}, "unauthorized")

    assert pushes == ["demo"]
    assert _git(omni, "rev-parse", "demo") == tip
    assert "omniscience_retry" not in events


def test_two_workspaces_registering_at_once_both_land_and_the_second_retries_without_forcing(tmp_path, addresses, monkeypatch):
    omni = _omniscience_remote(tmp_path, "_global")
    a_tree, a = _workspace(tmp_path, "a", "alice", omni, addresses)
    b_tree, b = _workspace(tmp_path, "b", "bob", omni, addresses)
    real_push = GitRunner.push_ref_as
    raced: list = []

    def push_after_the_other_registered(self, repo, local_ref, remote_ref, **kwargs):
        if not raced:
            raced.append(None)
            raced[0] = b.omniscience_register(b_tree["root"])  # bob gets in first
        return real_push(self, repo, local_ref, remote_ref, **kwargs)

    monkeypatch.setattr(GitRunner, "push_ref_as", push_after_the_other_registered)

    first = a.omniscience_register(a_tree["root"])

    assert raced and raced[0]["appended"] is True
    assert first["appended"] is True and first["retries"] >= 1
    assert len(_blobs(omni, "demo", "register/")) == 2
    assert sorted(_checkpoint(omni)["records"]) == sorted([first["record_hash"], raced[0]["record_hash"]])
    # nothing was forced: every tip the remote ever had is an ancestor of the one it has
    assert subprocess.run(["git", "rev-list", "--count", "demo"], cwd=omni, capture_output=True, text=True).stdout.strip() == "3"  # start, bob, alice


def test_a_crash_between_commit_and_push_then_a_rerun_leaves_one_record(tmp_path, addresses, monkeypatch):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    real_push = GitRunner.push_ref_as
    calls: list = []

    def crash(self, repo, local_ref, remote_ref, **kwargs):
        if not calls:
            calls.append(1)
            raise GitSyncError("simulated crash before the push left")
        return real_push(self, repo, local_ref, remote_ref, **kwargs)

    monkeypatch.setattr(GitRunner, "push_ref_as", crash)
    with pytest.raises(GitSyncError, match="simulated crash"):
        client.omniscience_register(tree["root"])
    assert not _git(omni, "branch", "--list", "demo")  # nothing reached the remote

    done = client.omniscience_register(tree["root"])

    assert done["appended"] is True
    assert len(_blobs(omni, "demo", "register/")) == 1
    assert _git(omni, "rev-list", "--count", "demo") == "2"  # start, and the one record


def test_hand_appended_files_are_proposed_and_only_a_valid_one_is_accepted(tmp_path, addresses):
    omni = _omniscience_remote(tmp_path, "_global")
    a_tree, a = _workspace(tmp_path, "a", "alice", omni, addresses)
    b_tree, b = _workspace(tmp_path, "b", "bob", omni, addresses)
    own = a.omniscience_register(a_tree["root"])
    bobs = b.omniscience_record(b_tree["root"], _newest(b_tree))
    good = OmniscienceRecord.from_dict(bobs["record"])
    import tomli_w

    _hand_push(tmp_path, omni, "demo", {
        good.filename: tomli_w.dumps(good.to_dict()),
        "register/garbage.toml": "this is [not toml",
        f"register/{'0' * 64}.toml": tomli_w.dumps(good.to_dict()),  # not named by its own hash
    })

    result = a.omniscience_register(a_tree["root"])

    step = result["checkpoint"]
    assert step["status"] == "extended" and step["records"] == 2
    assert sorted(_checkpoint(omni)["records"]) == sorted([own["record_hash"], good.record_hash])
    reasons = {item["path"]: item["reason"] for item in step["unaccepted"]}
    assert set(reasons) == {"register/garbage.toml", f"register/{'0' * 64}.toml"}
    assert "not TOML" in reasons["register/garbage.toml"] and "named by" in reasons[f"register/{'0' * 64}.toml"] or "named" in reasons[f"register/{'0' * 64}.toml"]


def test_a_record_whose_memory_commit_cannot_be_fetched_stays_unaccepted(tmp_path, addresses):
    omni = _omniscience_remote(tmp_path, "_global")
    a_tree, a = _workspace(tmp_path, "a", "alice", omni, addresses)
    b_tree, b = _workspace(tmp_path, "b", "bob", omni, addresses)
    bobs = OmniscienceRecord.from_dict(b.omniscience_record(b_tree["root"], _newest(b_tree))["record"])
    import tomli_w

    _hand_push(tmp_path, omni, "_global", {})  # make sure the branch has a tip
    a.omniscience_register(a_tree["root"])
    shutil.rmtree(b_tree["memory_remote"])  # bob's memory is gone: his record names a commit nobody can fetch
    _hand_push(tmp_path, omni, "demo", {bobs.filename: tomli_w.dumps(bobs.to_dict())})

    step = a.omniscience_register(a_tree["root"])["checkpoint"]

    assert step["records"] == 1
    assert [item["path"] for item in step["unaccepted"]] == [bobs.filename]
    assert "cannot be read" in step["unaccepted"][0]["reason"]


def test_a_forced_rewrite_of_the_index_is_reported_and_not_repaired(tmp_path, addresses, capsys):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    client.omniscience_register(tree["root"])
    _hand_push(tmp_path, omni, "_global", {"README.md": "history was rewritten\n"}, force=True, orphan=True)
    rewritten_tip = _git(omni, "rev-parse", "_global")

    result = client.omniscience_register(tree["root"])

    assert result["checkpoint"]["status"] == "rewritten" and "rewritten" in result["checkpoint"]["problem"]
    assert _git(omni, "rev-parse", "_global") == rewritten_tip  # not repaired, not extended over
    _latest_state_declares_the_mounts(client, tree)

    assert cli_main(["omniscience", "register", "--search-dir", str(tree["root"])]) == 1
    assert "checkpoint=rewritten" in capsys.readouterr().out


def test_a_user_tree_is_refused_by_name_before_anything_is_touched(tmp_path, addresses):
    workspace = tmp_path / "u"
    client = _tree(workspace, _USER_CGS)

    with pytest.raises(GitSyncError, match="omniscience register was refused"):
        client.omniscience_register(workspace)
    with pytest.raises(GitSyncError, match="omniscience observe was refused"):
        client.omniscience_observe(workspace)


def test_a_read_only_or_dirty_mount_is_refused(tmp_path, addresses):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    (tree["omni"] / "stray.txt").write_text("x", encoding="utf-8")

    with pytest.raises(GitSyncError, match="uncommitted changes"):
        client.omniscience_register(tree["root"])


def test_the_reserved_name_cannot_be_registered(tmp_path, addresses):
    from ComplexGitSync.omniscience_repository import OmniscienceRepository

    assert OmniscienceRepository.branch("_global", "main") == "_global"  # the case the register refuses, by name


def _latest_state_declares_the_mounts(client, tree) -> None:
    """The command line loads the newest State: make it the one that declares the memory and the register."""
    client.write_gts_snapshot(command_origin="load")
    client.memory_push(tree["root"])


def _newest(tree) -> str:
    [state] = sorted((tree["mount"] / "state").glob("*.gts"))
    return state.stem


# -- observe ----------------------------------------------------------------------


class _Ticking:
    """A clock that moves a minute on every reading, so two findings are never in the same second."""

    def __init__(self) -> None:
        self.moment = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        self.moment += timedelta(minutes=1)
        return self.moment

    def time_ns(self) -> int:
        return int(self.moment.timestamp() * 1e9)


def _observed(client, tree, **kwargs):
    return client.omniscience_observe(tree["root"], **kwargs)


def _statuses(result) -> dict[str, str]:
    return {f"{o['target']['kind']}": o["finding"]["status"] for o in result["observations"]}


def test_observe_is_never_a_side_effect_of_register(tmp_path, addresses):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    client.omniscience_register(tree["root"])

    assert not _blobs(omni, "demo", "observations/")


def test_an_available_memory_is_observed_and_nothing_else_changes(tmp_path, addresses):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    registered = client.omniscience_register(tree["root"])
    leaves = _checkpoint(omni)

    result = _observed(client, tree)

    assert _statuses(result) == {"memory": "AVAILABLE"} and result["report"]["verdict"] == "ok"
    [path] = _blobs(omni, "demo", "observations/")
    assert tomllib.loads(_git(omni, "show", f"demo:{path}"))["finding"]["method"] == "ls-remote"
    assert _blobs(omni, "demo", "register/") == [registered["filename"]]  # no leaf removed, none rewritten
    assert _checkpoint(omni) == leaves


def test_a_missing_memory_is_not_found_and_when_it_reappears_it_is_available_again(tmp_path, addresses):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    client.omniscience_register(tree["root"])
    client.clock = _Ticking()
    moved = tmp_path / "memory-moved.git"
    tree["memory_remote"].rename(moved)

    gone = _observed(client, tree)

    assert _statuses(gone) == {"memory": "NOT_FOUND"}
    assert gone["report"]["verdict"] == "warning" and gone["report"]["failures"] == []
    assert len(_blobs(omni, "demo", "register/")) == 1  # no leaf removed

    moved.rename(tree["memory_remote"])
    back = _observed(client, tree)

    assert _statuses(back) == {"memory": "AVAILABLE"} and back["report"]["verdict"] == "ok"
    assert len(_blobs(omni, "demo", "observations/")) == 2  # both findings stay on the record


def test_a_commit_lost_from_a_repository_that_is_still_there_is_commit_missing(tmp_path, addresses):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    client.omniscience_register(tree["root"])
    shutil.rmtree(tree["memory_remote"])
    subprocess.run(["git", "init", "--bare", "-b", "demo", str(tree["memory_remote"])], check=True, capture_output=True)  # there, and empty

    assert _statuses(_observed(client, tree)) == {"memory": "COMMIT_MISSING"}


def test_a_deleted_ref_with_the_commit_still_reachable_is_ref_missing(tmp_path, addresses):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    client.omniscience_register(tree["root"])
    remote = tree["memory_remote"]
    commit = _git(remote, "rev-parse", "demo")
    _git(remote, "update-ref", "refs/heads/kept-elsewhere", commit)
    _git(remote, "update-ref", "-d", "refs/heads/demo")

    assert _statuses(_observed(client, tree)) == {"memory": "REF_MISSING"}


def test_revoked_access_is_access_denied_and_is_not_a_deletion(tmp_path, addresses, monkeypatch):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    client.omniscience_register(tree["root"])
    monkeypatch.setattr(GitRunner, "remote_refs", lambda self, url: (None, "git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository."))

    result = _observed(client, tree)

    assert _statuses(result) == {"memory": "ACCESS_DENIED"} and result["report"]["failures"] == []


def test_a_network_outage_is_unreachable_and_never_a_deletion(tmp_path, addresses, monkeypatch):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    client.omniscience_register(tree["root"])
    monkeypatch.setattr(GitRunner, "remote_refs", lambda self, url: (None, "fatal: unable to access 'https://github.com/alice/memory.git/': Could not resolve host: github.com"))

    result = _observed(client, tree)

    assert _statuses(result) == {"memory": "UNREACHABLE"} and result["report"]["verdict"] == "warning"


def test_a_closed_branch_comes_from_the_ledger_and_never_from_a_failed_fetch(tmp_path, addresses):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    from ComplexGitSync.orchestre.omniscience_observer import OmniscienceObserver

    remote = tree["memory_remote"]
    commit = _git(remote, "rev-parse", "demo")
    _git(remote, "update-ref", "refs/heads/ancestors", commit)
    _git(remote, "update-ref", "-d", "refs/heads/demo")
    observer = OmniscienceObserver(client)

    declared = observer._probe("github:alice/memory", "refs/heads/demo", commit, {"memory:refs/heads/demo": 7})
    undeclared = observer._probe("github:alice/memory", "refs/heads/demo", commit, {})
    monkey_failed = observer._probe("github:alice/nowhere", "refs/heads/demo", commit, {"nowhere:refs/heads/demo": 7})

    assert declared[0] is ObservationStatus.CLOSED and declared[2] == {"ledger_seq": 7}
    assert undeclared[0] is ObservationStatus.REF_MISSING
    assert monkey_failed[0] is ObservationStatus.NOT_FOUND  # a failed fetch is never "closed"


def _fake_gpg(tmp_path: Path) -> Path:
    script = tmp_path / "fake-gpg"
    script.write_text('#!/bin/sh\ncat >/dev/null\necho >&2\necho "[GNUPG:] SIG_CREATED D 1 8 00 0 0 KEY" >&2\nprintf -- "-----BEGIN PGP SIGNATURE-----\\nfake\\n-----END PGP SIGNATURE-----\\n"\n', encoding="utf-8")
    script.chmod(0o755)
    return script


def test_a_deletion_is_confirmed_only_by_a_signed_owner_note_about_a_target_the_host_says_is_gone(tmp_path, addresses):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    client.omniscience_register(tree["root"])

    with pytest.raises(GitSyncError, match="needs a reason"):
        _observed(client, tree, confirm_deleted="github:alice/memory")
    with pytest.raises(GitSyncError, match="not a deletion to confirm"):  # it is still there
        _observed(client, tree, confirm_deleted="github:alice/memory", reason="renamed")

    tree["memory_remote"].rename(tmp_path / "gone.git")
    _git(tree["omni"], "config", "gpg.program", "false")
    _git(tree["omni"], "config", "user.signingkey", "KEY")
    with pytest.raises(GitSyncError, match="must be a signed commit"):
        _observed(client, tree, confirm_deleted="github:alice/memory", reason="owner deleted it")
    assert not _blobs(omni, "demo", "observations/")  # nothing recorded without a signature

    _git(tree["omni"], "config", "gpg.program", str(_fake_gpg(tmp_path)))
    result = _observed(client, tree, confirm_deleted="github:alice/memory", reason="owner deleted it")

    assert _statuses(result) == {"memory": "DELETION_CONFIRMED"}
    assert result["observations"][0]["finding"]["evidence"]["reason"] == "owner deleted it"
    assert len(_blobs(omni, "demo", "register/")) == 1  # still registered


def test_observe_is_on_the_command_line(tmp_path, addresses, capsys):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _workspace(tmp_path, "a", "alice", omni, addresses)
    client.omniscience_register(tree["root"])
    _latest_state_declares_the_mounts(client, tree)

    code = cli_main(["omniscience", "observe", "--json", "--search-dir", str(tree["root"])])

    out = capsys.readouterr().out
    printed = json.loads(out[out.index('{\n  "project"'):])
    assert code == 0 and printed["report"]["verdict"] == "ok"

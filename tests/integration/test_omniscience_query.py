"""`cgitsync omniscience get|branches|history|state|proof|verify|status|report|memory`: asking the shared register.

The end-to-end of the Omniscience workstream (OmniscienceQuery, O5): two projects with two branches each,
four developers on four workspaces, one bare ``omniscience`` remote and one bare ``.memory`` remote per
workspace, as in ``test_omniscience_append.py``. Real Git throughout; every address is mapped to a local
path by patching the one function the package resolves addresses through.
"""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
import test_push_folds_memory as fixtures
import tomli_w
from test_omniscience_append import (
    _blobs,
    _checkpoint,
    _hand_push,
    _latest_state_declares_the_mounts,
    _newest,
    _observed,
    _Ticking,
    _workspace,
)
from test_omniscience_register import _omniscience_remote
from test_push_folds_memory import _git, _identify

from ComplexGitSync import orchestre
from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.git_runner import GitRunner
from ComplexGitSync.global_index import GlobalIndex
from ComplexGitSync.gts_document import GtsDocument
from ComplexGitSync.omniscience_record import OmniscienceRecord
from ComplexGitSync.operating_space import OperatingSpace
from ComplexGitSync.registry import RegistryTranslator

_ORIGINAL_SNAPSHOT = fixtures._root_snapshot  # patched per workspace below, always from the original


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


def _world(tmp_path, addresses, monkeypatch, name, owner, project, branch, omni):
    """A DEV workspace of *project* on *branch*, with its own pushed memory, the register mounted, and nothing registered yet."""
    def renamed(root, *, sha):
        text = _ORIGINAL_SNAPSHOT(root, sha=sha).replace('name = "demo"', f'name = "{project}"')
        return text.replace('_ref_name = "main"', f'_ref_name = "{branch}"')

    monkeypatch.setattr(fixtures, "_root_snapshot", renamed)
    return _workspace(tmp_path, name, owner, omni, addresses)


def _records(omni: Path) -> list[OmniscienceRecord]:
    found = []
    for branch in ("demo", "demo_feature", "atlas", "atlas_feature"):
        for path in _blobs(omni, branch, "register/"):
            found.append(OmniscienceRecord.from_dict(tomllib.loads(_git(omni, "show", f"{branch}:{path}"))))
    return found


def _one(tmp_path, addresses, monkeypatch, project="demo", branch="main"):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _world(tmp_path, addresses, monkeypatch, "a", "alice", project, branch, omni)
    registered = client.omniscience_register(tree["root"])
    return omni, tree, client, registered


# -- the workstream's end-to-end --------------------------------------------------


def test_two_projects_with_two_branches_by_four_developers_resolve_prove_and_restore(tmp_path, addresses, monkeypatch, capsys):
    omni = _omniscience_remote(tmp_path, "_global")
    people = {
        ("demo", "main"): "alice", ("demo", "feature"): "bob", ("atlas", "main"): "carol", ("atlas", "feature"): "dave",
    }
    trees = {}
    for (project, branch), owner in people.items():
        trees[project, branch] = _world(tmp_path, addresses, monkeypatch, owner, owner, project, branch, omni)
        trees[project, branch][1].omniscience_register(trees[project, branch][0]["root"])
    tree, client = trees["demo", "main"]
    root = tree["root"]

    # both projects resolve by name, each with both branches
    for project in ("demo", "atlas"):
        got = client.omniscience_get(root, project)
        assert got["status"] == "verified" and got["branches"] == ["feature", "main"] and got["states"] == 2
        assert got["os_id"] == OperatingSpace.id_of(project)
    # H_GLOBAL covers all four branches
    records = _records(omni)
    assert len(records) == 4 and _checkpoint(omni)["global_root"] == GlobalIndex.of(records).root
    assert client.omniscience_get(root, "demo")["global_root"] == _checkpoint(omni)["global_root"]

    # every proof verifies, and every registered memory reads back verified
    for project in ("demo", "atlas"):
        history = client.omniscience_history(root, project)["history"]
        assert [item["checkpoint_number"] for item in history] == sorted(item["checkpoint_number"] for item in history)
        for item in history:
            state = item["state_id"].removeprefix("state(").removesuffix(")")[:12]
            assert client.omniscience_proof(root, project, state)["proof_verified"] is True
            assert client.omniscience_verify(root, project, state)["status"] == "verified"
        for branch in ("main", "feature"):
            read = client.omniscience_memory(root, project, branch)
            assert read["status"] == "verified" and read["ledger"]["state"] == "verified", read["reason"]
    assert client.omniscience_branches(root, "atlas")["branches"][0]["register_branch"] == "atlas_feature"

    # a third workspace restores a registered State found through the register ...
    bobs = client.omniscience_history(root, "demo", branch="feature")["history"][0]
    earlier = {p: client.omniscience_get(root, p)["checkpoint"] for p in ("demo", "atlas")}
    third_tree, third = _world(tmp_path, addresses, monkeypatch, "e", "erin", "demo", "main", omni)
    _latest_state_declares_the_mounts(third, third_tree)
    restored = tmp_path / "restored" / "bobs.gts"
    code = cli_main(["omniscience", "state", "demo", bobs["state_id"][6:18], "--out", str(restored), "--search-dir", str(third_tree["root"])])
    assert code == 0 and restored.is_file(), capsys.readouterr().out
    document = GtsDocument.from_toml(restored)
    assert f"state({document.compute_snapshot_hash()})" == bobs["state_id"]
    space = OperatingSpace(RegistryTranslator.from_gts_document(document, tree_root=tmp_path))
    assert space.os_id == OperatingSpace.id_of("demo")  # the same Operating Space on another machine

    # ... and continues its history: the new registration follows, the earlier ones keep verifying
    third.omniscience_register(third_tree["root"])
    history = third.omniscience_history(third_tree["root"], "demo")["history"]
    assert len(history) == 3 and history[-1]["checkpoint_number"] > bobs["checkpoint_number"]
    for item in history[:2]:
        state = item["state_id"][6:18]
        assert third.omniscience_proof(third_tree["root"], "demo", state, checkpoint=earlier["demo"][:12])["proof_verified"] is True
        assert third.omniscience_proof(third_tree["root"], "demo", state)["proof_verified"] is True


# -- outcomes ---------------------------------------------------------------------


def test_an_unknown_project_state_or_checkpoint_is_not_registered_and_nothing_is_invented(tmp_path, addresses, monkeypatch):
    omni, tree, client, _ = _one(tmp_path, addresses, monkeypatch)
    root = tree["root"]

    assert client.omniscience_get(root, "nobody")["status"] == "not_registered"
    assert client.omniscience_proof(root, "demo", "0" * 12)["status"] == "not_registered"
    assert client.omniscience_proof(root, "demo", "not hex")["status"] == "not_registered"
    assert client.omniscience_get(root, "demo", checkpoint="ffff")["status"] == "not_registered"
    assert client.omniscience_history(root, "demo", branch="nope")["status"] == "not_registered"
    assert client.omniscience_memory(root, "demo", "nope")["status"] == "not_registered"
    assert client.omniscience_status(root, "demo", "github:nobody/else")["status"] == "not_registered"


def test_a_register_that_is_not_mounted_or_has_no_checkpoint_says_so(tmp_path, addresses, monkeypatch):
    omni = _omniscience_remote(tmp_path, "_global")
    tree, client = _world(tmp_path, addresses, monkeypatch, "a", "alice", "demo", "main", omni)

    empty = client.omniscience_get(tree["root"], "demo")
    assert empty["status"] == "not_registered" and "no checkpoint" in empty["reason"]

    shutil.rmtree(tree["root"] / ".omniscience")
    gone = client.omniscience_get(tree["root"], "demo")
    assert gone["status"] == "unavailable" and "not mounted" in gone["reason"]


def test_a_file_added_by_hand_is_proposed_not_registered(tmp_path, addresses, monkeypatch):
    omni, tree, client, registered = _one(tmp_path, addresses, monkeypatch)
    _hand_push(tmp_path, omni, "demo", {"register/garbage.toml": "this is [not toml"})

    report = client.omniscience_report(tree["root"], "demo")

    assert report["status"] == "verified" and report["records"] == 1
    assert [item["path"] for item in report["proposed"]] == ["register/garbage.toml"]
    assert client.omniscience_get(tree["root"], "demo")["proposed"] == 1


def test_the_query_writes_nothing_to_the_register_the_memory_or_the_worktree(tmp_path, addresses, monkeypatch):
    omni, tree, client, _ = _one(tmp_path, addresses, monkeypatch)
    state = _newest(tree)[:10]
    _latest_state_declares_the_mounts(client, tree)
    before = (
        _git(omni, "for-each-ref"), _git(tree["memory_remote"], "for-each-ref"), _git(tree["omni"], "status", "--porcelain"),
        _git(tree["omni"], "rev-parse", "HEAD"), _git(tree["mount"], "rev-parse", "HEAD"), sorted(p.name for p in (tree["root"] / ".cgitsync").iterdir()),
    )

    client.omniscience_get(tree["root"], "demo")
    client.omniscience_history(tree["root"], "demo")
    client.omniscience_proof(tree["root"], "demo", state)
    client.omniscience_verify(tree["root"], "demo", state)
    client.omniscience_memory(tree["root"], "demo", "main")
    client.omniscience_state(tree["root"], "demo", state)
    client.omniscience_status(tree["root"], "demo", "memory")
    client.omniscience_report(tree["root"], "demo")

    assert before == (
        _git(omni, "for-each-ref"), _git(tree["memory_remote"], "for-each-ref"), _git(tree["omni"], "status", "--porcelain"),
        _git(tree["omni"], "rev-parse", "HEAD"), _git(tree["mount"], "rev-parse", "HEAD"), sorted(p.name for p in (tree["root"] / ".cgitsync").iterdir()),
    )  # the temporary clone is gone, and so is every other trace


# -- the register itself ----------------------------------------------------------


def test_a_forced_rewrite_of_the_register_is_reported_rewritten_and_never_repaired(tmp_path, addresses, monkeypatch):
    omni, tree, client, _ = _one(tmp_path, addresses, monkeypatch)
    state = _newest(tree)[:10]
    assert client.omniscience_verify(tree["root"], "demo", state)["register"]["state"] == "intact"
    _hand_push(tmp_path, omni, "_global", {"README.md": "history was rewritten\n"}, force=True, orphan=True)
    rewritten = _git(omni, "rev-parse", "_global")

    answer = client.omniscience_verify(tree["root"], "demo", state)

    assert answer["status"] == "invalid" and answer["register"]["state"] == "rewritten"
    assert "no longer contains" in answer["register"]["reason"]
    assert _git(omni, "rev-parse", "_global") == rewritten  # reported, not repaired
    assert client.omniscience_report(tree["root"], "demo")["status"] == "invalid"


def test_a_forked_chain_is_contradicted(tmp_path, addresses, monkeypatch):
    omni, tree, client, _ = _one(tmp_path, addresses, monkeypatch)
    state = _newest(tree)[:10]
    body = _checkpoint(omni)
    fork = {**body, "parent": "", "records": [], "global_root": GlobalIndex.of([]).root}
    from ComplexGitSync.global_integrity import GlobalIntegrity

    name = GlobalIntegrity.checkpoint_hash(fork)
    _hand_push(tmp_path, omni, "_global", {f"checkpoints/{name}.toml": tomli_w.dumps(fork)})

    answer = client.omniscience_verify(tree["root"], "demo", state)

    assert answer["status"] == "invalid" and answer["register"]["state"] == "contradicted" and "forked" in answer["register"]["reason"]


def test_a_signed_tip_is_attested(tmp_path, addresses, monkeypatch):
    omni, tree, client, _ = _one(tmp_path, addresses, monkeypatch)
    state = _newest(tree)[:10]
    monkeypatch.setattr(GitRunner, "commit_signature", lambda self, repo, sha: "G")

    answer = client.omniscience_verify(tree["root"], "demo", state)

    assert answer["status"] == "verified" and answer["register"]["state"] == "attested"
    assert answer["register"]["protections"] == {"branch_protection": "not checked", "signed_head_commit": True, "last_seen_tip_known": True}


def test_a_checkpoint_naming_a_record_that_is_gone_is_invalid(tmp_path, addresses, monkeypatch):
    omni, tree, client, registered = _one(tmp_path, addresses, monkeypatch)
    state = _newest(tree)[:10]
    _hand_push(tmp_path, omni, "demo", {}, force=False)
    scratch = tmp_path / "remove"
    _git(tmp_path, "clone", "-b", "demo", str(omni), str(scratch))
    _identify(scratch)
    _git(scratch, "rm", registered["filename"])
    _git(scratch, "commit", "-m", "by hand: remove a record")
    _git(scratch, "push", "origin", "HEAD:refs/heads/demo")

    answer = client.omniscience_proof(tree["root"], "demo", state)

    assert answer["status"] == "invalid" and "no longer holds" in answer["reason"]
    assert client.omniscience_verify(tree["root"], "demo", state)["register"]["state"] == "contradicted"


# -- a registered memory ----------------------------------------------------------


def _tampered_world(tmp_path, addresses, monkeypatch, mode):
    """A registered State, then a hand-registered record whose memory commit is a copy of the memory with its State damaged."""
    omni, tree, client, registered = _one(tmp_path, addresses, monkeypatch)
    good = OmniscienceRecord.from_dict(client.omniscience_record(tree["root"], _newest(tree))["record"])
    scratch = tmp_path / "tamper"
    _git(tmp_path, "clone", "-b", "demo", str(tree["memory_remote"]), str(scratch))
    _identify(scratch)
    path = scratch / "state" / f"{_newest(tree)}.gts"
    if mode == "edited":
        path.write_text(re.sub(r'commit_sha = "[0-9a-f]+"', 'commit_sha = "' + "a" * 40 + '"', path.read_text(encoding="utf-8"), count=1), encoding="utf-8")
    else:
        path.unlink()
    _git(scratch, "commit", "-am", "tampered")
    _git(scratch, "push", "origin", "HEAD:refs/heads/tamper")
    commit = _git(scratch, "rev-parse", "HEAD")
    bad = replace(good, memory_commit=commit, local_memory_git_commit=commit, memory_ref="refs/heads/tamper")
    _hand_push(tmp_path, omni, "demo", {bad.filename: tomli_w.dumps(bad.to_dict())})
    step = client.omniscience_register(tree["root"])["checkpoint"]
    assert step["status"] == "extended" and step["records"] == 2, step
    return omni, tree, client


def test_a_tampered_memory_commit_is_located_down_to_the_repository(tmp_path, addresses, monkeypatch):
    omni, tree, client = _tampered_world(tmp_path, addresses, monkeypatch, "edited")

    answer = client.omniscience_memory(tree["root"], "demo", "main")

    assert answer["status"] == "invalid" and answer["detail"] == "invalid_proof"
    assert answer["diagnosis"] == "located" and answer["located"] == ["."], answer["reason"]
    assert "located at ." in answer["reason"]


def test_a_memory_whose_evidence_is_gone_says_the_diagnosis_is_unavailable(tmp_path, addresses, monkeypatch):
    omni, tree, client = _tampered_world(tmp_path, addresses, monkeypatch, "removed")

    answer = client.omniscience_memory(tree["root"], "demo", "main")

    assert answer["status"] == "invalid" and answer["detail"] == "invalid_proof"
    assert answer["diagnosis"] == "unavailable" and answer["located"] == [] and "diagnosis unavailable" in answer["reason"]


def test_a_ledger_that_does_not_hold_at_the_registered_commit_is_ledger_corrupt(tmp_path, addresses, monkeypatch):
    omni, tree, client, _ = _one(tmp_path, addresses, monkeypatch)
    good = OmniscienceRecord.from_dict(client.omniscience_record(tree["root"], _newest(tree))["record"])
    scratch = tmp_path / "tamper"
    _git(tmp_path, "clone", "-b", "demo", str(tree["memory_remote"]), str(scratch))
    _identify(scratch)
    [entry] = sorted((scratch / "lgr").glob("0*.toml"))
    entry.write_text(entry.read_text(encoding="utf-8").replace("outcome", "outcome_", 1) if "outcome" in entry.read_text(encoding="utf-8") else entry.read_text(encoding="utf-8") + "\nextra = 1\n", encoding="utf-8")
    _git(scratch, "commit", "-am", "tampered ledger")
    _git(scratch, "push", "origin", "HEAD:refs/heads/tamper")
    commit = _git(scratch, "rev-parse", "HEAD")
    bad = replace(good, memory_commit=commit, local_memory_git_commit=commit, memory_ref="refs/heads/tamper")
    _hand_push(tmp_path, omni, "demo", {bad.filename: tomli_w.dumps(bad.to_dict())})
    client.omniscience_register(tree["root"])

    answer = client.omniscience_memory(tree["root"], "demo", "main")

    assert answer["status"] == "invalid" and answer["detail"] in ("ledger_corrupt", "invalid_proof")


def test_a_reader_the_provider_refuses_gets_unauthorized_and_none_of_the_content(tmp_path, addresses, monkeypatch):
    omni, tree, client, _ = _one(tmp_path, addresses, monkeypatch)
    state = _newest(tree)[:10]
    real = GitRunner.clone

    def refused(self, remote_url, destination, *, branch):
        if "memory.git" in str(remote_url):
            from ComplexGitSync.errors import GitSyncError

            raise GitSyncError("git clone failed: ERROR: Permission denied (publickey). fatal: Could not read from remote repository.")
        return real(self, remote_url, destination, branch=branch)

    monkeypatch.setattr(GitRunner, "clone", refused)

    memory = client.omniscience_memory(tree["root"], "demo", "main")
    got = client.omniscience_state(tree["root"], "demo", state, destination=tmp_path / "never.gts")

    for answer in (memory, got):
        assert answer["status"] == "unauthorized"
        assert not {"states", "ledger", "state", "memory"} & set(answer)
    assert got["registration"]["operating_space"]["state_id"] == f"state({_newest(tree)})" and got["proof_verified"] is True
    assert not (tmp_path / "never.gts").exists()
    assert client.omniscience_get(tree["root"], "demo")["status"] == "verified"  # the register itself is still readable


def test_a_memory_that_cannot_be_reached_is_remote_unavailable_and_never_deleted(tmp_path, addresses, monkeypatch):
    omni, tree, client, _ = _one(tmp_path, addresses, monkeypatch)
    shutil.rmtree(tree["memory_remote"])

    answer = client.omniscience_memory(tree["root"], "demo", "main")

    assert answer["status"] == "unavailable" and answer["detail"] == "remote_unavailable"
    assert "not a finding that it is gone" in answer["reason"]


def test_a_commit_the_ref_no_longer_leads_to_is_commit_unreachable(tmp_path, addresses, monkeypatch):
    omni, tree, client, _ = _one(tmp_path, addresses, monkeypatch)
    remote = tree["memory_remote"]
    scratch = tmp_path / "reset"
    _git(tmp_path, "clone", "-b", "demo", str(remote), str(scratch))
    _identify(scratch)
    _git(scratch, "checkout", "--orphan", "other")
    _git(scratch, "rm", "-rf", ".")
    (scratch / "x.txt").write_text("x\n", encoding="utf-8")
    _git(scratch, "add", "x.txt")
    _git(scratch, "commit", "-m", "unrelated")
    _git(scratch, "push", "--force", "origin", "HEAD:refs/heads/demo")  # the registered commit is no longer on the ref

    answer = client.omniscience_memory(tree["root"], "demo", "main")

    assert answer["status"] == "unavailable" and answer["detail"] == "commit_unreachable"


def test_a_state_is_written_only_once_and_never_over_a_file(tmp_path, addresses, monkeypatch):
    omni, tree, client, _ = _one(tmp_path, addresses, monkeypatch)
    state = _newest(tree)[:10]
    out = tmp_path / "out" / "state.gts"

    first = client.omniscience_state(tree["root"], "demo", state, destination=out)

    assert first["status"] == "verified" and first["written"] == str(out) and "state_toml" not in first
    assert GtsDocument.from_toml(out).compute_snapshot_hash() == _newest(tree)
    from ComplexGitSync.errors import GitSyncError

    with pytest.raises(GitSyncError, match="never overwrites"):
        client.omniscience_state(tree["root"], "demo", state, destination=out)


# -- availability -----------------------------------------------------------------


def test_a_repository_deleted_after_registration_keeps_its_proof_but_is_never_reported_verified(tmp_path, addresses, monkeypatch):
    omni, tree, client, _ = _one(tmp_path, addresses, monkeypatch)
    state = _newest(tree)[:10]
    _latest_state_declares_the_mounts(client, tree)
    client.clock = _Ticking()  # two findings are never in the same second

    unseen = client.omniscience_status(tree["root"], "demo", "memory")
    assert unseen["status"] == "unavailable" and unseen["detail"] == "not_observed" and unseen["registration_proof_verified"] is True

    assert _observed(client, tree)["report"]["verdict"] == "ok"
    seen = client.omniscience_status(tree["root"], "demo", "memory")
    assert seen["status"] == "verified" and seen["availability"] == "AVAILABLE"

    shutil.rmtree(tree["memory_remote"])
    assert _observed(client, tree)["report"]["verdict"] == "warning"
    gone = client.omniscience_status(tree["root"], "demo", "github:alice/memory")

    assert gone["status"] == "unavailable" and gone["availability"] == "NOT_FOUND" and gone["detail"] == "not_found"
    assert gone["registration_proof_verified"] is True and gone["observation"]["status"] == "NOT_FOUND"
    assert client.omniscience_proof(tree["root"], "demo", state)["status"] == "verified"  # the proof of registration stands
    assert client.omniscience_verify(tree["root"], "demo", state)["status"] == "verified"
    report = client.omniscience_report(tree["root"], "demo")
    assert report["status"] == "verified" and report["completeness"]["verdict"] == "warning"


# -- the command line -------------------------------------------------------------


def test_the_nine_subcommands_print_a_status_and_exit_0_only_for_verified(tmp_path, addresses, monkeypatch, capsys):
    omni, tree, client, _ = _one(tmp_path, addresses, monkeypatch)
    state = _newest(tree)[:10]
    _latest_state_declares_the_mounts(client, tree)
    where = ["--search-dir", str(tree["root"])]

    def run(*args):
        code = cli_main(["omniscience", *args, *where])
        return code, capsys.readouterr().out

    for args in (("get", "demo"), ("branches", "demo"), ("history", "demo"), ("proof", "demo", state), ("verify", "demo", state),
                 ("memory", "demo", "main"), ("state", "demo", state), ("report", "demo")):
        code, out = run(*args)
        assert code == 0 and "\nstatus=verified" in "\n" + out, (args, out)
        code, out = run(*args, "--json")
        assert code == 0 and json.loads(out[out.index("{"):])["status"] == "verified", (args, out)
    code, out = run("history", "demo", "--branch", "main")
    assert code == 0 and "main state(" in out
    code, out = run("status", "demo", "memory")  # registered, never observed: not verified
    assert code == 1 and "\nstatus=unavailable (not_observed)" in "\n" + out and "availability=not_observed" in out
    code, out = run("get", "nobody")
    assert code == 1 and "\nstatus=not_registered" in "\n" + out
    code, out = run("verify", "demo", state)
    assert "register=intact" in out and "proof: leaf 0 of 1" in out

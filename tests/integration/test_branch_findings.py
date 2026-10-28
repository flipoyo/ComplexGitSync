"""A dying or dead project branch, found and handed over (BranchProtection M5).

Real bare remotes, real clones. ``cgitsync`` only finds and hands over: nothing here is written to a register,
and a finding never changes a ref.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

from support.ledgers import append_entry
from test_close_branch import _git, _loaded, _push_new_branch, _two_repo_workspace

from ComplexGitSync.cli import main
from ComplexGitSync.tree.branch_findings import BranchFinding, FindingKind
from ComplexGitSync.tree.git_tree import ROOT_REPO_ID
from ComplexGitSync.tree.memory.commit_log import CommitLog, CommitRecord, PublicationRecord
from ComplexGitSync.tree.memory.ledger_entry import Relocation
from ComplexGitSync.tree.memory.ledger_store import LedgerStore

_STATE = "a" * 64


class _Clock:
    def now(self):
        return datetime(2026, 10, 28, 9, 0, 0, tzinfo=UTC)

    def time_ns(self):
        return 1

    def pid(self):
        return 1

    def token_hex(self, nbytes):
        return "ab" * nbytes


def _found(tree) -> dict[tuple[str, str], BranchFinding]:
    return {(f.branch, f.kind.value): f for f in _loaded(tree["snapshot"]).branch_findings()}


def _publish(tree, branch: str, entry: int = 7) -> None:
    """What a ``push`` of *branch* from the root leaves in the commit log."""
    root = _loaded(tree["snapshot"]).get_dependency_registry().get(ROOT_REPO_ID)
    log = CommitLog(tree["root"] / ".cgitsync")
    sha = _git(tree["root"], "rev-parse", "HEAD")
    log.append_commits(_STATE, [CommitRecord(entry, root.name, root.repo_id, "project", branch, sha, "m", "2026-10-28T09:00:00Z")])
    log.append_publications(_STATE, [PublicationRecord(entry, root.name, sha, "origin", f"refs/heads/{branch}", "2026-10-28T09:00:01Z")])


def _delete_on_origin(tree, branch: str) -> None:
    _git(tree["project_remote"], "branch", "-D", branch)
    _git(tree["root"], "fetch", "--prune", "origin")


def test_a_fresh_tree_has_no_finding(tmp_path):
    assert _found(_two_repo_workspace(tmp_path)) == {}


def test_a_branch_pushed_then_deleted_on_origin_has_vanished(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _publish(tree, "feature-x", entry=7)
    _delete_on_origin(tree, "feature-x")

    finding = _found(tree)[("feature-x", "vanished")]

    assert finding.kind.severity == "error"
    assert finding.ledger_seq == 7
    assert "the ledger holds no relocation" in finding.evidence[0][1]


def test_a_branch_closed_by_plain_git_is_vanished_even_when_nothing_would_be_lost(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _publish(tree, "feature-x")
    _git(tree["root"], "branch", "-D", "feature-x")
    _delete_on_origin(tree, "feature-x")

    assert ("feature-x", "vanished") in _found(tree)


def test_a_branch_the_ledger_kept_on_ancestors_is_not_vanished(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _publish(tree, "feature-x")
    _git(tree["root"], "branch", "-D", "feature-x")
    _delete_on_origin(tree, "feature-x")
    store = LedgerStore(tree["root"] / ".cgitsync" / "lgr")
    relocation = Relocation("commit:demo:abc", "demo:refs/heads/feature-x", "demo:refs/heads/ancestors", "abc")
    append_entry(store, command="branch-delete", argv=["branch", "delete", "feature-x"], state_id="state(a)", state_dir="state(a)_1", outcome="ok", clock=_Clock(), relocations=[relocation])

    assert _found(tree) == {}


def test_a_branch_deleted_by_branch_delete_with_nothing_to_keep_is_not_vanished(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _publish(tree, "feature-x")
    _git(tree["root"], "branch", "-D", "feature-x")
    _delete_on_origin(tree, "feature-x")
    store = LedgerStore(tree["root"] / ".cgitsync" / "lgr")
    append_entry(store, command="branch_delete", argv=["branch", "delete", "feature-x"], state_id="state(a)", state_dir="state(a)_1", outcome="ok", clock=_Clock())

    assert _found(tree) == {}


def test_a_root_that_was_never_fetched_is_judged_by_nothing(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _publish(tree, "feature-x")
    _git(tree["project_remote"], "branch", "-D", "feature-x")
    for ref in _git(tree["root"], "for-each-ref", "--format=%(refname)", "refs/remotes/origin").split():
        _git(tree["root"], "update-ref", "-d", ref)

    assert _found(tree) == {}


def test_a_fetch_that_fails_produces_no_finding(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _publish(tree, "feature-x")
    moved = tree["project_remote"].with_name("away.git")
    tree["project_remote"].rename(moved)

    failed = subprocess.run(["git", "-C", str(tree["root"]), "fetch", "--prune", "origin"], capture_output=True, text=True)

    assert failed.returncode != 0
    assert [kind for _, kind in _found(tree)] == ["split"]  # the branch is still there as of the last fetch: not "vanished"


def test_a_branch_closed_in_some_repositories_and_live_in_others_is_half_closed(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _push_new_branch(tree["config"], "demo_feature-x")
    _git(tree["root"], "branch", "-m", "feature-x", "closed/feature-x")
    _git(tree["root"], "push", "origin", "closed/feature-x", ":feature-x")
    _git(tree["root"], "fetch", "--prune", "origin")

    finding = _found(tree)[("feature-x", "half-closed")]

    assert dict(finding.evidence) == {"demo": "closed", "conf": "live"}
    assert finding.kind.severity == "warning"


def test_a_branch_some_repositories_hold_and_others_lack_is_split(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")

    finding = _found(tree)[("feature-x", "split")]

    assert dict(finding.evidence) == {"demo": "live", "conf": "absent"}


def test_a_private_branch_whose_project_branch_is_gone_is_orphaned(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["config"], "demo_old-thing")

    finding = _found(tree)[("old-thing", "orphaned-private")]

    assert finding.evidence == (("conf", "has 'demo_old-thing'"),)


def test_a_memory_branch_archived_on_purpose_is_not_orphaned(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["config"], "demo_memory-dev.archived-20261008")

    assert _found(tree) == {}


def test_a_branch_closed_everywhere_is_reported_closed_as_information(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _push_new_branch(tree["config"], "demo_feature-x")
    _loaded(tree["snapshot"]).close_branch("feature-x")

    found = _found(tree)

    assert found[("feature-x", "closed")].kind.severity == "info"
    assert [kind for _, kind in found] == ["closed"]


def test_the_tag_the_default_branch_and_ancestors_are_never_judged(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _git(tree["root"], "tag", "v1")
    _git(tree["root"], "push", "origin", "v1")
    _push_new_branch(tree["root"], "ancestors")
    _push_new_branch(tree["config"], "demo_ancestors")
    _publish(tree, "main")
    _publish(tree, "ancestors")
    _git(tree["project_remote"], "branch", "-D", "ancestors")
    _git(tree["root"], "fetch", "--prune", "origin")

    assert _found(tree) == {}


def test_finding_a_dead_branch_changes_no_ref(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _publish(tree, "feature-x")
    _delete_on_origin(tree, "feature-x")
    before = [_git(tree[key], "for-each-ref") for key in ("root", "config")] + [_git(tree["root"], "rev-parse", "HEAD")]

    assert _loaded(tree["snapshot"]).branch_findings()

    assert [_git(tree[key], "for-each-ref") for key in ("root", "config")] + [_git(tree["root"], "rev-parse", "HEAD")] == before


def test_a_finding_is_content_addressed_and_whole():
    one = BranchFinding("demo", "feature-x", FindingKind.SPLIT, (("demo", "live"),))
    same = BranchFinding("demo", "feature-x", FindingKind.SPLIT, (("demo", "live"),))
    other = BranchFinding("demo", "feature-y", FindingKind.SPLIT, (("demo", "live"),))

    assert one.name == same.name != other.name
    assert one.to_record() == {**one.to_dict(), "name": one.name}
    assert one.to_dict()["severity"] == "warning"
    assert one.describe().startswith("split branch 'feature-x'")


def test_branch_list_prints_the_finding_and_hands_it_over_as_json(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")

    assert main(["branch", "list", "--gts", str(tree["snapshot"])]) == 0
    printed = capsys.readouterr().out
    assert main(["branch", "list", "--json", "--gts", str(tree["snapshot"])]) == 0
    handed = json.loads(capsys.readouterr().out)

    assert "warning: split branch 'feature-x'" in printed
    assert [(f["branch"], f["kind"]) for f in handed["branch_findings"]] == [("feature-x", "split")]
    assert {b["name"] for b in handed["branches"]} == {"main", "feature-x"}


def test_status_prints_the_finding_and_its_json_carries_it(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")

    client = _loaded(tree["snapshot"])
    text = client.status()
    payload = json.loads(client.status_json())

    assert "warning: split branch 'feature-x'" in text
    assert [f["kind"] for f in payload["branch_findings"]] == ["split"]

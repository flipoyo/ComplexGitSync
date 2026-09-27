"""What ``cgitsync autofix`` answers after a tree-wide merge has refused.

The incident this covers is in
``.agent/.local/.localSpec/DevTickets/archive/20260927_MergeLogGap_DevPlanTicket.md``
§1: a real ``merge --all`` refused on two conflicting repositories, and
``autofix``, run immediately afterwards, said *"no failing command found in
the run log"*. Two separate defects produced that one sentence — the refused
run never persisted a log for ``autofix`` to read (covered in
``tests/unit/test_cli_shared.py``), and nothing in the repair registry
recognised a merge conflict even when handed one. This file covers the
second, plus the first end to end through the real dispatcher.

Real repositories and a real ``GitRunner``: the claim under test is that
``autofix`` agrees with what ``git merge`` would actually do, and a fake
could only agree with itself. The fixture is the same shape as
``test_merge_conflict_preflight.py``'s, deliberately — one conflicting root,
one clean leaf.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.autofix.repair_from_cli import FromCliRepair, NoMatchingRepairError
from ComplexGitSync.autofix.repair_merge_conflict import MergeConflictRepair
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_repo import (
    AccessProtocol,
    DiscoveryState,
    GitProvider,
    NodeType,
    RefKind,
    RepoLifecycleState,
    SyncState,
    WorkingRepo,
)
from ComplexGitSync.git_runner import GitRunner
from ComplexGitSync.git_tree import WorkingGitTree
from ComplexGitSync.operations import merge_tree

_MERGE_BRANCH = "feature"


def _git(repo_path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo_path, check=True, capture_output=True)


def _init_repo(repo_path: Path, *, conflicting: bool) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    target = repo_path / "prose.txt"

    _git(repo_path, "init", "-b", "main")
    _git(repo_path, "config", "user.email", "t@example.com")
    _git(repo_path, "config", "user.name", "Test")
    target.write_text("base\n", encoding="utf-8")
    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-m", "base")

    _git(repo_path, "checkout", "-b", _MERGE_BRANCH)
    target.write_text("from-feature\n", encoding="utf-8")
    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-m", "feature change")

    _git(repo_path, "checkout", "main")
    if conflicting:
        target.write_text("from-main\n", encoding="utf-8")
        _git(repo_path, "add", "-A")
        _git(repo_path, "commit", "-m", "main change")

    remote_path = repo_path.parent / f"{repo_path.name}-remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote_path)], check=True, capture_output=True
    )
    _git(repo_path, "remote", "add", "origin", str(remote_path))
    _git(repo_path, "push", "-u", "origin", "main")
    _git(repo_path, "push", "origin", _MERGE_BRANCH)


def _entry(repo_id, name, node_type, parent_id, absolute_path, root_path) -> WorkingRepo:
    return WorkingRepo(
        repo_id=repo_id,
        name=name,
        node_type=node_type,
        parent_id=parent_id,
        absolute_path=absolute_path,
        relative_path=(
            Path(".") if parent_id is None else absolute_path.relative_to(root_path)
        ),
        current_ref_kind=RefKind.BRANCH,
        current_ref_name="main",
        target_ref_kind=RefKind.BRANCH,
        target_ref_name="main",
        resolved_ref_kind=RefKind.BRANCH,
        resolved_ref_name="main",
        commit_sha=GitRunner().rev_parse_head(absolute_path),
        repo_lifecycle_state=RepoLifecycleState.READY,
        sync_state=SyncState.ALIGNED,
        discovery_state=DiscoveryState.RESOLVED,
        is_reachable=True,
        gitprovider=GitProvider.GITHUB,
        project_owner_name="owner",
        project_name=name,
        access_protocol=AccessProtocol.SSH,
        default_branch="main",
        remote_name="origin",
    )


@pytest.fixture
def refused_merge(tmp_path):
    """A tree whose root conflicts, and the error its refused merge raised."""
    root_path = tmp_path / "project"
    leaf_path = root_path / "deps" / "leaf"
    _init_repo(root_path, conflicting=True)
    _init_repo(leaf_path, conflicting=False)
    (root_path / ".gitignore").write_text("deps/\n", encoding="utf-8")
    _git(root_path, "add", "-A")
    _git(root_path, "commit", "-m", "ignore nested repo")

    tree = WorkingGitTree()
    tree.add(_entry("root", "project", NodeType.ROOT, None, root_path, root_path))
    tree.add(
        _entry("root:deps/leaf", "leaf", NodeType.LEAF, "root", leaf_path, root_path)
    )
    tree.recompute_tree_state()
    assert tree.is_ready()

    runner = GitRunner()
    with pytest.raises(GitSyncError) as raised:
        merge_tree(tree, runner, _MERGE_BRANCH)
    return tree, runner, str(raised.value)


def test_autofix_diagnoses_the_repository_a_refused_merge_blamed(refused_merge):
    """The answer that used to be "no failing command found"."""
    tree, runner, error = refused_merge
    assert "project: merging 'feature' conflicts" in error

    outcome = FromCliRepair().run(tree, runner, error=error)

    # Diagnosed, never repaired: a content conflict is a person's call, and
    # a repair that silently picked a side would be worse than none.
    assert outcome.repaired is False
    assert "project" in outcome.detail
    assert "still conflicts" in outcome.detail
    assert "merge --resolve feature" in outcome.detail
    # The full refusal is quoted back, because autofix runs in a later
    # invocation than the one that printed it.
    assert error in outcome.detail


def test_autofix_re_checks_git_rather_than_trusting_the_log(refused_merge):
    """A log line says what was true when the merge ran. If the owner has
    resolved the conflict since, saying it is still there would be a lie."""
    tree, runner, error = refused_merge
    root_path = tree.get("root").absolute_path
    # Resolve it the way a person would: make the two sides agree.
    _git(root_path, "checkout", _MERGE_BRANCH)
    (root_path / "prose.txt").write_text("from-main\n", encoding="utf-8")
    _git(root_path, "add", "-A")
    _git(root_path, "commit", "-m", "align with main")
    _git(root_path, "checkout", "main")

    outcome = FromCliRepair().run(tree, runner, error=error)

    assert outcome.repaired is False
    assert "now merges cleanly" in outcome.detail


def test_repair_declines_an_unrelated_error(refused_merge):
    """`matches` is two conditions, not one: a merge refusal *and* this
    repository named in it. An error that is neither falls through to the
    registry's own "I don't know what this is"."""
    tree, runner, _error = refused_merge
    with pytest.raises(NoMatchingRepairError):
        FromCliRepair().run(tree, runner, error="fatal: could not read Username")


def test_repair_declines_a_merge_refusal_that_named_another_repository(refused_merge):
    """A twelve-repository tree must not report one conflict twelve times."""
    tree, runner, _error = refused_merge
    repair = MergeConflictRepair()
    leaf = tree.get("root:deps/leaf")
    from ComplexGitSync.autofix.base import Situation

    named_elsewhere = "merge refused; no repository was merged: project: merging 'feature' conflicts"
    assert repair.matches(Situation(repo=tree.get("root"), source_error=named_elsewhere))
    assert not repair.matches(Situation(repo=leaf, source_error=named_elsewhere))


def test_plain_merge_records_a_state_and_names_the_branch_it_merged_into(tmp_path):
    """WP1: a merge moves HEAD, so a State has to say so.

    A successful plain ``merge`` used to write no snapshot at all, leaving
    ``status``'s RECORDED column stale about commits the merge had just
    made, and — because binding a log file hung off writing a State — no
    log either.
    """
    from ComplexGitSync.orchestre import ComplexGitSyncClient, create_run_logger

    root_path = tmp_path / "project"
    _init_repo(root_path, conflicting=False)

    tree = WorkingGitTree()
    tree.add(_entry("root", "project", NodeType.ROOT, None, root_path, root_path))
    tree.recompute_tree_state()

    client = ComplexGitSyncClient()
    client.registry = tree
    client.orchestre.git_tree = tree
    # A real logger, because the branch assertion below reads what the merge
    # recorded. A directly-built client has `run_logger = None`, which would
    # leave that assertion silently unexercised.
    client.run_logger = create_run_logger("merge")

    merged = client.merge(_MERGE_BRANCH)
    assert [name for name, _ref in merged] == ["project"]

    states = sorted((root_path / ".cgitsync" / "state").glob("*.gts"))
    assert states, "a merge that moved HEAD must leave a State behind"

    # The branch merged into is recorded, not left implicit in whatever HEAD
    # happened to be: `merge b` is `merge b --into <the tree's branch>`.
    events = [
        json.loads(line)
        for line in client.run_logger._buffered_lines
        if line.strip()
    ]
    starts = [event for event in events if event.get("event") == "merge_start"]
    assert [event["into_branch"] for event in starts] == ["main"]

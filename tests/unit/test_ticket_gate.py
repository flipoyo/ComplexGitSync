"""The pair rule's gate: no archived planning ticket without its orchestrator's record.

Backs the PairRuleGate ticket (WP0, WP2, WP3). Two halves: the gate itself,
over a synthetic archive and synthetic records; and ``cgitsync commit``, which
must refuse to add such a ticket in a tree that adopted DevSpec and commit
nothing, and stay out of the way of every other commit.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from test_commit_message import _git, _tree

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.memory.conformity import ConformityCriterion, ConformityScore
from ComplexGitSync.memory.self_history import AgentInfo, SelfHistoryRecord
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.ticket_gate import CUTOFF, TicketGate

_DEV = AgentInfo(role="Dev", vendor="vendor-name", model="model-name")
_ORCHESTRATOR = AgentInfo(role="Orchestration", vendor="vendor-name", model="model-name")
_RECORD = SelfHistoryRecord(
    ticket="Gated",
    goal="Implement the Gated ticket.",
    action="Quoted the work against the checklist.",
    worker=_DEV,
    orchestrator=_ORCHESTRATOR,
    conformity=ConformityScore(
        spec_respect=ConformityCriterion(score=33, basis="measured", reasoning="checks pass"),
        gating=ConformityCriterion(score=33, basis="measured", reasoning="nothing pushed"),
        quality=ConformityCriterion(score=30, basis="asserted", reasoning="fine"),
    ),
    recorded_at="2026-10-09T00:00:00+00:00",
)
_GATED = f"{CUTOFF}_Gated_DevPlanTicket.md"


def _devspec_tree(root: Path) -> Path:
    """A tree that adopted DevSpec, with an empty archive; returns the archive."""
    (root / ".agent" / ".distant" / "dev-sync").mkdir(parents=True, exist_ok=True)
    (root / ".agent" / ".distant" / "dev-sync" / "AgentConduct.md").write_text("rules\n")
    archive = root / ".agent" / ".local" / ".dev" / "DevTickets" / "archive"
    archive.mkdir(parents=True)
    return archive


def _gate(root: Path) -> TicketGate:
    gate = TicketGate.for_tree(root)
    assert gate is not None
    return gate


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_a_ticket_archived_without_a_record_fails_by_name(tmp_path):
    (_devspec_tree(tmp_path) / _GATED).write_text("# Gated\n")

    assert _gate(tmp_path).unrecorded() == ["Gated"]
    with pytest.raises(GitSyncError, match=r"(?s)AgentConduct.md §4.*- Gated"):
        _gate(tmp_path).require()


def test_a_record_whose_orchestrator_has_the_workers_role_does_not_count(tmp_path):
    (_devspec_tree(tmp_path) / _GATED).write_text("# Gated\n")
    replace(_RECORD, worker=_ORCHESTRATOR).write(tmp_path / ".cgitsync" / ".self-history")

    assert _gate(tmp_path).unrecorded() == ["Gated"]


def test_a_record_with_no_orchestration_role_does_not_count(tmp_path):
    (_devspec_tree(tmp_path) / _GATED).write_text("# Gated\n")
    replace(_RECORD, orchestrator=AgentInfo(role="Editing", vendor="v", model="m")).write(
        tmp_path / ".cgitsync" / ".self-history"
    )

    assert _gate(tmp_path).unrecorded() == ["Gated"]


@pytest.mark.parametrize("half", [(".self-history",), (".memory", ".self-history")])
def test_a_proper_record_pending_or_folded_passes(tmp_path, half):
    (_devspec_tree(tmp_path) / _GATED).write_text("# Gated\n")
    _RECORD.write(tmp_path.joinpath(".cgitsync", *half))

    assert _gate(tmp_path).unrecorded() == []
    _gate(tmp_path).require()


def test_an_unreadable_record_refuses_naming_its_file(tmp_path):
    (_devspec_tree(tmp_path) / _GATED).write_text("# Gated\n")
    path = _RECORD.write(tmp_path / ".cgitsync" / ".self-history")
    path.write_text(path.read_text().replace("Gated", "Tampered"))  # digest no longer matches its name

    with pytest.raises(GitSyncError, match=path.name):
        _gate(tmp_path).unrecorded()


def test_a_ticket_archived_before_the_cutoff_is_exempt(tmp_path):
    (_devspec_tree(tmp_path) / "20261008_Older_DevPlanTicket.md").write_text("# Older\n")

    assert _gate(tmp_path).gated() == []
    assert _gate(tmp_path).unrecorded() == []


def test_a_deep_archived_copy_and_a_closed_short_ticket_are_not_candidates(tmp_path):
    archive = _devspec_tree(tmp_path)
    for sub in (".deepArchive", ".closedUserTicket"):
        (archive / sub).mkdir()
    (archive / ".deepArchive" / _GATED).write_text("# Gated\n")
    (archive / ".closedUserTicket" / f"{CUTOFF}_gated.md").write_text("asked\n")

    assert _gate(tmp_path).gated() == []


def test_only_the_paths_given_are_checked(tmp_path):
    archive = _devspec_tree(tmp_path)
    (archive / _GATED).write_text("# Gated\n")

    assert _gate(tmp_path).unrecorded([archive / "README.md"]) == []
    assert _gate(tmp_path).unrecorded([archive / _GATED]) == ["Gated"]


def test_a_tree_without_agentconduct_or_without_an_archive_is_not_bound(tmp_path):
    assert TicketGate.for_tree(tmp_path) is None
    (tmp_path / ".agent" / ".distant" / "dev-sync").mkdir(parents=True)
    (tmp_path / ".agent" / ".distant" / "dev-sync" / "AgentConduct.md").write_text("rules\n")
    assert TicketGate.for_tree(tmp_path) is None


def test_added_paths_reads_what_a_commit_adds(tmp_path):
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "a.md").write_text("a")
    porcelain = ["A  staged.md", "R  old.md -> moved.md", " M changed.md", "?? loose.md", "?? new/"]

    assert TicketGate.added_paths(tmp_path, porcelain, stage_all=False) == [
        tmp_path / "staged.md",
        tmp_path / "moved.md",
    ]
    assert TicketGate.added_paths(tmp_path, porcelain, stage_all=True) == [
        tmp_path / "staged.md",
        tmp_path / "moved.md",
        tmp_path / "loose.md",
        tmp_path / "new" / "a.md",
    ]


def test_the_deepest_repository_holding_the_archive_owns_it(tmp_path):
    archive = _devspec_tree(tmp_path)
    dev = archive.parents[1]

    assert _gate(tmp_path).owning_path([tmp_path, dev, tmp_path / "elsewhere"]) == dev
    assert _gate(tmp_path).owning_path([tmp_path / "elsewhere"]) is None


# ---------------------------------------------------------------------------
# cgitsync commit: refuse before anything is staged or committed
# ---------------------------------------------------------------------------


def _archive_in(root: Path) -> Path:
    archive = root / ".agent" / ".local" / ".dev" / "DevTickets" / "archive"
    archive.mkdir(parents=True)
    (archive / "README.md").write_text("archive\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "archive")
    return archive


def test_commit_refuses_to_add_an_unrecorded_ticket_and_commits_nothing(tmp_path):
    client, root = _tree(tmp_path, devspec=True)
    archive = _archive_in(root)
    (archive / _GATED).write_text("# Gated\n")
    before = _git(root, "rev-parse", "HEAD")

    with pytest.raises(GitSyncError, match="Gated"):
        client.commit("ComplexGitSync3.3.0 Archives the Gated ticket.")

    assert _git(root, "rev-parse", "HEAD") == before
    assert _GATED not in _git(root, "diff", "--cached", "--name-only")


def test_commit_adds_a_recorded_ticket(tmp_path):
    client, root = _tree(tmp_path, devspec=True)
    archive = _archive_in(root)
    (archive / _GATED).write_text("# Gated\n")
    _RECORD.write(root / ".cgitsync" / ".self-history")
    before = _git(root, "rev-parse", "HEAD")

    client.commit("ComplexGitSync3.3.0 Archives the Gated ticket.")

    assert _git(root, "rev-parse", "HEAD") != before


def test_commit_ignores_an_unrecorded_ticket_it_does_not_add(tmp_path):
    """A ticket already committed is check-tickets' business, not every later commit's."""
    client, root = _tree(tmp_path, devspec=True)
    archive = _archive_in(root)
    (archive / _GATED).write_text("# Gated\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "committed by hand, outside cgitsync")
    (root / "work.txt").write_text("one")
    before = _git(root, "rev-parse", "HEAD")

    client.commit("ComplexGitSync3.3.0 Adds some work.")

    assert _git(root, "rev-parse", "HEAD") != before


def _with_private_dev(tmp_path: Path) -> tuple[ComplexGitSyncClient, Path, Path]:
    """The real layout: the archive in its own private, writable repository nested in the root."""
    _client, root = _tree(tmp_path, devspec=True)
    (root / ".gitignore").write_text(".agent/.local/.dev/\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "ignore the private mount")
    _git(root, "push", "origin", "main")
    dev = root / ".agent" / ".local" / ".dev"
    archive = dev / "DevTickets" / "archive"
    archive.mkdir(parents=True)
    (archive / "README.md").write_text("archive\n")
    _git(dev, "init", "-b", "demo")  # a private repository sits on the project-named branch
    _git(dev, "config", "user.email", "t@example.test")
    _git(dev, "config", "user.name", "T")
    _git(dev, "add", "-A")
    _git(dev, "commit", "-m", "archive")
    remote = tmp_path / "dev.git"
    subprocess.run(["git", "init", "--bare", "-b", "demo", str(remote)], check=True, capture_output=True)
    _git(dev, "remote", "add", "origin", str(remote))
    _git(dev, "push", "-u", "origin", "demo")
    snapshot = tmp_path / "demo.gts"
    text = snapshot.read_text().replace(_git(root, "rev-parse", "HEAD~1"), _git(root, "rev-parse", "HEAD"))
    snapshot.write_text(
        text
        + f"""
[[repo_state]]
name = "dev"
node_type = "leaf"
absolute_path = "{dev.as_posix()}"
parent_absolute_path = "{root.as_posix()}"
relative_path = ".agent/.local/.dev"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "demo"
target_ref_kind = "branch"
target_ref_name = "demo"
resolved_ref_kind = "branch"
resolved_ref_name = "demo"
commit_sha = "{_git(dev, "rev-parse", "HEAD")}"
project_owner_name = "owner"
project_name = "dev"
gitprovider = "github"
private = true
writable = true
"""
    )
    client = ComplexGitSyncClient()
    client.load_gts(snapshot)
    return client, root, archive


def test_commit_private_refuses_an_unrecorded_ticket_in_the_nested_private_repository(tmp_path):
    client, root, archive = _with_private_dev(tmp_path)
    (archive / _GATED).write_text("# Gated\n")
    before = _git(archive, "rev-parse", "HEAD")

    with pytest.raises(GitSyncError, match="Gated"):
        client.commit("ComplexGitSync3.3.0 Archives the Gated ticket.", private=True)

    assert _git(archive, "rev-parse", "HEAD") == before
    _RECORD.write(root / ".cgitsync" / ".self-history")
    client.commit("ComplexGitSync3.3.0 Archives the Gated ticket.", private=True)
    assert _git(archive, "rev-parse", "HEAD") != before


def test_a_project_scope_commit_never_looks_at_the_private_archive(tmp_path):
    client, root, archive = _with_private_dev(tmp_path)
    (archive / _GATED).write_text("# Gated\n")
    (root / "work.txt").write_text("one")
    before = _git(root, "rev-parse", "HEAD")

    client.commit("ComplexGitSync3.3.0 Adds some work.")

    assert _git(root, "rev-parse", "HEAD") != before
    assert _GATED in _git(archive, "status", "--porcelain")  # left for the private commit

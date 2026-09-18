"""A branch made after the clone must show, and measure, its upstream.

Real git throughout: the bug this file guards against was invisible to every
fake, because a fake agrees with itself. ``git clone --single-branch`` narrows
``remote.origin.fetch`` to the cloned branch and leaves it there, so
``push -u`` could write ``branch.X.merge`` without ever creating
``refs/remotes/origin/X`` — and ``@{upstream}``, which needs the
remote-tracking ref rather than the config, failed on every branch made after
the clone. ``status`` then printed ``-`` and ``unknown`` for a branch that had
just been pushed successfully, and ``checkout`` could not see a branch a
colleague had pushed.

See ``.localSpec/DevTickets/archive/20260911_UpstreamBranchDisplay_DevPlanTicket.md``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.git_runner import GitRunner

WIDE_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"


def _run_git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_path, check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


def _identify(repo_path: Path) -> None:
    _run_git(repo_path, "config", "user.email", "integration@complexgitsync.test")
    _run_git(repo_path, "config", "user.name", "ComplexGitSync Integration")


def _seeded_remote(tmp_path: Path) -> Path:
    """A bare remote holding one commit on ``main``."""
    remote = tmp_path / "demo-remote.git"
    _run_git(tmp_path, "init", "--bare", remote.as_posix())
    seed = tmp_path / "seed"
    seed.mkdir()
    _run_git(seed, "init", "-b", "main")
    _identify(seed)
    (seed / "README.md").write_text("initial\n", encoding="utf-8")
    _run_git(seed, "add", "README.md")
    _run_git(seed, "commit", "-m", "initial")
    _run_git(seed, "remote", "add", "origin", remote.as_posix())
    _run_git(seed, "push", "-u", "origin", "main")
    _run_git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    return remote


def _latest_gts(repo_path: Path) -> Path:
    """The newest snapshot the CLI wrote under ``.cgitsync/``.

    Every command writes one, and the next command is meant to read it —
    that is what a real session does when ``--gts`` is omitted and discovery
    picks the latest. Chaining them explicitly keeps the test hermetic
    without pinning a snapshot that goes stale the moment a branch is made.
    """
    snapshots = sorted(
        (repo_path / ".cgitsync").rglob("*.gts"), key=lambda path: path.stat().st_mtime
    )
    return snapshots[-1]


def _write_ready_gts(snapshot_path: Path, *, root_path: Path, commit_sha: str) -> Path:
    snapshot_path.write_text(
        f"""
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "{root_path.as_posix()}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "{root_path.as_posix()}"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "{commit_sha}"
project_owner_name = "owner"
project_name = "demo"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return snapshot_path


@pytest.fixture()
def cloned_workspace(tmp_path: Path) -> dict[str, Path]:
    """A workspace cloned the way ``cgitsync`` clones, plus its .gts."""
    remote = _seeded_remote(tmp_path)
    clone = tmp_path / "demo"
    GitRunner().clone(remote.as_posix(), clone, branch="main")
    _identify(clone)
    snapshot = _write_ready_gts(
        tmp_path / "demo.gts",
        root_path=clone.resolve(),
        commit_sha=_run_git(clone, "rev-parse", "HEAD"),
    )
    return {"remote": remote, "repo": clone, "snapshot": snapshot}


# ---------------------------------------------------------------------------
# 1. The clone keeps a refspec that maps every branch
# ---------------------------------------------------------------------------


def test_clone_leaves_a_refspec_that_maps_every_branch(cloned_workspace):
    assert (
        _run_git(cloned_workspace["repo"], "config", "--get", "remote.origin.fetch")
        == WIDE_REFSPEC
    )


def test_clone_still_downloads_a_single_branch(cloned_workspace, tmp_path):
    """The narrow *download* is what keeps a clone cheap; only the stored
    refspec is widened. A branch pushed to the remote after this clone was
    taken must therefore be absent until something fetches it."""
    other = tmp_path / "other"
    _run_git(tmp_path, "clone", cloned_workspace["remote"].as_posix(), other.as_posix())
    _identify(other)
    _run_git(other, "checkout", "-b", "colleague")
    (other / "theirs.txt").write_text("theirs\n", encoding="utf-8")
    _run_git(other, "add", "theirs.txt")
    _run_git(other, "commit", "-m", "colleague work")
    _run_git(other, "push", "-u", "origin", "colleague")

    clone = cloned_workspace["repo"]
    assert (
        subprocess.run(
            ["git", "rev-parse", "--verify", "refs/remotes/origin/colleague"],
            cwd=clone,
            capture_output=True,
        ).returncode
        != 0
    )

    # ... and must appear on the first fetch, which the narrow refspec could
    # never have done. This is what `cgitsync checkout <colleague's branch>`
    # depends on.
    _run_git(clone, "fetch", "origin")
    assert GitRunner().branch_known(clone, "colleague")


# ---------------------------------------------------------------------------
# 2. The reported failure, end to end: branch -> push -> status
# ---------------------------------------------------------------------------


def test_status_shows_upstream_and_synced_after_branch_and_push(cloned_workspace, capsys):
    """The exact table row from the field report, which used to read ``-``/``unknown``."""
    repo = cloned_workspace["repo"]

    assert cli_main(["branch", "apoub", "--gts", str(cloned_workspace["snapshot"])]) == 0
    assert cli_main(["checkout", "apoub", "--gts", str(_latest_gts(repo))]) == 0
    assert cli_main(["push", "--gts", str(_latest_gts(repo))]) == 0
    capsys.readouterr()

    assert cli_main(["status", "--gts", str(_latest_gts(repo))]) == 0
    out = capsys.readouterr().out
    row = [line for line in out.splitlines() if line.startswith("demo ")][0].split()

    assert row[3] == "apoub", out
    assert row[4] == "origin/apoub", out
    assert row[6] == "synced", out
    assert "unmeasured=0" in out.splitlines()[0], out


def test_status_names_a_never_pushed_branch_rather_than_calling_it_unknown(
    cloned_workspace, capsys
):
    """A branch that was never pushed has nothing to measure, and says so.

    The distinction is the point: ``unknown`` is reserved for an upstream
    that is named but does not resolve, which is a fault worth chasing.
    """
    snapshot = cloned_workspace["snapshot"]
    _run_git(cloned_workspace["repo"], "checkout", "-b", "never-pushed")

    assert cli_main(["status", "--gts", str(snapshot)]) == 0
    out = capsys.readouterr().out
    row = [line for line in out.splitlines() if line.startswith("demo")][0].split()

    assert row[4] == "-", out
    assert row[6] == "no-upstream", out
    assert "unmeasured=1" in out.splitlines()[0], out
    assert "ahead=0 behind=0 unmeasured=1" in out.splitlines()[0], out
    assert "legend: SYNC" in out, out


# ---------------------------------------------------------------------------
# 3. A workspace cloned before the fix repairs itself
# ---------------------------------------------------------------------------


def test_push_repairs_a_narrow_refspec_left_by_an_older_clone(tmp_path, capsys):
    """No re-clone needed: the command that needs the refspec widens it first."""
    remote = _seeded_remote(tmp_path)
    clone = tmp_path / "demo"
    # Exactly what a pre-fix `cgitsync clone` left behind.
    _run_git(
        tmp_path,
        "clone",
        "--branch",
        "main",
        "--single-branch",
        remote.as_posix(),
        clone.as_posix(),
    )
    _identify(clone)
    assert (
        _run_git(clone, "config", "--get", "remote.origin.fetch")
        == "+refs/heads/main:refs/remotes/origin/main"
    )

    snapshot = _write_ready_gts(
        tmp_path / "demo.gts",
        root_path=clone.resolve(),
        commit_sha=_run_git(clone, "rev-parse", "HEAD"),
    )
    assert cli_main(["branch", "apoub", "--gts", str(snapshot)]) == 0
    assert cli_main(["checkout", "apoub", "--gts", str(_latest_gts(clone))]) == 0
    (clone / "mine.txt").write_text("mine\n", encoding="utf-8")
    assert cli_main(["add", "--gts", str(_latest_gts(clone))]) == 0
    assert cli_main(["commit", "mine", "--gts", str(_latest_gts(clone))]) == 0
    assert cli_main(["push", "--gts", str(_latest_gts(clone))]) == 0
    capsys.readouterr()

    assert _run_git(clone, "config", "--get", "remote.origin.fetch") == WIDE_REFSPEC
    assert GitRunner().upstream_ref(clone) == "origin/apoub"
    assert GitRunner().branch_tracking_counts(clone) == (0, 0)


# ---------------------------------------------------------------------------
# 4. A branch somebody else pushed is that branch, not a new one
# ---------------------------------------------------------------------------


def _colleague_pushes(tmp_path: Path, remote: Path, branch: str) -> str:
    """Another clone of *remote* pushes *branch* with real work on it."""
    other = tmp_path / "other"
    _run_git(tmp_path, "clone", remote.as_posix(), other.as_posix())
    _identify(other)
    _run_git(other, "checkout", "-b", branch)
    (other / "theirs.txt").write_text("theirs\n", encoding="utf-8")
    _run_git(other, "add", "theirs.txt")
    _run_git(other, "commit", "-m", "colleague work")
    _run_git(other, "push", "-u", "origin", branch)
    return _run_git(other, "rev-parse", "HEAD")


def test_checkout_joins_a_colleagues_branch_instead_of_forking_its_name(
    cloned_workspace, tmp_path, capsys
):
    """The third and worst symptom: ``checkout`` reported success on the wrong commits.

    ``create_global_branch`` asked only whether the branch existed *locally*,
    so a branch a colleague had pushed was created fresh at our own HEAD —
    ``status`` then read ``READY``/``ALIGNED`` on a branch that shared nothing
    but its name with theirs.
    """
    their_sha = _colleague_pushes(tmp_path, cloned_workspace["remote"], "colleague")
    repo = cloned_workspace["repo"]
    # A pull is what brings their ref here; the wide refspec is what lets it.
    assert cli_main(["pull", str(cloned_workspace["snapshot"])]) == 0
    capsys.readouterr()

    assert cli_main(["checkout", "colleague", "--gts", str(_latest_gts(repo))]) == 0
    capsys.readouterr()

    assert _run_git(repo, "rev-parse", "HEAD") == their_sha
    assert (repo / "theirs.txt").read_text(encoding="utf-8") == "theirs\n"
    # ... and it tracks their branch, so status measures it from the first command.
    assert GitRunner().upstream_ref(repo) == "origin/colleague"


def test_checkout_joins_a_colleagues_branch_with_no_prior_pull(cloned_workspace, tmp_path, capsys):
    """CheckoutForkGuard: `checkout` must not depend on some other command
    having fetched first.

    Unlike the test above, no ``pull`` runs before ``checkout`` — this
    clone has never heard of the colleague's branch, locally or as a
    cached remote-tracking ref, which used to be exactly the shape that
    forked it fresh at HEAD
    (``.localSpec/DevTickets/openTickets/main_1-1_CheckoutForkGuard_DevPlanTicket.md``).
    """
    their_sha = _colleague_pushes(tmp_path, cloned_workspace["remote"], "never-fetched")
    repo = cloned_workspace["repo"]

    assert cli_main(["checkout", "never-fetched", "--gts", str(cloned_workspace["snapshot"])]) == 0
    capsys.readouterr()

    assert _run_git(repo, "rev-parse", "HEAD") == their_sha
    assert (repo / "theirs.txt").read_text(encoding="utf-8") == "theirs\n"
    assert GitRunner().upstream_ref(repo) == "origin/never-fetched"


def test_checkout_still_creates_a_brand_new_branch_at_head(cloned_workspace, capsys):
    """A name the remote has never heard of is still created where we stand."""
    repo = cloned_workspace["repo"]
    head_before = _run_git(repo, "rev-parse", "HEAD")

    assert cli_main(["checkout", "mine-alone", "--gts", str(cloned_workspace["snapshot"])]) == 0
    capsys.readouterr()

    assert _run_git(repo, "rev-parse", "HEAD") == head_before
    assert _run_git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "mine-alone"
    assert GitRunner().upstream_configured(repo) is False

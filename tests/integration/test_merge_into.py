"""Checking out a branch and merging into it, as one command.

The bug this file guards against is not a merge bug. This project manages a
tree that contains this project, installed editable, so `cgitsync checkout
main` replaces the code that runs the *next* command — and `checkout` then
`merge` therefore makes the older branch perform its own merge. A single
process is immune: Python has already imported its modules.

Real Git throughout, because every claim here is about what Git does to a
branch that is not checked out — a thing no fake can be wrong about
convincingly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.orchestre import ComplexGitSyncClient


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def _identify(repo: Path) -> None:
    _git(repo, "config", "user.email", "t@e.st")
    _git(repo, "config", "user.name", "Test")


def _seeded_remote(tmp_path: Path, name: str, branch: str) -> Path:
    remote = tmp_path / f"{name}.git"
    _git(tmp_path, "init", "--bare", "-b", branch, remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", branch)
    _identify(seed)
    (seed / "README.md").write_text("initial\n", encoding="utf-8")
    (seed / ".gitignore").write_text(".cgitsync/\n.conf/\n", encoding="utf-8")
    _git(seed, "add", "README.md", ".gitignore")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "-u", "origin", branch)
    return remote


def _snapshot(root: Path, config: Path, *, root_sha: str, config_sha: str) -> str:
    return f"""
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "{root.as_posix()}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "{root.as_posix()}"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "feature"
target_ref_kind = "branch"
target_ref_name = "feature"
resolved_ref_kind = "branch"
resolved_ref_name = "feature"
commit_sha = "{root_sha}"
project_owner_name = "owner"
project_name = "demo"
gitprovider = "github"

[[repo_state]]
name = "conf"
node_type = "leaf"
absolute_path = "{config.as_posix()}"
parent_absolute_path = "{root.as_posix()}"
relative_path = ".conf"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "demo_feature"
target_ref_kind = "branch"
target_ref_name = "demo_feature"
resolved_ref_kind = "branch"
resolved_ref_name = "demo_feature"
commit_sha = "{config_sha}"
default_branch = "demo"
project_owner_name = "owner"
project_name = "conf"
gitprovider = "github"
private = true
writable = true
""".strip() + "\n"


def _tree(tmp_path: Path, *, diverge: bool = False) -> dict[str, Path]:
    """A two-repo tree on `feature`, with `main` behind it.

    The project repository and its private/local configuration repository,
    each with the branch pair the rule derives: `main`/`feature` for the
    project, `demo`/`demo_feature` for the configuration repository.
    """
    root = tmp_path / "demo"
    config = root / ".conf"
    project_remote = _seeded_remote(tmp_path, "demo", "main")
    config_remote = _seeded_remote(tmp_path, "conf", "demo")
    _git(tmp_path, "clone", "-b", "main", project_remote.as_posix(), root.as_posix())
    _identify(root)
    config.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "clone", "-b", "demo", config_remote.as_posix(), config.as_posix())
    _identify(config)

    for repo, branch in ((root, "feature"), (config, "demo_feature")):
        _git(repo, "checkout", "-b", branch)
        (repo / "work.txt").write_text(f"{branch}\n", encoding="utf-8")
        _git(repo, "add", "work.txt")
        _git(repo, "commit", "-m", f"work on {branch}")

    if diverge:
        # A commit on the target that the source does not have. It has to be
        # made *after* the branch point, or the target is still an ancestor
        # and the merge is a fast-forward after all.
        _git(root, "checkout", "main")
        (root / "on-main.txt").write_text("main only\n", encoding="utf-8")
        _git(root, "add", "on-main.txt")
        _git(root, "commit", "-m", "work on main")
        _git(root, "checkout", "feature")

    snapshot = tmp_path / "demo.gts"
    snapshot.write_text(
        _snapshot(
            root,
            config,
            root_sha=_git(root, "rev-parse", "HEAD"),
            config_sha=_git(config, "rev-parse", "HEAD"),
        ),
        encoding="utf-8",
    )
    return {"root": root, "config": config, "snapshot": snapshot}


def _loaded(snapshot: Path) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    client.load_gts(snapshot)
    return client


# ---------------------------------------------------------------------------
# One command: the checkout and the merge
# ---------------------------------------------------------------------------


def test_one_command_checks_out_the_target_and_merges_into_it(tmp_path):
    tree = _tree(tmp_path)
    assert _git(tree["root"], "branch", "--show-current") == "feature"

    _loaded(tree["snapshot"]).merge_into("feature", "main")

    assert _git(tree["root"], "branch", "--show-current") == "main"
    assert (tree["root"] / "work.txt").is_file()
    assert _git(tree["root"], "rev-parse", "main") == _git(tree["root"], "rev-parse", "feature")


def test_a_private_repository_translates_both_branch_names(tmp_path):
    """`main` and `feature` become `demo` and `demo_feature`, by the rule."""
    tree = _tree(tmp_path)

    [plan] = _loaded(tree["snapshot"]).merge_into_plan("feature", "main", private=True)

    assert plan.name == "conf"
    assert plan.source == "demo_feature"
    assert plan.target == "demo"


def test_the_private_half_merges_too(tmp_path):
    tree = _tree(tmp_path)

    _loaded(tree["snapshot"]).merge_into("feature", "main", private=True)

    assert _git(tree["config"], "branch", "--show-current") == "demo"
    assert (tree["config"] / "work.txt").is_file()


def test_a_fast_forward_is_reported_as_one(tmp_path):
    """The common case, and the one that explains an untouched-looking repo."""
    tree = _tree(tmp_path)

    [outcome] = _loaded(tree["snapshot"]).merge_into("feature", "main")

    assert outcome.status == "fast-forward"


def test_a_real_merge_is_told_apart_from_a_fast_forward(tmp_path):
    tree = _tree(tmp_path, diverge=True)

    [outcome] = _loaded(tree["snapshot"]).merge_into("feature", "main")

    assert outcome.status == "merge"
    assert (tree["root"] / "on-main.txt").is_file()
    assert (tree["root"] / "work.txt").is_file()


def test_a_target_that_already_has_the_source_is_not_a_failure(tmp_path):
    tree = _tree(tmp_path)
    client = _loaded(tree["snapshot"])
    client.merge_into("feature", "main")

    again = _loaded(tree["snapshot"]).merge_into("feature", "main")

    assert [row.status for row in again] == ["already-merged"]


# ---------------------------------------------------------------------------
# A refusal leaves the tree where it was
# ---------------------------------------------------------------------------


def test_a_conflict_leaves_the_tree_on_the_source_branch(tmp_path):
    """The promise of the whole command: refuse before touching anything."""
    tree = _tree(tmp_path)
    # The same file, changed differently on each branch.
    _git(tree["root"], "checkout", "main")
    (tree["root"] / "work.txt").write_text("main's version\n", encoding="utf-8")
    _git(tree["root"], "add", "work.txt")
    _git(tree["root"], "commit", "-m", "conflicting work on main")
    _git(tree["root"], "checkout", "feature")

    with pytest.raises(GitSyncError, match="nothing was checked out"):
        _loaded(tree["snapshot"]).merge_into("feature", "main")

    assert _git(tree["root"], "branch", "--show-current") == "feature"
    assert (tree["root"] / "work.txt").read_text(encoding="utf-8") == "feature\n"


def test_a_missing_target_is_named_and_nothing_is_created(tmp_path):
    tree = _tree(tmp_path)

    with pytest.raises(GitSyncError, match="no branch 'not-there'"):
        _loaded(tree["snapshot"]).merge_into("feature", "not-there")

    assert _git(tree["root"], "branch", "--show-current") == "feature"
    branches = _git(tree["root"], "branch", "--format=%(refname:short)").split()
    assert "not-there" not in branches


def test_a_missing_source_is_skipped_and_said_out_loud(tmp_path):
    tree = _tree(tmp_path)

    with pytest.warns(UserWarning, match="no branch 'never-existed'"):
        outcomes = _loaded(tree["snapshot"]).merge_into("never-existed", "main")

    assert [row.status for row in outcomes] == ["no-source"]
    assert _git(tree["root"], "branch", "--show-current") == "feature"


# ---------------------------------------------------------------------------
# The dry run cannot disagree with the merge
# ---------------------------------------------------------------------------


def test_the_dry_run_predicts_what_the_merge_does(tmp_path):
    tree = _tree(tmp_path, diverge=True)
    client = _loaded(tree["snapshot"])

    predicted = [row.status for row in client.merge_into_plan("feature", "main")]
    actual = [row.status for row in client.merge_into("feature", "main")]

    assert predicted == actual


def test_the_dry_run_changes_nothing(tmp_path):
    tree = _tree(tmp_path)
    before = _git(tree["root"], "rev-parse", "HEAD")

    _loaded(tree["snapshot"]).merge_into_plan("feature", "main")

    assert _git(tree["root"], "rev-parse", "HEAD") == before
    assert _git(tree["root"], "branch", "--show-current") == "feature"


# ---------------------------------------------------------------------------
# What the tree records afterwards
# ---------------------------------------------------------------------------


def test_the_merge_records_a_state(tmp_path):
    """`checkout` writes one because the tree moved; so must this."""
    tree = _tree(tmp_path)
    client = _loaded(tree["snapshot"])

    client.merge_into("feature", "main")

    entries = ComplexGitSyncClient().memory_status(tree["root"])
    assert entries["entries"] >= 1
    assert entries["verification"] == "verified"


def test_the_ledger_names_the_command_that_did_it(tmp_path):
    from ComplexGitSync.memory.ledger_store import read_all_entries

    tree = _tree(tmp_path)
    _loaded(tree["snapshot"]).merge_into("feature", "main")

    entries = read_all_entries(tree["root"] / ".cgitsync" / "lgr")
    assert entries[-1].command == "merge-into"


# ---------------------------------------------------------------------------
# The self-hosting question this command exists for
# ---------------------------------------------------------------------------


def test_a_tree_that_is_not_self_hosted_has_no_build_to_warn_about(tmp_path):
    """`build_installed_from` answers only for a workspace holding this tool."""
    tree = _tree(tmp_path)

    assert _loaded(tree["snapshot"]).build_installed_from("main") is None


def test_a_tree_that_holds_this_tool_warns_before_replacing_it(tmp_path, monkeypatch):
    """The warning the whole command exists to make unnecessary."""
    tree = _tree(tmp_path)
    client = _loaded(tree["snapshot"])
    monkeypatch.setattr(
        type(client), "build_installed_from", lambda self, branch: "0002.01"
    )

    with pytest.warns(UserWarning, match="an older build"):
        client.checkout("main")


def test_merge_into_does_not_offer_itself_as_the_remedy(tmp_path, monkeypatch):
    """`merge --into` is the fix; suggesting it inside itself reads as a loop."""
    tree = _tree(tmp_path)
    client = _loaded(tree["snapshot"])
    monkeypatch.setattr(
        type(client), "build_installed_from", lambda self, branch: "0002.01"
    )

    with pytest.warns(UserWarning, match="an older build") as caught:
        client.merge_into_plan("feature", "main")

    assert "--into" not in str(caught[0].message)


def test_merge_into_and_resolve_cannot_be_combined(tmp_path, capsys):
    """They promise opposite things about a conflict."""
    from ComplexGitSync.cli import main as cli_main

    tree = _tree(tmp_path)
    exit_code = cli_main(
        [
            "merge",
            "feature",
            "--into",
            "main",
            "--resolve",
            "--gts",
            str(tree["snapshot"]),
        ]
    )

    assert exit_code != 0
    assert "cannot be combined" in capsys.readouterr().err

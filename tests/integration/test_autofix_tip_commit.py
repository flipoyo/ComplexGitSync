"""autofix reads a tip commit when there is no error to start from.

The AutofixBlindSpot ticket. Commit ``701a98f`` reached the remote with every backtick-quoted
phrase eaten by a shell and nothing logged, so `autofix` found "no failing command". These tests
build real repositories (bare remotes, a bootstrapped tree) and check that the malformed tip commit
is found, that a repair never touches a good commit, a merge commit or a repository this project
cannot write, and that a commit a remote already holds is never rewritten without ``--force`` — nor pushed with it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.autofix.base import Situation
from ComplexGitSync.autofix.repair_commit_message import MalformedCommitMessageRepair
from ComplexGitSync.autofix.repair_from_cli import NoMatchingRepairError
from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_repo import WorkingRepo
from ComplexGitSync.orchestre import ComplexGitSyncClient

#: Commit 701a98f, as `git show -s --format=%B` printed it: every backtick-quoted phrase gone.
INCIDENT = """\
cgitsync3.1.1 Fixes a live crash: self-history's push path assumed any
adopted mount already had a commit, but an unborn branch makes main raise
instead of answering none the way a detached HEAD does; / before the
first  is now a harmless no-op. Also repoints pixi.toml's  task at ,
its actual mount path since the release skill was renamed — the stale
 path had been silently breaking every  run"""

GOOD = "cgitsync3.9.0 A clean message in plain English."
FIXED = "cgitsync3.9.9 Fixes a live crash in self-history's push path; memory push before the first record is now a no-op."


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "-c", "user.name=T", "-c", "user.email=t@e.st", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _seed(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    remote = tmp_path / f"{name}-remote.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    for relative, text in {"README.md": f"{name}\n", **files}.items():
        (seed / relative).parent.mkdir(parents=True, exist_ok=True)
        (seed / relative).write_text(text, encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", f"cgitsync3.0.0 seed {name}")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-q", "-u", "origin", "main")
    return remote


_ADOPTED = {
    "pyproject.toml": '[project]\nname = "ComplexGitSync"\nversion = "3.9.9"\n[project.scripts]\ncgitsync = "x:main"\n',
    ".agent/.distant/dev-sync/AgentConduct.md": "# conduct\n",
}


def _tree(tmp_path: Path, monkeypatch, *, leaf_flags: str = "") -> dict[str, Path | ComplexGitSyncClient]:
    """A bootstrapped tree: a root that adopted DevSpec, one project repository, one read-only private one."""
    remotes = {
        "demo": _seed(tmp_path, "demo", _ADOPTED),
        "leaf": _seed(tmp_path, "leaf", {}),
        "conf": _seed(tmp_path, "conf", {}),
        ".memory": _seed(tmp_path, ".memory", {}),
    }
    monkeypatch.setenv("CGSPATH", str(tmp_path / "cgspath"))
    monkeypatch.delenv("CGSHOME", raising=False)
    cgs = tmp_path / "demo.cgs"
    cgs.write_text(
        'project = { name = "demo", default_branch = "main" }\nrepos = [\n'
        '    { repository = "github:owner/demo", relative_path = "." },\n'
        f'    {{ repository = "github:owner/leaf", relative_path = "deps/leaf"{leaf_flags} }},\n'
        '    { repository = "github:owner/conf", relative_path = "conf", private = true },\n'
        '    { repository = "github:owner/.memory", relative_path = ".cgitsync/.memory", private = true, writable = true, nested_config = "disabled" },\n]\n',
        encoding="utf-8",
    )
    client = ComplexGitSyncClient()
    monkeypatch.setattr(client, "_build_remote_url", lambda entry: str(remotes[entry.project_name]))
    client.bootstrap(cgs, "demo", cgs_path=tmp_path / "cgspath")
    workspace = tmp_path / "cgspath" / "demo"
    return {"client": client, "workspace": workspace, "leaf": workspace / "deps" / "leaf", "conf": workspace / "conf"}


def _amend(path: Path, message: str) -> None:
    """What a bare `git commit` outside cgitsync leaves behind: a tip commit with *message*."""
    _git(path, "commit", "-q", "--allow-empty", "-m", message)


def _subject(path: Path) -> str:
    return _git(path, "log", "-1", "--format=%s")


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    for key, value in (("GIT_AUTHOR_NAME", "Test"), ("GIT_COMMITTER_NAME", "Test"),
                       ("GIT_AUTHOR_EMAIL", "t@example.com"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(key, value)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    return _tree(tmp_path, monkeypatch)


def _flagged(tree) -> dict[str, dict]:
    answer = tree["client"].autofix_tip_commits()
    return {row["repository"]: row for row in answer["findings"]}


# ---------------------------------------------------------------------------
# Finding it, with no error to start from
# ---------------------------------------------------------------------------


def test_the_incident_commit_is_found_with_the_rules_it_breaks_and_the_damage_it_shows(tree):
    _amend(tree["leaf"], INCIDENT)

    row = _flagged(tree)["leaf"]

    findings = "\n".join(row["findings"])
    assert "3 lines at most" in findings                      # a rule, from AgentConduct §2
    assert "suspected shell damage: two spaces" in findings    # a trace, "pixi.toml's  task"
    assert "suspected shell damage: a space before a comma" in findings  # "at ,"
    assert row["published"] is False
    assert row["sha"] == _git(tree["leaf"], "rev-parse", "HEAD")


def test_a_clean_message_is_not_flagged_even_from_an_earlier_release(tree):
    _amend(tree["leaf"], GOOD)  # 3.9.0, while the project is at 3.9.9: rightly written then

    assert "leaf" not in _flagged(tree)


def test_a_merge_commit_is_never_judged_because_git_wrote_its_message(tree):
    leaf = tree["leaf"]
    _git(leaf, "checkout", "-q", "-b", "side")
    _amend(leaf, "cgitsync3.0.1 side work")
    _git(leaf, "checkout", "-q", "main")
    _amend(leaf, "cgitsync3.0.2 main work")
    _git(leaf, "merge", "-q", "--no-ff", "side", "-m", "Merge branch 'side'")

    assert "leaf" not in _flagged(tree)


def test_a_repository_this_project_may_not_write_is_not_judged(tree):
    _amend(tree["conf"], INCIDENT)  # private and read-only: someone else's repository

    assert "conf" not in _flagged(tree)


def test_the_memory_mount_is_not_judged_its_message_is_the_tools_own(tree):
    mount = tree["workspace"] / ".cgitsync" / ".memory"
    assert any(repo.name == ".memory" for repo in tree["client"].registry.values())  # it IS in the tree, declared and writable
    _amend(mount, "demo memory, 2026-10-01: 3 state(s), 3 ledger entr(ies)")  # the tool's own words, not the house rule

    assert ".memory" not in _flagged(tree)


def test_a_tree_that_has_not_adopted_devspec_is_judged_only_for_shell_damage():
    repo = WorkingRepo(name="x", repo_id="root")
    repair = MalformedCommitMessageRepair()

    not_adopted = Situation(repo=repo, source_error="", commit_message="Fix stuff", project_root=None)
    damaged = Situation(repo=repo, source_error="", commit_message="Fixes  the thing at ,", project_root=None)

    assert repair.matches(not_adopted) is False  # no house rule applies, no damage shown
    assert repair.matches(damaged) is True


def test_an_error_driven_situation_never_matches_this_repair():
    repo = WorkingRepo(name="x", repo_id="root")

    assert MalformedCommitMessageRepair().matches(Situation(repo=repo, source_error="! [rejected] fetch first")) is False


# ---------------------------------------------------------------------------
# Repairing it, and never what it must not touch
# ---------------------------------------------------------------------------


def test_an_unpublished_commit_is_amended_and_whatever_was_staged_stays_staged(tree):
    leaf = tree["leaf"]
    _amend(leaf, INCIDENT)
    before = _git(leaf, "rev-parse", "HEAD")
    (leaf / "wip.txt").write_text("not committed yet\n", encoding="utf-8")
    _git(leaf, "add", "wip.txt")

    outcome = tree["client"].autofix_amend("leaf", FIXED)

    assert outcome.repaired is True
    assert _git(leaf, "log", "-1", "--format=%B") == FIXED
    assert _git(leaf, "rev-parse", "HEAD") != before          # a new commit, as an amend makes
    assert _git(leaf, "status", "--short") == "A  wip.txt"    # staged, and not folded into the commit
    assert "wip.txt" not in _git(leaf, "show", "--name-only", "--format=", "HEAD")
    assert "leaf" not in _flagged(tree)


def test_a_commit_a_remote_already_holds_is_refused_without_force_and_left_as_it_is(tree):
    leaf = tree["leaf"]
    _amend(leaf, INCIDENT)
    _git(leaf, "push", "-q", "origin", "main")
    pushed = _git(leaf, "rev-parse", "HEAD")
    assert _flagged(tree)["leaf"]["published"] is True

    with pytest.raises(GitSyncError, match="already on a remote.*--force"):
        tree["client"].autofix_amend("leaf", FIXED)

    assert _git(leaf, "rev-parse", "HEAD") == pushed
    assert _git(leaf, "log", "-1", "--format=%B") == INCIDENT


def test_force_rewrites_only_locally_and_never_pushes(tree):
    leaf = tree["leaf"]
    _amend(leaf, INCIDENT)
    _git(leaf, "push", "-q", "origin", "main")
    pushed = _git(leaf, "rev-parse", "HEAD")
    remote_before = _git(leaf, "ls-remote", "origin", "refs/heads/main")

    outcome = tree["client"].autofix_amend("leaf", FIXED, force=True)

    assert outcome.repaired is True
    assert _git(leaf, "log", "-1", "--format=%B") == FIXED
    assert _git(leaf, "rev-parse", "HEAD") != pushed
    assert _git(leaf, "ls-remote", "origin", "refs/heads/main") == remote_before   # the remote still has the old commit
    assert "git push --force-with-lease" in outcome.detail and "does not push it for you" in outcome.detail


@pytest.mark.parametrize("replacement", ["", "   ", "Fix stuff", "cgitsync3.9.9 uses `a backtick`", "cgitsync3.9.9 Fixes  a thing"])
def test_a_replacement_that_is_itself_malformed_is_refused(tree, replacement):
    _amend(tree["leaf"], INCIDENT)

    with pytest.raises(GitSyncError, match="replacement message was refused"):
        tree["client"].autofix_amend("leaf", replacement)

    assert _git(tree["leaf"], "log", "-1", "--format=%B") == INCIDENT


def test_a_commit_that_is_not_malformed_is_never_rewritten_because_someone_asked(tree):
    _amend(tree["leaf"], GOOD)
    before = _git(tree["leaf"], "rev-parse", "HEAD")

    with pytest.raises(NoMatchingRepairError, match="not malformed"):
        tree["client"].autofix_amend("leaf", FIXED)

    assert _git(tree["leaf"], "rev-parse", "HEAD") == before


def test_a_read_only_repository_cannot_be_amended(tree):
    _amend(tree["conf"], INCIDENT)

    with pytest.raises(NoMatchingRepairError, match="no repository named 'conf'"):
        tree["client"].autofix_amend("conf", FIXED)


def _stop_a_rebase(leaf: Path) -> None:
    """Leave `git rebase -i` stopped *on* a commit with a malformed message (the `edit` stop)."""
    _amend(leaf, INCIDENT)
    _amend(leaf, "cgitsync3.0.1 a later commit")
    env = {**__import__("os").environ, "GIT_SEQUENCE_EDITOR": "sed -i '1s/^pick/edit/'"}
    stopped = subprocess.run(["git", "rebase", "-i", "HEAD~2"], cwd=leaf, capture_output=True, text=True, env=env)
    assert stopped.returncode == 0 and (leaf / ".git" / "rebase-merge").exists()
    assert _git(leaf, "log", "-1", "--format=%B") == INCIDENT  # the stop is on the malformed commit


def test_nothing_is_amended_while_a_rebase_is_stopped(tree):
    leaf = tree["leaf"]
    _stop_a_rebase(leaf)
    head = _git(leaf, "rev-parse", "HEAD")

    with pytest.raises(GitSyncError, match="middle of a rebase, and the latest commit is the one it stands on"):
        tree["client"].autofix_amend("leaf", FIXED)

    assert _git(leaf, "rev-parse", "HEAD") == head


def test_nothing_is_amended_while_a_cherry_pick_is_stopped(tree):
    leaf = tree["leaf"]
    (leaf / "f.txt").write_text("one\n", encoding="utf-8")
    _git(leaf, "add", "f.txt")
    _git(leaf, "commit", "-q", "-m", "cgitsync3.0.1 one")
    _git(leaf, "checkout", "-q", "-b", "other", "HEAD~1")
    (leaf / "f.txt").write_text("two\n", encoding="utf-8")
    _git(leaf, "add", "f.txt")
    _git(leaf, "commit", "-q", "-m", INCIDENT)
    picked = subprocess.run(["git", "cherry-pick", "main"], cwd=leaf, capture_output=True, text=True)
    assert picked.returncode != 0

    with pytest.raises(GitSyncError, match="middle of a cherry-pick, and the latest commit is the one it stands on"):
        tree["client"].autofix_amend("leaf", FIXED)


def test_a_french_colon_is_not_a_trace_of_eaten_text():
    repair = MalformedCommitMessageRepair()

    assert repair.shell_damage("cgitsync3.9.1 Trois choses : une, deux ; trois.") == []
    assert repair.shell_damage("cgitsync3.9.1 Fixes the thing at , once.") != []


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_the_command_reports_and_exits_1_when_something_is_malformed(tree, capsys):
    _amend(tree["leaf"], INCIDENT)

    code = cli_main(["autofix", "--tip-commit", "--search-dir", str(tree["workspace"])])

    out = capsys.readouterr().out
    assert code == 1
    assert "repository=leaf" in out and "suspected shell damage" in out
    assert "fix: cgitsync autofix --tip-commit --repo leaf --message-file" in out


def test_the_command_exits_0_when_every_tip_commit_is_fine(tree, capsys):
    code = cli_main(["autofix", "--tip-commit", "--search-dir", str(tree["workspace"])])

    assert code == 0
    assert "malformed=0" in capsys.readouterr().out


def test_the_command_amends_from_a_file_which_a_shell_cannot_damage(tree, tmp_path, capsys):
    _amend(tree["leaf"], INCIDENT)
    corrected = tmp_path / "corrected.txt"
    corrected.write_text(FIXED + "\n", encoding="utf-8")

    code = cli_main(["autofix", "--tip-commit", "--repo", "leaf", "--message-file", str(corrected), "--search-dir", str(tree["workspace"])])

    assert code == 0
    assert "repaired=True" in capsys.readouterr().out
    assert _subject(tree["leaf"]) == FIXED


def test_a_corrected_message_file_that_is_not_utf8_is_refused_clearly(tree, tmp_path, capsys):
    _amend(tree["leaf"], INCIDENT)
    broken = tmp_path / "latin1.txt"
    broken.write_bytes("cgitsync3.9.9 caf\xe9".encode("latin-1"))

    code = cli_main(["autofix", "--tip-commit", "--repo", "leaf", "--message-file", str(broken), "--search-dir", str(tree["workspace"])])

    assert code == 1
    assert "not valid UTF-8" in capsys.readouterr().err
    assert _git(tree["leaf"], "log", "-1", "--format=%B") == INCIDENT


def test_a_commit_not_seen_on_a_remote_is_worded_as_what_it_is(tree, capsys):
    _amend(tree["leaf"], INCIDENT)

    cli_main(["autofix", "--tip-commit", "--search-dir", str(tree["workspace"])])

    assert "on_a_remote=not-seen-from-this-clone" in capsys.readouterr().out


def test_the_command_refuses_a_nonsense_combination(tree, capsys):
    workspace = str(tree["workspace"])

    assert cli_main(["autofix", "--message", "x", "--search-dir", workspace]) == 1             # needs --tip-commit
    assert cli_main(["autofix", "--tip-commit", "--error", "boom", "--search-dir", workspace]) == 1
    assert cli_main(["autofix", "--tip-commit", "--message", "x", "--message-file", "f", "--search-dir", workspace]) == 1
    assert cli_main(["autofix", "--tip-commit", "--message", "cgitsync3.9.9 ok", "--search-dir", workspace]) == 1  # no --repo
    assert "--tip-commit" in capsys.readouterr().err

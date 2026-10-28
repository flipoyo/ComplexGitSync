"""`cgitsync autofix` with no failing command reads the tip commits it cannot be told about.

Backs the AutofixBlindSpot ticket. A commit made by a bare `git commit` raises
nothing and logs nothing; the shell-mangled message of commit 701a98f is rebuilt
here with real Git. `autofix` reports and proposes ways to extract the message,
and every ref and message stays exactly as it was.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_close_branch import _git, _loaded, _two_repo_workspace

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.tree.autofix import NoMatchingRepairError

#: What 701a98f became: every backticked phrase gone, the live output of
#: `git rev-parse --abbrev-ref HEAD` in its place, all on one line.
MANGLED = (
    "cgitsync3.1.1 Fixes a live crash: self-history's push path assumed any adopted mount already had a "
    "commit, but an unborn branch makes main raise instead of answering none; / before the first  is now "
    "a harmless no-op. Also repoints pixi.toml's  task at , its actual mount path"
)


def _adopt_devspec(tree: dict[str, Path]) -> None:
    """The two files that make `CommitMessagePolicy` bind this tree (untracked: only read).

    The configuration repository gets a conforming tip commit too, so the fixture's
    own seed commit is not what the test ends up reporting.
    """
    root = tree["root"]
    _commit(tree["config"], "demo-1.0.0 the configuration repository, set up.")
    conduct = root / ".agent" / ".distant" / "dev-sync"
    conduct.mkdir(parents=True)
    (conduct / "AgentConduct.md").write_text("# conduct\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "1.0.0"\n', encoding="utf-8")


def _commit(repo: Path, message: str, *extra: str) -> str:
    (repo / "change.txt").write_text(message, encoding="utf-8")
    _git(repo, "add", "change.txt")
    _git(repo, "commit", "-m", message, *extra)
    return _git(repo, "rev-parse", "HEAD")


def _everything(tree: dict[str, Path]) -> tuple:
    return tuple(
        (_git(tree[key], "rev-parse", "HEAD"), _git(tree[key], "branch", "--list"), _git(tree[key], "log", "-1", "--format=%B"))
        for key in ("root", "config")
    )


def test_a_shell_mangled_tip_commit_is_reported_and_nothing_changes(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _adopt_devspec(tree)
    sha = _commit(tree["root"], MANGLED)
    before = _everything(tree)

    outcome = _loaded(tree["snapshot"]).autofix()

    assert outcome.repaired is False
    assert sha[:12] in outcome.detail and "suspected shell damage" in outcome.detail
    assert f"show -s --format=%B {sha}" in outcome.detail and f"cat-file commit {sha}" in outcome.detail
    assert _everything(tree) == before


def test_a_message_that_breaks_the_house_rule_is_reported_with_the_rule(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _adopt_devspec(tree)
    _commit(tree["root"], "demo-1.0.0 one\n\ntwo\nthree\nCo-Authored-By: Someone <a@b.c>")

    detail = _loaded(tree["snapshot"]).autofix().detail

    assert "lines at most" in detail and "agent-credit trailer" in detail


def test_the_run_log_still_wins_when_the_last_command_failed(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _adopt_devspec(tree)
    _commit(tree["root"], MANGLED)

    with pytest.raises(NoMatchingRepairError) as unknown:
        _loaded(tree["snapshot"]).autofix(error="something nobody registered a repair for")

    assert "did not match any registered repair" in str(unknown.value)


def test_a_commit_someone_else_made_is_not_judged(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _adopt_devspec(tree)
    _commit(tree["root"], MANGLED, "--author=Someone Else <else@example.test>")

    with pytest.raises(NoMatchingRepairError) as nothing:
        _loaded(tree["snapshot"]).autofix()

    assert "no failing command found in the run log" in str(nothing.value)


def test_a_conforming_tip_commit_is_not_reported(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _adopt_devspec(tree)
    _commit(tree["root"], "demo-1.0.0 a plain change, plainly put.")

    with pytest.raises(NoMatchingRepairError):
        _loaded(tree["snapshot"]).autofix()


def test_the_command_line_prints_the_report_and_refuses(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)
    _adopt_devspec(tree)
    sha = _commit(tree["root"], MANGLED)

    assert cli_main(["autofix", str(tree["snapshot"])]) == 1

    assert sha[:12] in capsys.readouterr().out


def test_a_message_naming_dotted_files_is_not_taken_for_shell_damage(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _adopt_devspec(tree)
    _commit(tree["root"], "demo-1.0.0 moves .cgs and .memory under .agent, e.g. .localSpec to .dev.")

    with pytest.raises(NoMatchingRepairError):
        _loaded(tree["snapshot"]).autofix()


@pytest.mark.parametrize("message", ["demo-1.0.0 repoints the task at , its mount.", "demo-1.0.0 a stray space ."])
def test_a_space_before_punctuation_is_suspected_damage(tmp_path, message):
    tree = _two_repo_workspace(tmp_path)
    _adopt_devspec(tree)
    _commit(tree["root"], message)

    assert "suspected shell damage" in _loaded(tree["snapshot"]).autofix().detail


def test_repo_narrows_the_inspection_and_names_a_missing_repository(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _adopt_devspec(tree)
    _commit(tree["root"], MANGLED)
    client = _loaded(tree["snapshot"])

    assert "suspected shell damage" in client.autofix(repo_name="demo").detail
    with pytest.raises(NoMatchingRepairError):
        client.autofix(repo_name="conf")  # its tip commit conforms
    with pytest.raises(NoMatchingRepairError) as missing:
        client.autofix(repo_name="nowhere")
    assert "no repository named 'nowhere'" in str(missing.value)


def test_a_tree_that_adopted_no_devspec_is_judged_by_the_damage_signs_alone(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _commit(tree["root"], "a short message with no prefix and no trailer")
    with pytest.raises(NoMatchingRepairError):
        _loaded(tree["snapshot"]).autofix()
    _commit(tree["root"], "a message that lost  a quoted phrase")

    detail = _loaded(tree["snapshot"]).autofix().detail

    assert "suspected shell damage" in detail and "must start with" not in detail

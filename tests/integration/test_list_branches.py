"""`cgitsync branch --list` — every branch of every repository in the tree, read-only."""

from __future__ import annotations

from test_close_branch import (
    _git,
    _loaded,
    _push_new_branch,
    _two_repo_workspace,
)

from ComplexGitSync.cli import main


def test_list_branches_names_each_repository_and_its_current_branch(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")

    result = {r.name: r for r in _loaded(tree["snapshot"]).list_branches()}

    assert result["demo"].branches == ("feature-x", "main")
    assert result["demo"].current == "main"
    assert result["conf"].branches == ("demo",)


def test_list_branches_private_narrows_to_the_writable_configuration_repos(tmp_path):
    tree = _two_repo_workspace(tmp_path)

    result = _loaded(tree["snapshot"]).list_branches(private=True)

    assert [r.name for r in result] == ["conf"]


def test_list_branches_changes_nothing(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    before = _git(tree["root"], "for-each-ref")

    _loaded(tree["snapshot"]).list_branches()

    assert _git(tree["root"], "for-each-ref") == before


def test_cli_branch_list_prints_one_line_per_repository(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)

    code = main(["branch", "--list", "--gts", str(tree["snapshot"])])

    out = capsys.readouterr().out
    assert code == 0
    assert "demo: *main" in out
    assert "conf: *demo" in out


def test_cli_branch_needs_a_name_or_list_but_not_both(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)

    assert main(["branch", "--gts", str(tree["snapshot"])]) == 2
    assert main(["branch", "x", "--list", "--gts", str(tree["snapshot"])]) == 2

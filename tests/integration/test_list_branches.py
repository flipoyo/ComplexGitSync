"""`cgitsync branch --list` — every branch of every repository in the tree, read-only."""

from __future__ import annotations

import pytest
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


def test_cli_branch_list_per_repo_prints_one_line_per_repository(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)

    code = main(["branch", "list", "--per-repo", "--gts", str(tree["snapshot"])])

    out = capsys.readouterr().out
    assert code == 0
    assert "demo: *main" in out
    assert "conf: *demo" in out


def test_cli_branch_takes_a_subcommand_and_create_takes_a_name(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)

    for argv in (["branch"], ["branch", "create", "--gts", str(tree["snapshot"])], ["branch", "list", "x"]):
        with pytest.raises(SystemExit) as refused:
            main(argv)
        assert refused.value.code == 2

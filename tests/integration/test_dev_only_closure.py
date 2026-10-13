"""`branch close` and `branch delete` are refused in a tree that holds no private repository.

Backs the DevOnlyClosure ticket. An accident guard, not a lock: the profile comes
from the `.cgs` the user holds. Real Git throughout, on `test_close_branch.py`'s
two-repository tree, declared once with its configuration repository private
(a developer's tree) and once without (a user's).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_close_branch import (
    _git,
    _loaded,
    _push_new_branch,
    _remote_branches,
    _two_repo_workspace,
)

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.errors import GitSyncError


def _as_a_user_tree(tree: dict[str, Path]) -> dict[str, Path]:
    """The same tree, its configuration repository no longer private."""
    text = tree["snapshot"].read_text(encoding="utf-8")
    tree["snapshot"].write_text(text.replace("private = true\nwritable = true\n", ""), encoding="utf-8")
    return tree


def _published(tree: dict[str, Path], branch: str) -> None:
    _push_new_branch(tree["root"], branch)


def _state(tree: dict[str, Path]) -> tuple[str, str, set[str]]:
    return (
        _git(tree["root"], "branch", "--list"),
        _git(tree["root"], "rev-parse", "HEAD"),
        _remote_branches(tree["project_remote"]),
    )


def test_close_is_refused_in_a_user_tree_and_nothing_moves(tmp_path, capsys):
    tree = _as_a_user_tree(_two_repo_workspace(tmp_path))
    _published(tree, "feature-x")
    before = _state(tree)

    with pytest.raises(GitSyncError) as refused:
        _loaded(tree["snapshot"]).close_branch("feature-x")

    assert "branch close was refused" in str(refused.value) and "private repository" in str(refused.value)
    assert _state(tree) == before


def test_delete_is_refused_in_a_user_tree_even_for_a_branch_already_closed(tmp_path):
    tree = _as_a_user_tree(_two_repo_workspace(tmp_path))
    _push_new_branch(tree["root"], "closed/feature-x")
    before = _state(tree)

    with pytest.raises(GitSyncError) as refused:
        _loaded(tree["snapshot"]).delete_branch("feature-x")

    assert "branch delete was refused" in str(refused.value)
    assert _state(tree) == before


def test_the_command_line_refuses_the_same_way(tmp_path, capsys):
    tree = _as_a_user_tree(_two_repo_workspace(tmp_path))
    _published(tree, "feature-x")
    before = _state(tree)

    assert cli_main(["branch", "close", "feature-x", "--gts", str(tree["snapshot"])]) == 1
    assert cli_main(["branch", "delete", "feature-x", "--gts", str(tree["snapshot"])]) == 1

    error = capsys.readouterr().err
    assert "branch close was refused" in error and "branch delete was refused" in error
    assert _state(tree) == before


def test_check_and_list_still_work_in_a_user_tree(tmp_path):
    tree = _as_a_user_tree(_two_repo_workspace(tmp_path))
    _published(tree, "feature-x")
    client = _loaded(tree["snapshot"])

    assert client.branch_ancestry("feature-x")
    assert client.list_branches()


def test_a_developer_tree_still_closes_and_deletes(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _published(tree, "feature-x")
    _push_new_branch(tree["config"], "demo_feature-x")

    client = _loaded(tree["snapshot"])
    client.close_branch("feature-x")
    client.delete_branch("feature-x")

    assert "closed/feature-x" not in _remote_branches(tree["project_remote"])


def test_every_caller_of_delete_remote_branch_is_known_and_gated():
    """Keeps 'only close and delete remove a branch on a remote' true after the next change.

    A new caller must go behind `WorkingGitTree.require_dev` (or, like `memory reboot`,
    act on a memory that a USER tree never publishes) and be added here on purpose.
    """
    source = Path(__file__).resolve().parents[2] / "src" / "ComplexGitSync"
    callers = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute) and node.attr == "delete_remote_branch" and path.name != "git_runner.py"
    }
    assert callers == {"operations/branch.py", "operations/ancestors.py", "orchestre/memory_commands.py"}
    tree_commands = (source / "orchestre" / "tree_commands.py").read_text(encoding="utf-8")
    assert 'registry.require_dev("branch close")' in tree_commands and 'registry.require_dev("branch delete")' in tree_commands

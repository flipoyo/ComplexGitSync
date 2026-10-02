"""`cgitsync branch --list` — the project's own branches and who holds each (ProjectBranchList)."""

from __future__ import annotations

from test_close_branch import (
    _git,
    _loaded,
    _push_new_branch,
    _two_repo_workspace,
)

from ComplexGitSync.cli import main


def _by_name(tree):
    return {b.name: b for b in _loaded(tree["snapshot"]).project_branches()}


def test_a_branch_only_on_origin_is_listed_as_origin(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _git(tree["root"], "branch", "-D", "feature-x")

    found = _by_name(tree)["feature-x"]

    assert found.on_origin and not found.local


def test_a_branch_every_repository_holds_is_fully_covered(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _push_new_branch(tree["config"], "demo_feature-x")

    found = _by_name(tree)["feature-x"]

    assert found.following == 2
    assert found.missing == ()


def test_a_branch_the_private_local_repository_lacks_names_it(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")

    found = _by_name(tree)["feature-x"]

    assert found.missing == ("conf",)
    assert found.following == 1


def test_main_is_covered_by_the_projects_own_settings_branch(tmp_path):
    tree = _two_repo_workspace(tmp_path)

    found = _by_name(tree)["main"]

    assert found.current
    assert found.missing == ()


def test_a_closed_branch_is_listed_apart_under_its_original_name(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _loaded(tree["snapshot"]).close_branch("feature-x")

    found = _loaded(tree["snapshot"]).project_branches()

    closed = [b for b in found if b.closed]
    assert [b.name for b in closed] == ["feature-x"]
    assert not any(b.name == "feature-x" and not b.closed for b in found)
    assert found[-1].closed


def test_a_detached_root_marks_no_branch(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _git(tree["root"], "checkout", "--detach")

    assert not any(b.current for b in _loaded(tree["snapshot"]).project_branches())


def test_project_branches_changes_nothing(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    before = (_git(tree["root"], "for-each-ref"), _git(tree["root"], "rev-parse", "HEAD"))

    _loaded(tree["snapshot"]).project_branches()

    assert (_git(tree["root"], "for-each-ref"), _git(tree["root"], "rev-parse", "HEAD")) == before


def test_cli_branch_list_prints_the_project_branches(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")

    code = main(["branch", "--list", "--gts", str(tree["snapshot"])])

    out = capsys.readouterr().out
    assert code == 0
    assert "cgitsync_branch=main" in out
    assert "* main" in out
    assert "missing in: conf" in out


def test_cli_per_repo_needs_list(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)

    assert main(["branch", "x", "--per-repo", "--gts", str(tree["snapshot"])]) == 2


def test_private_narrows_coverage_but_not_the_branches_listed(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")

    found = {b.name: b for b in _loaded(tree["snapshot"]).project_branches(private=True)}

    assert "feature-x" in found
    assert found["feature-x"].following == 0
    assert found["feature-x"].missing == ("conf",)
    assert found["main"].following == 1


def test_a_repository_not_cloned_yet_is_named_not_counted_missing(tmp_path):
    import shutil

    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    shutil.rmtree(tree["config"])

    found = _by_name(tree)["feature-x"]

    assert found.uncloned == ("conf",)
    assert found.missing == ()


def test_cli_names_a_detached_root_as_detached(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)
    _git(tree["root"], "checkout", "--detach")

    code = main(["branch", "--list", "--gts", str(tree["snapshot"])])

    out = capsys.readouterr().out
    assert code == 0
    assert "cgitsync_branch=detached" in out
    assert "* " not in out


def test_a_tag_pinned_repository_is_not_counted(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    text = tree["snapshot"].read_text(encoding="utf-8")
    pinned = text.replace(
        'target_ref_kind = "branch"\ntarget_ref_name = "demo"',
        'target_ref_kind = "tag"\ntarget_ref_name = "demo"',
    )
    assert pinned != text
    tree["snapshot"].write_text(pinned, encoding="utf-8")

    found = _by_name(tree)["feature-x"]

    assert found.following == 1
    assert found.missing == ()

"""``cgitsync release freeze | list | load`` on the CGSil1 sandbox.

Backs the ReleaseCommand ticket. The sandbox declares no memory, so it is a
USER tree whose ledger never leaves the disk: a release another workspace
made can only be found through the project itself, which is the case
Tutorial 2 now teaches.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from test_tuto_cgsi1 import (  # noqa: F401  (cgsi1_sandbox is a fixture)
    _patch_git_identity,
    _patch_remote_urls,
    _run_git,
    cgsi1_sandbox,
)

from ComplexGitSync.cli import main as cli_main


def _clear_readonly(func, path, _exc_info):
    """``shutil.rmtree`` ``onerror``: Git for Windows writes ``.git/objects`` read-only."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _workspace(sandbox, monkeypatch, tmp_path: Path, name: str) -> Path:
    """Bootstrap the sandbox's .cgs into its own workspace and point CGSHOME at it."""
    assert cli_main(["bootstrap", str(sandbox["cgs_path"]), name, "--cgs-path", str(tmp_path / name)]) == 0
    home = tmp_path / name / name
    monkeypatch.setenv("CGSHOME", str(home))
    return home


def _note(home: Path, text: str) -> None:
    (home / "CGSil2" / "notes.txt").write_text(text, encoding="utf-8")
    assert cli_main(["add"]) == 0
    assert cli_main(["commit", f"note: {text.strip()}"]) == 0
    assert cli_main(["push"]) == 0


@pytest.fixture()
def sandbox(cgsi1_sandbox, monkeypatch):  # noqa: F811
    _patch_remote_urls(monkeypatch, cgsi1_sandbox)
    _patch_git_identity(monkeypatch)
    return cgsi1_sandbox


def test_a_release_is_tagged_with_the_project_name_and_the_next_number(sandbox, monkeypatch, tmp_path, capsys):
    home = _workspace(sandbox, monkeypatch, tmp_path, "one")
    (home / "CGSil2" / "notes.txt").write_text("a first note\n", encoding="utf-8")

    assert cli_main(["release", "freeze", "first release of the sandbox"]) == 0

    assert "CGSil1-1" in capsys.readouterr().out
    for remote in ("CGSil1_remote", "CGSil2_remote", "CGSih1_remote"):
        assert "refs/tags/CGSil1-1" in _run_git(sandbox[remote], "show-ref", "--tags")
    # The root's tag is annotated and carries the State; the others are plain.
    assert _run_git(sandbox["CGSil1_remote"], "cat-file", "-t", "CGSil1-1") == "tag"
    assert "[cgitsync_release]" in _run_git(sandbox["CGSil1_remote"], "cat-file", "-p", "CGSil1-1")
    assert _run_git(sandbox["CGSil2_remote"], "cat-file", "-t", "CGSil1-1") == "commit"


def test_numbers_follow_and_a_forced_tag_gets_the_project_prefix(sandbox, monkeypatch, tmp_path, capsys):
    home = _workspace(sandbox, monkeypatch, tmp_path, "one")
    (home / "CGSil2" / "notes.txt").write_text("one\n", encoding="utf-8")
    assert cli_main(["release", "freeze", "first"]) == 0
    (home / "CGSil2" / "notes.txt").write_text("two\n", encoding="utf-8")
    assert cli_main(["release", "freeze", "beta", "--force-tag", "beta"]) == 0
    (home / "CGSil2" / "notes.txt").write_text("three\n", encoding="utf-8")
    assert cli_main(["release", "freeze", "second"]) == 0

    tags = _run_git(sandbox["CGSil1_remote"], "tag", "--list")
    assert set(tags.split()) >= {"CGSil1-1", "CGSil1-beta", "CGSil1-2"}


def test_the_version_in_pixi_toml_names_the_release_and_cannot_be_released_twice(sandbox, monkeypatch, tmp_path, capsys):
    home = _workspace(sandbox, monkeypatch, tmp_path, "one")
    (home / "pixi.toml").write_text('[workspace]\nname = "CGSil1"\nversion = "2.0.0"\n', encoding="utf-8")
    assert cli_main(["release", "freeze", "version two"]) == 0
    assert "refs/tags/CGSil1-2.0.0" in _run_git(sandbox["CGSil1_remote"], "show-ref", "--tags")
    capsys.readouterr()

    (home / "CGSil2" / "notes.txt").write_text("more\n", encoding="utf-8")
    assert cli_main(["release", "freeze", "version two again"]) != 0
    err = capsys.readouterr().err
    assert "CGSil1-2.0.0" in err and "pixi.toml" in err and "--force-tag" in err


def test_a_second_workspace_lists_and_loads_a_release_it_never_saw(sandbox, monkeypatch, tmp_path, capsys):
    """Tutorial 2, Steps 7-8: the release is found through the project, not one disk."""
    one = _workspace(sandbox, monkeypatch, tmp_path, "one")
    _note(one, "a first note\n")
    assert cli_main(["release", "freeze", "first release of the sandbox"]) == 0

    two = _workspace(sandbox, monkeypatch, tmp_path, "two")
    _note(two, "a first note\nsomething we will regret\n")
    capsys.readouterr()

    assert cli_main(["release", "list"]) == 0
    listing = capsys.readouterr().out
    assert "CGSil1-1" in listing

    assert cli_main(["release", "load", "1"]) == 0
    assert (two / "CGSil2" / "notes.txt").read_text(encoding="utf-8") == "a first note\n"
    head = _run_git(one / "CGSil2", "rev-parse", "CGSil1-1^{commit}")
    assert _run_git(two / "CGSil2", "rev-parse", "HEAD") == head
    # Detached: load never moves a branch.
    assert "regret" in _run_git(two / "CGSil2", "show", "main:notes.txt")


def test_load_refuses_a_dirty_tree_and_an_unknown_release(sandbox, monkeypatch, tmp_path, capsys):
    one = _workspace(sandbox, monkeypatch, tmp_path, "one")
    _note(one, "a first note\n")
    assert cli_main(["release", "freeze", "first"]) == 0
    capsys.readouterr()

    assert cli_main(["release", "load", "7"]) != 0
    assert "CGSil1-7" in capsys.readouterr().err

    (one / "CGSil2" / "notes.txt").write_text("unsaved\n", encoding="utf-8")
    assert cli_main(["release", "load", "1"]) != 0
    assert "CGSil2" in capsys.readouterr().err
    assert (one / "CGSil2" / "notes.txt").read_text(encoding="utf-8") == "unsaved\n"


def test_load_into_a_new_workspace(sandbox, monkeypatch, tmp_path, capsys):
    one = _workspace(sandbox, monkeypatch, tmp_path, "one")
    _note(one, "a first note\n")
    assert cli_main(["release", "freeze", "first"]) == 0
    _note(one, "later\n")

    assert cli_main(["release", "load", "CGSil1-1", "--workspace", "r1", "--cgs-path", str(tmp_path / "r")]) == 0
    assert (tmp_path / "r" / "r1" / "CGSil2" / "notes.txt").read_text(encoding="utf-8") == "a first note\n"
    assert (one / "CGSil2" / "notes.txt").read_text(encoding="utf-8") == "later\n"


def test_freeze_release_is_gone_and_names_its_replacement(capsys):
    with pytest.raises(SystemExit):
        cli_main(["freeze-release", "v1.0", "message"])
    assert "release freeze" in capsys.readouterr().err


def test_a_release_tag_never_carries_a_private_repository(sandbox, monkeypatch, tmp_path, capsys):
    """D2: the root tag may be public; the private repositories stay in the ledger."""
    from test_tuto_cgsi1 import _cgsi1_tutorial_cgs

    cgs = tmp_path / "CGSil1-private.cgs"
    cgs.write_text(
        _cgsi1_tutorial_cgs().replace(
            '{ repository = "github:flipoyo/CGSih1", nested_config = "disabled" }',
            '{ repository = "github:flipoyo/CGSih1", nested_config = "disabled", private = true }',
        ),
        encoding="utf-8",
    )
    sandbox = dict(sandbox, cgs_path=cgs)
    home = _workspace(sandbox, monkeypatch, tmp_path, "dev")
    _note(home, "a first note\n")
    assert cli_main(["release", "freeze", "first"]) == 0

    message = _run_git(sandbox["CGSil1_remote"], "cat-file", "-p", "CGSil1-1")
    assert "CGSil2" in message
    assert "CGSih1" not in message


def test_each_source_says_where_the_release_was_read(sandbox, monkeypatch, tmp_path, capsys):
    """D1: the maker reads it from its ledger and the tag; a colleague, from the tag alone."""
    one = _workspace(sandbox, monkeypatch, tmp_path, "one")
    _note(one, "a first note\n")
    assert cli_main(["release", "freeze", "first"]) == 0
    capsys.readouterr()
    assert cli_main(["release", "list", "--json"]) == 0
    assert '"source": "ledger+tag"' in capsys.readouterr().out
    assert cli_main(["verify", "check"]) == 0  # the additive keys keep the chain whole

    _workspace(sandbox, monkeypatch, tmp_path, "two")
    capsys.readouterr()
    assert cli_main(["release", "list", "--json"]) == 0
    assert '"source": "tag"' in capsys.readouterr().out


def test_two_projects_sharing_a_repository_both_release_1_0_and_each_lists_its_own(sandbox, monkeypatch, tmp_path, capsys):
    """F5: the project's name keeps each project's releases apart in a shared repository."""
    other = tmp_path / "CGSil2-alone.cgs"
    other.write_text(
        'project = "CGSil2"\nrepos = [\n    "gitlab:CGS_test/CGSil2",\n'
        '    { repository = "github:flipoyo/CGSih1", nested_config = "disabled" },\n]\n',
        encoding="utf-8",
    )
    a = _workspace(sandbox, monkeypatch, tmp_path, "a")
    _note(a, "from A\n")
    assert cli_main(["release", "freeze", "A one", "--force-tag", "1.0"]) == 0

    b = _workspace(dict(sandbox, cgs_path=other), monkeypatch, tmp_path, "b")
    (b / "CGSih1" / "b.txt").write_text("from B\n", encoding="utf-8")
    assert cli_main(["release", "freeze", "B one", "--force-tag", "1.0"]) == 0
    capsys.readouterr()

    shared = set(_run_git(sandbox["CGSih1_remote"], "tag", "--list").split())
    assert {"CGSil1-1.0", "CGSil2-1.0"} <= shared
    assert cli_main(["release", "list"]) == 0
    listing = capsys.readouterr().out
    assert "CGSil2-1.0" in listing and "CGSil1-1.0" not in listing


def test_a_read_only_repository_is_put_back_at_its_recorded_commit(sandbox, monkeypatch, tmp_path, capsys):
    """It carries no tag (nothing may push to it), but the State records its commit."""
    from test_tuto_cgsi1 import _cgsi1_tutorial_cgs

    cgs = tmp_path / "CGSil1-private.cgs"
    cgs.write_text(
        _cgsi1_tutorial_cgs().replace(
            '{ repository = "github:flipoyo/CGSih1", nested_config = "disabled" }',
            '{ repository = "github:flipoyo/CGSih1", nested_config = "disabled", private = true }',
        ),
        encoding="utf-8",
    )
    home = _workspace(dict(sandbox, cgs_path=cgs), monkeypatch, tmp_path, "dev")
    _note(home, "a first note\n")
    recorded = _run_git(home / "CGSih1", "rev-parse", "HEAD")
    assert cli_main(["release", "freeze", "first"]) == 0
    (home / "CGSih1" / "later.txt").write_text("later\n", encoding="utf-8")
    _run_git(home / "CGSih1", "add", "later.txt")
    _run_git(home / "CGSih1", "commit", "-m", "a later commit, made by hand")

    assert cli_main(["release", "load", "1"]) == 0

    assert _run_git(home / "CGSih1", "rev-parse", "HEAD") == recorded
    assert _run_git(home / "CGSih1", "tag", "--list") == ""


def test_a_tag_with_no_state_is_listed_as_tag_only_and_cannot_be_loaded(sandbox, monkeypatch, tmp_path, capsys):
    home = _workspace(sandbox, monkeypatch, tmp_path, "one")
    _run_git(home, "tag", "CGSil1-old")
    _run_git(home, "push", "origin", "CGSil1-old")
    capsys.readouterr()

    assert cli_main(["release", "list"]) == 0
    assert "tag only" in capsys.readouterr().out
    assert cli_main(["release", "load", "old"]) != 0
    assert "--ref-kind tag" in capsys.readouterr().err


def test_a_repository_git_cannot_move_puts_every_other_one_back(sandbox, monkeypatch, tmp_path, capsys):
    """Refused before anything is left moved: an untracked file the release would overwrite."""
    home = _workspace(sandbox, monkeypatch, tmp_path, "one")
    _note(home, "a first note\n")
    assert cli_main(["release", "freeze", "first"]) == 0
    _run_git(home / "CGSil2", "rm", "-q", "notes.txt")
    (home / "CGSih1" / "later.txt").write_text("later\n", encoding="utf-8")
    (home / "later.txt").write_text("later\n", encoding="utf-8")
    assert cli_main(["add"]) == 0
    assert cli_main(["commit", "after the release"]) == 0
    (home / "CGSil2" / "notes.txt").write_text("untracked, in the way\n", encoding="utf-8")
    before = {repo: _run_git(home / repo, "rev-parse", "HEAD") for repo in (".", "CGSil2", "CGSih1")}
    capsys.readouterr()

    assert cli_main(["release", "load", "1"]) != 0

    assert "back where it was" in capsys.readouterr().err
    for repo, sha in before.items():
        assert _run_git(home / repo, "rev-parse", "HEAD") == sha
        assert _run_git(home / repo, "branch", "--show-current") == "main"


def _released_then_loaded(sandbox, monkeypatch, tmp_path) -> Path:
    """A workspace that released ``CGSil1-1``, moved on, and loaded the release in place."""
    home = _workspace(sandbox, monkeypatch, tmp_path, "one")
    _note(home, "a first note\n")
    assert cli_main(["release", "freeze", "first"]) == 0
    _note(home, "later\n")
    assert cli_main(["release", "load", "1"]) == 0
    return home


def _commit_by_hand(repo: Path, text: str) -> str:
    """A commit made with plain Git on a detached ``HEAD``: ``cgitsync commit`` refuses one."""
    (repo / "notes.txt").write_text(text, encoding="utf-8")
    _run_git(repo, "add", "notes.txt")
    _run_git(repo, "commit", "-m", "made while detached")
    return _run_git(repo, "rev-parse", "HEAD")


def test_checkout_a_branch_re_attaches_a_loaded_release(sandbox, monkeypatch, tmp_path, capsys):
    home = _released_then_loaded(sandbox, monkeypatch, tmp_path)
    assert _run_git(home / "CGSil2", "branch", "--show-current") == ""

    assert cli_main(["checkout", "main"]) == 0

    assert _run_git(home / "CGSil2", "branch", "--show-current") == "main"
    assert (home / "CGSil2" / "notes.txt").read_text(encoding="utf-8") == "later\n"
    capsys.readouterr()
    assert cli_main(["status"]) == 0
    assert "errors=0" in capsys.readouterr().out


def test_a_branch_made_from_a_loaded_release_keeps_a_commit_made_while_detached(sandbox, monkeypatch, tmp_path, capsys):
    home = _released_then_loaded(sandbox, monkeypatch, tmp_path)
    release = _run_git(home / "CGSil2", "rev-parse", "HEAD")
    _commit_by_hand(home / "CGSil2", "a fix to the release\n")

    assert cli_main(["branch", "create", "fix"]) == 0
    assert cli_main(["checkout", "fix"]) == 0

    assert _run_git(home / "CGSil2", "branch", "--show-current") == "fix"
    assert _run_git(home / "CGSil2", "rev-parse", "fix~1") == release
    assert (home / "CGSil2" / "notes.txt").read_text(encoding="utf-8") == "a fix to the release\n"
    assert _run_git(home / "CGSil2", "show", "main:notes.txt") == "later"
    assert cli_main(["push"]) == 0
    assert _run_git(sandbox["CGSil2_remote"], "show", "fix:notes.txt") == "a fix to the release"
    assert _run_git(sandbox["CGSil2_remote"], "show", "main:notes.txt") == "later"


def test_checkout_refuses_to_leave_a_commit_made_while_detached(sandbox, monkeypatch, tmp_path, capsys):
    home = _released_then_loaded(sandbox, monkeypatch, tmp_path)
    kept = _commit_by_hand(home / "CGSil2", "a fix to the release\n")
    capsys.readouterr()

    assert cli_main(["checkout", "main"]) != 0

    error = capsys.readouterr().err
    assert "CGSil2" in error and kept[:12] in error and "branch create" in error
    assert _run_git(home / "CGSil2", "rev-parse", "HEAD") == kept
    assert _run_git(home / "CGSil2", "branch", "--show-current") == ""
    assert cli_main(["branch", "create", "fix"]) == 0
    assert cli_main(["checkout", "fix"]) == 0


def test_the_freeze_message_is_in_the_root_tag_and_the_tag_reads_like_a_gts(sandbox, monkeypatch, tmp_path, capsys):
    import tomllib

    home = _workspace(sandbox, monkeypatch, tmp_path, "one")
    _note(home, "a first note\n")
    assert cli_main(["release", "freeze", "first release of the sandbox"]) == 0
    capsys.readouterr()

    tag = _run_git(sandbox["CGSil1_remote"], "cat-file", "-p", "CGSil1-1")
    assert "CGSil1-1: first release of the sandbox" in tag
    body = tag[tag.index("[cgitsync_release]") :]
    document = tomllib.loads(body)
    assert document["cgitsync_release"]["message"] == "first release of the sandbox"
    assert {"document", "project", "tree_state", "repo_state"} <= set(document)
    assert max(len(line) for line in tag.splitlines()) < 200
    from ComplexGitSync.gts_document import GtsDocument

    state = {key: value for key, value in document.items() if key != "cgitsync_release"}
    assert GtsDocument.from_dict(state).ensure_snapshot_hash() == state["document"]["snapshot_hash"]
    assert cli_main(["release", "list", "--json"]) == 0
    assert '"message": "first release of the sandbox"' in capsys.readouterr().out


def test_a_freeze_that_commits_nothing_still_writes_its_message_into_the_tag(sandbox, monkeypatch, tmp_path):
    home = _workspace(sandbox, monkeypatch, tmp_path, "one")
    _note(home, "a first note\n")
    assert cli_main(["release", "freeze", "nothing new here"]) == 0
    assert "CGSil1-1: nothing new here" in _run_git(sandbox["CGSil1_remote"], "cat-file", "-p", "CGSil1-1")


def test_a_tag_in_the_6_0_1_layout_still_loads(sandbox, monkeypatch, tmp_path):
    import tomllib

    import tomli_w

    one = _workspace(sandbox, monkeypatch, tmp_path, "one")
    _note(one, "a first note\n")
    assert cli_main(["release", "freeze", "first"]) == 0
    _note(one, "later\n")
    # Rewrite the release tag the way 6.0.1 wrote it: the State as one escaped string.
    old = _run_git(one, "cat-file", "-p", "CGSil1-1")
    document = tomllib.loads(old[old.index("[cgitsync_release]") :])
    table = document.pop("cgitsync_release")
    table.pop("message")
    table["state"] = tomli_w.dumps(document)
    legacy = "CGSil1-1: release of CGSil1\n\n" + tomli_w.dumps({"cgitsync_release": table})
    _run_git(one, "tag", "-f", "-a", "--cleanup=verbatim", "-m", legacy, "CGSil1-1", "CGSil1-1^{commit}")
    _run_git(one, "push", "--force", "origin", "CGSil1-1")
    # A second workspace never saw the release: its State can only come from the tag.
    two = _workspace(sandbox, monkeypatch, tmp_path, "two")
    assert cli_main(["release", "load", "1"]) == 0
    assert (two / "CGSil2" / "notes.txt").read_text(encoding="utf-8") == "a first note\n"


def test_dry_run_shows_the_subject_the_tag_will_carry(sandbox, monkeypatch, tmp_path, capsys):
    _workspace(sandbox, monkeypatch, tmp_path, "one")
    capsys.readouterr()
    assert cli_main(["release", "freeze", "a release\nwith a second line", "--dry-run"]) == 0
    assert "tag_subject=CGSil1-1: a release" in capsys.readouterr().out


def test_load_into_a_workspace_prints_the_export_line_bootstrap_prints(sandbox, monkeypatch, tmp_path, capsys):
    one = _workspace(sandbox, monkeypatch, tmp_path, "one")
    _note(one, "a first note\n")
    assert cli_main(["release", "freeze", "first"]) == 0
    capsys.readouterr()

    assert cli_main(["release", "load", "1", "--workspace", "r1", "--cgs-path", str(tmp_path / "r")]) == 0

    out = capsys.readouterr().out
    assert f"export CGSHOME={(tmp_path / 'r' / 'r1').as_posix()}" in out and "To use this workspace, run:" in out


def test_load_in_place_clones_a_repository_the_tree_lacks_and_says_how_to_leave(sandbox, monkeypatch, tmp_path, capsys):
    import shutil

    one = _workspace(sandbox, monkeypatch, tmp_path, "one")
    _note(one, "a first note\n")
    assert cli_main(["release", "freeze", "first"]) == 0
    _note(one, "later\n")
    shutil.rmtree(one / "CGSih1", onerror=_clear_readonly)
    capsys.readouterr()

    assert cli_main(["release", "load", "1"]) == 0

    out = capsys.readouterr().out
    assert "Cloned, being missing: CGSih1" in out
    assert "cgitsync checkout main" in out and "branch create" in out
    for name in ("CGSih1", "CGSil2"):
        assert _run_git(one / name, "branch", "--show-current") == ""
    assert _run_git(one / "CGSih1", "rev-parse", "HEAD") == _run_git(sandbox["CGSih1_remote"], "rev-parse", "CGSil1-1^{commit}")
    assert (one / "CGSil2" / "notes.txt").read_text(encoding="utf-8") == "a first note\n"
    assert cli_main(["checkout", "main"]) == 0
    assert _run_git(one / "CGSih1", "branch", "--show-current") == "main"

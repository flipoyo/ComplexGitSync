"""``cgitsync release freeze | list | load`` on the CGSil1 sandbox.

Backs the ReleaseCommand ticket. The sandbox declares no memory, so it is a
USER tree whose ledger never leaves the disk: a release another workspace
made can only be found through the project itself, which is the case
Tutorial 2 now teaches.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_tuto_cgsi1 import (  # noqa: F401  (cgsi1_sandbox is a fixture)
    _patch_git_identity,
    _patch_remote_urls,
    _run_git,
    cgsi1_sandbox,
)

from ComplexGitSync.cli import main as cli_main


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

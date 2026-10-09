"""The pure parts of a release: its version, its tag, and the message its root tag carries.

Backs the ReleaseCommand ticket (§4 and D2). The end-to-end paths are in
``tests/integration/test_release_command.py``.
"""

from __future__ import annotations

import pytest

from ComplexGitSync.git_repo import WorkingRepo
from ComplexGitSync.git_tree import WorkingGitTree
from ComplexGitSync.orchestre.release_commands import ReleaseCommands
from ComplexGitSync.project_version import ProjectVersion


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        ('[workspace]\nname = "P"\nversion = "1.2.3"\n', "1.2.3"),
        ('[project]\nname = "P"\nversion = "2026.07"\n', "2026.07"),  # a build version, as written
        ('[workspace]\nversion = "1.0.0"\n[project]\nversion = "9.9.9"\n', "1.0.0"),  # [workspace] first
        ('[workspace]\nname = "P"\n', None),
        ('[workspace]\nversion = "  "\n', None),
        ("this is [not toml", None),
    ],
)
def test_the_version_is_read_from_pixi_toml_as_written(tmp_path, manifest, expected):
    (tmp_path / "pixi.toml").write_text(manifest)
    assert ProjectVersion.read(tmp_path) == expected


def test_no_manifest_means_no_version(tmp_path):
    assert ProjectVersion.read(tmp_path) is None


def test_a_forced_tag_comes_first_and_is_prefixed_once():
    assert ReleaseCommands.suffix("P", force_tag="beta", version="1.0", tags=[]) == ("beta", "--force-tag")
    assert ReleaseCommands.suffix("P", force_tag="P-beta", version=None, tags=[]) == ("beta", "--force-tag")


def test_the_version_comes_next():
    assert ReleaseCommands.suffix("P", force_tag=None, version="2.0.0", tags=["P-7"]) == ("2.0.0", "pixi.toml")


def test_without_a_version_the_next_number_follows_the_highest():
    tags = ["P-1", "P-3", "P-beta", "P-2.0.0", "Q-9", "v1.0"]
    assert ReleaseCommands.suffix("P", force_tag=None, version=None, tags=tags) == ("4", "next number")
    assert ReleaseCommands.suffix("P", force_tag=None, version=None, tags=[]) == ("1", "next number")


def test_the_annotation_is_read_back_and_any_other_message_is_not():
    message = 'P-1: release of P\n\n[cgitsync_release]\nproject = "P"\ntag = "P-1"\nstate = """\n[document]\n"""\n'
    assert ReleaseCommands.parse_annotation(message)["project"] == "P"
    assert ReleaseCommands.parse_annotation("an ordinary tag message") == {}
    assert ReleaseCommands.parse_annotation("[cgitsync_release]\nthis is [not toml") == {}


def test_the_project_tree_drops_every_private_repository(tmp_path):
    tree = WorkingGitTree()
    tree.add(WorkingRepo(repo_id="root", name="root", absolute_path=tmp_path))
    tree.add(WorkingRepo(repo_id="lib", name="lib", absolute_path=tmp_path / "lib", parent_id="root"))
    tree.add(WorkingRepo(repo_id="spec", name="spec", absolute_path=tmp_path / "spec", parent_id="root", private=True))

    kept = ReleaseCommands.project_tree(tree)

    assert set(kept.repos) == {"root", "lib"}
    assert set(tree.repos) == {"root", "lib", "spec"}  # the live tree is untouched


def test_ledger_and_tag_times_share_one_utc_form_so_they_sort_together():
    assert ReleaseCommands.utc("2026-10-09T10:40:15+02:00") == "2026-10-09T08:40:15Z"
    assert ReleaseCommands.utc("2026-10-09T08:40:16Z") == "2026-10-09T08:40:16Z"
    assert ReleaseCommands.utc("not a time") == "not a time"

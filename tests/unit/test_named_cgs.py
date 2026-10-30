"""A `yyyy-xxx.cgs` file's name is a contract (BeforeCGS-BETA §7): AD1 refused by name, AD2 cut at the last `-`."""

from __future__ import annotations

from pathlib import Path

import pytest

from ComplexGitSync.errors import ConfigValidationError
from ComplexGitSync.tree.cgs_format import CgsDocument
from ComplexGitSync.tree.named_cgs import FALLBACK, NamedCgs

_REPOS = '\n[[repos]]\ngitprovider = "github"\nproject_owner_name = "o"\nproject_name = "gx4g"\nrelative_path = "."\n'


def _write(tmp_path, name: str, project: str) -> Path:
    path = tmp_path / name
    path.write_text(f"{project}\n{_REPOS}", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("name", "parsed"),
    [("gx4g-dev.cgs", ("gx4g", "dev")), ("my-long-name-opx.cgs", ("my-long-name", "opx")), ("molonari-light.cgs", None),
     ("install.cgs", None), ("complexgitsync-dev.cgs", ("complexgitsync", "dev")), ("a-ab.cgs", None),
     ("gx4g-v9.cgs", None), ("gx4g-v10.cgs", None), ("gx4g-v99.cgs", None), ("gx4g-v100.cgs", None), ("gx4g-vxy.cgs", ("gx4g", "vxy"))],
)
def test_the_name_is_cut_at_the_last_dash_and_xxx_is_exactly_three_characters(name, parsed):
    assert NamedCgs.parse(name) == parsed


def test_a_file_that_follows_its_name_is_read(tmp_path):
    path = _write(tmp_path, "gx4g-dev.cgs", f'project = {{ name = "gx4g", default_branch = "dev", fallback_branch = "{FALLBACK}" }}')

    document = CgsDocument.from_toml(path)

    assert document.project_name == "gx4g"
    assert {repo["fallback_branch"] for repo in document.repos} == {"CGS-BETA"}
    assert {repo["default_branch"] for repo in document.repos} == {"dev"}


@pytest.mark.parametrize(
    "project",
    ['project = { name = "gx4g", default_branch = "main" }', 'project = { name = "gx4g", default_branch = "dev" }', 'project = "gx4g"'],
)
def test_a_file_that_contradicts_its_name_is_refused_by_name_with_the_line_it_expects(tmp_path, project):
    path = _write(tmp_path, "gx4g-dev.cgs", project)

    with pytest.raises(ConfigValidationError, match=r'default_branch = "dev", fallback_branch = "CGS-BETA"'):
        CgsDocument.from_toml(path)


def test_a_file_whose_yyyy_is_not_its_project_is_not_concerned(tmp_path):
    path = _write(tmp_path, "gx4g-dev.cgs", 'project = { name = "other", default_branch = "main" }')

    assert CgsDocument.from_toml(path).project_name == "other"


def test_a_project_without_a_fallback_keeps_main_as_it_always_did(tmp_path):
    path = _write(tmp_path, "plain.cgs", 'project = { name = "gx4g", default_branch = "main" }')

    assert {repo["fallback_branch"] for repo in CgsDocument.from_toml(path).repos} == {"main"}


def test_the_checked_in_examples_follow_the_rule():
    examples = Path(__file__).resolve().parents[2] / "examples"

    for path in sorted(examples.glob("*.cgs")):
        CgsDocument.from_toml(path)  # refused by name if it were concerned and wrong
    assert NamedCgs.parse(examples / "gx4g-dev.cgs") == ("gx4g", "dev")
    for level in ("opx", "dev", "usr", "pub"):
        assert NamedCgs.parse(examples / f"complexgitsync-{level}.cgs") == ("complexgitsync", level)


def test_a_repository_without_the_projects_fallback_branch_falls_to_main(tmp_path):
    """AD3: nobody creates CGS-BETA yet, so the chain is the target, then CGS-BETA, then main."""
    from types import SimpleNamespace

    from ComplexGitSync.git_repo.git_repo import RefKind
    from ComplexGitSync.orchestre import ComplexGitSyncClient

    class Remote:
        def __init__(self, held):
            self.held = held

        def remote_branch_exists(self, _url, branch):
            return branch in self.held

    client = ComplexGitSyncClient(git_runner=Remote({"main"}))
    entry = SimpleNamespace(target_ref_kind=RefKind.BRANCH, target_ref_name="dev", default_branch="dev", fallback_branch="CGS-BETA",
                            effective_private=False, effective_writable=False, name="r")

    assert client._select_clone_ref(entry, "url") == ("main", RefKind.BRANCH)
    client.git_runner = Remote({"CGS-BETA", "main"})
    assert client._select_clone_ref(entry, "url") == ("CGS-BETA", RefKind.BRANCH)
    client.git_runner = Remote({"dev", "main"})
    assert client._select_clone_ref(entry, "url") == ("dev", RefKind.BRANCH)

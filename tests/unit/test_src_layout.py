"""The layout SrcLayout gave ``src/ComplexGitSync/``: what each folder may import, and the old paths.

- ``git_repo/`` is the dependency gx4g will share, so it imports nothing from the rest of the package;
- every module sits in a folder, or among the few that stay above them;
- the paths the move changed still answer for one release, with the *same* module behind both.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

import ComplexGitSync
from ComplexGitSync import legacy_paths
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_repo.git_runner import GitRunner as RepoGitRunner
from ComplexGitSync.git_repo.injected import GitRepoError
from ComplexGitSync.tree.git_runner import GitRunner

PACKAGE = Path(ComplexGitSync.__file__).parent
GIT_REPO = PACKAGE / "git_repo"

#: What stays at the top, above the folders: the entry points and what every folder may use.
ABOVE = {
    "__init__.py", "__main__.py", "errors.py", "json_render.py", "legacy_paths.py",
    "status_render.py", "universal_clock.py",
}
FOLDERS = {"cli", "git_repo", "merkle", "omniscience", "operating_space", "orchestre", "tree"}


def test_git_repo_imports_nothing_of_the_rest_of_the_package():
    siblings = {path.stem for path in GIT_REPO.glob("*.py")}
    imported: list[str] = []
    for path in sorted(GIT_REPO.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                outside = node.level > 1 or (node.level == 1 and node.module not in siblings)
                if outside or (node.level == 0 and (node.module or "").split(".")[0] == "ComplexGitSync"):
                    imported.append(f"{path.name}: from {'.' * node.level}{node.module or ''}")
            elif isinstance(node, ast.Import):
                imported += [
                    f"{path.name}: import {alias.name}"
                    for alias in node.names
                    if alias.name.split(".")[0] == "ComplexGitSync"
                ]

    assert imported == []


def test_every_module_sits_in_a_folder_or_among_the_few_above_them():
    top = {path.name for path in PACKAGE.glob("*.py")}
    folders = {path.name for path in PACKAGE.iterdir() if path.is_dir() and path.name != "__pycache__"}

    assert top == ABOVE
    assert folders == FOLDERS


def test_a_bare_runner_raises_its_own_error_and_the_cgitsync_runner_raises_gitsyncerror(tmp_path):
    missing = tmp_path / "absent"

    with pytest.raises(GitRepoError) as bare:
        RepoGitRunner().rev_parse_head(missing)
    with pytest.raises(GitSyncError):
        GitRunner().rev_parse_head(missing)

    assert not isinstance(bare.value, GitSyncError)


def test_the_runner_scratches_where_it_is_told_to(tmp_path):
    seen: list[str] = []

    def scratch(prefix: str):
        seen.append(prefix)
        return RepoGitRunner().scratch(prefix)

    runner = RepoGitRunner(scratch=scratch)
    with runner.scratch("probe-") as directory:
        assert Path(directory).is_dir()

    assert seen == ["probe-"]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("memory.store", "tree.memory.store"),
        ("operations.push", "tree.operations.push"),
        ("autofix.base", "tree.autofix.base"),
        ("git_branch", "git_repo.git_branch"),
        ("git_runner", "tree.git_runner"),
        ("global_index", "omniscience.global_index"),
        ("orchestre.omniscience_view", "omniscience.omniscience_view"),
        ("registry", "tree.registry"),
    ],
)
def test_an_old_import_path_gives_the_module_that_moved(old, new):
    legacy = importlib.import_module(f"ComplexGitSync.{old}")
    moved = importlib.import_module(f"ComplexGitSync.{new}")

    assert legacy is moved
    assert moved.__spec__.name == f"ComplexGitSync.{new}"


def test_the_packages_that_took_a_module_s_name_re_export_what_it_held():
    from ComplexGitSync.git_repo import GitRepo, RepoScope
    from ComplexGitSync.operating_space import OperatingSpace

    assert GitRepo.__module__ == "ComplexGitSync.git_repo.git_repo"
    assert RepoScope and OperatingSpace


def test_a_path_that_never_moved_is_left_to_python():
    assert legacy_paths.new_path("orchestre.client") is None
    assert legacy_paths.new_path("merkle.merkle_root") is None
    assert legacy_paths.new_path("git_repo.git_runner") is None

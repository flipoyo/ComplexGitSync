"""`scripts/check_build_version.py` — a build bump must come with a version bump."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_build_version.py"
_spec = importlib.util.spec_from_file_location("check_build_version", SCRIPT)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    (repo / "src" / "ComplexGitSync").mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@e.st")
    _git(repo, "config", "user.name", "T")
    _write(repo, "0003.01", "1.0.0")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo


def _write(repo: Path, build: str, version: str) -> None:
    (repo / check.INIT_PATH).write_text(f'__version__ = "{version}"\n__build__ = "{build}"\n', encoding="utf-8")


def _commit(repo: Path, build: str, version: str) -> None:
    _write(repo, build, version)
    _git(repo, "commit", "-am", f"{build} {version}")


def test_a_build_bump_with_a_patch_bump_passes(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "0003.02", "1.0.1")

    assert check.violations(repo, "HEAD~1..HEAD") == []


def test_a_build_bump_alone_is_reported(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "0003.02", "1.0.0")

    found = check.violations(repo, "HEAD~1..HEAD")

    assert len(found) == 1 and "0003.01 -> 0003.02" in found[0]


def test_a_version_that_goes_down_is_reported(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "0003.02", "0.9.9")

    assert check.violations(repo, "HEAD~1..HEAD")


def test_a_release_with_no_build_change_passes(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "0003.01", "1.1.0")

    assert check.violations(repo, "HEAD~1..HEAD") == []



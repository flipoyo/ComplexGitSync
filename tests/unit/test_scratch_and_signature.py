"""A scratch directory under a parent of the caller's choosing, and Git's verdict on a commit's signature."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ComplexGitSync.git_runner import GitRunner
from ComplexGitSync.universal_clock import SystemClock


def test_a_scratch_directory_is_made_under_the_parent_asked_for_and_removed_on_exit(tmp_path):
    with SystemClock.scratch_directory("probe-", str(tmp_path)) as scratch:
        made = Path(scratch)
        assert made.parent == tmp_path and made.name.startswith("probe-") and made.is_dir()

    assert not made.exists()


def test_a_scratch_directory_without_a_parent_is_made_where_the_os_keeps_temporary_files():
    with SystemClock.scratch_directory("probe-") as scratch:
        assert Path(scratch).is_dir()


def _commit(repo: Path) -> str:
    env = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.org", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "one"], check=True, env=env)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def test_an_unsigned_commit_is_N_and_so_is_one_git_cannot_find(tmp_path):
    sha = _commit(tmp_path)
    runner = GitRunner()

    assert runner.commit_signature(tmp_path, sha) == "N"
    assert runner.commit_signature(tmp_path, "0" * 40) == "N"

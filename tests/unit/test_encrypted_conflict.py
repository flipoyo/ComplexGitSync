"""EncryptedConflict: a conflict on a git-crypt file is named as such, locked or unlocked (EncryptedMerge E3).

Classification reads only ``.gitattributes`` through ``git check-attr``, so it runs on a real repository where the
``git-crypt`` program is not installed: a path is encrypted when its attributes say so, whatever the clone holds.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ComplexGitSync.git_repo.git_runner import GitRunner
from ComplexGitSync.tree.operations.encrypted_conflict import EncryptedConflict


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@example.test", "-c", "user.name=Tester", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".gitattributes").write_text("data/** filter=git-crypt diff=git-crypt\n", encoding="utf-8")
    return tmp_path


def test_a_conflicting_path_under_an_encrypted_pattern_is_encrypted_whatever_it_holds(tmp_path):
    root = _repo(tmp_path)
    (root / "data").mkdir()
    (root / "data/amounts.csv").write_text("plain text, as an unlocked clone shows it\n", encoding="utf-8")
    runner = GitRunner()

    found = EncryptedConflict.paths(runner, root, (Path("data/amounts.csv"), Path("src/code.py")))

    assert found == (Path("data/amounts.csv"),)


def test_no_encrypted_path_when_the_repository_asks_for_none(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")

    assert EncryptedConflict.paths(GitRunner(), tmp_path, (Path("data/amounts.csv"),)) == ()


def test_an_encrypted_file_says_how_to_choose_a_side_and_a_binary_one_does_not():
    notes = EncryptedConflict.notes((Path("data/x.csv"), Path("logo.png")), (Path("data/x.csv"),), "feature")

    assert "git checkout --ours -- data/x.csv" in notes[0] and "git checkout --theirs -- data/x.csv" in notes[0]
    assert "git add data/x.csv" in notes[0] and "cgitsync merge --resolve feature" in notes[0] and "no merge tool" in notes[0]
    assert notes[1].startswith("binary file logo.png") and "git checkout" not in notes[1]


def test_nothing_to_say_without_a_binary_conflict():
    assert EncryptedConflict.notes((), (), "feature") == []

"""GitCrypt and its guard, with a runner that answers from a script: what each git question returns decides the verdict.

The real repositories are in ``tests/integration/test_encrypted_repos.py``; here only the reading of git's answers is
tested, so it runs where ``git-crypt`` is not installed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ComplexGitSync.git_repo.git_crypt import GitCrypt
from ComplexGitSync.orchestre.status_crypt import CRYPT_LEGEND, with_crypt_column

NUL = "\0"


class _Runner:
    """Answers ``git`` questions from a dict keyed by the first argument; anything else fails (non-zero)."""

    def __init__(self, answers: dict[str, str], blobs: dict[str, bytes] | None = None, upstream: str | None = None):
        self.answers = answers
        self.blobs = blobs or {}
        self.upstream = upstream
        self.asked: list[tuple[str, ...]] = []

    def _query(self, *args, cwd=None):
        self.asked.append(args)
        if args[0] in self.answers:
            return SimpleNamespace(returncode=0, stdout=self.answers[args[0]])
        return SimpleNamespace(returncode=1, stdout="")

    def _query_bytes(self, *args, cwd=None):
        blob = self.blobs.get(args[-1])
        return SimpleNamespace(returncode=0 if blob is not None else 1, stdout=blob or b"")

    def upstream_ref(self, repo_path):
        return self.upstream


def _attrs(**values: str) -> str:
    return "".join(f"{path}{NUL}filter{NUL}{value}{NUL}" for path, value in values.items())


def _raw(*changes: tuple[str, str]) -> str:
    return "".join(f":100644 100644 {'0' * 40} {blob} A{NUL}{path}{NUL}" for path, blob in changes)


def test_a_path_is_attributed_only_when_its_filter_is_git_crypt():
    runner = _Runner({"check-attr": _attrs(**{"data/a.csv": "git-crypt", "README.md": "unspecified", "x.bin": "lfs"})})

    assert GitCrypt(runner).attributed(".", ["data/a.csv", "README.md", "x.bin"]) == ["data/a.csv"]


def test_a_repository_is_not_encrypted_when_no_tracked_path_asks_for_it():
    runner = _Runner({"ls-files": f"README.md{NUL}", "check-attr": _attrs(**{"README.md": "unspecified"})})

    assert GitCrypt(runner).state(".") is None


@pytest.mark.parametrize(("configured", "expected"), [(True, "unlocked"), (False, "locked")])
def test_an_encrypted_repository_is_unlocked_when_its_clone_configures_the_filter(configured, expected):
    answers = {"ls-files": f"data/a.csv{NUL}", "check-attr": _attrs(**{"data/a.csv": "git-crypt"})}
    if configured:
        answers["config"] = "git-crypt smudge\n"

    assert GitCrypt(_Runner(answers)).state(".") == expected


def test_a_staged_blob_without_the_magic_is_plaintext_and_one_with_it_is_not():
    runner = _Runner(
        {"diff": _raw(("data/clear.csv", "c" * 40), ("data/sealed.csv", "e" * 40), ("data/empty.csv", "f" * 40), ("notes.md", "a" * 40)),
         "check-attr": _attrs(**{"data/clear.csv": "git-crypt", "data/sealed.csv": "git-crypt", "data/empty.csv": "git-crypt", "notes.md": "unspecified"})},
        blobs={"c" * 40: b"1,2\n", "e" * 40: GitCrypt.MAGIC + b"ciphertext", "f" * 40: b"", "a" * 40: b"in clear"},
    )

    assert GitCrypt(runner).staged_plaintext(".") == ["data/clear.csv"]


def test_a_push_reads_what_the_upstream_lacks_and_without_an_upstream_what_no_remote_holds():
    answers = {"log": _raw(("data/a.csv", "c" * 40)), "check-attr": _attrs(**{"data/a.csv": "git-crypt"})}
    with_upstream = _Runner(answers, blobs={"c" * 40: b"clear"}, upstream="origin/main")
    without = _Runner(answers, blobs={"c" * 40: b"clear"})

    assert GitCrypt(with_upstream).pushed_plaintext(".") == GitCrypt(without).pushed_plaintext(".") == ["data/a.csv"]
    assert "@{upstream}" in next(a for a in with_upstream.asked if a[0] == "log")
    assert "--remotes" in next(a for a in without.asked if a[0] == "log")


def test_a_git_failure_is_never_a_refusal():
    runner = _Runner({})

    assert GitCrypt(runner).staged_plaintext(".") == []
    assert GitCrypt(runner).pushed_plaintext(".") == []
    assert GitCrypt(runner).state(".") is None


def test_the_crypt_column_is_added_only_when_a_repository_is_encrypted():
    table = "REPOSITORY  PATH\n-----------------\nroot        .\ndata        d"

    assert with_crypt_column(table, [None, None]) == table
    assert with_crypt_column(table, [None, "locked"]).split("\n") == [
        "REPOSITORY  PATH  CRYPT",
        "-----------------  ------",
        "root        .  -",
        "data        d  locked",
    ]


def test_the_legend_says_that_cgitsync_never_unlocks():
    assert "never unlocks" in CRYPT_LEGEND


def test_the_message_of_a_refusal_is_cut_after_five_paths():
    from ComplexGitSync.tree.operations.encryption_guard import EncryptionGuard

    message = EncryptionGuard._message([f"data/{n}.csv" for n in range(8)], staged=True)

    assert "data/4.csv and 3 more" in message
    assert "data/5.csv" not in message

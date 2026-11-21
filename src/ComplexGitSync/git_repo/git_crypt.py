"""git-crypt, as cgitsync sees it: which files are meant to be encrypted, and whether they are.

Ring: 2 (reads a repository through :class:`GitRunner`; writes nothing)
Contract: cgitsync never unlocks, never handles a key and never runs
    ``git-crypt`` (EncryptedRepos §2). It reads three facts from Git alone:
    the ``filter`` attribute of a path (``.gitattributes``), whether the clone
    has the filter configured (the clone is *unlocked*), and the first bytes
    of a stored blob. A git-crypt blob begins with ``\\0GITCRYPT``; a blob
    under an encrypted path that does not is plaintext, which is what a
    tree that was never unlocked commits for a new file.
Imports: git_runner

The filter's name is one constant, :attr:`GitCrypt.FILTER`, so another tool
could be recognised later (EncryptedRepos §5 Q2: git-crypt only, for now).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .git_runner import GitRunner


class GitCrypt:
    """Questions about the encrypted files of one repository at a time; read-only."""

    #: The ``filter`` attribute a path carries when it is meant to be encrypted.
    FILTER = "git-crypt"

    #: What every blob git-crypt writes begins with.
    MAGIC = b"\0GITCRYPT"

    #: ``state`` of a repository that has encrypted paths and the filter configured.
    UNLOCKED = "unlocked"

    #: ``state`` of a repository that has encrypted paths and no filter: nothing is decrypted, nothing is encrypted.
    LOCKED = "locked"

    #: Paths per ``git check-attr`` call, so a large tree never overruns the command line.
    _CHUNK = 200

    def __init__(self, runner: GitRunner) -> None:
        self.runner = runner

    def state(self, repo_path: Path | str) -> str | None:
        """:attr:`LOCKED` or :attr:`UNLOCKED` for a repository with an encrypted path, else ``None``."""
        tracked = self.runner._query("ls-files", "-z", cwd=repo_path)
        if tracked.returncode != 0 or not self.attributed(repo_path, self._split(tracked.stdout)):
            return None
        return self.UNLOCKED if self._filter_configured(repo_path) else self.LOCKED

    def attributed(self, repo_path: Path | str, paths: Iterable[str]) -> list[str]:
        """The *paths* whose ``filter`` attribute is :attr:`FILTER`, in the order given."""
        wanted = list(paths)
        found: set[str] = set()
        for start in range(0, len(wanted), self._CHUNK):
            chunk = wanted[start : start + self._CHUNK]
            answer = self.runner._query("check-attr", "-z", "filter", "--", *chunk, cwd=repo_path)
            if answer.returncode != 0:
                continue
            fields = self._split(answer.stdout)
            # path, attribute, value — three fields per answer.
            for index in range(0, len(fields) - 2, 3):
                if fields[index + 2] == self.FILTER:
                    found.add(fields[index])
        return [path for path in wanted if path in found]

    def staged_plaintext(self, repo_path: Path | str) -> list[str]:
        """Staged paths that are meant to be encrypted and whose staged blob is not, sorted."""
        raw = self.runner._query("diff", "--cached", "--raw", "-z", "--no-renames", "--diff-filter=AM", cwd=repo_path)
        return self._plaintext(repo_path, self._blobs(raw.stdout) if raw.returncode == 0 else {})

    def pushed_plaintext(self, repo_path: Path | str) -> list[str]:
        """Paths meant to be encrypted that a push would send in clear, sorted.

        Every commit the push adds is read, not only the tip: a plaintext blob
        that a later commit re-encrypted is still in the history that is sent.
        The range is what the upstream lacks, or, with no upstream, what no
        remote-tracking branch holds.
        """
        if self.runner.upstream_ref(repo_path) is not None:
            bounds = ["HEAD", "--not", "@{upstream}"]
        else:
            bounds = ["HEAD", "--not", "--remotes"]
        raw = self.runner._query("log", "--raw", "-z", "--no-renames", "--diff-filter=AM", "--format=", *bounds, cwd=repo_path)
        return self._plaintext(repo_path, self._blobs(raw.stdout) if raw.returncode == 0 else {})

    def _plaintext(self, repo_path: Path | str, blobs: dict[str, set[str]]) -> list[str]:
        """The attributed paths among *blobs* (path -> blob ids) with at least one blob that lacks :attr:`MAGIC`."""
        bad: list[str] = []
        for path in self.attributed(repo_path, sorted(blobs)):
            if any(not self._is_encrypted(repo_path, blob) for blob in sorted(blobs[path])):
                bad.append(path)
        return bad

    def _is_encrypted(self, repo_path: Path | str, blob: str) -> bool:
        """``True`` for a git-crypt blob, and for an empty one, which git-crypt never encrypts."""
        content = self.runner._query_bytes("cat-file", "blob", blob, cwd=repo_path)
        if content.returncode != 0:
            return True  # Cannot be read: not this check's to refuse.
        return not content.stdout or content.stdout.startswith(self.MAGIC)

    def _filter_configured(self, repo_path: Path | str) -> bool:
        """``True`` once ``git-crypt unlock`` has written its filter into this clone's configuration."""
        return self.runner._query("config", "--get", f"filter.{self.FILTER}.clean", cwd=repo_path).returncode == 0

    @staticmethod
    def _split(output: str) -> list[str]:
        """The NUL-separated fields of *output*, with the empty trailing one dropped."""
        return [field for field in output.split("\0") if field]

    @staticmethod
    def _blobs(raw: str) -> dict[str, set[str]]:
        """Parse ``--raw -z`` output into ``path -> {blob ids}``.

        Each change is a metadata field ``:<mode> <mode> <old> <new> <status>``
        followed by its path.
        """
        blobs: dict[str, set[str]] = {}
        fields = GitCrypt._split(raw)
        index = 0
        while index < len(fields) - 1:
            if fields[index].startswith(":"):
                parts = fields[index].split()
                if len(parts) >= 5:
                    blobs.setdefault(fields[index + 1], set()).add(parts[3])
                index += 2
            else:
                index += 1
        return blobs


__all__ = ["GitCrypt"]

"""encrypted_conflict — a conflict on a git-crypt file, and what to tell the person who has to choose a side.

Ring: 2
Contract: ``EncryptedConflict.paths`` names, among the files a merge stopped on, those a ``.gitattributes`` asks
    git-crypt to encrypt, locked or unlocked; ``EncryptedConflict.notes`` says, for each file a merge stopped on that
    cannot be merged in a tool, how to choose a side by hand (EncryptedMerge). It runs no ``git-crypt``, opens no
    merge tool and writes nothing.
Imports: git_repo
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ...git_repo.git_crypt import GitCrypt

if TYPE_CHECKING:
    from ...orchestre import GitRunner


class EncryptedConflict:
    """Two edits of one encrypted file are a binary conflict, whether the clone is locked or not."""

    @staticmethod
    def paths(git_runner: GitRunner, repo_path: Path, conflicting: tuple[Path, ...]) -> tuple[Path, ...]:
        """The *conflicting* paths with git-crypt's ``filter`` attribute, read from Git alone.

        Git decides "binary" from the stored ciphertext, but an unlocked clone shows one side decrypted with no
        conflict markers, so the content alone would not say it.
        """
        named = set(GitCrypt(git_runner).attributed(repo_path, [path.as_posix() for path in conflicting]))
        return tuple(path for path in conflicting if path.as_posix() in named)

    @staticmethod
    def notes(binary: tuple[Path, ...], encrypted: tuple[Path, ...], source: str | None) -> list[str]:
        """One line per binary file the merge stopped on; an encrypted one also says how to choose a side."""
        return [
            (
                f"encrypted file {path}: Git cannot merge two edits of an encrypted file, and no merge tool is opened. "
                f"Choose a side by hand: 'git checkout --ours -- {path}' or 'git checkout --theirs -- {path}', "
                f"then 'git add {path}', then run 'cgitsync merge --resolve {source}' again."
                if path in encrypted
                else f"binary file {path}: Git left this branch's version in place; the other version is on {source!r}. "
                "Nothing was staged for you."
            )
            for path in binary
        ]


__all__ = ["EncryptedConflict"]

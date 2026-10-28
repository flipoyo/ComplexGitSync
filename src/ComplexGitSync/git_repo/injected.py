"""injected — what this folder cannot import, so the caller hands it in.

Ring: 0 (pure; the one fallback is a temporary directory)
Contract: ``GitRepoError``, the error a bare ``GitRunner`` raises, and
    ``scratch_directory``, the throwaway directory it probes in. They are the
    *defaults* of two arguments of ``GitRunner``: ``error``, the exception type
    every failed command raises, and ``scratch``, how a probe gets its directory.
Imports: none

Why they are arguments
----------------------
``git_repo/`` imports nothing else from ``ComplexGitSync``, and a test holds it
to that, because it is the dependency gx4g will share. The runner used to import
``GitSyncError`` from ``errors`` and read its scratch directory through
``SystemClock``; both are the package's, not the repository's. So ``cgitsync``
names them in ``tree/git_runner.py``, and a bare runner falls back to what is
here.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from tempfile import TemporaryDirectory


class GitRepoError(Exception):
    """A git command failed, or was refused before it ran — what a bare ``GitRunner`` raises."""


#: How a probe gets its throwaway directory: a prefix in, a context manager out.
Scratch = Callable[[str], AbstractContextManager[str]]


def scratch_directory(prefix: str) -> AbstractContextManager[str]:
    """A throwaway directory under the OS's temporary directory, removed on exit."""
    return TemporaryDirectory(prefix=prefix)


__all__ = ["GitRepoError", "Scratch", "scratch_directory"]

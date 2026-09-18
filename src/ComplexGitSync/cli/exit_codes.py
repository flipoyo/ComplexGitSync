"""exit_codes — what a cgitsync exit code means, and which failures map to which.

Ring: 4 (CLI adapter)
Contract: three documented exit codes, and one function saying which code an
    expected failure gets. Returns ``None`` for anything it does not
    recognise, so a programming defect still reaches the user as a traceback
    instead of being disguised as a tidy failure.
Imports: errors

The distinction that matters to a script is between **"I asked and the
answer is no"** and **"I could not ask"**. A CI job treats those
differently: the first is a result to act on, the second is a broken
invocation to fix. Everything below exists to keep those two apart.
"""

from __future__ import annotations

import tomllib

from ..errors import (
    ConfigValidationError,
    GitSyncError,
    NestedConfigDiscoveryError,
    TreeNotReadyError,
    UnsupportedSnapshotFormatError,
)

#: The command did what was asked.
EXIT_OK = 0

#: The command ran and the answer is no — a merge conflict, a tree that is
#: not READY, a verification that found something, a document that does not
#: validate when validating was the job.
EXIT_REFUSED = 1

#: The command could not run — bad arguments, no workspace, a missing or
#: unreadable file, a document too broken to act on.
EXIT_UNUSABLE = 2

#: Commands for which an invalid document is *the answer* rather than a
#: reason they could not run. ``validate`` exists to say whether a document
#: is valid; answering "no" is a successful run of it, and a script gating on
#: it needs that to read as a result, not as a broken invocation.
_DOCUMENT_JUDGING_COMMANDS = frozenset({"validate"})


def exit_code_for(exc: BaseException, *, command: str | None = None) -> int | None:
    """The documented exit code for *exc*, or ``None`` if it is not expected.

    ``None`` is the important return value: it means nothing here claims to
    understand the failure, so the caller re-raises and the user sees the
    traceback. Catching every exception and returning a tidy ``2`` would
    turn every bug in this codebase into a "bad input" message, which is how
    a defect survives for months.
    """
    if isinstance(exc, UnsupportedSnapshotFormatError):
        # Not a verdict this build can reach, so it never gets the
        # document-judging treatment below — not even for `validate`,
        # whose job is normally to answer "invalid", not to be refused.
        return EXIT_UNUSABLE
    if isinstance(exc, ConfigValidationError):
        if command in _DOCUMENT_JUDGING_COMMANDS:
            return EXIT_REFUSED
        return EXIT_UNUSABLE
    if isinstance(exc, TreeNotReadyError):
        return EXIT_REFUSED
    if isinstance(exc, GitSyncError):
        # Operational refusals: a preflight that blocked, a conflict, a
        # remote that said no. The command ran; the answer is no.
        return EXIT_REFUSED
    if isinstance(exc, NestedConfigDiscoveryError):
        return EXIT_UNUSABLE
    if isinstance(exc, tomllib.TOMLDecodeError):
        return EXIT_UNUSABLE
    if isinstance(exc, OSError):
        # FileNotFoundError, NotADirectoryError, PermissionError: the
        # workspace, the snapshot or the directory is not there or not
        # readable. Nothing was asked, because there was nothing to ask.
        return EXIT_UNUSABLE
    return None


def diagnostic(exc: BaseException, *, command: str | None = None) -> str:
    """One line naming the command and what went wrong, for stderr.

    No traceback: a stack trace says "this program is broken", and these
    failures are the program working correctly on input it cannot use. The
    message inside the exception is already the useful part.
    """
    name = f"cgitsync {command}" if command else "cgitsync"
    message = str(exc).strip() or exc.__class__.__name__
    return f"{name}: {message}"


__all__ = [
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_UNUSABLE",
    "diagnostic",
    "exit_code_for",
]

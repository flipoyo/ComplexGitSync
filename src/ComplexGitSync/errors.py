"""errors — ComplexGitSync custom exception hierarchy.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: define the package's public exception types; raise nothing itself.
Imports: none
"""


class ComplexGitSyncError(Exception):
    """Base exception for the package."""


class ConfigValidationError(ComplexGitSyncError):
    """Raised when a .cgs or .gts document is invalid."""


class UnsupportedSnapshotFormatError(ConfigValidationError):
    """A ``.gts`` declares a format newer than this build understands.

    Distinct from an ordinary :class:`ConfigValidationError` because it is
    not a verdict this build is capable of reaching — it cannot check
    whether the document is well-formed under a canonicalisation it has
    never seen, only that the number is higher than the one it knows
    (`.localSpec/DevTickets/archive/20260918_SnapshotVersionGuard_DevPlanTicket.md`).
    A subclass of :class:`ConfigValidationError` so every existing catch of
    that type still sweeps it up; the CLI still tells the two apart to exit
    `2` unconditionally, even for ``validate``, whose job is normally to
    judge a document rather than to be refused by one.
    """


class GitSyncError(ComplexGitSyncError):
    """Raised for irrecoverable Git synchronization failures."""


class NestedConfigDiscoveryError(ComplexGitSyncError):
    """Raised when nested .cgs discovery fails or is ambiguous."""


class TreeNotReadyError(ComplexGitSyncError):
    """Raised when an operation requires a READY tree."""

"""errors — ComplexGitSync custom exception hierarchy.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: define the package's public exception types; raise nothing itself.
Imports: none
"""


class ComplexGitSyncError(Exception):
    """Base exception for the package."""


class ConfigValidationError(ComplexGitSyncError):
    """Raised when a .cgs or .gts document is invalid."""


class GitSyncError(ComplexGitSyncError):
    """Raised for irrecoverable Git synchronization failures."""


class NestedConfigDiscoveryError(ComplexGitSyncError):
    """Raised when nested .cgs discovery fails or is ambiguous."""


class TreeNotReadyError(ComplexGitSyncError):
    """Raised when an operation requires a READY tree."""

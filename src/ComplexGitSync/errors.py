"""ComplexGitSync custom exception hierarchy."""


class ComplexGitSyncError(Exception):
    """Base exception for the package."""


class ConfigValidationError(ComplexGitSyncError):
    """Raised when a .cgs or .gts document is invalid."""


class ArchitectureNotLoadedError(ComplexGitSyncError):
    """Raised when an operation requires a loaded project but none is available."""


class GitSyncError(ComplexGitSyncError):
    """Raised for irrecoverable Git synchronization failures."""


class FallbackRejectedError(ComplexGitSyncError):
    """Raised when an interactive fallback is rejected."""


class NestedConfigDiscoveryError(ComplexGitSyncError):
    """Raised when nested .cgs discovery fails or is ambiguous."""


class TreeNotReadyError(ComplexGitSyncError):
    """Raised when an operation requires a READY tree."""

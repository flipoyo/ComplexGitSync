from __future__ import annotations

from enum import Enum


class _AuthorityScope(str, Enum):
    """Language-neutral prototype of the future kernel capability."""

    CGS_KERNEL_V1 = "cgs-kernel-v1"


CGS_AUTHORITY = _AuthorityScope.CGS_KERNEL_V1


def require_cgs_authority(authority: _AuthorityScope) -> None:
    if authority != CGS_AUTHORITY:
        from .errors import ErrorCode, OwnershipError

        raise OwnershipError(
            ErrorCode.OWNERSHIP_VIOLATION,
            "authoritative infrastructure can only be created by CGS",
        )

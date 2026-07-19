from __future__ import annotations


class _AuthorityCapability:
    """Opaque prototype capability; the Rust boundary will hold the real token."""

    __slots__ = ()

    def __new__(cls) -> "_AuthorityCapability":
        raise TypeError("CGS authority cannot be constructed")

    def __repr__(self) -> str:
        return "<_AuthorityCapability redacted>"

    def __reduce__(self) -> object:
        raise TypeError("CGS authority cannot be serialized")


CGS_AUTHORITY = object.__new__(_AuthorityCapability)


def require_cgs_authority(authority: object | None) -> None:
    if authority is not CGS_AUTHORITY:
        from .errors import ErrorCode, OwnershipError

        raise OwnershipError(
            ErrorCode.OWNERSHIP_VIOLATION,
            "authoritative infrastructure can only be created by CGS",
        )

"""project_alias — the name a project goes by in the repositories it shares with other projects.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: given the digest of a project's real name (its ``os_id``, the SHA-256 the caller computes, so the
    hash stays where hashes are made) and an ``anonymous`` mode, say which alias candidates it may take, in order
    (AnonymousProject §2). Nothing here reads a repository:
    whether a candidate is ours, free or taken is decided by the caller, which has the remotes. The real name
    never leaves the project's own ``.cgs``.
Imports: none

``"hash"`` aliases are the first six hex digits of ``sha256(name)``, then ``-1``, ``-2`` on a collision.
``"sequence"`` aliases are ``anonymous-1``, ``anonymous-2``, and so on.
"""

from __future__ import annotations

import re
from collections.abc import Iterator


class ProjectAlias:
    """The alias rules: candidates, the claim, and what a name must not look like."""

    #: The values of the project table's ``anonymous`` key.
    HASH = "hash"
    SEQUENCE = "sequence"
    MODES = (HASH, SEQUENCE)

    #: Hex digits kept from the digest in ``"hash"`` mode.
    DIGITS = 6

    #: The file at the root of an alias branch that says whose it is.
    CLAIM_FILE = ".cgitsync-alias"

    #: ``"sequence"`` aliases are this followed by ``-<i>``.
    SEQUENCE_STEM = "anonymous"

    _SHAPE = re.compile(r"^(anonymous-[0-9]+|[0-9a-f]{6}(-[0-9]+)?)$")

    @staticmethod
    def truncation(digest: str) -> str:
        """The first :attr:`DIGITS` hex digits of *digest*: the ``"hash"`` alias before any suffix."""
        return digest[: ProjectAlias.DIGITS]

    @staticmethod
    def candidates(digest: str, mode: str) -> Iterator[str]:
        """The aliases the project with this *digest* may take, in the order they are tried, without end.

        The caller takes the first that is free or already its own, so the choice is the same on every machine
        and needs no local state. An unknown *mode* raises ``ValueError``: ``validate`` refuses it first.
        """
        if mode == ProjectAlias.HASH:
            stem = ProjectAlias.truncation(digest)
            yield stem
            index = 1
            while True:
                yield f"{stem}-{index}"
                index += 1
        elif mode == ProjectAlias.SEQUENCE:
            index = 1
            while True:
                yield f"{ProjectAlias.SEQUENCE_STEM}-{index}"
                index += 1
        else:
            raise ValueError(f"anonymous must be one of {', '.join(map(repr, ProjectAlias.MODES))}, not {mode!r}")

    @staticmethod
    def first(digest: str, mode: str) -> str:
        """The first candidate: the alias when nothing is taken, which is what an offline command uses."""
        return next(ProjectAlias.candidates(digest, mode))

    @staticmethod
    def looks_like_alias(name: str) -> bool:
        """Whether *name* has the shape of an alias, so a project may not be called that by hand.

        Nobody can then sit on another project's alias by typing it as a name.
        """
        return ProjectAlias._SHAPE.fullmatch(name) is not None


__all__ = ["ProjectAlias"]

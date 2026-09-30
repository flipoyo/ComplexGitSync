"""outcome — What one tree-wide write did to one repository.

Ring: 2
Contract: What one tree-wide write did to one repository.
Imports: none
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class RepoOutcome:
    """What one tree-wide write command did to one repository.

    A sweep that writes nowhere and a sweep that writes everywhere used to
    print the same thing, which is what makes "nothing happened" so hard to
    diagnose: the user cannot tell a command that found no work from one
    that never looked at their repository at all. Every repository the
    command visited gets one of these, in the order it was visited.

    ``acted`` answers "did anything change here?". ``detail`` says what
    changed (a new commit's sha, the ref pushed, how many paths were
    staged) or, when ``acted`` is ``False``, why nothing did.
    """

    name: str
    acted: bool
    detail: str


__all__ = [
    "RepoOutcome",
]

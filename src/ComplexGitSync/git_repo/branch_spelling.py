"""branch_spelling — whether a branch name nothing holds is a misspelling of one that exists.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: ``BranchSpelling.close_to`` names the branches a typed name could be a misspelling of, from the names a
    tree already holds. It reads no repository and starts no process; ``checkout`` hands it the names it
    already read, and only when the typed name exists nowhere.
Imports: none

A branch name is close to another when they differ by at most two edits (a swap of two neighbouring letters is one
edit) and by at most one edit when the typed name has five characters or fewer (CheckoutBareTip CD3). Case is
ignored; an ``<owner>_`` prefix (the tree's project, a twin's ``ComplexGitSync``, a nested Parent) is not part of
the name a user types, so it is stripped from the known names first, and only those prefixes. Two names that differ only in digits (``hotfix-1``, ``hotfix-2``) are numbered siblings, never a typo.
"""

from __future__ import annotations

from collections.abc import Iterable


class BranchSpelling:
    """The misspelling rule for a branch name no repository holds."""

    #: A typed name this short, or shorter, tolerates one edit, not two.
    SHORT = 5

    @staticmethod
    def typed(known: str, owners: Iterable[str] = ()) -> str:
        """What a user types for *known*: each leading ``<owner>_`` the tree derives a branch with is not part of it.

        *owners* are the names a tree prefixes with (its project, ``ComplexGitSync`` for a twin's memory, a nested
        Parent): a legacy branch such as ``release_1`` keeps its underscore, since no owner is called ``release``.
        """
        names = tuple(owners)
        while (owner := next((o for o in names if o and known.startswith(f"{o}_")), None)) is not None:
            known = known[len(owner) + 1:]
        return known

    @staticmethod
    def distance(left: str, right: str) -> int:
        """Edits to turn *left* into *right*: insert, delete, replace, or swap two neighbouring letters (one edit)."""
        previous, before = list(range(len(right) + 1)), list(range(len(right) + 1))
        for i, a in enumerate(left, 1):
            row = [i] + [0] * len(right)
            for j, b in enumerate(right, 1):
                row[j] = min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (a != b))
                if i > 1 and j > 1 and a == right[j - 2] and left[i - 2] == b:
                    row[j] = min(row[j], before[j - 2] + 1)
            before, previous = previous, row
        return previous[-1]

    @classmethod
    def held(cls, name: str, known: Iterable[str], owners: Iterable[str] = ()) -> bool:
        """Whether a known branch, derived or bare, is *name* as a user types it."""
        owners = tuple(owners)
        return name in {cls.typed(branch, owners) for branch in known}

    @classmethod
    def close_to(cls, name: str, known: Iterable[str], owners: Iterable[str] = ()) -> tuple[str, ...]:
        """The known branch names *name* may be a misspelling of, as typed (no prefix), sorted; empty when it is new.

        A name any known branch already carries, derived or bare, is that branch and not a misspelling, so the
        answer is empty then: the caller asks only for a name it could not find, and this is the second look.
        """
        known, owners = tuple(known), tuple(owners)
        if cls.held(name, known, owners):
            return ()
        candidates = {cls.typed(branch, owners) for branch in known}
        limit = 1 if len(name) <= cls.SHORT else 2
        lowered = name.lower()
        return tuple(sorted(
            other for other in candidates
            if other and not cls._numbered_siblings(name, other)
            and (other.lower() == lowered or cls.distance(lowered, other.lower()) <= limit)
        ))

    @staticmethod
    def _numbered_siblings(left: str, right: str) -> bool:
        """Whether the two names differ only in their digits: ``hotfix-1`` and ``hotfix-2`` are both intended."""
        strip = str.maketrans("", "", "0123456789")
        return left != right and left.translate(strip) == right.translate(strip)


__all__ = ["BranchSpelling"]

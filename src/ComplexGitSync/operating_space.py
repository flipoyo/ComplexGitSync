"""operating_space — a project's tree, and the id every machine computes for it.

Ring: 1 (reads a loaded tree; no I/O of its own)
Contract: ``OS_ID = SHA256(UTF8(canonical(project_name)))``, as 64 lowercase
    hex characters with no domain prefix; domain separation happens one level
    up, in the Omniscience record that is keyed by it. An ``OperatingSpace``
    holds a :class:`~.git_tree.WorkingGitTree`, not a copy of it: no lifecycle
    and none of the tree's machinery is reimplemented here. It answers who
    the tree is, from the root's project name, and what rule the name breaks
    when it breaks one.
Imports: errors, git_tree, git_tree_branch

The canonical form is frozen here, once. A project name is normalised to
Unicode NFC and otherwise kept as written: case is kept, nothing is trimmed.
A name with leading or trailing whitespace, a control character, or no
characters at all is rejected rather than repaired, so two distinct accepted
names never fold into one. Changing any of this would change every id.

The id is not a State hash and is not part of ``integrity_schema = 1``: no
schema-1 hash or golden vector depends on it; the vectors in
``tests/unit/test_operating_space.py`` are the cross-platform contract. A name
the rules reject does not stop a tree from loading: :attr:`OperatingSpace.os_id`
is then ``None``, :attr:`OperatingSpace.problem` names the rule, and the
Omniscience register refuses the tree.
"""

from __future__ import annotations

import hashlib
import unicodedata
import warnings
from dataclasses import dataclass
from typing import Any

from .errors import ConfigValidationError
from .git_tree import WorkingGitTree
from .git_tree_branch import tree_project_name


@dataclass(frozen=True, slots=True)
class OperatingSpace:
    """One project's tree. The pure functions on a name are static; the rest read the tree."""

    tree: WorkingGitTree

    @staticmethod
    def problem_of(name: str) -> str | None:
        """The rule *name* breaks, or ``None`` when it is accepted."""
        canonical = unicodedata.normalize("NFC", name)
        if not canonical:
            return "a project name must not be empty"
        if canonical != canonical.strip():
            return "a project name must not start or end with whitespace"
        if any(unicodedata.category(char) == "Cc" for char in canonical):
            return "a project name must not contain a control character"
        return None

    @staticmethod
    def canonical(name: str) -> str:
        """*name* in its frozen canonical form (Unicode NFC), or an error naming the rule broken."""
        broken = OperatingSpace.problem_of(name)
        if broken is not None:
            raise ConfigValidationError(f"{name!r} is not a valid project name: {broken}.")
        return unicodedata.normalize("NFC", name)

    @staticmethod
    def id_of(name: str) -> str:
        """The ``os_id`` of the project called *name*: SHA-256 hex of its canonical UTF-8 bytes."""
        return hashlib.sha256(OperatingSpace.canonical(name).encode("utf-8")).hexdigest()

    @property
    def project_name(self) -> str | None:
        """The root's project name; a child repository's own name never counts."""
        return tree_project_name(self.tree)

    @property
    def problem(self) -> str | None:
        """The rule the project name breaks, or ``None`` when it is accepted (or absent)."""
        name = self.project_name
        return None if name is None else OperatingSpace.problem_of(name)

    @property
    def os_id(self) -> str | None:
        """The id, or ``None`` when the tree has no root or its name is rejected."""
        name = self.project_name
        return None if name is None or self.problem else OperatingSpace.id_of(name)

    def warn_if_rejected(self) -> None:
        """Say, once, that the name breaks a rule; the tree keeps working, registration refuses it."""
        if self.problem:
            warnings.warn(f"{self.problem}; Omniscience will refuse to register this tree.", stacklevel=3)

    def stamp(self, payload: dict[str, Any]) -> dict[str, Any]:
        """*payload* with this tree's ``os_id`` added: what ``status --json`` prints."""
        return {**payload, "os_id": self.os_id}

    @staticmethod
    def id_of_state(project_name: object) -> str | None:
        """The id for the project name a State records, or ``None`` when it has none or is rejected."""
        name = str(project_name or "")
        return None if OperatingSpace.problem_of(name) else OperatingSpace.id_of(name)


__all__ = ["OperatingSpace"]

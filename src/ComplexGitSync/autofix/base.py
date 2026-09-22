"""autofix.base — the shape every ``repair_*.py`` module implements.

Ring: 2 (orchestrates git_runner.py and memory/; imports no subprocess itself)
Contract: define ``RepairOutcome``, ``Situation``, the ``Repair`` protocol,
    and the chain-shaped-repository registry — nothing here runs Git or
    touches the filesystem directly.
Imports: git_repo, git_runner

Design reference: main_1-1_Autofix_DevPlanTicket.md, sections 7-8 (D1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..git_repo import WorkingRepo
    from ..git_runner import GitRunnerProtocol


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    """What a :class:`Repair` actually did, or why it did nothing.

    ``repaired=False`` is not a failure — a repair that looked and found
    nothing to fix (already resolved, or a merge that applied cleanly on
    its own) reports that honestly rather than pretending it acted.
    """

    repaired: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Situation:
    """One already-raised error, paired with the repository it named.

    Carries nothing else — a :class:`Repair`'s own ``matches``/``repair``
    decide everything from ``repo`` and ``source_error`` themselves, so a
    new repair module never has to ask this dataclass to grow a field for
    its own purposes.
    """

    repo: "WorkingRepo"
    source_error: str


class Repair(Protocol):
    """What every ``repair_*.py`` module's class implements.

    ``name`` is what a log message and a commit message call this repair
    by — never the module's own filename, which is free to change.
    """

    name: str

    def matches(self, situation: Situation) -> bool:
        """Does this repair know how to handle ``situation``?

        Pure pattern-matching over ``situation.source_error`` plus
        whatever ``situation.repo`` says about its own mounted content
        shape (see :data:`CHAIN_SHAPED_REPOS`) — runs no Git itself, so
        :class:`~.repair_from_cli.FromCliRepair` can try every registered
        repair cheaply before committing to one.
        """
        ...

    def repair(self, situation: Situation, runner: "GitRunnerProtocol") -> RepairOutcome:
        """Perform the fix. Only ever called after :meth:`matches` returned
        ``True`` for the same ``situation``. Raises rather than guesses if
        a precondition specific to this repair fails — never partially
        writes, per the ticket's D3."""
        ...


#: D1 — which mounted repositories have content with a sequencing
#: invariant a plain merge does not know about, and where that content
#: lives. Repository *name* (``WorkingRepo.name``, e.g. ``.memory``) to
#: the directory, relative to the repository root, that holds the chain.
#: An explicit registry, not content-sniffing — the same reasoning
#: ``git_branch.py``'s privacy rule and ``git_repo.py``'s provider registry
#: already use elsewhere in this project. ``Omniscience``'s own register
#: never needs an entry here, by its own one-file-per-record design.
CHAIN_SHAPED_REPOS: dict[str, str] = {
    ".memory": "lgr",
}


def is_chain_shaped(repo_name: str) -> bool:
    """Whether *repo_name* is registered in :data:`CHAIN_SHAPED_REPOS`."""
    return repo_name in CHAIN_SHAPED_REPOS

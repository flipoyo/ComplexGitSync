"""data_result — what a data backend answers, in words that never flatter.

Ring: 0 (pure — values only, no I/O)
Contract: ``DataResult`` is the one answer a data phase gives: a ``DataStatus``,
    the backend, repository, command and phase it is about, whether a retry is
    safe, and diagnostics already scrubbed of secrets. ``DataPlan`` is what a
    planning phase proposes and a staging phase consumes. Nothing here runs.
Imports: none

A clean Git tree is never evidence for a good data state, and ``UNKNOWN`` is a
real answer: offline, it is the only honest one about a remote
(DataBackendContract §2).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DataStatus(Enum):
    """The eight states a data phase may report (DataBackendContract §2)."""

    READY = "ready"
    DIRTY = "dirty"
    NOT_MATERIALIZED = "not_materialized"
    MISSING_CACHE = "missing_cache"
    REMOTE_UNAVAILABLE = "remote_unavailable"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DataPlan:
    """What a planning phase proposes for some paths of one repository; nothing is done yet."""

    paths: tuple[str, ...] = ()
    #: One line per action the backend would take, in order, for a reader or a dry run.
    steps: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DataResult:
    """One phase's answer for one repository.

    A backend fills ``state``, ``retry_safe``, ``diagnostics`` and, for a
    planning or dry-run answer, ``plan``. ``DataManager`` stamps ``backend``,
    ``repo``, ``operation`` and ``step`` itself and scrubs ``diagnostics``, so a
    backend can neither misname what it answered nor leak a secret through it.
    """

    state: DataStatus
    backend: str = ""
    repo: str = ""
    #: The command the phase ran for (a ``DataCommand`` value, as the CLI spells it).
    operation: str = ""
    #: The phase attempted (a ``DataPhase`` value).
    step: str = ""
    retry_safe: bool = False
    diagnostics: tuple[str, ...] = ()
    plan: DataPlan | None = None

    @property
    def ok(self) -> bool:
        """True only for ``READY``: every other word, ``UNKNOWN`` included, is not a success."""
        return self.state is DataStatus.READY

    def describe(self) -> str:
        """One line naming the repository, the phase and the state, then the diagnostics."""
        head = f"{self.repo}: {self.step} for {self.operation} via {self.backend or 'no backend'} -> {self.state.value}"
        return "; ".join((head, *self.diagnostics))


__all__ = ["DataPlan", "DataResult", "DataStatus"]

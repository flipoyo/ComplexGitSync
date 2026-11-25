"""data_command — which data phase each cgitsync command asks of a backend.

Ring: 0 (pure — a table, no I/O)
Contract: ``DataCommand`` has one member per command of the data dispatch
    matrix, spelled as the CLI spells it, and says which ``DataPhase`` it runs,
    in order, and whether it must stay offline. ``DataContext`` carries the
    arguments a phase needs. A command is an enum, never a string, so no
    unchecked literal spreads through the modules that dispatch
    (DataBackendContract D4, owner, 2026-11-25).
Imports: none

The phases are semantic, not CLI verbs: DVC needs explicit calls where Git LFS
rides on Git's own filters, so a phase a backend has nothing to do in answers
``READY`` rather than being a no-op named after another tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DataPhase(Enum):
    """The phases of the ``DataBackend`` protocol, named as its methods are."""

    INSPECT = "inspect"
    PREPARE_STAGING = "prepare_staging"
    PRE_GIT_COMMIT = "pre_git_commit"
    PRE_GIT_CHANGE = "pre_git_change"
    MATERIALIZE = "materialize"
    PUBLISH = "publish"
    VERIFY_RELEASE = "verify_release"

    @property
    def writes(self) -> bool:
        """True for a phase that may change the workspace, a cache or a remote; a dry run never runs one."""
        return self in _WRITING_PHASES


_WRITING_PHASES = frozenset(
    {DataPhase.PREPARE_STAGING, DataPhase.PRE_GIT_COMMIT, DataPhase.MATERIALIZE, DataPhase.PUBLISH}
)


class DataCommand(Enum):
    """One member per command of the dispatch matrix (DataManager_DVC §5), reconciled with today's CLI."""

    VALIDATE = "validate"
    DISCOVER = "discover"
    INITIALISE = "initialise"
    BOOTSTRAP = "bootstrap"
    ADD = "add"
    RM = "rm"
    COMMIT = "commit"
    STATUS = "status"
    VIEW_TREE = "view-tree"
    PULL = "pull"
    PULL_FORCE = "pull --force"
    CHECKOUT = "checkout"
    BRANCH = "branch"
    MERGE = "merge"
    PUSH = "push"
    TAG = "tag"
    RELEASE_FREEZE = "release freeze"
    RELEASE_LOAD = "release load"
    VERIFY = "verify"

    @property
    def phases(self) -> tuple[DataPhase, ...]:
        """The phases this command asks of a backend, in the order it runs them around Git."""
        return _PHASES[self]

    @property
    def offline(self) -> bool:
        """True when the command must never reach a data remote, whatever its context allows."""
        return self in _OFFLINE

    @property
    def destructive(self) -> bool:
        """True when the Git change that follows may discard local data."""
        return self is DataCommand.PULL_FORCE


_INSPECT = (DataPhase.INSPECT,)
_CHANGE_THEN_MATERIALIZE = (DataPhase.PRE_GIT_CHANGE, DataPhase.MATERIALIZE)
_PHASES: dict[DataCommand, tuple[DataPhase, ...]] = {
    DataCommand.VALIDATE: _INSPECT,
    DataCommand.DISCOVER: _INSPECT,
    DataCommand.INITIALISE: _CHANGE_THEN_MATERIALIZE,
    DataCommand.BOOTSTRAP: (DataPhase.MATERIALIZE,),
    DataCommand.ADD: (DataPhase.PREPARE_STAGING,),
    DataCommand.RM: (DataPhase.PREPARE_STAGING,),
    DataCommand.COMMIT: (DataPhase.PRE_GIT_COMMIT,),
    DataCommand.STATUS: _INSPECT,
    DataCommand.VIEW_TREE: _INSPECT,
    DataCommand.PULL: _CHANGE_THEN_MATERIALIZE,
    DataCommand.PULL_FORCE: _CHANGE_THEN_MATERIALIZE,
    DataCommand.CHECKOUT: _CHANGE_THEN_MATERIALIZE,
    DataCommand.BRANCH: (DataPhase.PRE_GIT_CHANGE,),
    DataCommand.MERGE: _CHANGE_THEN_MATERIALIZE,
    DataCommand.PUSH: (DataPhase.PUBLISH,),
    DataCommand.TAG: (DataPhase.VERIFY_RELEASE,),
    DataCommand.RELEASE_FREEZE: (DataPhase.PRE_GIT_COMMIT, DataPhase.PUBLISH, DataPhase.VERIFY_RELEASE),
    DataCommand.RELEASE_LOAD: (DataPhase.MATERIALIZE,),
    DataCommand.VERIFY: _INSPECT,
}
#: Commands kept offline by the matrix: ``checkout`` reads the local cache only, and reports what is missing.
_OFFLINE = frozenset(
    {DataCommand.VALIDATE, DataCommand.DISCOVER, DataCommand.STATUS, DataCommand.VIEW_TREE,
     DataCommand.CHECKOUT, DataCommand.VERIFY}
)


@dataclass(frozen=True, slots=True)
class DataContext:
    """What a phase needs beyond the repository. The defaults reach no network and write nothing extra."""

    dry_run: bool = False
    #: Reaching a data remote needs ``offline=False`` and a command the matrix lets go online.
    offline: bool = True
    #: The paths ``add`` or ``rm`` named; ``add`` with none updates what the backend already tracks.
    paths: tuple[str, ...] = ()
    no_stage: bool = False
    destructive: bool = False
    #: The Git revisions a ``publish`` advertises, and the one a ``verify_release`` checks.
    revisions: tuple[str, ...] = ()
    revision: str | None = None


__all__ = ["DataCommand", "DataContext", "DataPhase"]

"""omniscience_reader — what the shared register holds, read from the mount's own Git objects.

Ring: 3
Contract: after a fetch, list the register's remote branches and, per branch,
    the registration records and observations it holds, each as a parsed value
    or as a named problem. Reads only: nothing is checked out, written or
    pushed, so a reader never disturbs the mount's worktree.
Imports: errors, git_runner, omniscience_observation, omniscience_record, omniscience_repository

A file that does not parse, does not validate, or is not named by its own hash
is not dropped: it comes back as a `RegisterFile` with a ``problem``, because
it stays visible as unaccepted and a reader must be able to say why.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..errors import ConfigValidationError
from ..git_repo.git_runner import GitRunnerProtocol
from .omniscience_observation import Observation
from .omniscience_record import OmniscienceRecord
from .omniscience_repository import GLOBAL_BRANCH

REGISTER_DIR = "register/"
OBSERVATION_DIR = "observations/"
_ORIGIN = "refs/remotes/origin/"


@dataclass(frozen=True, slots=True)
class RegisterFile:
    """One file under ``register/`` or ``observations/`` of one branch: its value, or why it is not one."""

    branch: str
    path: str
    value: OmniscienceRecord | Observation | None = None
    problem: str | None = None


class RegisterReader:
    """Reads the register's branches through the runner. Holds the mount and the runner, nothing else."""

    def __init__(self, git: GitRunnerProtocol, mount: Path) -> None:
        self.git = git
        self.mount = mount

    def remote_branches(self) -> dict[str, str]:
        """Every branch of the remote the mount last fetched, name to commit."""
        return {name[len(_ORIGIN):]: sha for name, sha in self.git.branch_refs(self.mount).items() if name.startswith(_ORIGIN)}

    def records(self, branch: str, sha: str) -> list[RegisterFile]:
        """Every file under ``register/`` at *sha*, which is *branch*'s tip."""
        found: list[RegisterFile] = []
        for path in sorted(self.git.tree_blobs(self.mount, sha, REGISTER_DIR)):
            found.append(self._record(branch, sha, path))
        return found

    def observations(self, branch: str, sha: str) -> list[RegisterFile]:
        """Every file under ``observations/`` at *sha*."""
        return [self._observation(branch, sha, path) for path in sorted(self.git.tree_blobs(self.mount, sha, OBSERVATION_DIR))]

    def _table(self, sha: str, path: str) -> dict:
        text = self.git.show_file(self.mount, sha, path)
        if text is None:
            raise ConfigValidationError("the file is not readable")
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigValidationError(f"not TOML: {exc}") from exc

    def _record(self, branch: str, sha: str, path: str) -> RegisterFile:
        try:
            record = OmniscienceRecord.from_dict(self._table(sha, path))
            if branch == GLOBAL_BRANCH:
                raise ConfigValidationError("a record on the index branch")
            if path != record.filename:
                raise ConfigValidationError(f"it is named {path} but its hash names {record.filename}")
        except ConfigValidationError as exc:
            return RegisterFile(branch, path, problem=str(exc))
        return RegisterFile(branch, path, value=record)

    def _observation(self, branch: str, sha: str, path: str) -> RegisterFile:
        try:
            observation = Observation.from_dict(self._table(sha, path))
            if path != observation.filename:
                raise ConfigValidationError(f"it is named {path} but its hash names {observation.filename}")
        except ConfigValidationError as exc:
            return RegisterFile(branch, path, problem=str(exc))
        return RegisterFile(branch, path, value=observation)


__all__ = ["OBSERVATION_DIR", "REGISTER_DIR", "RegisterFile", "RegisterReader"]

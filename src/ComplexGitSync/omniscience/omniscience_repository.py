"""omniscience_repository — what the Omniscience mount is, as a repository.

Ring: 1 (decides what to do, never runs Git itself)
Contract: the `.cgs` entry that mounts the one shared, private
    ``omniscience`` repository, the branch of it that is a project's, and the
    name of the branch that holds the global index. Every Git command is run
    by the caller through ``git_runner.py``, as for every other repository.
Imports: git_branch

One physical repository, shared by every project, with one branch per project
(``<project>`` for ``main``, ``<project>_<branch>`` otherwise: the private/local
rule, asked of ``git_branch.py``) and one distinguished branch, ``_global``,
for the index. A developer who only inspects mounts it private and read-only
(private/distant); a contributor mounts the same repository writable
(private/local). Nothing here is a new kind of mount, and no credential is
read, stored or sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..git_repo.git_branch import DEFAULT_BRANCH, private_local_branch

#: The repository every registered project is a branch of.
DEFAULT_OMNISCIENCE_REPOSITORY = "github:flipoyo/omniscience"

#: Where it is mounted in the tree that inspects or contributes to it.
OMNISCIENCE_MOUNT_PATH = ".omniscience"

#: The branch holding the global index: checkpoints, never a project's records.
#: It is reserved, so a project named like it cannot be registered.
GLOBAL_BRANCH = "_global"


@dataclass(frozen=True)
class OmniscienceRepository:
    """The mount of the shared Omniscience repository. Stateless: everything is a static method."""

    @staticmethod
    def branch(project_name: str, project_branch: str) -> str:
        """Which branch of the repository a project branch's registrations live on."""
        return private_local_branch(project_name, project_branch)

    @staticmethod
    def mount_entry(repository: str, *, contribute: bool, project_name: str) -> dict[str, Any]:
        """The `.cgs` entry that mounts *repository* at ``.omniscience``.

        Always private. Writable only for a contributor; an inspector gets the
        same repository read-only. ``default_branch`` names the project's own
        branch and the ordinary derivation does the rest, as for a memory.
        """
        entry: dict[str, Any] = {
            "repository": repository,
            "relative_path": OMNISCIENCE_MOUNT_PATH,
            "default_branch": project_name,
            "fallback_branch": DEFAULT_BRANCH,
            "private": True,
        }
        if contribute:  # absent means read-only in a `.cgs`
            entry["writable"] = True
        # Like a memory, a leaf: it holds records and checkpoints, never a nested tree.
        entry["nested_config"] = "disabled"
        return entry


__all__ = ["DEFAULT_OMNISCIENCE_REPOSITORY", "GLOBAL_BRANCH", "OMNISCIENCE_MOUNT_PATH", "OmniscienceRepository"]

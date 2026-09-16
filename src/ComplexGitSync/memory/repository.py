"""repository — what a memory needs in order to be a repository.

Ring: 1 (filesystem; decides what to do, never runs Git itself)
Contract: propose the `.cgs` entry that mounts a memory, say what a memory
    holds that is worth committing, and write the commit message for it.
    Every Git command is run by the caller through `git_runner.py`, as it is
    for every other repository in the tree.
Imports: git_branch

Why the Git stays outside
-------------------------
A memory is a private/local repository like ``.localSpec`` or ``.claude``:
one shared repository, one branch per project, mounted at ``.cgitsync``.
Nothing about cloning, committing or pushing it is special, so nothing here
learns to do any of it — this module decides *what* and `orchestre.py` asks
`git_runner.py` to do it. The package docstring states that boundary; this
is the module that would have broken it first.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..git_branch import DEFAULT_BRANCH, private_local_branch

#: Where a memory is mounted in the tree it remembers.
MOUNT_PATH = ".cgitsync"

#: The repository every project's memory is a branch of. One repository,
#: one branch per project — the owner's decision of 2026-09-16, reversing
#: the architecture's D2. See MemoryArchitecture §2.2 for what it costs.
DEFAULT_MEMORY_REPOSITORY = ".memory"


def memory_repository_id(owner: str, *, provider: str = "github") -> str:
    """The repository id a memory is proposed under: ``github:<owner>/.memory``."""
    return f"{provider}:{owner}/{DEFAULT_MEMORY_REPOSITORY}"


def memory_branch(project_name: str, project_branch: str) -> str:
    """Which branch of the memory repository this project's memory is on.

    The ordinary private/local rule, asked of the one module that owns it:
    the project's name while the project is on its main branch, and
    ``<project>_<branch>`` while it is on one of its own. A memory therefore
    forks when the project branches and merges back when the branch does —
    the same behaviour `.localSpec` has, with the same consequence that work
    recorded on one branch is invisible from the other until the merge.
    """
    return private_local_branch(project_name, project_branch)


def mount_entry(
    owner: str,
    project_name: str,
    *,
    provider: str = "github",
) -> dict[str, Any]:
    """The `.cgs` entry that mounts this project's memory.

    An ordinary private, writable repository entry — nothing here is a new
    kind of mount, and that is the whole argument for the shape. The branch
    is left to the ordinary derivation: ``default_branch`` names the
    project's own branch of the memory and the rule does the rest.
    """
    return {
        "repository": memory_repository_id(owner, provider=provider),
        "relative_path": MOUNT_PATH,
        "default_branch": project_name,
        "fallback_branch": DEFAULT_BRANCH,
        "private": True,
        "writable": True,
    }


def format_mount_entry(entry: dict[str, Any]) -> str:
    """The entry as a line a user can paste into a `.cgs`'s ``repos``."""
    rendered = ", ".join(
        f"{key} = {'true' if value is True else repr(str(value)).replace(chr(39), chr(34))}"
        for key, value in entry.items()
    )
    return f"    {{ {rendered} }},"


def creation_command(entry: dict[str, Any]) -> str:
    """The one command that creates the repository, for the user to run.

    ComplexGitSync never creates a repository on a host: it speaks Git and
    nothing else, and teaching it a provider's API would mean a network call
    and a stored credential where there is neither. So `init` proposes, says
    this, and waits.
    """
    repository = str(entry["repository"])
    _, _, path = repository.partition(":")
    return f"gh repo create {path} --private"


def uncommitted_memory_paths(status_lines: list[str]) -> list[str]:
    """What a memory has gained, from `git status --porcelain` over its mount.

    Everything is fair game — a memory's whole content is States, ledger
    entries, commit logs and logs, all of which it wrote itself. There is no
    filtering to do, so this only answers "is there anything", which is what
    decides whether a push has work to do.
    """
    return [line[3:].strip() for line in status_lines if line.strip()]


def commit_message(project_name: str, states: int, entries: int) -> str:
    """What a memory's own commit says.

    Written here rather than in the CLI because it is a fact about the
    memory, and because `CLAUDE.md`'s rule about commit messages governs
    what *people* write; this is the tool's own bookkeeping, like the
    `--commit-gitignore` message, and it says plainly what it carries.
    """
    moment = f"{datetime.now(UTC):%Y-%m-%d}"
    return (
        f"{project_name} memory, {moment}: {states} state(s), {entries} ledger entr(ies)"
    )


def memory_mount_path(workspace: Path) -> Path:
    """Where the memory sits in *workspace*."""
    return workspace / MOUNT_PATH


__all__ = [
    "DEFAULT_MEMORY_REPOSITORY",
    "MOUNT_PATH",
    "commit_message",
    "creation_command",
    "format_mount_entry",
    "memory_branch",
    "memory_mount_path",
    "memory_repository_id",
    "mount_entry",
    "uncommitted_memory_paths",
]

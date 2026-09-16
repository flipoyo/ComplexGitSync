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

import re
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


#: Where the `repos` array begins in a hand-written `.cgs`.
_REPOS_ARRAY = re.compile(r"^[ \t]*repos[ \t]*=[ \t]*\[", re.MULTILINE)


def entry_already_present(text: str, repository: str, relative_path: str) -> bool:
    """Whether this `.cgs` already mounts *repository* at *relative_path*.

    Read off the written text rather than a parsed document, because the
    text is what is about to be edited: a file that already says this must
    not be rewritten at all, not even into an equivalent form.
    """
    for line in text.splitlines():
        if repository in line and relative_path in line and not line.lstrip().startswith("#"):
            return True
    return False


def insert_repo_entry(text: str, line: str) -> str:
    """Append *line* to this `.cgs`'s ``repos`` array, as text.

    **The file is edited, not regenerated.** A `.cgs` is hand-written and
    its comments are the half a person reads —
    ``examples/complexgitsync4dev.cgs`` is thirty lines of explanation and
    five of ``repos``. Re-serialising it through ``cgs_format.to_cgs()``
    would produce a valid file that had lost every one of them. So the entry
    is spliced in before the array's closing bracket and everything else is
    left byte for byte as it was.

    Raises :class:`ValueError` when there is no ``repos`` array to add to —
    the caller turns that into a message naming the file.
    """
    opening = _REPOS_ARRAY.search(text)
    if opening is None:
        raise ValueError("no 'repos' array")
    closing = _matching_bracket(text, opening.end() - 1)
    if closing is None:
        raise ValueError("the 'repos' array is never closed")

    before = text[:closing]
    # A last entry with no trailing comma is legal TOML and common in
    # hand-written files; the new line needs one in front of it either way.
    trimmed = before.rstrip()
    if trimmed and trimmed[-1] not in "[,":
        before = f"{trimmed},\n"
    elif not before.endswith("\n"):
        before = f"{before}\n"
    return f"{before}{line}\n{text[closing:]}"


def _matching_bracket(text: str, opening_index: int) -> int | None:
    """The index of the ``]`` closing the ``[`` at *opening_index*.

    Counts nesting and skips anything inside a quote or a comment, so
    neither a bracket in a string nor an apostrophe in a comment — "the
    project's own repository" — can end the array early. That apostrophe is
    not a hypothetical: it is in the first `.cgs` this was tried on.
    """
    depth = 0
    quote = ""
    index = opening_index
    while index < len(text):
        character = text[index]
        if quote:
            if character == "\\":
                index += 2
                continue
            if character == quote:
                quote = ""
        elif character == "#":
            newline = text.find("\n", index)
            index = len(text) if newline == -1 else newline
            continue
        elif character in "\"'":
            quote = character
        elif character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def creation_command(entry: dict[str, Any]) -> str:
    """The one command that creates the repository, for the user to run.

    `init` proposes and waits; `cgitsync repo create` is what runs this for
    you, by handing it to the provider's own tool. Either way **no
    credential is read, stored or sent by this project** — that was always
    the reason it refused to create repositories, and it is the half of the
    rule that stayed. See `provider.py`.
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
    "entry_already_present",
    "insert_repo_entry",
    "format_mount_entry",
    "memory_branch",
    "memory_mount_path",
    "memory_repository_id",
    "mount_entry",
    "uncommitted_memory_paths",
]

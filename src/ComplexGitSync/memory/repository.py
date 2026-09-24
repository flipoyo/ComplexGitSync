"""repository — what a memory needs in order to be a repository.

Ring: 1 (filesystem; decides what to do, never runs Git itself)
Contract: propose the `.cgs` entry that mounts a memory, say what a memory
    holds that is worth committing, and write the commit message for it.
    Every Git command is run by the caller through `git_runner.py`, as it is
    for every other repository in the tree.
Imports: git_branch, universal_clock

Why the Git stays outside
-------------------------
A memory is a private/local repository like ``.localSpec`` or ``.claude``:
one shared repository, one branch per project, mounted at
``.cgitsync/.memory``. Nothing about cloning, committing or pushing it is
special, so nothing here learns to do any of it — this module decides
*what* and `orchestre.py` asks `git_runner.py` to do it. The package
docstring states that boundary; this is the module that would have broken
it first.

Why the mount nests inside ``.cgitsync`` rather than being it
---------------------------------------------------------------
``.cgitsync`` is every workspace's own local state area — States, the
ledger, commit logs, run logs — written by every command, memory-mounted
workspace or not. A git worktree that is *also* written to by whatever
command happens to be running can never reliably be checked out or merged:
`WorkingTransitionState` (``.agent/.local/.localSpec/DevTickets/openTickets/memory-dev_1-2_WorkingTransitionState_DevPlanTicket.md``)
is the record of hitting that live, on this project's own tree.
``.cgitsync/.memory`` is the fix — the git-tracked mount sits one level
inside `.cgitsync`, so nothing but ``memory push``'s own fold step ever
writes into its worktree. Everything else under ``.cgitsync`` — outside
``.memory/`` — is the pending increment: written exactly as before this
milestone, since `.cgitsync` itself never moved.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..git_branch import DEFAULT_BRANCH, private_local_branch
from ..universal_clock import ClockProtocol

#: The workspace's own state area — every command's live-write target,
#: mounted or not. Not owned by this module (it predates the memory
#: feature; see `settings.py`), but named here as the one place the mount
#: path is built from it.
_STATE_DIR_NAME = ".cgitsync"

#: The subdirectory, inside the workspace's state area, that becomes a real
#: git repository once a memory is adopted — the frontier between what the
#: memory already holds (`.memory/`) and what has accumulated since the
#: last `memory push` (everything else under `.cgitsync`).
MEMORY_SUBDIR_NAME = ".memory"

#: Where a memory is mounted in the tree it remembers.
MOUNT_PATH = f"{_STATE_DIR_NAME}/{MEMORY_SUBDIR_NAME}"

#: The repository every project's memory is a branch of. One repository,
#: one branch per project — the owner's decision of 2026-09-16, reversing
#: the architecture's D2. See MemoryArchitecture §2.2 for what it costs.
DEFAULT_MEMORY_REPOSITORY = ".memory"

#: The subdirectory, inside a memory's own mount, that becomes a *second*
#: real git repository once self-history is adopted (AgentReport WP2) — one
#: level deeper than ``MEMORY_SUBDIR_NAME``, nested inside it rather than
#: beside it so `git_tree.propagate_privacy` makes it private/local for
#: free, the same argument the ticket's §2 makes for nesting it here at all.
SELF_HISTORY_SUBDIR_NAME = ".self-history"

#: The repository self-history is a branch of, mirroring
#: ``DEFAULT_MEMORY_REPOSITORY``: one repository, one branch per project.
DEFAULT_SELF_HISTORY_REPOSITORY = ".self-history"

#: The nested `.cgs` `.memory`'s own entry names explicitly — never
#: ``"auto"`` — because a memory's own `.cgs/` directory of exported reboot
#: specs would otherwise have to be told apart from a real nested file (see
#: `mount_entry`'s own comment on `nested_config`).
CONFIG_MEMORY_FILENAME = "config-memory.cgs"


def memory_repository_id(owner: str, *, provider: str = "github") -> str:
    """The repository id a memory is proposed under: ``github:<owner>/.memory``."""
    return f"{provider}:{owner}/{DEFAULT_MEMORY_REPOSITORY}"


def self_history_repository_id(owner: str, *, provider: str = "github") -> str:
    """The repository id self-history is proposed under: ``github:<owner>/.self-history``."""
    return f"{provider}:{owner}/{DEFAULT_SELF_HISTORY_REPOSITORY}"


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
        # A memory is a leaf, never a parent tree, and it holds its own
        # directory named .cgs (the stable per-branch spec copies) that
        # "auto" nested-config discovery would otherwise have to be told
        # apart from a real nested .cgs file.
        "nested_config": "disabled",
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


def commit_message(project_name: str, states: int, entries: int, *, clock: ClockProtocol) -> str:
    """What a memory's own commit says.

    Written here rather than in the CLI because it is a fact about the
    memory, and because `CLAUDE.md`'s rule about commit messages governs
    what *people* write; this is the tool's own bookkeeping, like the
    `--commit-gitignore` message, and it says plainly what it carries.

    ``clock`` is required rather than defaulted to a real one: a default
    that quietly reads the wall clock is exactly the seam
    `.agent/.local/.localSpec/DevTickets/archive/20260920_ClockSeam_DevPlanTicket.md`
    closed elsewhere and `main_1-1_UniversalClock_DevPlanTicket.md`
    generalises — this is one of the two sites a test asserts on the exact
    string produced, so it stays explicit rather than gaining a default
    the caller could forget to override.
    """
    moment = f"{clock.now():%Y-%m-%d}"
    return (
        f"{project_name} memory, {moment}: {states} state(s), {entries} ledger entr(ies)"
    )


def self_history_commit_message(project_name: str, records: int, *, clock: ClockProtocol) -> str:
    """What self-history's own commit says — `commit_message`'s sibling.

    Counts records, not States and ledger entries: self-history is not the
    memory, it is a second repository nested inside it, and its commit
    should say what it actually holds.
    """
    moment = f"{clock.now():%Y-%m-%d}"
    return f"{project_name} self-history, {moment}: {records} record(s)"


def memory_mount_path(workspace: Path) -> Path:
    """Where the memory's git repository sits in *workspace*: ``.cgitsync/.memory``."""
    return workspace / MOUNT_PATH


def memory_pending_path(workspace: Path) -> Path:
    """Where a memory's not-yet-folded content accumulates: ``.cgitsync``.

    The mount's own parent — one directory holds both, so a workspace with
    no memory mounted needs no separate concept: everything just lives
    directly under this path, exactly as before `MEMORY_SUBDIR_NAME` ever
    existed, and `memory_mount_path` simply has nothing under it yet.
    """
    return memory_mount_path(workspace).parent


def self_history_mount_path(workspace: Path) -> Path:
    """Where self-history's git repository sits: ``.cgitsync/.memory/.self-history``.

    One level inside the memory's own mount, per the ticket's §2 table —
    the folded half. Its pending half is `.cgitsync/.self-history`, a
    sibling of the memory's own mount rather than nested under it; see
    ``memory.self_history.self_history_dirs``, which owns that pair, the
    same way this module owns the memory's own mount/pending pair above.
    """
    return memory_mount_path(workspace) / SELF_HISTORY_SUBDIR_NAME


def config_memory_path(workspace: Path) -> Path:
    """Where the nested `.cgs` that discovers self-history lives, once adopted."""
    return memory_mount_path(workspace) / CONFIG_MEMORY_FILENAME


def config_memory_document(memory_owner: str, project_default_branch: str) -> str:
    """The `config-memory.cgs` text that makes self-history discoverable.

    Two entries: the memory re-asserting its own identity at
    ``relative_path = "."`` — the same self-reference
    ``docs/DocCGS.cgs`` used for ``DocComplexGitSync`` before DocSpec's own
    de-nesting, which resolves to an absolute path already registered and
    is therefore a safe no-op rather than a duplicate child (see
    `discovery.discover_nested_configs`'s ``registered_paths`` guard) — and
    self-history itself, nested at ``.self-history``. Neither entry states
    its own branch: both fall back to this document's own
    ``project.default_branch``, so self-history's branch can never drift
    from the memory's own without this file changing too.
    """
    memory_repository = memory_repository_id(memory_owner)
    self_history_repository = self_history_repository_id(memory_owner)
    return (
        f'project = {{ name = "{DEFAULT_MEMORY_REPOSITORY}", '
        f'default_branch = "{project_default_branch}" }}\n'
        "\n"
        "repos = [\n"
        f'    {{ repository = "{memory_repository}", relative_path = "." }},\n'
        f'    {{ repository = "{self_history_repository}", '
        f'relative_path = "{SELF_HISTORY_SUBDIR_NAME}", fallback_branch = "main", '
        "private = true, writable = true },\n"
        "]\n"
    )


__all__ = [
    "CONFIG_MEMORY_FILENAME",
    "DEFAULT_MEMORY_REPOSITORY",
    "DEFAULT_SELF_HISTORY_REPOSITORY",
    "MEMORY_SUBDIR_NAME",
    "MOUNT_PATH",
    "SELF_HISTORY_SUBDIR_NAME",
    "commit_message",
    "config_memory_document",
    "config_memory_path",
    "creation_command",
    "entry_already_present",
    "insert_repo_entry",
    "format_mount_entry",
    "memory_branch",
    "memory_mount_path",
    "memory_pending_path",
    "memory_repository_id",
    "mount_entry",
    "self_history_commit_message",
    "self_history_mount_path",
    "self_history_repository_id",
    "uncommitted_memory_paths",
]

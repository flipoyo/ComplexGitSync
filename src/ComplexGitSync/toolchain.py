"""toolchain — which versions of which tools produced a record.

Ring: 2 (asks git_runner, which owns the subprocess boundary; imports no
    subprocess of its own)
Contract: the five version strings a ledger entry carries — cgitsync, git,
    pixi, and the data backends dvc and git-lfs — read at most once per
    process and reported as `none` when a tool is not installed.
Imports: git_runner

Why a memory records this
-------------------------
A memory is evidence, and evidence that does not say what produced it is
worth less than it looks. "A tree was synchronised on 4 March" answers much
less than "…by cgitsync 2.64 driving git 2.39.5", when the question two
years later is why a restored release does not match.

The owner asked for it in those words
(``.agent/.local/.localSpec/DevTickets/archive/.closedUserTicket/20260916_memory-dependencies.md``):
record the versions used at state genesis and at the time of each record.

Three rules, settled with the owner on 2026-09-16 and stated in
``.agent/.local/.localSpec/AdditionalSpecs.md``, *The hash-chained register*:

1. **All five, in every entry.** Each line stands on its own; a truncated
   or partly synced chain still says what made each record.
2. **`none` when a tool is not installed** — the word, never an empty
   string, which reads like "not asked" rather than "asked, and there is
   none".
3. **Asked at most once per command.** ``dvc --version`` starts a Python
   interpreter and takes about a second; asking it per entry would be felt
   on every ``cgitsync status`` in a data workspace. A backend is asked
   only when something actually used it.

**Versions are provenance, never identity.** They describe the machine that
observed a tree, not the tree, so they never enter a State's name — two
machines holding the same tree with different git versions must agree on
what they hold. See ``.agent/.local/.localSpec/AdditionalSpecs.md``, *What a State's name
is computed from*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import __version__

if TYPE_CHECKING:
    from .git_runner import GitRunner

#: What a tool that is not installed records. A word, so a reader and a
#: parser see the same thing.
ABSENT = "none"

#: The tools every entry reports, in the order the owner named them.
TOOLS = ("cgitsync", "git", "pixi", "dvc", "git-lfs")

#: Executable name per tool, for the four that are external programs.
_EXECUTABLES = {
    "git": "git",
    "pixi": "pixi",
    "dvc": "dvc",
    "git-lfs": "git-lfs",
}

#: Read once per process, reused for every entry a command writes.
_CACHE: dict[str, str] = {}


def tool_version(name: str, git_runner: GitRunner) -> str:
    """The recorded version of *name*, or :data:`ABSENT`.

    Cached for the life of the process: a command that writes several
    entries asks each tool once, and a command that never touches a data
    backend never pays for asking it.
    """
    if name in _CACHE:
        return _CACHE[name]
    if name == "cgitsync":
        version = str(__version__)
    else:
        executable = _EXECUTABLES.get(name)
        reported = git_runner.tool_version(executable) if executable else None
        version = reported or ABSENT
    _CACHE[name] = version
    return version


def toolchain(git_runner: GitRunner, *, backends: bool = False) -> dict[str, str]:
    """The versions to record on an entry written now.

    *backends* is what stops an ordinary workspace paying for a tool it does
    not use: with it false — every command that touched no data repository —
    ``dvc`` and ``git-lfs`` are recorded as :data:`ABSENT` without being
    asked. They are only asked when the operation being recorded actually
    used one.
    """
    recorded = {
        "cgitsync": tool_version("cgitsync", git_runner),
        "git": tool_version("git", git_runner),
        "pixi": tool_version("pixi", git_runner),
    }
    for backend in ("dvc", "git-lfs"):
        recorded[backend] = tool_version(backend, git_runner) if backends else ABSENT
    return recorded


def reset_cache() -> None:
    """Forget every version read so far — for tests, and for a long-lived process."""
    _CACHE.clear()


__all__ = ["ABSENT", "TOOLS", "reset_cache", "tool_version", "toolchain"]

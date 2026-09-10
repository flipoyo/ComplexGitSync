"""Refuse to clear a clone destination that still holds unpushed work.

Ring: 2
Contract: read-only questions about a directory that ``initialise`` is about
    to delete and re-clone. Decides nothing about *whether* a mount point is
    owned outright -- it only answers "would clearing this lose work that
    exists nowhere else?".
Imports: git_repo, git_runner

``_clone_registry_entry`` clears a populated nested destination with
``shutil.rmtree`` before cloning into it. That call was written for the
interrupted-clone case: a half-written directory from a run that died has to
go before a fresh clone can land. Nothing distinguished that case from a
repository somebody had been working in all week, so a plain ``initialise``
on a populated workspace destroyed unpushed commits and uncommitted edits,
with the reflog left holding a single ``clone:`` entry and nothing to
recover from.

This module is the guard that distinguishes them. It asks two read-only
questions of each destination and never touches a worktree, which is what
lets the caller ask about *every* pending repository before deleting any of
them -- the same shape as ``operations.merge_tree``'s preflight, and for the
same reason: a refusal anywhere must leave everything on disk.

What it deliberately does not do: decide whether a mount point belongs
outright to the repository mounted there. That is the wider question
``AppendCloneMode`` asks about the same ``rmtree``, and about the second
erasure site (``force_pull`` running ``git clean -fd``) that this module
does not cover. A guard that refuses to destroy unpushed work is compatible
with either answer that ticket reaches.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .git_repo import WorkingRepo
    from .git_runner import GitRunner

__all__ = ["BlockedDestination", "blocked_destinations", "format_block_error"]


@dataclass(frozen=True)
class BlockedDestination:
    """One repository that must not be cleared, and why."""

    name: str
    path: Path
    reason: str


def is_populated_destination(path: Path) -> bool:
    """Return ``True`` when *path* is a directory holding anything at all.

    This is the condition that used to trigger the ``rmtree`` on its own.
    """
    return path.is_dir() and next(path.iterdir(), None) is not None


def is_git_checkout(path: Path) -> bool:
    """Return ``True`` when *path* looks like a Git working tree.

    A ``.git`` entry, file or directory -- a worktree or submodule checkout
    records it as a file, so this must not insist on a directory.
    """
    return (path / ".git").exists()


def destination_block_reason(path: Path, git_runner: "GitRunner") -> str | None:
    """Return why clearing *path* would lose work, or ``None`` when it is safe.

    Safe means one of two things: the directory is not a Git checkout at all
    (the interrupted-clone case the ``rmtree`` exists for), or everything in
    it can be fetched again -- a clean worktree whose commits all live on some
    remote.

    The commit question is asked as "which commits here does no remote hold?"
    rather than "is this branch ahead of its upstream". The sharper form
    matters in both directions: a branch with no upstream still blocks when it
    carries local commits, and a detached HEAD parked on a commit the remote
    already has does *not* block -- which is precisely what a submodule
    checkout looks like, and blocking those would break
    ``init-from-submodules`` for no gain.

    Every question here is read-only. None of them writes to the worktree,
    the index, or the remote.
    """
    if not is_git_checkout(path):
        return None

    if git_runner.status_porcelain(path):
        return "uncommitted changes"

    local_only = git_runner.local_only_commit_count(path)
    if local_only:
        commits = "commit" if local_only == 1 else "commits"
        branch = git_runner.current_branch(path) or "HEAD"
        where = f"on '{branch}'" if branch != "HEAD" else "on a detached HEAD"
        return f"{local_only} {commits} {where} that no remote has"

    return None


def blocked_destinations(
    entries: Iterable["WorkingRepo"],
    git_runner: "GitRunner",
) -> list[BlockedDestination]:
    """Return every pending entry whose destination holds unpushed work.

    Ordered by path so the error message is stable between runs, and so a
    test can assert on it without sorting first.
    """
    blocked: list[BlockedDestination] = []
    for entry in entries:
        path = entry.absolute_path
        if not is_populated_destination(path):
            continue
        reason = destination_block_reason(path, git_runner)
        if reason is not None:
            blocked.append(BlockedDestination(name=entry.name, path=path, reason=reason))
    return sorted(blocked, key=lambda item: str(item.path))


def format_block_error(blocked: Sequence[BlockedDestination]) -> str:
    """Build the single refusal message naming every blocked repository.

    One message for the whole run, not one per repository: someone who has
    to clear three repositories should learn that from the first refusal
    rather than from three consecutive ones.
    """
    lines = [
        "initialise would delete and re-clone these repositories, and each "
        "holds work that exists nowhere else:",
        "",
    ]
    lines.extend(f"  {item.name} ({item.path}): {item.reason}" for item in blocked)
    lines.extend(
        [
            "",
            "Nothing has been deleted. Either commit and push the work above, "
            "or re-run with --force-reclone to delete it deliberately.",
        ]
    )
    return "\n".join(lines)

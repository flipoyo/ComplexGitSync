"""workspace_locking — the client's writing methods each hold the workspace lock for their whole run.

Ring: 3
Contract: ``locks_writes`` wraps every method named in ``WRITING_METHODS`` (and ``WRITING_WHEN``'s, only when
    its flag is true) so it
    takes ``.cgitsync/.lock`` (``WorkspaceLock``) before it runs and gives it
    back after, which is how the CLI and a Python caller are covered by the
    same code. A workspace with no ``.cgitsync`` yet has nothing to protect, so
    the method runs as before. A fresh client (nothing loaded) that is handed a
    source path, as ``pull`` is, locks the workspace that path sits in. Read-only
    methods are never wrapped.
Imports: workspace_lock

Design: StateLocking D1 (the client, not the CLI, takes the lock).
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from ..tree.workspace_lock import WorkspaceLock

#: Every client method that changes a repository or the workspace's state area.
WRITING_METHODS = (
    "pull", "pull_force", "restart", "refresh_private", "autofix", "checkout", "branch", "close_branch", "delete_branch", "commit",
    "merge", "merge_into", "merge_resolve", "merge_resolve_all", "add", "remove", "push", "tag", "git",
    "freeze_release", "freeze", "load_release", "memory_push", "memory_reboot", "memory_merge",
    "memory_adopt", "memory_migrate", "memory_branch", "memory_setup", "self_history_add", "self_history_adopt",
)

#: Methods that write only when one argument is true: ``verify(repair=True)`` rewrites the ``HEAD`` cache,
#: ``verify()`` reads and never waits for the lock (CliReduction, ``verify repair``).
WRITING_WHEN = {"verify": "repair"}

T = TypeVar("T", bound=type)


def locks_writes(cls: T) -> T:
    """Class decorator: take the workspace lock around each of ``WRITING_METHODS``."""
    for name in WRITING_METHODS:
        setattr(cls, name, _locked(getattr(cls, name)))
    for name, flag in WRITING_WHEN.items():
        setattr(cls, name, _locked(getattr(cls, name), only_when=flag))
    return cls


def _workspace(client: Any, given: dict[str, Any]) -> Path:
    """The workspace a call writes: its ``cgshome``, else the one its source path sits in, else the client's own."""
    if given.get("cgshome"):
        return Path(given["cgshome"])
    source = given.get("source_path") or given.get("config_path")
    if client.registry is None and source is not None:
        resolved = Path(source).resolve()
        found = next((parent for parent in (resolved.parent, *resolved.parents) if (parent / ".cgitsync").is_dir()), None)
        if found is not None:
            return found
    return client._workspace_root()


def _locked(method: Callable[..., Any], *, only_when: str | None = None) -> Callable[..., Any]:
    signature = inspect.signature(method)

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        given = signature.bind_partial(self, *args, **kwargs).arguments
        state_dir = _workspace(self, given) / ".cgitsync"
        if not state_dir.is_dir() or (only_when is not None and not given.get(only_when)):
            return method(self, *args, **kwargs)
        with WorkspaceLock(state_dir, self.clock).hold(method.__name__):
            return method(self, *args, **kwargs)

    return wrapper


def unlock_workspace(cgshome: str | Path, clock: Any, *, force: bool = False) -> Any:
    """Clear the lock a killed command left in *cgshome*; the holder it had, or ``None`` when there was none."""
    return WorkspaceLock(Path(cgshome) / ".cgitsync", clock).clear(force=force)


__all__ = ["WRITING_METHODS", "WRITING_WHEN", "locks_writes", "unlock_workspace"]

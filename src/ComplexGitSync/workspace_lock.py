"""workspace_lock — one advisory lock per workspace, so two cgitsync runs never write at once.

Ring: 1 (filesystem only, no subprocess)
Contract: ``WorkspaceLock`` takes ``.cgitsync/.lock`` for the whole of one writing
    command and releases it at the end. A second command that finds it held
    is refused, naming the workspace, the holder and the lock's age, before
    it touches any repository. A lock whose process is gone is *stale*: it
    refuses too, and ``clear`` (``cgitsync unlock``) is the one way to remove it.
Imports: errors, universal_clock

Design: StateLocking (``AdditionalSpecs.md``, *The workspace lock*).

The lock is a file made with ``O_CREAT | O_EXCL``, so exactly one process
creates it. It holds the process id, the moment it was taken and the command
that took it, and nothing that names a person or a machine. It lives beside
``.cgitsync/logs/``, never inside the ``.memory`` mount, so the fold never
moves it and a push never carries it. Within one process the lock is
re-entrant: ``push`` calling ``memory_push`` does not meet itself.
"""

from __future__ import annotations

import json
import os
import threading
import tomllib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from .errors import GitSyncError
from .universal_clock import ClockProtocol, SystemClock

LOCK_FILE_NAME = ".lock"


class WorkspaceBusyError(GitSyncError):
    """Another cgitsync holds this workspace's lock, or left one behind."""


@dataclass(frozen=True)
class LockHolder:
    """What the lock file says about the command that took it."""

    pid: int
    started_at: str
    command: str


def process_exists(pid: int) -> bool:
    """Whether a process with this id is running on this machine (signal 0 only checks)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists, and is somebody else's
    except OSError:
        return False
    return True


class WorkspaceLock:
    """The lock of one workspace's ``.cgitsync`` directory."""

    #: Locks this process holds, by directory, with how many writing calls are inside each.
    _held: ClassVar[dict[Path, int]] = {}
    _guard: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, state_dir: Path, clock: ClockProtocol | None = None, *, exists: Callable[[int], bool] = process_exists) -> None:
        self.state_dir = state_dir.resolve()
        self.path = self.state_dir / LOCK_FILE_NAME
        self.clock = clock or SystemClock()
        self.exists = exists

    @contextmanager
    def hold(self, command: str) -> Iterator[None]:
        """Take the lock for the body of the ``with``, or raise :class:`WorkspaceBusyError` before it runs."""
        with self._guard:
            nested = self._held.get(self.state_dir, 0) > 0
            if not nested:
                self._create(command)
            self._held[self.state_dir] = self._held.get(self.state_dir, 0) + 1
        try:
            yield
        finally:
            with self._guard:
                self._held[self.state_dir] -= 1
                if self._held[self.state_dir] == 0:
                    del self._held[self.state_dir]
                    self.path.unlink(missing_ok=True)

    def holder(self) -> LockHolder | None:
        """Who holds the lock file, or ``None`` when there is none (or it is unreadable)."""
        try:
            table = tomllib.loads(self.path.read_text(encoding="utf-8"))
            return LockHolder(int(table["pid"]), str(table["started_at"]), str(table["command"]))
        except (OSError, ValueError, KeyError):
            return None

    def is_stale(self, holder: LockHolder) -> bool:
        return not self.exists(holder.pid)

    def clear(self, *, force: bool = False) -> LockHolder | None:
        """Remove a stale lock and return who held it; ``None`` when there was none.

        A lock whose process still runs is refused unless *force*.
        """
        if not self.path.exists():
            return None
        holder = self.holder()
        if holder is not None and not self.is_stale(holder) and not force:
            raise WorkspaceBusyError(f"process {holder.pid} is still running {holder.command!r} in {self.state_dir.parent}; not clearing its lock (--force removes it anyway).")
        self.path.unlink(missing_ok=True)
        return holder

    def age(self, holder: LockHolder) -> str:
        """How long ago the lock was taken, as ``1h 02m 03s``."""
        seconds = max(0, int((self.clock.now() - datetime.fromisoformat(holder.started_at)).total_seconds()))
        hours, rest = divmod(seconds, 3600)
        minutes, seconds = divmod(rest, 60)
        return (f"{hours}h " if hours else "") + (f"{minutes:02d}m " if hours or minutes else "") + f"{seconds:02d}s"

    def _create(self, command: str) -> None:
        body = f"pid = {self.clock.pid()}\nstarted_at = {json.dumps(self.clock.now().isoformat())}\ncommand = {json.dumps(command)}\n"
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            raise self._refusal() from None
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)

    def _refusal(self) -> WorkspaceBusyError:
        workspace, holder = self.state_dir.parent, self.holder()
        if holder is None:
            return WorkspaceBusyError(f"Another cgitsync is taking the lock of {workspace} right now. Nothing was touched; try again.")
        since = f"since {holder.started_at} ({self.age(holder)} ago)"
        if self.is_stale(holder):
            return WorkspaceBusyError(f"{workspace} holds a stale lock, left {since} by {holder.command!r} (process {holder.pid}, which is gone). Nothing was touched. 'cgitsync unlock' clears it.")
        return WorkspaceBusyError(f"Another cgitsync is working in {workspace} {since}: {holder.command!r} (process {holder.pid}). Nothing was touched; wait for it to finish.")


__all__ = ["LOCK_FILE_NAME", "LockHolder", "WorkspaceBusyError", "WorkspaceLock", "process_exists"]

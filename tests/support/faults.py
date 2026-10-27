"""faults — make one named Git call fail on its nth call, without any code in ``src/``.

Robustness D1. Two modes, because they prove different things (Q3):
- ``raise``: the call raises ``GitSyncError`` and the command carries on in the
  same process. This is the error path. It never claims to cover a crash.
- ``kill``: the command runs in a child Python process that calls
  ``os._exit(137)`` at the call, with no ``finally`` and no context-manager exit,
  and the test then reads the disk.

Deterministic: no sleep, no randomness, no network.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from ComplexGitSync.errors import GitSyncError

KILLED = 137


class FaultingGitRunner:
    """The real runner, but ``method``'s nth call (1-based) raises ``GitSyncError``.

    Everything else is delegated untouched. ``calls`` records every call of
    ``method``, so a test can say which repository the fault landed on.
    """

    def __init__(self, inner: Any, method: str, nth: int, *, message: str = "simulated failure") -> None:
        self._inner, self._method, self._nth, self._message = inner, method, nth, message
        self.calls: list[tuple[Any, ...]] = []

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if name != self._method:
            return attribute

        def faulty(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(args)
            if len(self.calls) == self._nth:
                raise GitSyncError(f"{self._message}: {name} call {self._nth}")
            return attribute(*args, **kwargs)

        return faulty


def run_cgitsync_killed(argv: list[str], target: str, nth: int, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run ``cgitsync <argv>`` in a child that dies with ``os._exit(137)`` at the nth call of *target*.

    *target* is a dotted path to a callable, such as
    ``ComplexGitSync.git_runner.GitRunner.create_tag``.
    """
    program = textwrap.dedent(
        """
        import importlib, os, sys
        target, nth = sys.argv[1], int(sys.argv[2])
        module_path, _, attribute = target.rpartition(".")
        owner = module_path
        while True:
            try:
                holder = importlib.import_module(owner)
                break
            except ImportError:
                owner, _, tail = owner.rpartition(".")
        parts = module_path[len(owner):].lstrip(".").split(".") if module_path != owner else []
        for part in parts:
            holder = getattr(holder, part)
        real, seen = getattr(holder, attribute), []

        def dying(*args, **kwargs):
            seen.append(1)
            if len(seen) == nth:
                os._exit(137)
            return real(*args, **kwargs)

        setattr(holder, attribute, dying)
        from ComplexGitSync.cli import main
        raise SystemExit(main(sys.argv[3:]))
        """
    )
    return subprocess.run([sys.executable, "-c", program, target, str(nth), *argv], cwd=cwd, text=True, capture_output=True)

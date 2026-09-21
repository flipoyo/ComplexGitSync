"""universal_clock — the one module that reads the wall clock.

Ring: 1 (the sole reader of the real wall clock, high-resolution counter,
    PID and entropy source anywhere in ``src/ComplexGitSync/`` — filesystem
    ring, no subprocess)
Contract: define :class:`ClockProtocol`, the interface every dated fact this
    project writes is injected through, and :class:`SystemClock`, the one
    real implementation. Every other module that needs "now" imports from
    here rather than reading ``datetime``/``time``/``os``/``secrets``
    itself, so a test replaces one object instead of monkeypatching a
    module-level ``datetime`` in nine different files.
Imports: none

Why this exists
----------------
Before this module, the real clock implementation (``SystemClock``) lived
inside ``orchestre.py`` — Ring 3. A Ring 1 or Ring 2 module cannot import
Ring 3, so ``settings.py``, ``paths.py``, ``registry.py`` and
``memory/store.py`` had no seam to reach for and read ``datetime.now(UTC)``
directly instead. Moving the implementation down to the lowest ring a clock
read may occupy — Ring 0 is I/O-free by rule, clock reads named explicitly
— makes it reachable from every module that needs it.

See ``.agent/.local/.localSpec/DevTickets/openTickets/main_1-1_UniversalClock_DevPlanTicket.md``
for the design this module implements, and
``.agent/.local/.localSpec/DevTickets/archive/20260920_ClockSeam_DevPlanTicket.md`` for
the two call sites (`orchestre.memory_reboot`'s archive name,
`memory/repository.commit_message`'s commit moment) that already used an
injectable clock before this module existed — `ledger_entry.ClockProtocol`,
which stays where it is: Ring 0 must be self-contained, so it keeps a
structurally identical Protocol of its own rather than importing this
(higher-ring) one. Protocols are structural, so a `SystemClock` from here
satisfies both without either module importing the other.
"""

from __future__ import annotations

import os
import secrets
import time
from datetime import UTC, datetime
from typing import Protocol


class ClockProtocol(Protocol):
    """Everything a caller needs to inject to make a dated fact
    deterministic under test — no direct clock, PID, or entropy read
    anywhere else in the package.
    """

    def now(self) -> datetime:
        """Current instant. Must be timezone-aware; UTC is assumed."""
        ...

    def time_ns(self) -> int:
        """High-resolution nanosecond counter, for anchor entropy."""
        ...

    def pid(self) -> int:
        """Current process id, for anchor entropy."""
        ...

    def token_hex(self, nbytes: int) -> str:
        """Random hex token, for anchor entropy."""
        ...


class SystemClock:
    """Real :class:`ClockProtocol` implementation.

    The only place in the package that reads the real wall clock, PID or
    entropy source — every other module goes through an injected
    :class:`ClockProtocol`, real (an instance of this class) or fake, and
    never reads ``datetime``/``time``/``os``/``secrets`` directly.
    """

    def now(self) -> datetime:
        return datetime.now(UTC)

    def time_ns(self) -> int:
        return time.time_ns()

    def pid(self) -> int:
        return os.getpid()

    def token_hex(self, nbytes: int) -> str:
        return secrets.token_hex(nbytes)


__all__ = ["ClockProtocol", "SystemClock"]

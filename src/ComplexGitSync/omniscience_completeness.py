"""omniscience_completeness — whether a project's registered repositories are all still there.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: from a project's observations, per target the latest finding, and
    the project's report: ``ok``, ``warning`` or ``failure``. A repository the
    host says is not there is a warning first, so that a provider's
    technical trouble does not condemn a project, and becomes a permanent
    failure only when it has been repeated for long enough.
Imports: omniscience_observation

The owner's rule (OmniscienceAppend, D4): a registered repository that is
``NOT_FOUND`` is first a warning; if it is repeated over a long time it is an
error of the permanent kind. "Repeated over a long time" is these two numbers,
which are this module's and nobody else's: at least ``PERMANENT_AFTER_COUNT``
consecutive ``NOT_FOUND`` findings on one target whose first and last are at
least ``PERMANENT_AFTER`` apart. Any other finding in between, ``AVAILABLE``
included, starts the count again: a repository that reappears is not a
failure, and its earlier findings stay on the record. Only ``NOT_FOUND``
escalates. ``ACCESS_DENIED`` and ``UNREACHABLE`` are the host's or the
network's, and stay warnings however often they recur.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .omniscience_observation import Observation, ObservationStatus

#: How long a target must have been ``NOT_FOUND`` before it is permanent.
PERMANENT_AFTER = timedelta(days=7)

#: And how many consecutive findings must say so.
PERMANENT_AFTER_COUNT = 2

#: Statuses that are no cause for concern.
_QUIET = frozenset({ObservationStatus.AVAILABLE, ObservationStatus.CLOSED, ObservationStatus.DELETION_CONFIRMED})


def _moment(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class CompletenessReport:
    """The latest finding per target, and the verdict on the project."""

    latest: tuple[Observation, ...]
    warnings: tuple[str, ...]
    failures: tuple[str, ...]

    @classmethod
    def of(cls, observations: Iterable[Observation]) -> CompletenessReport:
        """The report over *observations*, in any order."""
        by_target: dict[tuple[str, str, str], list[Observation]] = {}
        for item in sorted(observations, key=lambda o: (_moment(o.observed_at), o.observation_hash)):
            by_target.setdefault((item.os_id, item.target_kind, item.target), []).append(item)
        latest: list[Observation] = []
        warnings: list[str] = []
        failures: list[str] = []
        for key in sorted(by_target):
            history = by_target[key]
            last = history[-1]
            latest.append(last)
            if last.status in _QUIET:
                continue
            label = f"{last.target_kind} {last.target}: {last.status.value}"
            run = cls._not_found_run(history)
            if run and len(run) >= PERMANENT_AFTER_COUNT and _moment(run[-1].observed_at) - _moment(run[0].observed_at) >= PERMANENT_AFTER:
                failures.append(f"{label} for {len(run)} observations since {run[0].observed_at}: permanent failure")
            else:
                warnings.append(label + (f" (since {run[0].observed_at})" if run else ""))
        return cls(tuple(latest), tuple(warnings), tuple(failures))

    @staticmethod
    def _not_found_run(history: list[Observation]) -> list[Observation]:
        """The trailing consecutive ``NOT_FOUND`` findings, oldest first; empty when the latest is something else."""
        run: list[Observation] = []
        for item in reversed(history):
            if item.status is not ObservationStatus.NOT_FOUND:
                break
            run.append(item)
        return run[::-1]

    @property
    def verdict(self) -> str:
        """``failure`` if any target is a permanent failure, else ``warning`` if any is a cause for concern, else ``ok``."""
        return "failure" if self.failures else "warning" if self.warnings else "ok"

    def to_dict(self) -> dict[str, Any]:
        """The report as plain data, for ``--json``."""
        return {
            "verdict": self.verdict,
            "warnings": list(self.warnings),
            "failures": list(self.failures),
            "latest": [{"kind": o.target_kind, "repository": o.target, "status": o.status.value, "observed_at": o.observed_at} for o in self.latest],
        }


__all__ = ["PERMANENT_AFTER", "PERMANENT_AFTER_COUNT", "CompletenessReport"]

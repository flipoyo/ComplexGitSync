"""The pair rule's gate: an archived planning ticket needs its orchestrator's record.

Ring: 1
Contract: decide whether every planning ticket archived on or after the
    cut-off has a self-history record written by an orchestrator, and name
    each one that has none. Reads the tree's ``DevTickets/archive`` and its
    self-history; runs no Git -- the caller says which files a commit adds.
Imports: errors, memory

**Why it exists.** The pair rule (``AgentConduct.md`` §4: a worker
implements, an independent orchestrator quotes the work and writes the
record) was written, cited in the digest and read every session, and still
broken, because breaking it cost nothing when the work was handed over. The
commit-message rule stopped being broken the day ``cgitsync commit`` began
to refuse; this is the same move for the pair rule (the PairRuleGate
ticket).

**What it checks.** A ticket ``YYYYMMDD_<Name>_DevPlanTicket.md`` at the top
of the archive, stamped on or after :data:`CUTOFF`, needs at least one
record, pending or folded, whose ``ticket`` is ``<Name>``, whose
orchestrator's role is ``Orchestration``, and whose worker's role is a
different one. Tickets archived before the cut-off are exempt: nobody wrote
records for them at the time, and back-filling would invent history.

**What it cannot prove.** That the orchestrator really was a separate
agent: one agent can write a record naming two roles. The gate turns
"forgot" into "refused"; it does not stop someone who means to lie, and
nothing that calls it should claim otherwise.

**Whom it binds.** A tree that adopted DevSpec (its root holds
``AgentConduct.md``) and keeps a ``DevTickets/archive`` in one of its
``.agent/.local`` mounts. Any other tree gets ``None``, as with
:class:`~ComplexGitSync.commit_message.CommitMessagePolicy`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import GitSyncError
from .memory.self_history import SelfHistoryRecord

__all__ = ["CUTOFF", "TicketGate"]

#: The first archive stamp the gate checks (owner, 2026-10-09, PairRuleGate D2).
CUTOFF = "20261009"

_CONDUCT_PATH = Path(".agent") / ".distant" / "dev-sync" / "AgentConduct.md"
_ARCHIVED = re.compile(r"^(?P<stamp>\d{8})_(?P<name>.+)_DevPlanTicket\.md$")
_ORCHESTRATION = "Orchestration"
#: ``git status --porcelain`` index codes for a path the next commit adds.
_ADDED_CODES = frozenset("ARC")


@dataclass(frozen=True)
class TicketGate:
    """Which archived planning tickets lack an orchestrator's record.

    :meth:`for_tree` is the only place the tree is searched; every other
    method answers from the two directories it found.
    """

    archive_dir: Path
    cgitsync_dir: Path
    cutoff: str = CUTOFF

    @classmethod
    def for_tree(cls, root: Path) -> TicketGate | None:
        """The gate binding the tree rooted at *root*, or ``None``."""
        root = Path(root)
        if not (root / _CONDUCT_PATH).is_file():
            return None
        archives = [path for path in sorted((root / ".agent" / ".local").glob("*/DevTickets/archive")) if path.is_dir()]
        if not archives:
            return None
        return cls(archive_dir=archives[0].resolve(), cgitsync_dir=root / ".cgitsync")

    def gated(self, paths: Iterable[Path] | None = None) -> list[str]:
        """Names of the tickets archived on or after the cut-off.

        With *paths*, only those among them; ``None`` means the whole
        archive. A deep-archived copy is never a candidate: it sits one
        level down, and its history ticket is checked instead.
        """
        if paths is None:
            candidates = sorted(self.archive_dir.glob("*_DevPlanTicket.md"))
        else:
            candidates = [Path(path) for path in paths if Path(path).resolve().parent == self.archive_dir]
        names: list[str] = []
        for path in candidates:
            match = _ARCHIVED.match(path.name)
            if match and match["stamp"] >= self.cutoff:
                names.append(match["name"])
        return names

    def unrecorded(self, paths: Iterable[Path] | None = None) -> list[str]:
        """The gated tickets no orchestrator's record names, in archive order."""
        names = self.gated(paths)
        if not names:
            return []
        recorded = {
            record.ticket
            for record in self._records()
            if record.orchestrator.role == _ORCHESTRATION and record.worker.role != record.orchestrator.role
        }
        return [name for name in names if name not in recorded]

    def _records(self) -> list[SelfHistoryRecord]:
        """Every record, folded and pending; one that cannot be read refuses, naming its file.

        It fails closed: a record the gate cannot read might be the one that
        names the ticket, so it is never skipped in silence.
        """
        records: list[SelfHistoryRecord] = []
        for directory in SelfHistoryRecord.dirs(self.cgitsync_dir):
            for path in sorted(directory.glob("*.toml")) if directory.is_dir() else ():
                try:
                    records.append(SelfHistoryRecord.read(path))
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    raise GitSyncError(
                        f"self-history record {path} cannot be read ({exc}); the pair-rule "
                        "gate cannot tell whether it names the ticket. Repair or remove it, then retry."
                    ) from exc
        return records

    def require(self, paths: Iterable[Path] | None = None) -> None:
        """Raise :class:`GitSyncError` naming every gated ticket with no record."""
        missing = self.unrecorded(paths)
        if not missing:
            return
        tickets = "\n".join(f"  - {name}" for name in missing)
        raise GitSyncError(
            "commit refused (pair rule, AgentConduct.md §4), nothing was committed: "
            "a planning ticket was archived without its orchestrator's record:\n"
            f"{tickets}\n"
            "Launch the orchestrator, let it quote the work and write the record "
            "with 'cgitsync self-history add --ticket <Name>', then commit again."
        )

    def owning_path(self, repo_paths: Iterable[Path]) -> Path | None:
        """The deepest of *repo_paths* that holds the archive, or ``None``."""
        owners = [Path(path) for path in repo_paths if self.archive_dir.is_relative_to(Path(path).resolve())]
        return max(owners, key=lambda path: len(path.resolve().parts), default=None)

    @staticmethod
    def added_paths(repo_path: Path, porcelain: Sequence[str], *, stage_all: bool) -> list[Path]:
        """What a commit in *repo_path* adds, read from its ``git status --porcelain`` lines.

        A path staged as added, renamed or copied always counts; an untracked
        one only when the commit stages everything first. An untracked
        directory is listed by Git as ``dir/``, so its files are counted.
        """
        added: list[Path] = []
        for line in porcelain:
            code, path = line[:2], line[3:]
            if code[0] in _ADDED_CODES:
                added.append(repo_path / path.split(" -> ")[-1].strip('"'))
            elif code == "??" and stage_all:
                untracked = repo_path / path.strip('"')
                added.extend(sorted(p for p in untracked.rglob("*") if p.is_file()) if path.endswith("/") else [untracked])
        return added

"""status_render — pure text rendering for `cgitsync status`'s repository table.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: given already-computed values (a `WorkingRepo` entry plus a root
    path, a `git status --porcelain` line, or a list of pre-built row
    tuples), format or classify them as text/paths — never runs `git`,
    never reads a file or the clock, and never mutates its input.
Imports: git_repo

Design reference: ``AgentSpec/IsolationPlan.md`` §1/§4 (`status_render.py`
row) and ``AgentSpec/20260828_Isolation_DevPlanTicket.md`` §2 (P5-status,
Wave 2 Lane A). ``ComplexGitSyncClient.status()`` itself is **not** moved
here — it calls ``self.git_runner.current_branch(...)``,
``.rev_parse_head(...)``, ``.upstream_ref(...)``, and
``.branch_tracking_counts(...)`` (real `git` subprocess calls) to build each
row, so it stays Ring 3 orchestration in ``orchestre.py``. This module only
holds the pure formatting/parsing helpers that method calls once each row's
raw values already exist. ``_unmanaged_gitlink_paths`` also stays in
``orchestre.py`` for the same reason — despite living next to these
functions there, it calls ``git_runner.tracked_gitlink_paths(...)``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .git_repo import WorkingRepo


def _status_display_path(entry: WorkingRepo, root_path: Path) -> str:
    """Return *entry*'s path relative to *root_path*, or its own recorded path.

    Falls back to ``entry.relative_path`` (or the absolute path, as a last
    resort) when *entry* does not live under *root_path* at all.
    """
    try:
        relative = entry.absolute_path.relative_to(root_path)
    except ValueError:
        return str(entry.relative_path or entry.absolute_path)
    if relative == Path("."):
        return "."
    return relative.as_posix()


def _status_line_path(status_line: str) -> Path | None:
    """Extract the path a single ``git status --porcelain`` line refers to.

    Handles the ``"old -> new"`` rename form (returns the *new* path) and
    strips the surrounding quotes Git adds for paths containing spaces.
    Returns ``None`` for a line too short to carry a path, or an empty path.
    """
    if len(status_line) < 4:
        return None
    raw_path = status_line[3:]
    if " -> " in raw_path:
        raw_path = raw_path.rsplit(" -> ", 1)[1]
    raw_path = raw_path.strip().strip('"')
    return Path(raw_path) if raw_path else None


def _status_line_targets_any(status_line: str, paths: set[Path]) -> bool:
    """True if *status_line*'s path equals, or is nested under, any of *paths*."""
    status_path = _status_line_path(status_line)
    if status_path is None:
        return False
    return any(status_path == path or _path_is_relative_to(status_path, path) for path in paths)


def _status_line_is_untracked(status_line: str) -> bool:
    """True if *status_line* is Git's ``"?? "`` (untracked) porcelain marker."""
    return status_line.startswith("?? ")


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    """True if *path* is *parent* itself or nested under it."""
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _render_status_table(rows: list[tuple[str, str, str, str, str, str, str, str]]) -> str:
    """Render *rows* as a fixed-column, whitespace-aligned status table.

    Column order (pinned by
    ``tests/integration/test_golden_release_gaps.py::TestStatusGoldenOutput``):
    ``REPOSITORY PATH LOCAL_BRANCH UPSTREAM_BRANCH LOCAL SYNC HEAD RECORDED``.
    Each column is left-justified to the widest value (header or data) it
    holds, columns are joined with two spaces, and a ``-`` separator line
    follows the header row.
    """
    headers = (
        "REPOSITORY",
        "PATH",
        "LOCAL_BRANCH",
        "UPSTREAM_BRANCH",
        "LOCAL",
        "SYNC",
        "HEAD",
        "RECORDED",
    )
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def render_row(columns: Sequence[str]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(columns))

    lines = [render_row(headers), "-" * (sum(widths) + 12)]
    lines.extend(render_row(row) for row in rows)
    return "\n".join(lines)

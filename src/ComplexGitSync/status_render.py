"""status_render — pure text rendering for `cgitsync status`'s repository table.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: given already-computed values (a `WorkingRepo` entry plus a root
    path, a `git status --porcelain` line, or a list of pre-built row
    tuples), format or classify them as text/paths — never runs `git`,
    never reads a file or the clock, and never mutates its input.
Imports: git_repo

Design reference: ``.agent/.local/.localSpec/DevTickets/archive/20260828_Isolation_DevPlanTicket.md``
§1/§4 (`status_render.py`
row) and ``.agent/.local/.localSpec/DevTickets/archive/20260828_Isolation_DevPlanTicket.md`` §2 (P5-status,
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
from dataclasses import dataclass
from pathlib import Path

from .git_repo import SyncState, WorkingRepo


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


#: What ``status`` says about a workspace that holds no repositories.
#: An empty tree is not a broken one: it is where every user starts, and
#: before this existed the tool's first answer to its first user was a
#: traceback.
EMPTY_WORKSPACE_LINE = "no living project yet"


def _render_empty_workspace(workspace: Path, use_case: str) -> str:
    """The whole of ``status`` for a workspace with nothing in it.

    No table, because there is nothing to put in one, and no summary
    counting seven kinds of zero. One line saying where you are, then the
    three commands that start a project — the question was answered, and the
    answer is "nothing here yet".
    """
    return "\n".join(
        [
            f"{EMPTY_WORKSPACE_LINE} use_case={use_case} cgshome={workspace}",
            "nothing has been cloned into this workspace. To start a project:",
            "  cgitsync bootstrap <project.cgs> <ProjectName>  "
            "— clone a tree into a workspace of its own",
            "  cgitsync initialise <project.cgs>               "
            "— build the tree a .cgs describes, here",
            "  cgitsync discover <directory> --write           "
            "— draft a .cgs from repositories already on disk",
        ]
    )


#: SYNC when the branch names an upstream that cannot be resolved — a real
#: error, and the only case worth investigating.
SYNC_UNKNOWN = "unknown"

#: SYNC when the branch names no upstream at all. Not an error: a private/local
#: repository that was never pushed is in exactly this state, and so is a branch
#: created this session. Kept distinct from :data:`SYNC_UNKNOWN` because
#: "nothing to compare against" and "the comparison failed" are different
#: answers, and printing both as ``unknown`` taught readers to ignore the column
#: (``.agent/.local/.localSpec/DevTickets/archive/20260911_UpstreamBranchDisplay_DevPlanTicket.md`` §3).
SYNC_NO_UPSTREAM = "no-upstream"

SYNC_LEGEND = (
    "legend: SYNC — synced/ahead/behind/diverged are measured against the "
    f"upstream branch; {SYNC_NO_UPSTREAM} = this branch was never pushed, so "
    f"there is nothing to measure; {SYNC_UNKNOWN} = it names an upstream that "
    "does not resolve, which 'cgitsync pull' or 'cgitsync push' repairs"
)

#: ``cgitsync_branch`` when the root repository is on no branch at all.
#: The same word the ``LOCAL_BRANCH`` column already uses for that state, so
#: one output never names it two ways.
TREE_BRANCH_DETACHED = "detached"

#: ``cgitsync_branch`` when there is no branch to report: the tree has no
#: root, or Git could not be asked. Kept distinct from
#: :data:`TREE_BRANCH_DETACHED` because "parked on a commit" and "nobody
#: could tell" are different answers. The field is printed either way — a
#: field that disappears is harder to read, by eye or by script, than one
#: that says it does not know.
TREE_BRANCH_UNKNOWN = "unknown"


def _tree_branch_label(tree_branch: str | None, *, detached: bool) -> str:
    """Name the branch the whole tree is on, for the ``summary`` line.

    *tree_branch* is the root repository's branch, which is the tree's:
    every other repository either follows it or derives its own name from
    it. *detached* separates the two ways it can be missing.
    """
    if tree_branch:
        return tree_branch
    return TREE_BRANCH_DETACHED if detached else TREE_BRANCH_UNKNOWN


PROJECT_SCOPE_LABEL = "project"
PRIVATE_LOCAL_SCOPE_LABEL = "private/local"
PRIVATE_DISTANT_SCOPE_LABEL = "private/distant"

SCOPE_LEGEND = (
    "legend: SCOPE — project = the work itself; "
    "private = a repository that configures the project, shared with your "
    "other projects; local = yours to write, distant = read-only"
)


def _status_tracking_label(
    sync_state: SyncState | None,
    tracking_counts: tuple[int, int] | None = None,
    *,
    upstream_configured: bool = True,
) -> str:
    """Name a repository's upstream relationship for the SYNC column.

    *sync_state* is ``None`` whenever the counts could not be taken, which
    covers two unrelated situations. *upstream_configured* separates them:
    ``False`` means the branch names no upstream, and there is nothing to
    measure; ``True`` means it names one that did not resolve, which is a
    fault. Defaults to ``True`` so that a caller who cannot tell keeps the
    louder of the two answers rather than quietly reporting "never pushed".
    """
    if sync_state is None:
        return SYNC_UNKNOWN if upstream_configured else SYNC_NO_UPSTREAM
    if sync_state == SyncState.ALIGNED:
        return "synced"
    if sync_state == SyncState.AHEAD:
        if tracking_counts is not None:
            return f"ahead(+{tracking_counts[0]})"
        return "ahead"
    if sync_state == SyncState.BEHIND:
        if tracking_counts is not None:
            return f"behind(-{tracking_counts[1]})"
        return "behind"
    if sync_state == SyncState.DIVERGED:
        if tracking_counts is not None:
            return f"diverged(+{tracking_counts[0]}/-{tracking_counts[1]})"
        return "diverged"
    return sync_state.value.lower()


@dataclass(frozen=True, slots=True)
class StatusCounts:
    """The tallies the ``summary`` line reports, counted over rendered rows.

    Counted from the table's own text rather than from the values behind it,
    so the summary can never disagree with the rows printed under it.
    """

    dirty: int = 0
    staged: int = 0
    ahead: int = 0
    behind: int = 0
    unmeasured: int = 0
    recorded_mismatch: int = 0
    errors: int = 0


def _status_summary_counts(
    rows: Sequence[tuple[str, str, str, str, str, str, str, str, str]],
) -> StatusCounts:
    """Tally *rows* for the ``summary`` line.

    ``unmeasured`` is the one that needs saying out loud: a repository whose
    SYNC is :data:`SYNC_NO_UPSTREAM` or :data:`SYNC_UNKNOWN` was never
    compared to a remote, and folding it into ``ahead=0 behind=0`` reports a
    measurement nobody took. Four repositories once read as "level with their
    upstream" when the truth was that none of them had been looked at.
    """
    dirty = staged = ahead = behind = unmeasured = mismatch = errors = 0
    for row in rows:
        local_state, upstream_state, head = row[5], row[6], row[7]
        if local_state != "clean":
            dirty += 1
        if "staged" in local_state:
            staged += 1
        if upstream_state.startswith("ahead"):
            ahead += 1
        elif upstream_state.startswith("behind"):
            behind += 1
        elif upstream_state.startswith("diverged"):
            ahead += 1
            behind += 1
        elif upstream_state in (SYNC_NO_UPSTREAM, SYNC_UNKNOWN):
            unmeasured += 1
        if head.endswith("*"):
            mismatch += 1
        if upstream_state == "error" or local_state == "error":
            errors += 1
    return StatusCounts(
        dirty=dirty,
        staged=staged,
        ahead=ahead,
        behind=behind,
        unmeasured=unmeasured,
        recorded_mismatch=mismatch,
        errors=errors,
    )


def _status_scope_label(entry: WorkingRepo) -> str:
    """Name *entry*'s scope in the words the status table shows a reader.

    Two facts, one column. **private** is a configuration repository: shared
    with other projects rather than owned by this one (``private`` in the
    ``.cgs``). **local** and **distant** then say whether this project may
    write to it (``writable``) or only read it. A repository this project
    owns outright is **project**, where the question does not arise.

    Reads the effective flags, so a repository nested inside a private one
    is named the same way its parent is.
    """
    if not entry.effective_private:
        return PROJECT_SCOPE_LABEL
    if entry.effective_writable:
        return PRIVATE_LOCAL_SCOPE_LABEL
    return PRIVATE_DISTANT_SCOPE_LABEL


def _render_status_table(rows: list[tuple[str, str, str, str, str, str, str, str, str]]) -> str:
    """Render *rows* as a fixed-column, whitespace-aligned status table.

    Column order (private by
    ``tests/integration/test_golden_release_gaps.py::TestStatusGoldenOutput``):
    ``REPOSITORY PATH SCOPE LOCAL_BRANCH UPSTREAM_BRANCH LOCAL SYNC HEAD
    RECORDED``. Each column is left-justified to the widest value (header or
    data) it holds, columns are joined with two spaces, and a ``-``
    separator line follows the header row.
    """
    headers = (
        "REPOSITORY",
        "PATH",
        "SCOPE",
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

    lines = [render_row(headers), "-" * (sum(widths) + 2 * (len(headers) - 1))]
    lines.extend(render_row(row) for row in rows)
    return "\n".join(lines)

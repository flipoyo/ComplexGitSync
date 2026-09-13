"""Standalone tests for the extracted ``status_render`` module.

These import directly from ``ComplexGitSync.status_render`` — never from
``ComplexGitSync.orchestre`` — to prove the extraction (P5-status of
``.localSpec/DevTickets/archive/20260828_Isolation_DevPlanTicket.md``) stands on its own: pure
text/path formatting, importable and fully testable with no Git binary, no
filesystem, and no network. This does not replace the existing golden
coverage of ``cgitsync status``'s printed output in
``tests/integration/test_golden_release_gaps.py::TestStatusGoldenOutput``
(still exercised end-to-end via ``orchestre.py``, which keeps its own copy
of these functions until a separate integration step retires them) — the
last test in this file proves byte-for-byte agreement between the two by
feeding ``_render_status_table`` the same synthetic row shape that golden
test expects.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ComplexGitSync.git_repo import SyncState, WorkingRepo
from ComplexGitSync.git_tree import WorkingGitTree, propagate_privacy
from ComplexGitSync.status_render import (
    SCOPE_LEGEND,
    SYNC_LEGEND,
    SYNC_NO_UPSTREAM,
    SYNC_UNKNOWN,
    _path_is_relative_to,
    _render_status_table,
    _status_display_path,
    _status_line_is_untracked,
    _status_line_path,
    _status_line_targets_any,
    _status_scope_label,
    _status_summary_counts,
    _status_tracking_label,
)

# ---------------------------------------------------------------------------
# _status_display_path
# ---------------------------------------------------------------------------


def test_status_display_path_root_entry_renders_dot():
    root = Path("/workspace/root")
    entry = WorkingRepo(absolute_path=root)

    assert _status_display_path(entry, root) == "."


def test_status_display_path_nested_entry_renders_posix_relative_path():
    root = Path("/workspace/root")
    entry = WorkingRepo(absolute_path=root / "libs" / "widgets")

    assert _status_display_path(entry, root) == "libs/widgets"


def test_status_display_path_outside_root_falls_back_to_relative_path():
    root = Path("/workspace/root")
    entry = WorkingRepo(
        absolute_path=Path("/elsewhere/repo"),
        relative_path=Path("recorded/relative"),
    )

    assert _status_display_path(entry, root) == "recorded/relative"


def test_status_display_path_outside_root_with_no_relative_path_falls_back_to_absolute():
    root = Path("/workspace/root")
    entry = WorkingRepo(absolute_path=Path("/elsewhere/repo"))

    assert _status_display_path(entry, root) == str(Path("/elsewhere/repo"))


# ---------------------------------------------------------------------------
# _status_line_path
# ---------------------------------------------------------------------------


def test_status_line_path_parses_plain_modified_entry():
    assert _status_line_path(" M src/file.py") == Path("src/file.py")


def test_status_line_path_parses_untracked_entry():
    assert _status_line_path("?? new_file.txt") == Path("new_file.txt")


def test_status_line_path_uses_new_side_of_a_rename():
    assert _status_line_path("R  old_name.py -> new_name.py") == Path("new_name.py")


def test_status_line_path_strips_surrounding_quotes():
    assert _status_line_path('?? "path with spaces.txt"') == Path("path with spaces.txt")


def test_status_line_path_returns_none_for_short_line():
    assert _status_line_path(" M") is None


def test_status_line_path_returns_none_for_empty_path():
    assert _status_line_path(" M  ") is None


# ---------------------------------------------------------------------------
# _status_line_is_untracked
# ---------------------------------------------------------------------------


def test_status_line_is_untracked_true_for_double_question_mark():
    assert _status_line_is_untracked("?? untracked.txt") is True


def test_status_line_is_untracked_false_for_modified():
    assert _status_line_is_untracked(" M tracked.txt") is False


# ---------------------------------------------------------------------------
# _path_is_relative_to
# ---------------------------------------------------------------------------


def test_path_is_relative_to_true_for_nested_path():
    assert _path_is_relative_to(Path(".cgitsync/state.toml"), Path(".cgitsync")) is True


def test_path_is_relative_to_true_for_identical_path():
    assert _path_is_relative_to(Path(".cgitsync"), Path(".cgitsync")) is True


def test_path_is_relative_to_false_for_unrelated_path():
    assert _path_is_relative_to(Path("src/file.py"), Path(".cgitsync")) is False


# ---------------------------------------------------------------------------
# _status_line_targets_any
# ---------------------------------------------------------------------------


def test_status_line_targets_any_true_when_nested_under_managed_path():
    managed = {Path(".cgitsync")}
    assert _status_line_targets_any(" M .cgitsync/state.toml", managed) is True


def test_status_line_targets_any_false_when_outside_every_managed_path():
    managed = {Path(".cgitsync")}
    assert _status_line_targets_any(" M src/file.py", managed) is False


def test_status_line_targets_any_false_for_unparseable_line():
    managed = {Path(".cgitsync")}
    assert _status_line_targets_any(" M", managed) is False


class TestSyncColumnSeparatesNeverPushedFromUnmeasurable:
    """``unknown`` used to mean two unrelated things, so it meant nothing.

    A private/local repository that was never pushed genuinely has no
    upstream. A branch that was pushed but whose remote-tracking ref is
    missing is a fault. Printing both as ``unknown`` is what taught readers
    to stop trusting the column
    (``.localSpec/DevTickets/archive/20260911_UpstreamBranchDisplay_DevPlanTicket.md``).
    """

    def test_a_branch_that_names_no_upstream_says_so(self):
        assert _status_tracking_label(None, None, upstream_configured=False) == SYNC_NO_UPSTREAM

    def test_a_named_upstream_that_does_not_resolve_stays_unknown(self):
        assert _status_tracking_label(None, None, upstream_configured=True) == SYNC_UNKNOWN

    def test_a_caller_that_cannot_tell_keeps_the_louder_answer(self):
        """The default must not quietly report "never pushed" for a fault."""
        assert _status_tracking_label(None) == SYNC_UNKNOWN

    @pytest.mark.parametrize(
        ("state", "counts", "expected"),
        [
            (SyncState.ALIGNED, (0, 0), "synced"),
            (SyncState.AHEAD, (2, 0), "ahead(+2)"),
            (SyncState.BEHIND, (0, 3), "behind(-3)"),
            (SyncState.DIVERGED, (1, 4), "diverged(+1/-4)"),
        ],
    )
    def test_a_measured_state_is_unaffected_by_the_new_question(self, state, counts, expected):
        assert _status_tracking_label(state, counts, upstream_configured=False) == expected

    def test_the_legend_explains_both_unmeasured_words(self):
        assert SYNC_NO_UPSTREAM in SYNC_LEGEND
        assert SYNC_UNKNOWN in SYNC_LEGEND


class TestSummaryCountsNeverInventAMeasurement:
    """``ahead=0 behind=0`` is a measurement, and must be earned."""

    @staticmethod
    def _row(sync: str, *, local: str = "clean", head: str = "abc12345") -> tuple[str, ...]:
        return ("repo", ".", "project", "main", "origin/main", local, sync, head, "abc12345")

    def test_an_unmeasured_repository_is_counted_apart_from_a_level_one(self):
        counts = _status_summary_counts(
            [self._row("synced"), self._row(SYNC_NO_UPSTREAM), self._row(SYNC_UNKNOWN)]
        )

        assert counts.unmeasured == 2
        assert counts.ahead == 0
        assert counts.behind == 0

    def test_a_diverged_repository_counts_on_both_sides_and_is_measured(self):
        counts = _status_summary_counts([self._row("diverged(+1/-2)")])

        assert (counts.ahead, counts.behind, counts.unmeasured) == (1, 1, 0)

    def test_local_state_and_recorded_mismatch_are_counted_from_the_same_rows(self):
        counts = _status_summary_counts(
            [
                self._row("ahead(+1)", local="staged+dirty", head="abc12345*"),
                self._row("error", local="error"),
            ]
        )

        assert counts.dirty == 2
        assert counts.staged == 1
        assert counts.recorded_mismatch == 1
        assert counts.errors == 1

    def test_no_rows_counts_nothing(self):
        counts = _status_summary_counts([])

        assert (counts.dirty, counts.ahead, counts.behind, counts.unmeasured) == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# _render_status_table
# ---------------------------------------------------------------------------


class TestScopeColumnNamesWhatARepositoryIs:
    """The words `status` shows a reader, and what each one means.

    A reader of the table should not have to know the `.cgs` field names.
    **private** says the repository is shared with other projects
    (``private``); **local** and **distant** say whether this project may
    write to it (``writable``) or only read it.
    """

    @pytest.mark.parametrize(
        ("private", "writable", "expected"),
        [
            (False, False, "project"),
            (True, True, "private/local"),
            (True, False, "private/distant"),
        ],
    )
    def test_each_kind_of_repository_gets_its_own_word(self, private, writable, expected):
        entry = WorkingRepo(repo_id="r", name="r", private=private, writable=writable)

        assert _status_scope_label(entry) == expected

    def test_a_repo_nested_in_a_private_one_is_named_the_same_way(self):
        """It inherits its parent's state, so it must read that way too."""
        tree = WorkingGitTree()
        tree.add(WorkingRepo(repo_id="shared", name="shared", private=True))
        tree.add(WorkingRepo(repo_id="leaf", name="leaf", parent_id="shared"))
        propagate_privacy(tree)

        assert _status_scope_label(tree.get("leaf")) == "private/distant"

    def test_the_legend_explains_every_word_the_column_can_print(self):
        for word in ("project", "private", "local", "distant"):
            assert word in SCOPE_LEGEND


def test_render_status_table_empty_rows_prints_header_and_separator_only():
    rendered = _render_status_table([])
    lines = rendered.splitlines()

    assert lines[0].split() == [
        "REPOSITORY",
        "PATH",
        "SCOPE",
        "LOCAL_BRANCH",
        "UPSTREAM_BRANCH",
        "LOCAL",
        "SYNC",
        "HEAD",
        "RECORDED",
    ]
    assert set(lines[1]) == {"-"}
    assert len(lines) == 2


def test_render_status_table_widens_columns_to_fit_longest_value():
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
    rows = [
        ("demo", ".", "project", "main", "origin/main", "clean", "synced", "abcd1234", "abcd1234")
    ]
    rendered = _render_status_table(rows)
    lines = rendered.splitlines()

    # Header, separator, one data row.
    assert len(lines) == 3
    assert lines[2].split() == list(rows[0])
    # Separator width matches the documented `sum(widths) + 2 * (columns - 1)`
    # formula, where each column's width is the longer of its header and its
    # data and the two counts the join.
    widths = [max(len(header), len(value)) for header, value in zip(headers, rows[0], strict=True)]
    assert len(lines[1]) == sum(widths) + 2 * (len(headers) - 1)


def test_render_status_table_matches_golden_status_output_shape():
    """Byte-for-byte agreement with the shape private by
    ``tests/integration/test_golden_release_gaps.py::TestStatusGoldenOutput``
    (``test_status_prints_complete_field_set_for_clean_ready_tree``) — same
    synthetic row tuple, same header/separator/data-row structure.
    """
    rows = [
        ("demo", ".", "project", "main", "origin/main", "clean", "synced", "abcd1234", "abcd1234")
    ]

    rendered = _render_status_table(rows)
    lines = rendered.splitlines()

    header_cells = lines[0].split()
    assert header_cells == [
        "REPOSITORY",
        "PATH",
        "SCOPE",
        "LOCAL_BRANCH",
        "UPSTREAM_BRANCH",
        "LOCAL",
        "SYNC",
        "HEAD",
        "RECORDED",
    ]
    assert set(lines[1]) == {"-"}

    data_cells = lines[2].split()
    assert data_cells[0] == "demo"
    assert data_cells[1] == "."
    assert data_cells[2] == "project"
    assert data_cells[3] == "main"
    assert data_cells[4] == "origin/main"
    assert data_cells[5] == "clean"
    assert data_cells[6] == "synced"
    assert data_cells[7] == data_cells[8]
    assert not data_cells[7].endswith("*")

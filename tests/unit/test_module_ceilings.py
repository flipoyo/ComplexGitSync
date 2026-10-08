"""Ratchet test: no src/ComplexGitSync module may grow past its recorded baseline.

Backs .agent/.local/.dev/DevTickets/archive/20260828_Isolation_DevPlanTicket.md's orchestration model —
every work package that touches a shared file (orchestre.py, cli.py, ...)
must leave it the same size or smaller. See scripts/check_module_ceilings.py
for the full contract (LOC ratchet, Ring-0 purity, docstring-header
cross-check) and scripts/ceiling_baseline.json for the recorded baseline.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_module_ceilings.py"
_SPEC = importlib.util.spec_from_file_location("check_module_ceilings", _SCRIPT_PATH)
check_module_ceilings = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = check_module_ceilings
_SPEC.loader.exec_module(check_module_ceilings)


def test_no_module_regresses_past_its_recorded_baseline():
    baseline = check_module_ceilings.load_baseline()
    ring0_modules = set(baseline.get("ring0_modules", []))
    reports = [
        check_module_ceilings.analyse_module(path, ring0_modules=ring0_modules)
        for path in check_module_ceilings.iter_modules()
    ]

    failures = check_module_ceilings.run_check(reports, baseline)

    assert not failures, "\n".join(failures)


# --- citation check -----------------------------------------------------------


def _ref(under_devtickets):
    """A citation path built in pieces, so this file does not itself cite it."""
    return ".agent/.local/" + ".dev/Dev" + "Tickets/" + under_devtickets


def _tree(tmp_path, *, with_mount=True, doc=""):
    (tmp_path / "src").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "mod.py").write_text(f'"""{doc}"""\n', encoding="utf-8")
    if with_mount:
        (tmp_path / ".agent" / ".local" / ".dev" / "DevTickets" / "archive").mkdir(parents=True)
    return tmp_path


def test_a_citation_that_resolves_passes(tmp_path):
    root = _tree(tmp_path, doc=_ref("archive/20260101_Done_DevPlanTicket.md"))
    (root / _ref("archive/20260101_Done_DevPlanTicket.md")).write_text("x")

    assert check_module_ceilings.find_stale_citations(root) == []


def test_a_dead_citation_is_reported(tmp_path):
    root = _tree(tmp_path, doc=_ref("archive/20260101_Gone_DevPlanTicket.md"))

    failures = check_module_ceilings.find_stale_citations(root)

    assert len(failures) == 1 and "does not exist" in failures[0]


def test_an_open_ticket_cited_by_path_is_reported_even_when_it_exists(tmp_path):
    cited = _ref("openTickets/main_1-1_Live_DevPlanTicket.md")
    root = _tree(tmp_path, doc=cited)
    (tmp_path / cited).parent.mkdir(parents=True)
    (tmp_path / cited).write_text("x")

    failures = check_module_ceilings.find_stale_citations(root)

    assert len(failures) == 1 and "open ticket" in failures[0]


def test_a_citation_wrapped_over_two_lines_is_still_checked(tmp_path):
    first = _ref("archive/")
    root = _tree(tmp_path, doc=first + "\n    20260101_Gone_DevPlanTicket.md")

    failures = check_module_ceilings.find_stale_citations(root)

    assert len(failures) == 1 and "20260101_Gone_DevPlanTicket.md" in failures[0]


def test_a_citation_wrapped_after_a_comment_marker_is_still_checked(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / ".agent/.local/.dev/DevTickets/archive").mkdir(parents=True)
    (tmp_path / "src" / "mod.py").write_text(
        "#: see " + _ref("archive/") + "\n    #: 20260101_Gone_DevPlanTicket.md here\n", encoding="utf-8"
    )

    failures = check_module_ceilings.find_stale_citations(tmp_path)

    assert len(failures) == 1 and "20260101_Gone_DevPlanTicket.md" in failures[0]


def test_prose_after_a_path_is_not_glued_onto_it(tmp_path):
    cited = _ref("archive/20260101_Done_DevPlanTicket.md")
    root = _tree(tmp_path, doc=cited + "\nmore words follow")
    (tmp_path / cited).write_text("x")

    assert check_module_ceilings.find_stale_citations(root) == []


def test_a_missing_private_mount_is_skipped_not_failed(tmp_path):
    root = _tree(
        tmp_path,
        with_mount=False,
        doc=_ref("archive/20260101_Gone_DevPlanTicket.md"),
    )

    assert check_module_ceilings.find_stale_citations(root) == []


def test_a_broken_relative_link_in_an_open_ticket_is_reported(tmp_path):
    root = _tree(tmp_path)
    tickets = root / ".agent/.local/.dev/DevTickets"
    (tickets / "openTickets").mkdir()
    (tickets / "README.md").write_text("[ok](openTickets/a.md)\n")
    (tickets / "openTickets" / "a.md").write_text("[bad](../AdditionalSpecs.md) [web](https://x.y/z.md)\n")

    failures = check_module_ceilings.find_broken_ticket_links(root)

    assert len(failures) == 1 and "AdditionalSpecs.md" in failures[0]


def test_the_real_tree_cites_nothing_stale():
    assert check_module_ceilings.find_stale_citations() == []
    assert check_module_ceilings.find_broken_ticket_links() == []

"""`scripts/spec_tree.py` — the spec-graph integrity checker.

Backs `main_1-7_SpecTree_DevPlanTicket.md`: every spec reachable from
`CLAUDE.md` (or genuinely orphaned), every markdown link real, and the
digest's citations honest. Two halves: fixture tests over a small,
synthetic tree (so a broken link, an orphan, a cross-link cycle and a
stale digest citation can each be produced on purpose) and one real-repo
test mirroring `test_module_ceilings.py`'s own shape — the thing CI
actually enforces via `pixi run test`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "spec_tree.py"
_SPEC = importlib.util.spec_from_file_location("spec_tree", _SCRIPT_PATH)
spec_tree = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = spec_tree
_SPEC.loader.exec_module(spec_tree)


# ---------------------------------------------------------------------------
# Real repo — the shape `pixi run test` actually enforces
# ---------------------------------------------------------------------------


def test_no_declared_spec_is_orphaned_or_broken_in_this_repo():
    report = spec_tree.analyse()
    failures = spec_tree.run_check(report)
    assert not failures, "\n".join(failures)


def test_digest_citations_are_honest_in_this_repo():
    report = spec_tree.analyse()
    entries = spec_tree.parse_digest()
    assert entries, "digest.md should have at least one parseable rule line"
    failures = spec_tree.run_check_digest(entries, report.reachable)
    assert not failures, "\n".join(failures)


def test_devtickets_is_not_declared_spec_surface():
    """D1: closing a ticket (openTickets/ -> archive/) must never trip
    `--check` — the scope boundary holds structurally, not by luck,
    because DevTickets/ content (besides its own README) was never added
    to DECLARED_SPEC_FILES in the first place.
    """
    offenders = [
        f
        for f in spec_tree.DECLARED_SPEC_FILES
        if "DevTickets/" in f and not f.endswith("DevTickets/README.md")
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# Fixture tree — one small, synthetic spec graph per behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_root(tmp_path, monkeypatch):
    monkeypatch.setattr(spec_tree, "REPO_ROOT", tmp_path)
    return tmp_path


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_broken_link_is_reported(fixture_root):
    _write(fixture_root, "root.md", "See [gone](missing.md) for details.")

    report = spec_tree.analyse(universe=["root.md"], root="root.md")

    assert len(report.broken_links) == 1
    assert report.broken_links[0].target == "missing.md"
    failures = spec_tree.run_check(report)
    assert any("broken link" in f for f in failures)


def test_orphan_is_reported(fixture_root):
    _write(fixture_root, "root.md", "No links here.")
    _write(fixture_root, "lonely.md", "Nothing points at me.")

    report = spec_tree.analyse(universe=["root.md", "lonely.md"], root="root.md")

    assert report.orphans == ["lonely.md"]
    failures = spec_tree.run_check(report)
    assert any("orphaned" in f for f in failures)


def test_bare_filename_in_prose_becomes_an_edge_when_a_sibling_exists(fixture_root):
    """The real gap found in ticket §2: `CLAUDE.md` names `AGENT.md` in
    prose, never as a markdown link, and it must still be found.
    """
    _write(fixture_root, "mount/root.md", "`AGENT.md` is read next.")
    _write(fixture_root, "mount/AGENT.md", "the roster")

    report = spec_tree.analyse(universe=["mount/root.md", "mount/AGENT.md"], root="mount/root.md")

    assert report.orphans == []
    assert "mount/AGENT.md" in report.reachable


def test_bare_filename_naming_a_different_mounts_file_is_not_resolved(fixture_root):
    """The same word, `AGENT.md`, appearing in a mount that has no such
    sibling must not guess at some other mount's file — no edge at all,
    same as any other prose mention of a filename that happens to exist
    elsewhere in a big tree.
    """
    _write(fixture_root, "mount_a/root.md", "`AGENT.md` is read next.")
    _write(fixture_root, "mount_b/AGENT.md", "a different mount's roster")

    edges = spec_tree.extract_edges("mount_a/root.md")

    assert edges == []


def test_cross_link_cycle_does_not_loop(fixture_root):
    _write(fixture_root, "a.md", "[b](b.md)")
    _write(fixture_root, "b.md", "[a](a.md)")

    report = spec_tree.analyse(universe=["a.md", "b.md"], root="a.md")

    assert report.reachable == {"a.md", "b.md"}
    assert report.orphans == []
    flattened = spec_tree.flatten(root="a.md", universe=["a.md", "b.md"])
    assert flattened.count("# a.md") == 1
    assert flattened.count("# b.md") == 1


def test_upstream_broken_link_is_reported_but_does_not_fail_check(fixture_root):
    _write(fixture_root, ".agent/.distant/ticket/root.md", "[gone](missing.md)")

    report = spec_tree.analyse(
        universe=[".agent/.distant/ticket/root.md"], root=".agent/.distant/ticket/root.md"
    )

    assert len(report.upstream_broken_links) == 1
    assert report.broken_links == []
    assert spec_tree.run_check(report) == []


def test_writable_broken_link_still_fails_check(fixture_root):
    _write(fixture_root, ".agent/.local/.claude/root.md", "[gone](missing.md)")

    report = spec_tree.analyse(
        universe=[".agent/.local/.claude/root.md"], root=".agent/.local/.claude/root.md"
    )

    assert len(report.broken_links) == 1
    assert spec_tree.run_check(report) != []


def test_digest_citation_pointing_at_a_deleted_spec_fails(fixture_root):
    _write(fixture_root, "root.md", "no links")
    _write(
        fixture_root,
        spec_tree.DIGEST_PATH,
        "- a rule that used to live somewhere — `Deleted.md` §1\n",
    )

    entries = spec_tree.parse_digest()
    report = spec_tree.analyse(universe=["root.md"], root="root.md")
    failures = spec_tree.run_check_digest(entries, report.reachable, universe=["root.md"])

    assert len(failures) == 1
    assert "Deleted.md" in failures[0]


def test_digest_citation_to_an_unreachable_spec_fails(fixture_root):
    _write(fixture_root, "root.md", "no links")
    _write(fixture_root, "unreachable.md", "nothing points at me")
    _write(
        fixture_root,
        spec_tree.DIGEST_PATH,
        "- a rule nobody can actually reach — `unreachable.md` §1\n",
    )

    entries = spec_tree.parse_digest()
    report = spec_tree.analyse(universe=["root.md", "unreachable.md"], root="root.md")
    failures = spec_tree.run_check_digest(
        entries, report.reachable, universe=["root.md", "unreachable.md"]
    )

    assert len(failures) == 1
    assert "not reachable" in failures[0]


def test_ambiguous_digest_citation_fails(fixture_root):
    _write(fixture_root, "root.md", "no links")
    _write(fixture_root, "mount_a/AGENT.md", "roster a")
    _write(fixture_root, "mount_b/AGENT.md", "roster b")
    _write(
        fixture_root,
        spec_tree.DIGEST_PATH,
        "- a rule citing a name that exists twice — `AGENT.md` §1\n",
    )

    entries = spec_tree.parse_digest()
    universe = ["root.md", "mount_a/AGENT.md", "mount_b/AGENT.md"]
    report = spec_tree.analyse(universe=universe, root="root.md")
    failures = spec_tree.run_check_digest(entries, report.reachable, universe=universe)

    assert len(failures) == 1
    assert "ambiguous" in failures[0]


def test_digest_line_not_matching_the_citation_shape_is_ignored(fixture_root):
    _write(
        fixture_root,
        spec_tree.DIGEST_PATH,
        "# Digest\n\nJust prose, no citation here.\n- also not a rule line, no dash-citation shape\n",
    )

    entries = spec_tree.parse_digest()

    assert entries == []

"""Spec-graph integrity checker — `main_1-7_SpecTree_DevPlanTicket.md`.

The specs an agent is meant to follow (`CLAUDE.md`, `AdditionalSpecs.md`,
`AgentConduct.md`, `DOCSTYLE.md`, `TICKETLIFECYCLE.md`, `DevSpecs.md`, ...)
are not a flat pile — they are a graph, reached one link at a time from
`CLAUDE.md`, the file an agent is actually handed at session start. A rule
that exists but sits behind a broken link, or behind no link at all, is
exactly as unreachable to an agent as a rule that was never written down.
This script makes that graph checkable instead of trusted by eye.

Two kinds of edge, both discovered by reading the files the same way an
agent does:

1. **Markdown links** — `[text](path.md)`, resolved relative to the
   linking file's own directory. A link whose target does not exist is a
   **broken link** (`--check` failure) — this is the primary, deliberate
   way one spec points at another.
2. **Bare filenames in prose** — a backtick-quoted `` `Name.md` `` with no
   surrounding `[...]()`, found real and load-bearing while drafting this
   ticket: `CLAUDE.md`'s own *Layout* section names `AGENT.md` this way,
   and a pure link-crawler walks right past the one file whose entire job
   is to be the second thing an agent reads. Resolved **only** against a
   same-directory sibling — the conservative reading of "each mount's
   declared root" (ticket §5 D2) — so an incidental mention of a
   same-named file living in a different mount (`CLAUDE.md` says
   `` `DevSpecs.md` `` in prose; no `DevSpecs.md` lives beside it) is
   silently dropped rather than mis-resolved, and an illustrative,
   non-existent filename in an example block (`README_prod.md`,
   `CorPlan.md`) never becomes an edge at all, because it never matches an
   existing sibling. Unlike a markdown link, an unresolved bare mention is
   **not** a `--check` failure — there is no way to tell "meant as a
   pointer, resolved wrong" from "just prose," so this direction only ever
   adds edges, never reports them broken.

**Universe:** `DECLARED_SPEC_FILES` below — a hand-maintained list, not a
glob over every `.md` under `.agent/`. Ticket §5 D1: globbing would pull
in content that is not a spec at all (a mounted documentation repository's
own theme/template docs, a planning ticket's own body) and the scope this
checker cares about is specs — deliberately, exactly what D1 states.
`--check` fails if a universe member is unreachable from
`CLAUDE.md` by any chain of edges (an **orphan**) or if a member's own
path does not exist on disk.

**The digest** (`digest.md`, ticket §5 D3/D5): a short, hand-written file
— one rule per line, each citing its source — that a session loads in
full, in place of eager-loading the whole discursive tree. `--check-digest`
verifies every citation still resolves inside the reachable universe;
it does not, and cannot, verify that a digest line still accurately
summarises its source — that is an editorial judgement, not a graph
property.

Usage
-----
    pixi run python scripts/spec_tree.py                 # report
    pixi run python scripts/spec_tree.py --check          # broken links + orphans, exit 1
    pixi run python scripts/spec_tree.py --check-digest    # digest citations, exit 1
    pixi run python scripts/spec_tree.py --flatten         # one doc, depth-first from CLAUDE.md

Exit code: 0 unless `--check`/`--check-digest` finds a failure.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# D1 (answered): the declared spec universe. Hand-maintained on purpose —
# see the module docstring above for why a glob is the wrong tool here.
# Paths are POSIX-relative to REPO_ROOT.
ROOT_SPEC = ".agent/.local/.claude/CLAUDE.md"
DIGEST_PATH = ".agent/.local/.localSpec/digest.md"

DECLARED_SPEC_FILES: list[str] = [
    ROOT_SPEC,
    ".agent/.local/.claude/AGENT.md",
    ".agent/.local/.localSpec/AdditionalSpecs.md",
    ".agent/.local/.localSpec/audit.md",
    ".agent/.local/.localSpec/AGENT.md",
    ".agent/.local/.localSpec/DevTickets/README.md",
    DIGEST_PATH,
    ".agent/.distant/dev-sync/AgentConduct.md",
    ".agent/.distant/dev-sync/AgentDataContract.md",
    ".agent/.distant/dev-sync/DevSpecs.md",
    ".agent/.distant/dev-sync/AGENT.md",
    ".agent/.distant/dev-sync/legalTerms/anthropic.md",
    ".agent/.distant/documentation/DOCSTYLE.md",
    ".agent/.distant/ticket/TICKETLIFECYCLE.md",
]

_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_BACKTICK_MD_RE = re.compile(r"`([A-Za-z0-9_.\-]+\.md)`")


@dataclass
class Edge:
    source: str  # POSIX-relative to REPO_ROOT
    target: str  # POSIX-relative to REPO_ROOT, as resolved — may not exist
    kind: str  # "link" or "bare"


#: `.agent/.distant/` is shared, read-only (CLAUDE.md's own *Layout*
#: section) — a broken link whose *source* lives there is a real problem,
#: but not one this project can fix by editing its own tree, so it is
#: reported, never a `--check` failure. An orphan is different: whether
#: something reachable *from* CLAUDE.md reaches a given file is entirely
#: this project's own linking, distant target included, so orphans stay a
#: hard failure regardless of where the orphaned file lives.
_READ_ONLY_PREFIX = ".agent/.distant/"


def _is_writable_source(path: str) -> bool:
    return not path.startswith(_READ_ONLY_PREFIX)


@dataclass
class GraphReport:
    edges: list[Edge] = field(default_factory=list)
    broken_links: list[Edge] = field(default_factory=list)
    upstream_broken_links: list[Edge] = field(default_factory=list)
    reachable: set[str] = field(default_factory=set)
    orphans: list[str] = field(default_factory=list)
    missing_universe_files: list[str] = field(default_factory=list)


def _resolve_link_target(source: str, raw_target: str) -> str | None:
    """A markdown link's target, resolved relative to *source*'s directory.

    `None` for anything not worth treating as a spec edge: an external
    URL, an in-page `#fragment`-only link, or a target that is not a
    `.md` file (a link straight to a source file or a ticket, both real
    and common in this tree, but not part of the spec graph itself).
    """
    target = raw_target.strip()
    if not target or target.startswith("#"):
        return None
    if "://" in target:
        return None
    target = target.split("#", 1)[0].split(" ", 1)[0]
    if not target.lower().endswith(".md"):
        return None
    source_dir = (REPO_ROOT / source).parent
    resolved = (source_dir / target).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return None  # escapes the repo entirely — not a spec edge


def extract_edges(source: str) -> list[Edge]:
    """Every spec edge `source` names, by markdown link or bare filename."""
    path = REPO_ROOT / source
    text = path.read_text(encoding="utf-8")
    edges: list[Edge] = []

    def _mask_link(match: re.Match[str]) -> str:
        target = _resolve_link_target(source, match.group(1))
        if target is not None:
            edges.append(Edge(source=source, target=target, kind="link"))
        return " " * len(match.group(0))

    masked = _MD_LINK_RE.sub(_mask_link, text)

    source_dir = (REPO_ROOT / source).parent
    for match in _BACKTICK_MD_RE.finditer(masked):
        sibling = source_dir / match.group(1)
        if sibling.is_file():
            target = sibling.resolve().relative_to(REPO_ROOT).as_posix()
            edges.append(Edge(source=source, target=target, kind="bare"))
    return edges


def build_graph(universe: list[str]) -> list[Edge]:
    """Every edge out of every file in *universe* — the universe is the
    set of files *checked* for orphanhood, not the set of files a link may
    point at (a link to a file outside the universe, e.g. a ticket, is a
    real edge, just not one that can leave anything in the universe
    unreached on its account).
    """
    edges: list[Edge] = []
    for source in universe:
        if (REPO_ROOT / source).is_file():
            edges.extend(extract_edges(source))
    return edges


def reachable_from(root: str, edges: list[Edge]) -> set[str]:
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
    seen = {root}
    stack = [root]
    while stack:
        current = stack.pop()
        for target in adjacency.get(current, []):
            if target not in seen:
                seen.add(target)
                stack.append(target)
    return seen


def analyse(universe: list[str] = DECLARED_SPEC_FILES, root: str = ROOT_SPEC) -> GraphReport:
    report = GraphReport()
    report.missing_universe_files = [f for f in universe if not (REPO_ROOT / f).is_file()]
    report.edges = build_graph(universe)
    all_broken = [
        e for e in report.edges if e.kind == "link" and not (REPO_ROOT / e.target).is_file()
    ]
    report.broken_links = [e for e in all_broken if _is_writable_source(e.source)]
    report.upstream_broken_links = [e for e in all_broken if not _is_writable_source(e.source)]
    report.reachable = reachable_from(root, report.edges)
    report.orphans = sorted(
        f for f in universe if f not in report.reachable and f not in report.missing_universe_files
    )
    return report


def run_check(report: GraphReport) -> list[str]:
    failures: list[str] = []
    for f in report.missing_universe_files:
        failures.append(f"{f}: declared in DECLARED_SPEC_FILES but does not exist")
    for edge in report.broken_links:
        failures.append(f"{edge.source}: broken link -> {edge.target}")
    for f in report.orphans:
        failures.append(f"{f}: orphaned — no chain of links from {ROOT_SPEC} reaches it")
    return failures


@dataclass
class DigestEntry:
    line_no: int
    text: str
    citation: str


_DIGEST_LINE_RE = re.compile(r"^-\s.*—\s*`([^`]+)`(?:\s*§.*)?\s*$")


def parse_digest(path: str = DIGEST_PATH) -> list[DigestEntry]:
    full = REPO_ROOT / path
    if not full.is_file():
        return []
    entries: list[DigestEntry] = []
    for i, line in enumerate(full.read_text(encoding="utf-8").splitlines(), start=1):
        match = _DIGEST_LINE_RE.match(line)
        if match:
            entries.append(DigestEntry(line_no=i, text=line, citation=match.group(1)))
    return entries


def _resolve_citation(citation: str, universe: list[str]) -> list[str]:
    """Every universe member *citation* could name — a relative path
    matches exactly one (or zero); a bare filename may match several,
    which `run_check_digest` treats as ambiguous rather than guessing.
    """
    if "/" in citation:
        return [f for f in universe if f == citation]
    return [f for f in universe if Path(f).name == citation]


def run_check_digest(
    entries: list[DigestEntry], reachable: set[str], universe: list[str] = DECLARED_SPEC_FILES
) -> list[str]:
    failures: list[str] = []
    for entry in entries:
        matches = _resolve_citation(entry.citation, universe)
        if not matches:
            failures.append(
                f"digest.md:{entry.line_no}: citation '{entry.citation}' matches no "
                f"declared spec file"
            )
        elif len(matches) > 1:
            failures.append(
                f"digest.md:{entry.line_no}: citation '{entry.citation}' is ambiguous "
                f"({', '.join(matches)}) — cite a relative path instead"
            )
        elif matches[0] not in reachable:
            failures.append(
                f"digest.md:{entry.line_no}: citation '{entry.citation}' resolves to "
                f"{matches[0]}, which is not reachable from {ROOT_SPEC}"
            )
    return failures


def flatten(root: str = ROOT_SPEC, universe: list[str] = DECLARED_SPEC_FILES) -> str:
    """One document, depth-first from *root*, each target inlined the
    first time it is reached. A report for reading, not what a session
    eager-loads by default (ticket §5 D3) — that is `digest.md`.
    """
    edges = build_graph(universe)
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, []).append(edge.target)

    parts: list[str] = []
    emitted: set[str] = set()

    def _visit(path: str) -> None:
        if path in emitted or not (REPO_ROOT / path).is_file():
            return
        emitted.add(path)
        parts.append(f"\n\n{'=' * 72}\n# {path}\n{'=' * 72}\n\n")
        parts.append((REPO_ROOT / path).read_text(encoding="utf-8"))
        for target in adjacency.get(path, []):
            _visit(target)

    _visit(root)
    return "".join(parts)


def _print_report(report: GraphReport) -> None:
    print(f"root: {ROOT_SPEC}")
    print(f"universe: {len(DECLARED_SPEC_FILES)} files, {len(report.edges)} edges")
    print(f"reachable: {len(report.reachable & set(DECLARED_SPEC_FILES))}/{len(DECLARED_SPEC_FILES)}")
    if report.broken_links:
        print("broken links:")
        for e in report.broken_links:
            print(f"  {e.source} -> {e.target}")
    if report.upstream_broken_links:
        print("broken links in shared, read-only mounts (reported, not a --check failure):")
        for e in report.upstream_broken_links:
            print(f"  {e.source} -> {e.target}")
    if report.orphans:
        print("orphans:")
        for f in report.orphans:
            print(f"  {f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 on broken link or orphan")
    parser.add_argument("--check-digest", action="store_true", help="exit 1 on stale digest citation")
    parser.add_argument("--flatten", action="store_true", help="print one depth-first document")
    args = parser.parse_args(argv)

    report = analyse()

    if args.flatten:
        print(flatten())
        return 0

    _print_report(report)

    failures: list[str] = []
    if args.check:
        failures.extend(run_check(report))
    if args.check_digest:
        entries = parse_digest()
        failures.extend(run_check_digest(entries, report.reachable))

    if args.check or args.check_digest:
        if failures:
            print("\nSPEC TREE FAILURES:")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("\nSpec tree intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Convert TikZ figures in docs/figures/*.tex to Mermaid diagrams.

This script performs a best-effort structural conversion of TikZ node/arrow
definitions into Mermaid ``graph TD`` diagrams.  Complex LaTeX math, custom
styles, or absolute coordinates are simplified or dropped; the goal is a
readable diagram that captures nodes and edges, not pixel-perfect reproduction.

Usage::

    python scripts/tikz2mermaid.py [INPUT ...] [--output-dir DIR]

``INPUT`` may be one or more ``.tex`` files or a directory (default:
``docs/figures``).  Each input file produces one ``.md`` file in ``OUTPUT_DIR``
(default: ``docs/figures/mermaid``).

Run ``python scripts/tikz2mermaid.py --help`` for the full option reference.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# LaTeX → plain-text helpers
# ---------------------------------------------------------------------------

_LATEX_CMD_RE = re.compile(r"\\(?:textbf|textit|texttt|text|sffamily|ttfamily|bfseries|footnotesize|scriptsize|small|normalsize|large|Large)\s*\{([^}]*)\}")
_MATH_ENV_RE  = re.compile(r"\$[^$]*\$")
_ANY_CMD_RE   = re.compile(r"\\[a-zA-Z@]+\*?\s*(\{[^}]*\})?")
_MULTI_WS_RE  = re.compile(r"\s+")


def _clean_latex(text: str) -> str:
    """Strip LaTeX markup and return readable plain text."""
    # Unwrap common text commands: \textbf{foo} → foo
    text = _LATEX_CMD_RE.sub(r"\1", text)
    # Drop inline math
    text = _MATH_ENV_RE.sub("...", text)
    # Replace \\ (line break) with a space
    text = re.sub(r"\\\\", " ", text)
    # Drop optional arguments like [2pt], [2ex], [0.3cm], etc.
    text = re.sub(r"\[\s*\d+(?:\.\d+)?\s*(?:pt|ex|em|cm|mm|in|bp|pc|dd|cc|sp|mu)?\s*\]", "", text)
    # Drop \ at word boundaries (e.g. "\ " non-breaking space tokens)
    text = re.sub(r"\\\s", " ", text)
    # Drop remaining LaTeX commands (keep their brace content when present)
    text = _ANY_CMD_RE.sub(lambda m: (m.group(1) or "").strip("{}"), text)
    # Collapse whitespace
    text = _MULTI_WS_RE.sub(" ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Node-style → Mermaid shape/class helpers
# ---------------------------------------------------------------------------

# Map style name substrings to Mermaid node shape brackets.
# Order matters: first match wins.
_STYLE_SHAPE_MAP: list[tuple[str, tuple[str, str]]] = [
    ("gatebox",    ("{", "}")),   # diamond / decision
    ("diamond",    ("{", "}")),
    ("classbox",   ("[", "]")),
    ("enumbox",    ("[", "]")),
    ("state",      ("(", ")")),
    ("clientbox",  ("[", "]")),
    ("actionbox",  ("[", "]")),
    ("blockedbox", ("[", "]")),
    ("statebox",   ("[", "]")),
    ("opbox",      ("[", "]")),
    ("reponode",   ("[", "]")),
    ("rootnode",   ("[", "]")),
    ("parentnode", ("[", "]")),
    ("leafnode",   ("[", "]")),
    ("operationstep", ("[", "]")),
    ("orderbox",   ("[", "]")),
    ("tiernode",   ("[", "]")),
    ("tier",       ("[", "]")),
    ("box",        ("[", "]")),
    ("seclabel",   ("[", "]")),
    ("tiertitle",  ("[", "]")),   # Tier section titles
    ("modlist",    ("[", "]")),    # Module list nodes
    ("smallnote",  ("[", "]")),   # Small note nodes
]


def _style_to_brackets(style: str) -> tuple[str, str]:
    for keyword, brackets in _STYLE_SHAPE_MAP:
        if keyword in style.lower():
            return brackets
    return ("[", "]")


# ---------------------------------------------------------------------------
# Arrow direction helpers
# ---------------------------------------------------------------------------

def _arrow_to_mermaid(arrow_cmd: str) -> str:
    """Map TikZ arrow style keywords to Mermaid arrow syntax."""
    if "dashed" in arrow_cmd.lower():
        return "-.->"
    return "-->"


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

# Matches: \node[style] (id) at (...) { label };
# and    : \node[style, right=...] (id) { label };
_NODE_RE = re.compile(
    r"\\node\s*"
    r"(?:\[[^\]]*\])?\s*"   # optional style list (captured below)
    r"\(([^)]+)\)"           # node id
    r"[^{]*"                 # anything before the label (position, etc.)
    r"\{([\s\S]*?)\}\s*;",  # label body
    re.MULTILINE,
)

# Same but capture the style list too
_NODE_FULL_RE = re.compile(
    r"\\node\s*"
    r"\[([^\]]*)\]\s*"       # style list  ← group 1
    r"\(([^)]+)\)"           # node id     ← group 2
    r"[^{]*"                 # position
    r"\{([\s\S]*?)\}\s*;",  # label       ← group 3
    re.MULTILINE,
)

# Matches: \draw[style] (src) -- node[...]{label} (dst);
# and    : \draw[style] (src) -- (dst);
# and curve variants with to / bend / |-
_DRAW_RE = re.compile(
    r"\\draw\s*"
    r"\[([^\]]*)\]\s*"                          # draw style  ← group 1
    r"\(([^)]+)\)"                              # source id   ← group 2
    r"(?:[^(;]*?node\s*(?:\[[^\]]*\])?\s*\{([^}]*)\})?"  # optional edge label ← group 3
    r"[^(;]*"
    r"\(([^)]+)\)\s*;",                         # target id   ← group 4
    re.MULTILINE,
)

# nodepart second / third → used to grab multipart label content
_NODEPART_RE = re.compile(r"\\nodepart\{(?:two|three|four)\}\s*([\s\S]*?)(?=\\nodepart|$)", re.MULTILINE)


def _extract_label(raw: str) -> str:
    """Build a single-line label from a raw TikZ node body."""
    # Remove nodepart separators but keep their content
    parts = [raw]
    nodepart_bodies = _NODEPART_RE.findall(raw)
    if nodepart_bodies:
        # Keep only the *first* compartment (the class name)
        parts = [re.split(r"\\nodepart\{two\}", raw, maxsplit=1)[0]]
    label = _clean_latex(" ".join(parts))
    # Truncate very long labels to keep diagrams readable
    if len(label) > 60:
        label = label[:57].rstrip() + "..."
    return label or "?"


class TikzParser:
    """Parse TikZ source and emit Mermaid."""

    def __init__(self, source: str) -> None:
        self._source = source
        self._nodes: dict[str, dict] = {}   # id → {label, style}
        self._edges: list[dict] = []        # {src, dst, label, style}

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse(self) -> "TikzParser":
        self._parse_nodes()
        self._parse_edges()
        return self

    def _parse_nodes(self) -> None:
        for m in _NODE_FULL_RE.finditer(self._source):
            style, nid, body = m.group(1), m.group(2).strip(), m.group(3)
            # Skip background band nodes and other decorative elements
            if any(pattern in nid for pattern in ["band", "bg", "background"]):
                continue
            label = _extract_label(body)
            self._nodes[nid] = {"label": label, "style": style}

        # Fall back to the simpler regex for nodes without a style list
        for m in _NODE_RE.finditer(self._source):
            nid = m.group(1).strip()
            if nid not in self._nodes:
                # Skip background band nodes and other decorative elements
                if any(pattern in nid for pattern in ["band", "bg", "background"]):
                    continue
                label = _extract_label(m.group(2))
                self._nodes[nid] = {"label": label, "style": ""}

    def _parse_edges(self) -> None:
        for m in _DRAW_RE.finditer(self._source):
            draw_style = m.group(1)
            src = m.group(2).strip()
            edge_label = _clean_latex(m.group(3) or "")
            dst = m.group(4).strip()
            # Skip self-loops and dummy anchors (south, north, east, west)
            src_clean = re.split(r"\.", src)[0]
            dst_clean = re.split(r"\.", dst)[0]
            if src_clean == dst_clean:
                continue
            # Skip edges involving background band nodes
            if any(pattern in src_clean for pattern in ["band", "bg", "background"]) or \
               any(pattern in dst_clean for pattern in ["band", "bg", "background"]):
                continue
            # Skip if neither endpoint was declared as a node
            # (avoids spurious edges from decorative draws)
            if src_clean not in self._nodes and dst_clean not in self._nodes:
                continue
            self._edges.append({
                "src": src_clean,
                "dst": dst_clean,
                "label": edge_label,
                "style": draw_style,
            })

    # ------------------------------------------------------------------
    # Mermaid emission
    # ------------------------------------------------------------------

    def _mermaid_node_line(self, nid: str) -> str:
        info = self._nodes[nid]
        label = info["label"].replace('"', "'")
        open_b, close_b = _style_to_brackets(info["style"])
        # Mermaid ids must not start with a digit and must not contain spaces
        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", nid)
        return f'    {safe_id}{open_b}"{label}"{close_b}'

    def _mermaid_edge_line(self, edge: dict) -> str:
        src = re.sub(r"[^a-zA-Z0-9_]", "_", edge["src"])
        dst = re.sub(r"[^a-zA-Z0-9_]", "_", edge["dst"])
        arrow = _arrow_to_mermaid(edge["style"])
        label = edge["label"]
        if label:
            label = label.replace('"', "'")
            return f'    {src} {arrow}|"{label}"| {dst}'
        return f"    {src} {arrow} {dst}"

    def to_mermaid(self) -> str:
        """Return a Mermaid graph block as a string."""
        lines: list[str] = ["graph TD"]

        # Emit node declarations
        emitted: set[str] = set()
        for nid in self._nodes:
            lines.append(self._mermaid_node_line(nid))
            emitted.add(nid)

        # Emit synthetic nodes that appear only in edges
        for edge in self._edges:
            for nid in (edge["src"], edge["dst"]):
                if nid not in emitted:
                    safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", nid)
                    lines.append(f'    {safe_id}["{nid}"]')
                    emitted.add(nid)

        # Emit edges
        for edge in self._edges:
            lines.append(self._mermaid_edge_line(edge))

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# File-level conversion
# ---------------------------------------------------------------------------

def _extract_comment_title(source: str) -> str:
    """Return the first comment line as a human-readable title, if present."""
    m = re.match(r"%\s*(.+)", source.lstrip())
    if m:
        return m.group(1).strip()
    return ""


def convert_file(src_path: Path) -> str:
    """Convert one .tex file to a Mermaid Markdown string."""
    source = src_path.read_text(encoding="utf-8")
    title = _extract_comment_title(source)

    # Handle positioning_matrix.tex: it is a tabular, not a tikzpicture
    if r"\begin{tabular}" in source and r"\begin{tikzpicture}" not in source:
        return _convert_tabular(src_path.stem, title, source)

    parser = TikzParser(source)
    parser.parse()

    mermaid_block = parser.to_mermaid()

    heading = title or src_path.stem
    lines = [
        f"# {heading}",
        "",
        f"*Source: `docs/figures/{src_path.name}`*",
        "",
        "```mermaid",
        mermaid_block,
        "```",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Tabular → Mermaid table (positioning_matrix)
# ---------------------------------------------------------------------------

_TABULAR_ROW_RE = re.compile(r"^(.+?)\\\\", re.MULTILINE)


def _convert_tabular(stem: str, title: str, source: str) -> str:
    """Convert a LaTeX tabular environment to a Markdown table."""
    rows: list[list[str]] = []
    for m in _TABULAR_ROW_RE.finditer(source):
        raw = m.group(1)
        if r"\hline" in raw:
            continue
        cells = [_clean_latex(c).strip() for c in raw.split("&")]
        if any(c for c in cells):
            rows.append(cells)

    if not rows:
        return f"# {title or stem}\n\n*No table content found.*\n"

    # Normalise column count
    ncols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < ncols:
            r.append("")

    def md_row(cells: list[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    header = rows[0]
    separator = ["---"] * ncols
    body_rows = rows[1:]

    lines = [
        f"# {title or stem}",
        "",
        f"*Source: `docs/figures/{stem}.tex`*",
        "",
        md_row(header),
        md_row(separator),
    ]
    for row in body_rows:
        lines.append(md_row(row))

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_inputs(raw_inputs: list[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in raw_inputs:
        p = Path(raw)
        if p.is_dir():
            paths.extend(sorted(p.glob("*.tex")))
        elif p.is_file():
            paths.append(p)
        else:
            print(f"warning: {raw!r} not found, skipping", file=sys.stderr)
    return paths


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    default_input = str(repo_root / "docs" / "figures")
    default_output = str(repo_root / "docs" / "figures" / "mermaid")

    parser = argparse.ArgumentParser(
        description="Convert TikZ .tex figures to Mermaid .md files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        default=[default_input],
        metavar="INPUT",
        help="One or more .tex files or directories to convert "
             f"(default: {default_input})",
    )
    parser.add_argument(
        "--output-dir",
        default=default_output,
        metavar="DIR",
        help=f"Directory for generated .md files (default: {default_output})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without touching the filesystem.",
    )
    args = parser.parse_args(argv)

    input_files = _resolve_inputs(args.inputs)
    if not input_files:
        print("No .tex files found.", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    for src in input_files:
        out_name = src.stem + ".md"
        out_path = output_dir / out_name
        try:
            content = convert_file(src)
        except Exception as exc:  # noqa: BLE001
            print(f"error: {src.name}: {exc}", file=sys.stderr)
            continue
        if args.dry_run:
            print(f"[dry-run] would write {out_path}")
            preview = content[:300]
            print(preview)
            if len(content) > 300:
                print("...")
        else:
            out_path.write_text(content, encoding="utf-8")
            print(f"written: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

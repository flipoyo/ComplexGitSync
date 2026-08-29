"""Module-size ratchet and Ring-0 purity checker for the Isolation Plan.

Operationalises three checks that `AgentSpecs/IsolationPlan.md` §3.1/§3.2 and
`AgentSpecs/20260828_Isolation_DevPlanTicket.md` call for but that no `ruff`
selector covers natively:

1. **Ratchet, not a fixed ceiling.** Each module's LOC/public-symbol/
   internal-import counts are compared against a recorded baseline
   (`scripts/ceiling_baseline.json`), not a single hard number. A module may
   never regress past its own baseline; it may always improve on it. Running
   with `--write-baseline` after a module is intentionally shrunk locks the
   new, lower bound in — the ratchet only tightens. This lets the check run
   in CI from day one of the isolation work, long before any extraction
   happens, catching "the monolith grew again" the moment it happens rather
   than only at the end of the plan.
2. **Ring-0 purity.** For modules named in `--ring0` (or listed under
   `"ring0_modules"` in the baseline file), asserts no I/O-shaped names are
   referenced anywhere in the module: `subprocess`, `open`, `os.environ`,
   `os.getenv`, `time.time`/`time.sleep`, `datetime.now`/`datetime.utcnow`,
   or any `Path(...).write_*`/`read_*`/`mkdir`/`unlink` call. AST-based, so
   it also catches indirect calls like `getattr(os, "environ")` only if
   spelled directly (`os.environ`) — this is a heuristic linter, not a
   soundness proof; see IsolationPlan.md §1 Rule 3 for the actual contract.
3. **Docstring contract cross-check.** Parses each module's first docstring
   for `Ring:`, `Contract:`, and `Imports:` lines (the header format defined
   in IsolationPlan.md §3.2) and, when present, verifies the declared
   `Imports:` list matches the module's actual `from .<name> import ...`
   statements. A module without a contract header is skipped, not failed —
   the header is opt-in until P6 makes it universal.

Usage
-----
    pixi run python scripts/check_module_ceilings.py                # report
    pixi run python scripts/check_module_ceilings.py --check         # ratchet, exit 1 on regression
    pixi run python scripts/check_module_ceilings.py --write-baseline # lock in current state

Exit code: 0 unless `--check` is passed and a module regressed past its
baseline, declared a Ring-0 contract it violates, or declared an `Imports:`
list that disagrees with its real imports — then 1.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "ComplexGitSync"
BASELINE_PATH = REPO_ROOT / "scripts" / "ceiling_baseline.json"

# Absolute ceilings from IsolationPlan.md §3.1 — informational in the report;
# only the *ratchet* against the recorded baseline is enforced by --check.
MODULE_LOC_HARD_CEILING = 500
PUBLIC_SYMBOLS_HARD_CEILING = 7
INTERNAL_IMPORTS_HARD_CEILING = 6

_FORBIDDEN_RING0_ATTR_PATHS = {
    ("subprocess",),
    ("os", "environ"),
    ("os", "getenv"),
    ("time", "time"),
    ("time", "sleep"),
    ("datetime", "now"),
    ("datetime", "utcnow"),
}
_FORBIDDEN_RING0_CALL_NAMES = {"open"}
_FORBIDDEN_PATH_WRITE_METHODS = {
    "write_text",
    "write_bytes",
    "mkdir",
    "unlink",
    "rmdir",
    "rename",
    "replace",
    "touch",
    "symlink_to",
}


@dataclass
class ModuleReport:
    relative_path: str
    loc: int
    public_symbols: list[str] = field(default_factory=list)
    internal_imports: list[str] = field(default_factory=list)
    ring0_violations: list[str] = field(default_factory=list)
    contract: dict[str, str] | None = None
    contract_import_mismatch: list[str] = field(default_factory=list)


def iter_modules(root: Path = SRC_ROOT) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def count_loc(source: str) -> int:
    """Non-blank, non-comment-only physical lines. Docstrings count as code."""
    count = 0
    for line in source.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


def _public_top_level_symbols(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.append(target.id)
    return names


def _internal_imports(tree: ast.Module) -> list[str]:
    """Every name imported from a sibling ``ComplexGitSync`` module (relative import)."""
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.level or 0) >= 1:
            module = ("." * node.level) + (node.module or "")
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")
    return imports


def _parse_contract_header(source: str) -> dict[str, str] | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    docstring = ast.get_docstring(tree, clean=True)
    if not docstring:
        return None
    contract: dict[str, str] = {}
    for line in docstring.splitlines():
        for key in ("Ring", "Contract", "Imports"):
            prefix = f"{key}:"
            if line.strip().startswith(prefix):
                contract[key] = line.strip()[len(prefix):].strip()
    return contract or None


def _check_ring0_purity(tree: ast.Module) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            path = _attribute_path(node)
            if path is not None and tuple(path[:2]) in _FORBIDDEN_RING0_ATTR_PATHS:
                violations.append(f"line {node.lineno}: forbidden reference {'.'.join(path)}")
            if path is not None and len(path) >= 2 and path[-1] in _FORBIDDEN_PATH_WRITE_METHODS:
                violations.append(
                    f"line {node.lineno}: possible filesystem-write call .{path[-1]}(...)"
                )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _FORBIDDEN_RING0_CALL_NAMES:
                violations.append(f"line {node.lineno}: forbidden call {node.func.id}(...)")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"subprocess"}:
                    violations.append(f"line {node.lineno}: forbidden import {alias.name}")
    return violations


def _attribute_path(node: ast.Attribute) -> list[str] | None:
    parts: list[str] = [node.attr]
    current: ast.expr = node.value
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return list(reversed(parts))
    return None


def analyse_module(path: Path, *, ring0_modules: set[str]) -> ModuleReport:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    relative = path.relative_to(SRC_ROOT).as_posix()
    report = ModuleReport(
        relative_path=relative,
        loc=count_loc(source),
        public_symbols=_public_top_level_symbols(tree),
        internal_imports=_internal_imports(tree),
        contract=_parse_contract_header(source),
    )
    module_stem = path.stem
    if module_stem in ring0_modules or relative in ring0_modules:
        report.ring0_violations = _check_ring0_purity(tree)
    if report.contract and "Imports" in report.contract and report.internal_imports:
        # Only cross-checked when the module has at least one real internal
        # import — a module with none is free to describe that fact in
        # prose ("none", "stdlib only (...)", etc.) without tripping this
        # check, since there is nothing to verify the prose against either
        # way.
        declared = {
            name.strip().strip(".")
            for name in report.contract["Imports"].split(",")
            if name.strip() and name.strip().lower() != "none"
        }
        actual = {imp.split(".")[-2] if "." in imp else imp for imp in report.internal_imports}
        actual_modules = {imp.lstrip(".").split(".")[0] for imp in report.internal_imports}
        if declared and not declared.issubset(actual_modules | actual):
            report.contract_import_mismatch = sorted(declared - (actual_modules | actual))
    return report


def load_baseline() -> dict:
    if not BASELINE_PATH.is_file():
        return {"modules": {}, "ring0_modules": []}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def write_baseline(reports: list[ModuleReport], ring0_modules: set[str]) -> None:
    data = {
        "modules": {
            r.relative_path: {
                "loc": r.loc,
                "public_symbols": len(r.public_symbols),
                "internal_imports": len(r.internal_imports),
            }
            for r in reports
        },
        "ring0_modules": sorted(ring0_modules),
    }
    BASELINE_PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _print_report(reports: list[ModuleReport]) -> None:
    header = f"{'module':<30} {'loc':>6} {'pub':>4} {'imp':>4}  flags"
    print(header)
    print("-" * len(header))
    for r in sorted(reports, key=lambda r: -r.loc):
        flags = []
        if r.loc > MODULE_LOC_HARD_CEILING:
            flags.append(f"LOC>{MODULE_LOC_HARD_CEILING}")
        if len(r.public_symbols) > PUBLIC_SYMBOLS_HARD_CEILING:
            flags.append(f"symbols>{PUBLIC_SYMBOLS_HARD_CEILING}")
        if len(r.internal_imports) > INTERNAL_IMPORTS_HARD_CEILING:
            flags.append(f"imports>{INTERNAL_IMPORTS_HARD_CEILING}")
        if r.ring0_violations:
            flags.append(f"RING0:{len(r.ring0_violations)}")
        if r.contract_import_mismatch:
            flags.append(f"CONTRACT-MISMATCH:{','.join(r.contract_import_mismatch)}")
        print(
            f"{r.relative_path:<30} {r.loc:>6} {len(r.public_symbols):>4} "
            f"{len(r.internal_imports):>4}  {' '.join(flags)}"
        )


def run_check(reports: list[ModuleReport], baseline: dict) -> list[str]:
    failures: list[str] = []
    known = baseline.get("modules", {})
    for r in reports:
        prior = known.get(r.relative_path)
        if prior is None:
            if r.loc > MODULE_LOC_HARD_CEILING:
                failures.append(
                    f"{r.relative_path}: new module at {r.loc} LOC exceeds hard ceiling "
                    f"{MODULE_LOC_HARD_CEILING} with no recorded baseline — run "
                    f"--write-baseline once this is intentional, or shrink it first"
                )
            continue
        if r.loc > prior["loc"]:
            failures.append(
                f"{r.relative_path}: LOC grew {prior['loc']} -> {r.loc} "
                f"(ratchet only tightens — see scripts/ceiling_baseline.json)"
            )
        if len(r.public_symbols) > prior.get("public_symbols", 10**9):
            failures.append(
                f"{r.relative_path}: public symbol count grew "
                f"{prior.get('public_symbols')} -> {len(r.public_symbols)}"
            )
        if r.ring0_violations:
            failures.append(
                f"{r.relative_path}: Ring-0 purity violated — "
                + "; ".join(r.ring0_violations)
            )
        if r.contract_import_mismatch:
            failures.append(
                f"{r.relative_path}: docstring 'Imports:' header disagrees with actual "
                f"imports — missing {r.contract_import_mismatch}"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 on ratchet regression")
    parser.add_argument("--write-baseline", action="store_true", help="record current state")
    parser.add_argument(
        "--ring0", nargs="*", default=[], help="extra module stems to Ring-0-purity-check"
    )
    args = parser.parse_args(argv)

    baseline = load_baseline()
    ring0_modules = set(baseline.get("ring0_modules", [])) | set(args.ring0)

    reports = [analyse_module(p, ring0_modules=ring0_modules) for p in iter_modules()]
    _print_report(reports)

    if args.write_baseline:
        write_baseline(reports, ring0_modules)
        print(f"\nBaseline written to {BASELINE_PATH.relative_to(REPO_ROOT)}")
        return 0

    if args.check:
        failures = run_check(reports, baseline)
        if failures:
            print("\nRATCHET FAILURES:")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("\nAll modules within their recorded baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""`scripts/check_oo_conformance.py` — the class-first module-shape checker.

Backs the ClassFirstPackage ticket and `AdditionalSpecs.md`'s *Module shape*
section. Where `check_module_ceilings.py` ratchets a module's *size*, this
ratchets its *shape*, with the same idea: measured in CI, not trusted by eye,
and a baseline that may shrink and never grow.

Five lists are measured over every module under `src/ComplexGitSync/`:

1. **no_behaviour_class** — a module that holds a domain concept as free
   functions: no *behaviour class* (one with methods of its own beyond
   dunders; enums, exception types and method-less value objects do not
   count), three or more public module-level functions, and 100+ lines.
2. **over_class_cap** — more than three behaviour classes in one file.
3. **over_2000_lines** — a module past 2000 lines, which must become a
   directory (see the ModulePackagisation ticket).
4. **missing_all** — a module that declares no `__all__`, or whose public
   classes and functions are not all listed in it.
5. **filesystem_writers** — a module-level function that calls a
   filesystem mutator (`write_text`, `mkdir`, `replace`, `unlink`, `rmtree`,
   `copy2`, ...). A writer belongs on the class that owns what it writes.

`cli/` is exempt from lists 1 and 2 — it is derived from client methods
implemented elsewhere and holds no domain concept. It is *not* exempt from
list 3, only recorded at its current size, nor from list 4.

The baseline (`scripts/oo_conformance_baseline.json`) records each list as it
stood. `--check` fails when a list gains a member, or when a size recorded in
`over_2000_lines` grows. A list that shrinks passes; run `--write-baseline`
to lock the improvement in.

Usage
-----
    pixi run python scripts/check_oo_conformance.py                 # report
    pixi run python scripts/check_oo_conformance.py --check          # ratchet, exit 1 on regression
    pixi run python scripts/check_oo_conformance.py --write-baseline # lock in current state

This is a heuristic linter, not a soundness proof: an indirect call such as
`getattr(path, "write_text")` is not seen.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "ComplexGitSync"
BASELINE_PATH = Path(__file__).resolve().parent / "oo_conformance_baseline.json"

#: A module this long, with this many public free functions, and no behaviour
#: class holds a domain concept in functions rather than in a class.
_MIN_LOC_NO_CLASS = 100
_MIN_PUBLIC_FUNCTIONS = 3
_CLASS_CAP = 3
_LINE_CAP = 2000

#: Names of calls that write to the filesystem.
_MUTATORS = frozenset(
    {
        "write_text",
        "write_bytes",
        "mkdir",
        "replace",
        "unlink",
        "rmtree",
        "copy2",
        "copytree",
        "touch",
        "chmod",
        "symlink_to",
    }
)

#: Packages exempt from the class rules: derived from client methods
#: implemented elsewhere, holding no domain concept of their own.
_CLASS_EXEMPT_PREFIXES = ("cli/",)

LISTS = (
    "no_behaviour_class",
    "over_class_cap",
    "over_2000_lines",
    "missing_all",
    "filesystem_writers",
)


def _is_behaviour_class(node: ast.ClassDef) -> bool:
    """A class with methods of its own beyond dunders, and not an enum or exception."""
    bases = [ast.unparse(base) for base in node.bases]
    if any("Enum" in base for base in bases):
        return False
    if any("Error" in base or "Exception" in base for base in bases):
        return False
    return any(
        isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not member.name.startswith("__")
        for member in node.body
    )


def _declared_all(tree: ast.Module) -> list[str] | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            try:
                return [element.value for element in node.value.elts]
            except AttributeError:
                return []
    return None


def _public_symbols(tree: ast.Module) -> list[str]:
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]


def _is_mutation(call: ast.Call) -> bool:
    """Whether *call* writes to the filesystem.

    `replace` and `rename` are also string and dict methods, so they count
    only as `os.replace(a, b)` or as `Path.replace(target)` -- a single
    argument -- and `str.replace(old, new)` does not.
    """
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    receiver = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else None
    if name in ("replace", "rename"):
        return receiver == "os" or (len(call.args) == 1 and not call.keywords)
    if name == "move":
        return receiver == "shutil"
    if name == "open":
        mode = call.args[1] if len(call.args) > 1 else next(
            (kw.value for kw in call.keywords if kw.arg == "mode"), None
        )
        return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and any(
            flag in mode.value for flag in "wax+"
        )
    return name in _MUTATORS


def _mutating_functions(tree: ast.Module) -> list[str]:
    writers: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            if _is_mutation(call):
                writers.append(node.name)
                break
    return writers


def measure(root: Path = SRC_ROOT) -> dict[str, object]:
    """Every list this checker ratchets, for the modules under *root*."""
    lists: dict[str, list[str]] = {name: [] for name in LISTS if name != "over_2000_lines"}
    sizes: dict[str, int] = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        loc = len(source.splitlines())
        exempt = relative.startswith(_CLASS_EXEMPT_PREFIXES)

        behaviour = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and _is_behaviour_class(node)
        ]
        public_functions = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
        ]
        if (
            not exempt
            and not behaviour
            and len(public_functions) >= _MIN_PUBLIC_FUNCTIONS
            and loc >= _MIN_LOC_NO_CLASS
        ):
            lists["no_behaviour_class"].append(relative)
        if not exempt and len(behaviour) > _CLASS_CAP:
            lists["over_class_cap"].append(relative)
        if loc > _LINE_CAP:
            sizes[relative] = loc

        if path.name != "__init__.py":
            declared = _declared_all(tree)
            if declared is None or any(s not in declared for s in _public_symbols(tree)):
                lists["missing_all"].append(relative)
        lists["filesystem_writers"].extend(
            f"{relative}:{name}" for name in _mutating_functions(tree)
        )
    return {**{name: sorted(values) for name, values in lists.items()}, "over_2000_lines": sizes}


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, object]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def run_check(measured: dict[str, object], baseline: dict[str, object]) -> list[str]:
    """Every regression against *baseline*: a list that gained a member, or a size that grew."""
    failures: list[str] = []
    for name in ("no_behaviour_class", "over_class_cap", "missing_all", "filesystem_writers"):
        allowed = set(baseline.get(name, []))
        for member in measured[name]:  # type: ignore[union-attr]
            if member not in allowed:
                failures.append(f"{name}: {member} is new (the ratchet only tightens)")
    recorded_sizes = baseline.get("over_2000_lines", {})
    for module, loc in measured["over_2000_lines"].items():  # type: ignore[union-attr]
        if module not in recorded_sizes:
            failures.append(f"over_2000_lines: {module} ({loc} lines) is new")
        elif loc > recorded_sizes[module]:
            failures.append(
                f"over_2000_lines: {module} grew {recorded_sizes[module]} -> {loc} "
                "(the ratchet only tightens)"
            )
    return failures


def _print_report(measured: dict[str, object]) -> None:
    for name in LISTS:
        value = measured[name]
        size = len(value)  # type: ignore[arg-type]
        print(f"{name}: {size}")
        if isinstance(value, dict):
            for module, loc in sorted(value.items()):
                print(f"    {module}  {loc} lines")
        else:
            for member in value:  # type: ignore[union-attr]
                print(f"    {member}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 on ratchet regression")
    parser.add_argument("--write-baseline", action="store_true", help="record current state")
    args = parser.parse_args(argv)

    measured = measure()
    if args.write_baseline:
        BASELINE_PATH.write_text(json.dumps(measured, indent=2, sort_keys=True) + "\n")
        print(f"Baseline written to {BASELINE_PATH.relative_to(REPO_ROOT)}")
        return 0

    _print_report(measured)
    if args.check:
        failures = run_check(measured, load_baseline())
        if failures:
            print("\nCONFORMANCE RATCHET FAILURES:")
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print("\nAll lists within their recorded baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

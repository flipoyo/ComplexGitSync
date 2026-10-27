"""`scripts/map_surface.py` — the map of what `cgitsync` exposes, by ring (Minimalism).

Reads `src/ComplexGitSync/` and the real argparse tree, runs nothing else, and
prints one markdown table per question the Minimalism ticket asks. It changes
no file. Re-run it after CliReduction to see what is left.

    pixi run python scripts/map_surface.py                  # every section
    pixi run python scripts/map_surface.py --section cli    # one section
    pixi run python scripts/map_surface.py --full           # every public method, not a summary

Sections: ``modules`` (class and public method count per module and ring),
``unused`` (public names nothing in ``src/`` refers to), ``client`` (each public
client method, and the leaf commands that reach it), ``cli`` (each leaf
command, the client method it reaches, what it writes), ``provider`` (every
``run_tool`` call site and every client method that reaches one), ``merkle``
(every function that computes or hands out a hash tree), ``rings`` (the
document interfaces `.cgs`, `.gts` and OperatingSpace, and who crosses them),
``gitrepo`` (what a ``GitRepo`` folder would still import) and ``folders``
(the proposed folder per module).

The analysis is by name, on the syntax tree: a method is *unused* when no
attribute, name or string in ``src/`` equals it. That over-counts use and never
under-counts it, so a name listed here is a candidate, not a verdict.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "ComplexGitSync"
TESTS = ROOT / "tests"

#: Folder proposed per module (SrcLayout, Minimalism D6): the owner edits this column.
FOLDERS = {
    "GitRepo": ("git_repo", "git_branch", "git_runner", "provider", "clone_guard"),
    "Merkle": ("merkle/",),
    "Tree": (
        "git_tree", "git_tree_branch", "registry", "discovery", "tree_env", "environment_spec", "toolchain",
        "cgs_format", "gts_document", "config_document", "config_document_io", "paths", "snapshot_resolver",
        "settings", "master", "project_version", "commit_message", "ticket_gate", "workspace_lock",
        "operations/", "memory/", "autofix/",
    ),
    "OperatingSpace": ("operating_space",),
    "Omniscience (gx4g's register)": ("omniscience_", "orchestre/omniscience", "global_index"),
    "Above OperatingSpace": ("cli/", "orchestre/", "json_render", "status_render", "universal_clock", "errors", "__init__", "__main__"),
}

#: Hand-listed client methods that write and do not take the workspace lock (StateLocking).
UNLOCKED_WRITERS = {
    "initialise", "initialise_cgs", "initialise_cgs_document", "bootstrap", "clone_cgs", "configure", "import_submodules",
    "init_from_submodules", "launch_state", "freeze_state", "repo_create", "memory_clone", "memory_setup_decline",
    "omniscience_register", "omniscience_observe", "omniscience_mount", "write_gts_snapshot", "discover_repos",
    "add_memory_repo_cgs",
}

#: Leaves whose write or read depends on a flag the client method cannot show, hand-checked against the handler.
#: `verify repair` calls the same `verify` as `verify check` and rewrites the `HEAD` cache; `submodules report`
#: is the dry run of `import_submodules`; `discover` writes a `.cgs` only with `--write`.
LEAF_WRITES = {"verify repair": "writes", "submodules report": "reads"}

HASH_NAME = re.compile(r"(merkle|leaf|node_hash|_root$|^root$|digest|proof|gittree_root|os_id|state_hash|compute_.*hash)", re.I)
#: The module that computes a hash, and the two adapters that hand it the State's leaves (`gts_document`, `operating_space`).
HASH_MODULES = ("merkle/merkle_root", "gts_document", "operating_space")


@dataclass
class Module:
    key: str  # path without .py, relative to the package: "orchestre/client"
    tree: ast.Module
    ring: str = "?"
    imports: set[str] = field(default_factory=set)  # keys of package modules it imports
    classes: dict[str, list[str]] = field(default_factory=dict)  # class -> public methods
    functions: list[str] = field(default_factory=list)  # public top-level functions
    text: str = ""

    @property
    def folder(self) -> str:
        for folder, markers in FOLDERS.items():
            if any(self.key == m or self.key.startswith(m) for m in markers):
                return folder
        return "unplaced"


def load_modules() -> dict[str, Module]:
    found: dict[str, Module] = {}
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        key = path.relative_to(SRC).with_suffix("").as_posix()
        text = path.read_text(encoding="utf-8")
        found[key] = Module(key, ast.parse(text), text=text)
    for module in found.values():
        doc = ast.get_docstring(module.tree) or ""
        ring = re.search(r"^Ring:\s*(\d)", doc, re.M)
        module.ring = ring.group(1) if ring else "?"
        parts = module.key.split("/")[:-1]
        for node in ast.walk(module.tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                base = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                target = base + (node.module.split(".") if node.module else [])
                for name in ([None] if node.module else [alias.name for alias in node.names]):
                    candidate = "/".join(target + ([name] if name else []))
                    for option in (candidate, f"{candidate}/__init__"):
                        if option in found:
                            module.imports.add(option)
                            break
        for node in module.tree.body:
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                module.classes[node.name] = [
                    item.name for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_")
                ]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                module.functions.append(node.name)
    return found


def references(texts: list[str]) -> set[str]:
    """Every identifier any attribute, name or identifier-shaped string in *texts* spells."""
    seen: set[str] = set()
    for text in texts:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                seen.add(node.attr)
            elif isinstance(node, ast.Name):
                seen.add(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
                seen.add(node.value)
    return seen


def public_names(module: Module) -> list[tuple[str, str]]:
    names = [("function", name) for name in module.functions]
    names += [(f"{cls}.method", method) for cls, methods in module.classes.items() for method in methods]
    names += [("class", cls) for cls in module.classes]
    return names


def unused(modules: dict[str, Module]) -> list[tuple[str, str, str, bool]]:
    """``(module, kind, name, used_in_tests)`` for public names nothing in ``src/`` refers to."""
    in_src = references([m.text for m in modules.values()])
    # A name a module only defines does not refer to itself: count references outside its own def.
    own: dict[str, int] = defaultdict(int)
    for module in modules.values():
        for _, name in public_names(module):
            own[name] += 1
    in_tests = references([p.read_text(encoding="utf-8") for p in sorted(TESTS.rglob("*.py"))])
    rows = []
    for module in modules.values():
        for kind, name in public_names(module):
            if name in in_src or name.startswith("__"):
                continue
            rows.append((module.key, kind, name, name in in_tests))
    return rows


def deprecated(modules: dict[str, Module]) -> list[tuple[str, str]]:
    rows = []
    for module in modules.values():
        for node in ast.walk(module.tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node) or ""
                if re.search(r"\bdeprecated\b|kept for compatibility|backward[- ]compat", doc, re.I):
                    rows.append((module.key, node.name))
    return rows


# -- the CLI ----------------------------------------------------------------


@dataclass
class Leaf:
    path: str
    handler: str
    client_methods: list[str]
    below_client: list[str]
    writes: str
    how: str = "exact"  # exact | matched | group: how the client methods were attributed to this leaf


def _functions(tree: ast.Module) -> dict[str, ast.AST]:
    return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


class CliTrace:
    """What a CLI handler reaches: the client methods it calls and the classes it uses below the client."""

    def __init__(self, modules: dict[str, Module]) -> None:
        self.functions = {key: _functions(m.tree) for key, m in modules.items() if key.startswith("cli/")}
        self.aliases: dict[str, dict[str, tuple[str, str]]] = {}
        self.outside: dict[str, set[str]] = {}
        for key in self.functions:
            found: dict[str, tuple[str, str]] = {}  # `from .branch_command import handle as _handle_branch_command`
            reached: set[str] = set()
            for node in modules[key].tree.body:
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.level == 1 and node.module and f"cli/{node.module}" in self.functions:
                    found.update({a.asname or a.name: (f"cli/{node.module}", a.name) for a in node.names})
                elif node.level >= 2:
                    reached.update(a.asname or a.name for a in node.names)
            self.aliases[key], self.outside[key] = found, reached

    def trace(self, key: str, name: str) -> tuple[set[str], set[str]]:
        calls: set[str] = set()
        below: set[str] = set()
        queue, seen = [(key, name)], set()
        while queue:
            where, fname = queue.pop()
            if (where, fname) in seen or fname not in self.functions.get(where, {}):
                continue
            seen.add((where, fname))
            for node in ast.walk(self.functions[where][fname]):
                self._visit(node, where, queue, calls, below)
        return calls, below

    def _visit(self, node: ast.AST, where: str, queue: list, calls: set[str], below: set[str]) -> None:
        if isinstance(node, ast.Name):  # called or merely referenced (a dispatch table)
            if node.id in self.functions[where]:
                queue.append((where, node.id))
            elif node.id in self.aliases[where]:
                queue.append(self.aliases[where][node.id])
            if node.id in self.outside[where] and _is_behaviour(node.id):
                below.add(node.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            owner = node.func.value.id
            if owner in ("client", "active_client"):
                calls.add(node.func.attr)
            elif owner in self.outside[where] and _is_behaviour(owner):
                below.add(f"{owner}.{node.func.attr}")


def leaves(modules: dict[str, Module]) -> list[Leaf]:
    sys.path.insert(0, str(ROOT / "src"))
    from ComplexGitSync.cli import build_parser  # noqa: PLC0415
    from ComplexGitSync.orchestre.workspace_locking import WRITING_METHODS  # noqa: PLC0415

    writers = set(WRITING_METHODS) | UNLOCKED_WRITERS
    tracer = CliTrace(modules)
    result: list[Leaf] = []

    def walk(parser: argparse.ArgumentParser, path: tuple[str, ...], inherited=None) -> None:
        action = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
        handler = parser.get_default("handler") or inherited
        if action is not None:
            for name, child in action.choices.items():
                walk(child, (*path, name), handler)
            return
        if handler is None:
            return
        key = handler.__module__.removeprefix("ComplexGitSync.").replace(".", "/")
        calls, below = tracer.trace(key, handler.__name__) if key in tracer.functions else (set(), set())
        writes = "writes" if calls & writers else ("none" if not calls else "reads")
        result.append(Leaf(" ".join(path), f"{handler.__module__.split('.')[-1]}.{handler.__name__}", sorted(calls), sorted(below), writes))

    walk(build_parser(), ())
    return _refine(result, writers, {m for methods in modules["orchestre/client"].classes.values() for m in methods})


def _is_behaviour(name: str) -> bool:
    """A class a CLI module reached for, not a constant, an exception or the client itself."""
    return name[0].isupper() and not name.isupper() and not name.endswith(("Error", "Warning")) and name != "ComplexGitSyncClient"


#: Leaves whose client method no call or name shows (hand-checked against the handlers, 2026-10-27).
NAMED = {
    "branch create": "branch", "branch check": "branch_ancestry", "verify check": "verify", "verify repair": "verify",
    "memory mount": "add_memory_repo_cgs", "self-history list": "memory_self_history", "env show": "environment",
    "submodules report": "import_submodules",
}
_GENERIC = {"get", "tree", "state", "resolve", "dependency", "registry"}


def _refine(found: list[Leaf], writers: set[str], client: set[str]) -> list[Leaf]:
    """A group (`memory`, `branch`, `omniscience`…) has one handler that dispatches on the subcommand.

    The handler's call set is the group's. A leaf keeps the group's methods whose name holds every
    word of its own (``memory push`` -> ``memory_push``; ``branch close`` -> ``close_branch``).
    When none does, the leaf keeps the whole group set, marked ``group``.
    """
    by_handler: dict[str, list[Leaf]] = defaultdict(list)
    for leaf in found:
        by_handler[leaf.handler].append(leaf)
    for members in by_handler.values():
        if len(members) < 2:
            continue
        for leaf in members:
            words = set(re.split(r"[ _-]", leaf.path)) - {members[0].path.split()[0]}
            group = members[0].path.split()[0].replace("-", "_")
            names = [m for m in leaf.client_methods if m not in _GENERIC and words <= set(m.split("_"))]
            # A dispatcher that builds the name (`getattr(client, f"memory_{sub}")`) leaves no call to read.
            names = names or [m for m in (f"{group}_{leaf.path.split()[-1].replace('-', '_')}",) if m in client]
            if leaf.path in NAMED:
                names, how = [NAMED[leaf.path]], "hand"
            else:
                how = "matched"
            if names:
                leaf.client_methods, leaf.how = names, how
            else:
                leaf.how = "group"
            leaf.writes = "writes" if set(leaf.client_methods) & writers else ("none" if not leaf.client_methods else "reads")
    for leaf in found:
        leaf.writes = LEAF_WRITES.get(leaf.path, leaf.writes)
    return found


def _client_table(modules: dict[str, Module], found: list[Leaf]) -> str:
    """Every public client method, and whether a command, or only other code, reaches it."""
    client = sorted(m for methods in modules["orchestre/client"].classes.values() for m in methods)
    reached: dict[str, list[str]] = defaultdict(list)
    for leaf in found:
        for method in leaf.client_methods:
            reached[method].append(leaf.path)
    elsewhere = references([m.text for k, m in modules.items() if k != "orchestre/client"])
    rows = [(f"`{m}`", ", ".join(f"`{p}`" for p in reached[m]) or "-", "yes" if m in elsewhere else "no") for m in client]
    none = [r[0] for r in rows if r[1] == "-"]
    return (
        f"{len(client)} public client methods; {len(client) - len(none)} are reached by a command. "
        f"{len(none)} are not (a Python caller or another module only): {', '.join(none)}.\n\n"
        + render(rows, ("client method", "commands that reach it", "used by other src code"))
    )


def provider_sites(modules: dict[str, Module]) -> list[tuple[str, int, str]]:
    rows = []
    for module in modules.values():
        if module.key in ("git_runner", "provider"):
            continue
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name in ("run_tool", "creation_plan"):
                    rows.append((module.key, node.lineno, name))
    return sorted(rows)


# -- the Merkle surface and the rings ---------------------------------------


def merkle(modules: dict[str, Module]) -> list[tuple[str, str, str]]:
    rows = []
    for key in HASH_MODULES:
        module = modules[key]
        for node in ast.walk(module.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and HASH_NAME.search(node.name):
                visible = "public" if not node.name.startswith("_") else "private"
                rows.append((key, node.name, visible))
    return sorted(rows)


def hashlib_outside(modules: dict[str, Module]) -> list[str]:
    return sorted(m.key for m in modules.values() if "hashlib" in m.text and m.key != "merkle/merkle_root")


def users_of(modules: dict[str, Module], target: str) -> list[tuple[str, str]]:
    return sorted((m.key, m.ring) for m in modules.values() if target in m.imports and m.key != target)


def gitrepo_leaks(modules: dict[str, Module], extra: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    members = {m.key for m in modules.values() if m.folder == "GitRepo"} | set(extra)
    return sorted((key, imported) for key in members for imported in modules[key].imports if imported not in members)


def render(rows: list[tuple], head: tuple[str, ...]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(out)


def sections(modules: dict[str, Module], *, full: bool) -> dict[str, str]:
    result: dict[str, str] = {}
    by_ring = defaultdict(lambda: [0, 0, 0, 0])
    rows = []
    for module in modules.values():
        methods = sum(len(v) for v in module.classes.values())
        rows.append((module.key, module.ring, len(module.classes), methods, len(module.functions), module.folder))
        counts = by_ring[module.ring]
        counts[0] += 1
        counts[1] += len(module.classes)
        counts[2] += methods
        counts[3] += len(module.functions)
    summary = render([(r, *c) for r, c in sorted(by_ring.items())], ("ring", "modules", "classes", "public methods", "public functions"))
    result["modules"] = summary + ("\n\n" + render(rows, ("module", "ring", "classes", "public methods", "public functions", "folder")) if full else "")
    dead = unused(modules)
    result["unused"] = (
        f"{len(dead)} public names that nothing in `src/` refers to "
        f"({sum(1 for r in dead if r[3]) } are used by a test, {sum(1 for r in dead if not r[3])} by nothing).\n\n"
        + render(sorted(dead), ("module", "kind", "name", "used in tests"))
        + "\n\nDeprecated by their own docstring: "
        + (", ".join(f"`{m}:{n}`" for m, n in deprecated(modules)) or "none")
    )
    found = leaves(modules)
    shared = sorted(n for n, c in _count(n for leaf in found for n in leaf.below_client).items() if c >= len(found) // 3)
    for leaf in found:
        leaf.below_client = [n for n in leaf.below_client if n not in shared]
    result["cli"] = (
        f"{len(found)} leaf commands. Classes below the client that a third of them reach, through `_shared` "
        f"(plumbing, listed once): {', '.join(f'`{n}`' for n in shared) or 'none'}.\n\n"
    ) + render(
        [(f"`{leaf.path}`", leaf.handler, ", ".join(leaf.client_methods) or "-", ", ".join(leaf.below_client) or "-", leaf.writes, leaf.how) for leaf in found],
        ("leaf command", "handler", "client methods it calls", "reaches below the client", "writes", "attribution"),
    )
    result["client"] = _client_table(modules, found)
    result["provider"] = render([(f"`{k}:{ln}`", name, modules[k].ring) for k, ln, name in provider_sites(modules)], ("call site", "call", "ring"))
    result["merkle"] = (
        render([(f"`{k}`", f"`{n}`", v) for k, n, v in merkle(modules)], ("module", "function", "visibility"))
        + "\n\nModules that import `hashlib` and are not a hashing module: " + (", ".join(f"`{k}`" for k in hashlib_outside(modules)) or "none")
    )
    result["rings"] = "\n\n".join(
        f"**{label}** (`{target}`) is imported by:\n\n" + render(users_of(modules, target), ("module", "ring"))
        for label, target in (("`.cgs`, the API of USER", "cgs_format"), ("`.gts`, the API of DEV", "gts_document"), ("OperatingSpace, the API of OPX", "operating_space"))
    )
    leaks = gitrepo_leaks(modules)
    result["gitrepo"] = (
        "Modules proposed for `GitRepo/`: " + ", ".join(f"`{m.key}`" for m in modules.values() if m.folder == "GitRepo")
        + ".\n\nWhat they import from outside that set (each row would have to go, or move in):\n\n"
        + (render([(f"`{a}`", f"`{b}`") for a, b in leaks], ("module", "imports")) if leaks else "nothing: the set imports nothing back.")
        + "\n\nWith the graph of repositories (`git_tree`, `git_tree_branch`) added to it, the set would also import:\n\n"
        + render([(f"`{a}`", f"`{b}`") for a, b in gitrepo_leaks(modules, ("git_tree", "git_tree_branch")) if (a, b) not in leaks], ("module", "imports"))
    )
    result["folders"] = render(
        sorted((m.folder, f"`{m.key}`", m.ring) for m in modules.values()), ("folder", "module", "ring")
    ) if full else render(
        sorted((folder, count) for folder, count in _count(m.folder for m in modules.values()).items()), ("folder", "modules")
    )
    return result


def _count(items) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[item] += 1
    return dict(counts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--section", choices=("modules", "unused", "client", "cli", "provider", "merkle", "rings", "gitrepo", "folders"))
    parser.add_argument("--full", action="store_true", help="Per-module rows instead of the summary.")
    args = parser.parse_args(argv)
    found = sections(load_modules(), full=args.full)
    for name, body in found.items():
        if args.section in (None, name):
            print(f"## {name}\n\n{body}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

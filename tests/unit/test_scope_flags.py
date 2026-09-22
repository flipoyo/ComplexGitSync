"""Scope flags: every one is read, and each one changes what is written.

Backs ``.agent/.local/.localSpec/DevTickets/archive/20260912_DeadScopeFlags_DevPlanTicket.md``. Three
flags were registered in the parser and never read — ``rm --private``,
``freeze --private`` and ``pull-force --private`` — so each reported
success while writing to the repositories the user was trying to avoid.

Two kinds of test here. The wiring tests pin each flag to the client call
it must reach, so removing the wiring fails. ``test_no_registered_argument
_is_ignored_by_its_handler`` is the general guard: it reads the parser and
the handlers and fails on *any* argument no handler consumes, so a fourth
dead flag cannot ship the way the first three did.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from ComplexGitSync.cli import configuration, expert, minimalist

# Submodules only, and the parser is rebuilt below rather than imported from
# ``cli/__init__.py``: sibling test modules load ``cli/`` from file paths and
# register a stand-in ``ComplexGitSync.cli`` in ``sys.modules`` that carries
# no attributes of its own, so ``from ComplexGitSync.cli import build_parser``
# fails or not depending on which test ran first.


def _build_full_parser() -> argparse.ArgumentParser:
    """The real parser: every command group, registered as ``main`` does."""
    parser = argparse.ArgumentParser(prog="cgitsync")
    subparsers = parser.add_subparsers(dest="command")
    minimalist.register_parsers(
        subparsers, add_gitignore_sync_arguments=expert._add_gitignore_sync_arguments
    )
    expert.register_parsers(subparsers)
    configuration.register_parsers(subparsers, non_negative_int=expert._non_negative_int)
    return parser


def _patch_client(monkeypatch, stub) -> None:
    """Install *stub* as the client the command under test will construct.

    Patched into the globals of the function that does the constructing,
    not into a module looked up by name — see the note above about two
    copies of ``cli/_shared.py`` being live at once.
    """
    monkeypatch.setitem(
        expert._run_with_logging.__globals__, "ComplexGitSyncClient", stub
    )


@pytest.fixture
def config_repo_tree(tmp_path):
    """A two-repo tree: the project's own root, and a writable config mount.

    Backed by real directories, because path resolution answers "which
    repository owns this file?" against the filesystem.
    """
    from ComplexGitSync.git_repo import WorkingRepo
    from ComplexGitSync.git_tree import ROOT_REPO_ID, WorkingGitTree, propagate_privacy

    root_path = tmp_path / "project"
    private_path = root_path / "deps" / "settings"
    private_path.mkdir(parents=True)

    root = WorkingRepo(
        repo_id=ROOT_REPO_ID,
        name="project",
        absolute_path=root_path,
        relative_path=Path("."),
    )
    private = WorkingRepo(
        repo_id="root:deps/settings",
        name="settings",
        parent_id=ROOT_REPO_ID,
        absolute_path=private_path,
        relative_path=Path("deps/settings"),
        private=True,
        writable=True,
    )
    tree = WorkingGitTree()
    tree.repos[root.repo_id] = root
    tree.repos[private.repo_id] = private
    propagate_privacy(tree)
    return tree, private


def _run(argv):
    parser = argparse.ArgumentParser(prog="cgitsync-scope-test")
    subparsers = parser.add_subparsers(dest="command")
    expert.register_parsers(subparsers)
    args = parser.parse_args(argv)
    return args.handler(args)


def _tree_state():
    return SimpleNamespace(
        lifecycle_state=SimpleNamespace(value="READY"),
        is_ready=True,
        registry_complete=True,
    )


# ---------------------------------------------------------------------------
# The general guard: no registered argument may go unread
# ---------------------------------------------------------------------------


def _arguments_read_by(function, *, depth: int = 3) -> set[str]:
    """Every ``args.<name>`` *function* reads, following what it delegates to.

    A handler may hand the whole namespace to a helper
    (``_resolve_commit_message(args)``), and the names that helper reads are
    read by the handler just as much. Passing bare ``args`` is therefore
    followed one call deep at a time, up to *depth*, rather than counted as
    "reads nothing" (a false alarm) or "reads everything" (a blind spot).
    """
    try:
        source = textwrap.dedent(inspect.getsource(function))
    except (OSError, TypeError):  # pragma: no cover - builtins have no source
        return set()
    names, delegates = _args_read_in(ast.parse(source))
    if depth > 0:
        for name in delegates:
            target = getattr(function, "__globals__", {}).get(name)
            if callable(target):
                names |= _arguments_read_by(target, depth=depth - 1)
    return names


def _args_read_in(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Split one function body into names read off ``args`` and calls given it."""
    names: set[str] = set()
    delegates: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _is_args(node.value):
            names.add(node.attr)
        elif isinstance(node, ast.Call):
            name = _getattr_name(node)
            if name is not None:
                names.add(name)
            elif _receives_args(node) and isinstance(node.func, ast.Name):
                delegates.add(node.func.id)
    return names, delegates


def _is_args(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "args"


def _getattr_name(call: ast.Call) -> str | None:
    """The literal name in ``getattr(args, "name", ...)``, if that is this call."""
    if not (isinstance(call.func, ast.Name) and call.func.id == "getattr"):
        return None
    if len(call.args) < 2 or not _is_args(call.args[0]):
        return None
    second = call.args[1]
    return second.value if isinstance(second, ast.Constant) else None


def _receives_args(call: ast.Call) -> bool:
    """Whether the whole namespace is handed to this call."""
    passed = list(call.args) + [keyword.value for keyword in call.keywords]
    return any(_is_args(argument) for argument in passed)


def _registered_arguments() -> dict[str, tuple[set[str], object]]:
    """Every subcommand's argument dests, with the handler that must read them."""
    parser = _build_full_parser()
    subcommands = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    registered: dict[str, tuple[set[str], object]] = {}
    for name, subparser in subcommands.choices.items():
        dests = {
            action.dest
            for action in subparser._actions
            if action.dest not in {"help", argparse.SUPPRESS}
        }
        registered[name] = (dests, subparser.get_default("handler"))
    return registered


def test_every_subcommand_has_a_handler():
    for name, (_, handler) in _registered_arguments().items():
        assert handler is not None, f"{name} registers no handler"


def test_no_registered_argument_is_ignored_by_its_handler():
    """A flag nothing reads must not be possible to ship twice.

    An accepted-and-ignored flag is worse than a missing one: a missing
    flag fails and the user tries something else, while this one reports
    success and does the thing the user was trying to avoid.
    """
    dead: dict[str, list[str]] = {}
    for name, (dests, handler) in _registered_arguments().items():
        read = _arguments_read_by(handler)
        ignored = sorted(dest for dest in dests if dest not in read)
        if ignored:
            dead[name] = ignored
    assert not dead, (
        "these arguments are registered in the parser and never read by their "
        f"handler: {dead}. Wire each one through to the client method that "
        "gives it meaning, or stop advertising it."
    )


# ---------------------------------------------------------------------------
# Wiring: each flag reaches the client call that gives it meaning
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flags, expected", [([], False), (["--private"], True)])
def test_rm_forwards_private_to_the_client(monkeypatch, tmp_path, capsys, flags, expected):
    captured: dict[str, object] = {}

    class StubClient:
        run_logger = None
        last_write_outcomes = ()

        def load_gts(self, path):
            pass

        def get_dependency_registry(self):
            raise RuntimeError("no registry in this stub")

        def remove(self, paths, *, private=False):
            captured["paths"] = list(paths)
            captured["private"] = private

        def get_tree_state(self):
            return _tree_state()

        def view_tree(self):
            return "ROOT project [main] clean synced"

    _patch_client(monkeypatch, StubClient)
    gts_path = tmp_path / "project.gts"
    gts_path.touch()

    assert _run(["rm", "notes.md", "--gts", str(gts_path), *flags]) == 0
    capsys.readouterr()

    assert captured["paths"] == ["notes.md"]
    assert captured["private"] is expected


@pytest.mark.parametrize("flags, expected", [([], False), (["--private"], True)])
def test_freeze_forwards_private_to_the_client(monkeypatch, tmp_path, capsys, flags, expected):
    captured: dict[str, object] = {}

    class StubClient:
        run_logger = None
        loaded_snapshot_path = None

        def load_gts(self, path):
            pass

        def get_dependency_registry(self):
            raise RuntimeError("no registry in this stub")

        def freeze(self, name, *, private=False, **kwargs):
            captured["name"] = name
            captured["private"] = private

        def get_tree_state(self):
            return _tree_state()

        def view_tree(self):
            return "ROOT project [main] clean synced"

    _patch_client(monkeypatch, StubClient)
    gts_path = tmp_path / "project.gts"
    gts_path.touch()

    assert _run(["freeze", "v1.0", "--gts", str(gts_path), *flags]) == 0
    capsys.readouterr()

    assert captured["name"] == "v1.0"
    assert captured["private"] is expected


@pytest.mark.parametrize("flags, expected", [([], False), (["--private"], True)])
def test_pull_force_forwards_private_to_the_client(monkeypatch, tmp_path, capsys, flags, expected):
    captured: dict[str, object] = {}

    class StubClient:
        run_logger = None

        def pull_force(self, source_path, *, force_access_protocol=None, private=False):
            captured["private"] = private
            return SimpleNamespace(get=lambda _: SimpleNamespace(absolute_path=Path("/tmp")))

        def get_tree_state(self):
            return _tree_state()

        def view_tree(self):
            return "ROOT project [main] clean synced"

    _patch_client(monkeypatch, StubClient)
    source = tmp_path / "project.gts"
    source.touch()

    assert _run(["pull-force", str(source), *flags]) == 0
    capsys.readouterr()

    assert captured["private"] is expected


# ---------------------------------------------------------------------------
# The warning: bare rm reaches a configuration repository, and says so
# ---------------------------------------------------------------------------


def _client_with_registry(registry):
    class StubClient:
        def get_dependency_registry(self):
            return registry

    return StubClient()


def test_bare_rm_warns_when_a_path_belongs_to_a_configuration_repo(config_repo_tree, capsys):
    registry, private_repo = config_repo_tree
    target = private_repo.absolute_path / "notes.md"
    target.write_text("x\n", encoding="utf-8")

    expert._warn_paths_reaching_configuration_repos(
        _client_with_registry(registry), [str(target)], private=False
    )

    warning = capsys.readouterr().err
    assert "configuration repository" in warning
    assert private_repo.name in warning
    assert "--private" in warning


def test_a_path_of_our_own_gets_no_warning(config_repo_tree, capsys):
    registry, _ = config_repo_tree
    target = registry.get("root").absolute_path / "src.py"
    target.write_text("x\n", encoding="utf-8")

    expert._warn_paths_reaching_configuration_repos(
        _client_with_registry(registry), [str(target)], private=False
    )

    assert capsys.readouterr().err == ""


def test_private_says_nothing_because_the_scope_already_does(config_repo_tree, capsys):
    registry, private_repo = config_repo_tree
    target = private_repo.absolute_path / "notes.md"
    target.write_text("x\n", encoding="utf-8")

    expert._warn_paths_reaching_configuration_repos(
        _client_with_registry(registry), [str(target)], private=True
    )

    assert capsys.readouterr().err == ""


def test_an_unresolvable_path_is_left_to_the_command_to_report(config_repo_tree, capsys):
    registry, _ = config_repo_tree

    expert._warn_paths_reaching_configuration_repos(
        _client_with_registry(registry), ["/nowhere/at/all.md"], private=False
    )

    assert capsys.readouterr().err == ""


def test_rm_dry_run_refuses_what_the_real_run_would_refuse(monkeypatch, tmp_path, capsys):
    """A preview that prints a plan the command then declines is worse than none."""
    from ComplexGitSync.errors import GitSyncError

    class StubClient:
        run_logger = None

        def load_gts(self, path):
            pass

        def get_dependency_registry(self):
            raise RuntimeError("no registry in this stub")

        def removals_outside_scope(self, paths, *, private=False):
            return ("README.md is inside 'project', which is outside this command's scope",)

        def remove(self, paths, *, private=False):
            raise AssertionError("a dry run must not remove anything")

        def get_tree_state(self):
            return _tree_state()

    _patch_client(monkeypatch, StubClient)
    gts_path = tmp_path / "project.gts"
    gts_path.touch()

    with pytest.raises(GitSyncError, match="outside this command's scope"):
        _run(["rm", "README.md", "--gts", str(gts_path), "--private", "--dry-run"])

    assert "plan_actions=" not in capsys.readouterr().out

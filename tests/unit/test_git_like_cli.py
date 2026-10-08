"""GitLikeCli: the CLI keeps what reads like Git, and nothing that deletes work.

The owner's rulings of 2026-10-02 (``main_1-1_GitLikeCli``): the commands and
flags in ``REMOVED_COMMANDS``/``REMOVED_OPTIONS`` are gone, and the ones in
``KEPT_COMMANDS`` stay, with every option that was asked to stay intact.
"""

from __future__ import annotations

import argparse

import pytest

from ComplexGitSync.cli import build_parser
from ComplexGitSync.orchestre import ComplexGitSyncClient

REMOVED_COMMANDS = {
    "clean-init", "purge", "clone", "freeze", "freeze-release-force",
    "launch-release", "configure", "create-cgs",
}
REMOVED_OPTIONS = {"--force-reclone", "--force-gitignore-sync"}
KEPT_COMMANDS = {
    "initialise", "bootstrap", "freeze-release", "status", "view-tree",
    "validate", "pull", "fetch", "autofix", "checkout", "branch",
    "add", "rm", "commit", "merge", "push", "tag",
    "submodules", "verify", "memory",
    "self-history", "discover", "repo", "env", "help",
}


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return dict(action.choices)


def _options(parser: argparse.ArgumentParser) -> set[str]:
    return {option for action in parser._actions for option in action.option_strings}


def test_removed_commands_are_not_registered():
    assert REMOVED_COMMANDS.isdisjoint(_subparsers(build_parser()))


def test_every_kept_command_is_registered():
    assert KEPT_COMMANDS <= set(_subparsers(build_parser()))


def test_no_command_registers_a_removed_option():
    for name, sub in _subparsers(build_parser()).items():
        assert REMOVED_OPTIONS.isdisjoint(_options(sub)), name


def test_discover_keeps_every_option():
    sub = _subparsers(build_parser())["discover"]
    assert {"--write", "--max-depth"} <= _options(sub)
    assert any(a.dest == "root" for a in sub._actions)


def test_init_from_submodules_keeps_every_option_including_force():
    sub = _subparsers(_subparsers(build_parser())["submodules"])["init"]
    assert {"--cgs", "--max-depth", "--dry-run", "--force", "--force-protocol"} <= _options(sub)


def test_view_tree_and_freeze_release_keep_their_options():
    parsers = _subparsers(build_parser())
    assert {"--depth", "--collapse", "--discover-nested"} <= _options(parsers["view-tree"])
    assert {"--dry-run", "--gts", "--force-protocol"} <= _options(parsers["freeze-release"])


def test_initialise_still_authors_a_cgs_from_project_and_repo():
    assert {"--project", "--repo"} <= _options(_subparsers(build_parser())["initialise"])


def test_the_cgs_writer_stays_on_the_client_without_a_command_of_its_own():
    assert callable(ComplexGitSyncClient.configure)
    assert "configure" not in _subparsers(build_parser())


@pytest.mark.parametrize("name", [
    "clean_init", "clean_initialise_cgs", "purge", "purge_cgs", "clone",
    "resolve_clone_root", "launch_release",
])
def test_removed_client_methods_are_gone(name):
    assert not hasattr(ComplexGitSyncClient, name)


@pytest.mark.parametrize("name", ["freeze", "clone_cgs"])
def test_internal_steps_stay_on_the_client(name):
    assert hasattr(ComplexGitSyncClient, name)


def _walk(parser, path=()):
    yield path, parser
    action = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    if action is not None:
        for name, child in action.choices.items():
            yield from _walk(child, (*path, name))


def test_private_is_accepted_by_exactly_these_commands():
    accepting = {" ".join(p) for p, sub in _walk(build_parser()) if p and "--private" in _options(sub)}
    assert accepting == {
        "pull", "fetch", "checkout", "branch create", "branch list", "branch close", "branch check", "branch delete",
        "add", "rm", "commit", "merge", "push", "tag",
    }

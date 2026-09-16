"""Fixtures shared by every ComplexGitSync test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_cgshome(monkeypatch):
    """Keep the developer's exported ``$CGSHOME`` out of every test.

    ``snapshot_resolver.describe_cgshome`` ranks ``$CGSHOME`` above the
    current working directory, so a developer who followed the documented
    bootstrap (``export CGSHOME=...``) makes every ``monkeypatch.chdir``
    based discovery test resolve *their own* workspace instead of the
    ``tmp_path`` the test just built. Those tests then pass in CI, where the
    variable is unset, and fail on the machine of anyone actually using the
    tool — the same trap the CLI now warns about at runtime.

    A test that needs the variable sets it itself with ``monkeypatch.setenv``,
    which still works: this only removes what the surrounding shell leaked in.
    """
    monkeypatch.delenv("CGSHOME", raising=False)


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path_factory):
    """Keep the developer's real ``$HOME`` out of every test.

    ``settings.cgs_root`` falls back to ``$HOME/.cgs``, and the default
    workspace is created there the first time a command finds nothing else.
    Without this fixture a test that exercises that path writes into the
    developer's own ``~/.cgs`` — the directory holding every workspace they
    actually work in — and a test that counts workspaces under the root
    counts theirs.

    ``HOME`` and ``USERPROFILE`` both move, because ``Path.home()`` reads
    whichever the platform uses. ``CGSPATH`` is cleared for the same reason
    ``CGSHOME`` is: a shell that exports it must not reach in here.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CGSPATH", raising=False)

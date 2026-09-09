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

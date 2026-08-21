"""Shared fixtures for the unit test suite."""

from __future__ import annotations

import pytest

from ComplexGitSync import MasterConfig


@pytest.fixture(autouse=True)
def _reset_master_config():
    """Reset MasterConfig's process-wide identity override between tests.

    MasterConfig._override_name/_override_email are class attributes, not
    per-instance state — any test that calls configure()/persist()/load()
    (directly, or indirectly via initialise_cgs/clean_init/restart/pull)
    would otherwise leak its override into every test that runs afterward
    in the same pytest session.
    """
    yield
    MasterConfig._override_name = None
    MasterConfig._override_email = None

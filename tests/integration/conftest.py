"""Shared fixtures for the CGSi integration test suite.

The CGSi topology (minimum 4 repos) exercises two key CGS structural
challenges in a single, reproducible test workspace:

  DUPLICATION — CGSil2 also references CGSih1.
  CYCLE       — CGSih2 references CGSih1 back.

Directory layout created by ``cgsi_workspace`` (under tmp_path):

  root/               ← CGSil1 (GitLab parent, project root)
    CGSil1.cgs
    CGSil2/           ← CGSil2 (GitLab child)
      CGSil2.cgs
    CGSih1/           ← CGSih1 (GitHub parent)
      CGSih1.cgs
      CGSih2/         ← CGSih2 (GitHub leaf)
        CGSih2.cgs

CGSil1.cgs   → CGSil2 (nested_config="auto"), CGSih1 (nested_config="auto")
CGSil2.cgs   → CGSih1 at ../CGSih1        (DUPLICATION; nested_config="disabled")
CGSih1.cgs   → CGSih2 (nested_config="auto")
CGSih2.cgs   → CGSih1 at ..              (CYCLE back-ref; nested_config="disabled")
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# .cgs content helpers
# ---------------------------------------------------------------------------


def _cgsi1_cgs() -> str:
    return """\
project = "CGSil1"
repos = [
    "gitlab:CGS_test/CGSil1",
    "gitlab:CGS_test/CGSil2",
    "github:flipoyo/CGSih1",
]
"""


def _cgsi2_cgs() -> str:
    return """\
project = "CGSil2"
repos = [
    "gitlab:CGS_test/CGSil2",
    { repository = "github:flipoyo/CGSih1", relative_path = "../CGSih1", nested_config = "disabled" },
]
"""


def _cgsih1_cgs() -> str:
    return """\
project = "CGSih1"
repos = [
    "github:flipoyo/CGSih1",
    "github:flipoyo/CGSih2",
]
"""


def _cgsih2_cgs() -> str:
    return """\
project = "CGSih2"
repos = [
    "github:flipoyo/CGSih2",
    { repository = "github:flipoyo/CGSih1", relative_path = "..", nested_config = "disabled" },
]
"""


# ---------------------------------------------------------------------------
# Workspace fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def cgsi_workspace(tmp_path: Path) -> dict[str, Path]:
    """Create a local workspace simulating the 4-repo CGSi topology.

    Returns a mapping with keys:
      ``"root_cgs"``   – path to ``CGSil1.cgs`` (the project root config)
      ``"CGSil1"``     – path to the CGSil1 directory (project root)
      ``"CGSil2"``     – path to the CGSil2 directory
      ``"CGSih1"``     – path to the CGSih1 directory
      ``"CGSih2"``     – path to the CGSih2 directory
    """
    root = tmp_path / "CGSil1"
    root.mkdir()

    cgsi2_dir = root / "CGSil2"
    cgsi2_dir.mkdir()

    cgsih1_dir = root / "CGSih1"
    cgsih1_dir.mkdir()

    cgsih2_dir = cgsih1_dir / "CGSih2"
    cgsih2_dir.mkdir()

    root_cgs = root / "CGSil1.cgs"
    root_cgs.write_text(_cgsi1_cgs(), encoding="utf-8")
    (cgsi2_dir / "CGSil2.cgs").write_text(_cgsi2_cgs(), encoding="utf-8")
    (cgsih1_dir / "CGSih1.cgs").write_text(_cgsih1_cgs(), encoding="utf-8")
    (cgsih2_dir / "CGSih2.cgs").write_text(_cgsih2_cgs(), encoding="utf-8")

    return {
        "root_cgs": root_cgs,
        "CGSil1": root,
        "CGSil2": cgsi2_dir,
        "CGSih1": cgsih1_dir,
        "CGSih2": cgsih2_dir,
    }

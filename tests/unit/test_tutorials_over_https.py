"""Every command a tutorial reader copies to clone a tree clones over HTTPS.

Backs TutorialsOverHttps (WP5, Q2): a reader with no SSH key copies the
tutorials line by line, so a cloning command written without
``--force-protocol https`` fails for them with ``Permission denied
(publickey)``. Tutorial 5 is exempt: it works on a developer's tree and stays
on SSH, the default, showing HTTPS only beside it (owner, 2026-10-20).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_TUTORIALS = Path(__file__).resolve().parents[2] / "tutorials"
_SSH_TUTORIAL = "05_private_repos.md"
_CLONING = re.compile(r"cgitsync (bootstrap|initialise|submodules init|release load)\b")


def _cloning_lines(path: Path) -> list[tuple[int, str]]:
    return [(number, line) for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if _CLONING.search(line) and "pixi run" in line]


@pytest.mark.parametrize("tutorial", sorted(p.name for p in _TUTORIALS.glob("0*.md") if p.name != _SSH_TUTORIAL))
def test_every_cloning_command_a_reader_copies_carries_force_protocol_https(tutorial):
    missing = [f"{tutorial}:{number}: {line.strip()}" for number, line in _cloning_lines(_TUTORIALS / tutorial) if "--force-protocol https" not in line]
    assert not missing, "a tutorial line clones over the .cgs default, SSH:\n" + "\n".join(missing)


def test_tutorial_5_keeps_ssh_and_shows_https_only_beside_it():
    lines = [line for _, line in _cloning_lines(_TUTORIALS / _SSH_TUTORIAL)]
    assert any("--force-protocol" not in line for line in lines)
    assert any("--force-protocol https" in line for line in lines)

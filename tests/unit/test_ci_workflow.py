"""CI must run what it says it runs (BeforeCGS-BETA §8, F2).

``golden-vectors`` named a test file MerkleRoot had deleted, so it ran no test on any system for seven releases
and nothing said so. This reads ``.github/workflows/ci.yml`` and fails when a test path it names is gone, or when
the pytest marker it selects collects no test.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_every_test_path_ci_names_exists():
    named = set(re.findall(r"\btests/[\w./-]+\.py\b", CI))

    assert [path for path in sorted(named) if not (ROOT / path).is_file()] == []


def test_every_marker_ci_selects_collects_a_test():
    markers = set(re.findall(r"pytest\s+-m\s+(\w+)", CI))
    marked = {
        marker: [
            path for path in (ROOT / "tests").rglob("test_*.py")
            if re.search(rf"pytest\.mark\.{marker}\b", path.read_text(encoding="utf-8"))
        ]
        for marker in markers
    }

    assert markers, "ci.yml selects no marker: the golden-vectors job would run the whole suite"
    assert [marker for marker, paths in marked.items() if not paths] == []


def test_the_full_suite_runs_on_the_three_platforms_pixi_declares():
    declared = set(re.findall(r'"(linux-64|win-64|osx-arm64)"', (ROOT / "pixi.toml").read_text(encoding="utf-8")))
    runners = set(re.findall(r"(ubuntu|macos|windows)-latest", CI.split("golden-vectors:")[0]))

    assert declared == {"linux-64", "win-64", "osx-arm64"}
    assert runners == {"ubuntu", "macos", "windows"}

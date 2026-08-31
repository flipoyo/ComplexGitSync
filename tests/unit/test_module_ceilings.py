"""Ratchet test: no src/ComplexGitSync module may grow past its recorded baseline.

Backs AgentSpec/20260828_Isolation_DevPlanTicket.md's orchestration model —
every work package that touches a shared file (orchestre.py, cli.py, ...)
must leave it the same size or smaller. See scripts/check_module_ceilings.py
for the full contract (LOC ratchet, Ring-0 purity, docstring-header
cross-check) and scripts/ceiling_baseline.json for the recorded baseline.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_module_ceilings.py"
_SPEC = importlib.util.spec_from_file_location("check_module_ceilings", _SCRIPT_PATH)
check_module_ceilings = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = check_module_ceilings
_SPEC.loader.exec_module(check_module_ceilings)


def test_no_module_regresses_past_its_recorded_baseline():
    baseline = check_module_ceilings.load_baseline()
    ring0_modules = set(baseline.get("ring0_modules", []))
    reports = [
        check_module_ceilings.analyse_module(path, ring0_modules=ring0_modules)
        for path in check_module_ceilings.iter_modules()
    ]

    failures = check_module_ceilings.run_check(reports, baseline)

    assert not failures, "\n".join(failures)

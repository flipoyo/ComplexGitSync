"""The class-first shape ratchet: real repository, and each rule on a fixture.

Backs `scripts/check_oo_conformance.py` (ClassFirstPackage WP5). The real-repo
test is what CI enforces via `pixi run test`; the fixture tests make each of
the five lists produce a finding on purpose, so a checker that silently stopped
measuring would fail here instead of passing green.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_oo_conformance.py"
_SPEC = importlib.util.spec_from_file_location("check_oo_conformance", _SCRIPT_PATH)
oo = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = oo
_SPEC.loader.exec_module(oo)


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def _functions(count: int) -> str:
    return "\n".join(f"def public_{i}():\n    return {i}\n" for i in range(count)) + "\n" * 110


def test_the_real_repository_holds_its_recorded_baseline():
    failures = oo.run_check(oo.measure(), oo.load_baseline())
    assert not failures, "\n".join(failures)


def test_a_module_of_free_functions_with_no_class_is_listed(tmp_path):
    root = _tree(tmp_path, {"loose.py": _functions(4) + '\n__all__ = []\n'})
    assert oo.measure(root)["no_behaviour_class"] == ["loose.py"]


def test_enums_exceptions_and_method_less_value_classes_are_not_behaviour(tmp_path):
    text = (
        "from enum import Enum\nfrom dataclasses import dataclass\n\n"
        "class Kind(Enum):\n    A = 1\n\n"
        "class Oops(Exception):\n    pass\n\n"
        "@dataclass\nclass Value:\n    x: int\n\n"
        + _functions(4)
    )
    root = _tree(tmp_path, {"values.py": text})
    assert oo.measure(root)["no_behaviour_class"] == ["values.py"]


def test_a_behaviour_class_takes_a_module_off_the_list(tmp_path):
    text = "class Owner:\n    def act(self):\n        return 1\n\n" + _functions(4)
    root = _tree(tmp_path, {"owned.py": text})
    assert oo.measure(root)["no_behaviour_class"] == []


def test_cli_is_exempt_from_the_class_rules(tmp_path):
    root = _tree(tmp_path, {"cli/commands.py": _functions(4)})
    measured = oo.measure(root)
    assert measured["no_behaviour_class"] == []
    assert measured["over_class_cap"] == []


def test_more_than_three_behaviour_classes_is_listed(tmp_path):
    body = "".join(f"class C{i}:\n    def act(self):\n        return {i}\n\n" for i in range(4))
    root = _tree(tmp_path, {"crowded.py": body})
    assert oo.measure(root)["over_class_cap"] == ["crowded.py"]


def test_a_module_over_2000_lines_is_listed_with_its_size_even_in_cli(tmp_path):
    long_module = "x = 1\n" * 2100
    root = _tree(tmp_path, {"cli/long.py": long_module})
    assert oo.measure(root)["over_2000_lines"] == {"cli/long.py": 2100}


def test_a_missing_or_incomplete_all_is_listed(tmp_path):
    root = _tree(
        tmp_path,
        {
            "none.py": "def a():\n    return 1\n",
            "partial.py": "def a():\n    return 1\n\ndef b():\n    return 2\n\n__all__ = ['a']\n",
            "whole.py": "def a():\n    return 1\n\n__all__ = ['a']\n",
        },
    )
    assert oo.measure(root)["missing_all"] == ["none.py", "partial.py"]


def test_a_module_level_function_that_writes_is_listed_but_a_string_replace_is_not(tmp_path):
    text = (
        "from pathlib import Path\n\n"
        "def writes(p):\n    Path(p).write_text('x')\n\n"
        "def opens(p):\n    open(p, 'w').close()\n\n"
        "def reads(p):\n    return open(p, 'rb').read()\n\n"
        "def strings(s):\n    return s.replace('a', 'b')\n\n"
        "__all__ = ['writes', 'opens', 'reads', 'strings']\n"
    )
    root = _tree(tmp_path, {"w.py": text})
    assert oo.measure(root)["filesystem_writers"] == ["w.py:opens", "w.py:writes"]


def test_a_method_that_writes_is_not_a_module_level_writer(tmp_path):
    text = (
        "from pathlib import Path\n\n"
        "class Store:\n    def save(self, p):\n        Path(p).write_text('x')\n\n"
        "__all__ = ['Store']\n"
    )
    root = _tree(tmp_path, {"store.py": text})
    assert oo.measure(root)["filesystem_writers"] == []


def test_the_ratchet_fails_when_a_list_gains_a_member_and_when_a_size_grows():
    baseline = {
        "no_behaviour_class": [],
        "over_class_cap": [],
        "missing_all": [],
        "filesystem_writers": [],
        "over_2000_lines": {"big.py": 2100},
    }
    grew = {**baseline, "no_behaviour_class": ["new.py"], "over_2000_lines": {"big.py": 2200}}
    failures = oo.run_check(grew, baseline)
    assert any("no_behaviour_class: new.py" in f for f in failures)
    assert any("big.py grew 2100 -> 2200" in f for f in failures)
    assert oo.run_check(baseline, baseline) == []


def test_a_list_that_shrinks_passes():
    baseline = {
        "no_behaviour_class": ["old.py"],
        "over_class_cap": [],
        "missing_all": [],
        "filesystem_writers": [],
        "over_2000_lines": {"big.py": 2100},
    }
    better = {**baseline, "no_behaviour_class": [], "over_2000_lines": {"big.py": 2000}}
    assert oo.run_check(better, baseline) == []

"""OperatingSpace — the golden vectors that freeze a project's identity.

Like the schema-1 vectors in test_merkle_state.py, these literals are the
cross-platform contract: if one changes, every id ever recorded changes.
"""

from __future__ import annotations

import pytest

from ComplexGitSync.errors import ConfigValidationError
from ComplexGitSync.operating_space.operating_space import OperatingSpace

GOLDEN = {
    "demo": "2a97516c354b68848cdbd8f54a226a0a55b21ed138e207ad6c5cbb9c00aa5aea",
    "CaWaQS": "a616355409e102474b1e74bacf366c883766a2e002a7d9350d166222019ecb1b",
    "cawaqs": "664c2a6ef03a343868378c84fa8cf1234dfd66c8d9745d72f8213f9ae09f75ed",
    "café": "850f7dc43910ff890f8879c0ed26fe697c93a067ad93a7d50f466a7028a9bf4e",
}


@pytest.mark.parametrize(("name", "expected"), GOLDEN.items())
def test_golden_vectors(name, expected):
    assert OperatingSpace.id_of(name) == expected


def test_case_is_kept_so_two_spellings_are_two_projects():
    assert OperatingSpace.id_of("CaWaQS") != OperatingSpace.id_of("cawaqs")


def test_composed_and_decomposed_unicode_are_one_name():
    assert OperatingSpace.id_of("café") == OperatingSpace.id_of("café")
    assert OperatingSpace.canonical("café") == "café"


@pytest.mark.parametrize("name", ["", " demo", "demo ", "de\tmo", "demo\n", "de\x00mo"])
def test_a_rejected_name_is_refused_with_its_rule_never_repaired(name):
    assert OperatingSpace.problem_of(name)
    with pytest.raises(ConfigValidationError, match="not a valid project name"):
        OperatingSpace.id_of(name)


def test_an_accepted_name_has_no_problem():
    assert OperatingSpace.problem_of("demo-1.x") is None

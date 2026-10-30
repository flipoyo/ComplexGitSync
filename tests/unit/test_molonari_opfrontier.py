"""CGS-MOLO -> CGS-BETA: ALPHA-side OpFrontier contract (issue #191)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from ComplexGitSync.tree.cgs_format import CgsDocument

_EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
_EXPECTED = {
    ".opx": ("GG4G", "DELTA", "PoE", "PRIVATE"),
    ".dev": ("GX4G", "DELTA", ".opx", ".distant"),
    ".usr": ("GX4G", "DELTA", ".opx", ".local"),
    ".gts": ("CGS", "ALPHA", ".cgs", ".cgitsync"),
}


def _fixture() -> dict:
    with (_EXAMPLES / "molonari-opfrontier.toml").open("rb") as stream:
        return tomllib.load(stream)


def _check_frontier(values: dict[str, list[str]]) -> None:
    assert tuple(values) == tuple(_EXPECTED)
    assert {key: tuple(value) for key, value in values.items()} == _EXPECTED


def test_prime_g_and_opfrontier_are_exact_and_ordered():
    data = _fixture()
    assert tuple(data["prime_g"].items()) == (
        ("NAME", "MOLONARI"),
        ("NODE", "MOLO"),
        ("EDGE", "TWIN"),
        ("OP", "CGS-BETA"),
    )
    _check_frontier(data["opfrontier"])


@pytest.mark.parametrize(
    ("level", "part", "replacement"),
    [(".opx", 0, "GX4G"), (".opx", 2, "poe"), (".dev", 3, ".local"),
     (".usr", 3, ".distant"), (".gts", 1, "BETA")],
)
def test_frontier_rejects_substitutions(level, part, replacement):
    values = {key: list(parts) for key, parts in _EXPECTED.items()}
    values[level][part] = replacement
    with pytest.raises(AssertionError):
        _check_frontier(values)


def test_frontier_rejects_changed_order():
    values = dict(reversed(list((key, list(value)) for key, value in _EXPECTED.items())))
    with pytest.raises(AssertionError):
        _check_frontier(values)


def test_opfrontier_direction_is_alpha_to_beta():
    transition = _fixture()["transition"]
    assert transition["source"] == "CGS-ALPHA"
    assert transition["target"] == "CGS-BETA"
    assert transition["project_branch"] == "CGS-MOLO"
    assert transition["alpha_distribution"] == "ComplexGitSync-8.5.0"
    assert transition["alpha_identity"] == "CGS-ALPHA-0.0.0"


@pytest.mark.parametrize(
    ("filename", "repositories"),
    [("molonari.cgs", {"MOLONARI1D", "MOLONARI-API", "MOLONARI-data", "MOLONARI-hardware", "MOLONARI-gate", "MOLONARI-computo", "ComplexGitSync"}),
     ("molonari-light.cgs", {"MOLONARI1D", "MOLONARI-API", "MOLONARI-hardware", "MOLONARI-gate", "MOLONARI-computo"})],
)
def test_alpha_examples_are_valid_public_gittrees_without_beta_repo(filename, repositories):
    doc = CgsDocument.from_toml(_EXAMPLES / filename)
    assert doc.project_name == "MOLONARI"
    assert doc.default_branch == "CGS-MOLO"
    assert {r["project_name"] for r in doc.repos} == repositories
    assert {r["project_name"] for r in doc.repos if r["relative_path"] == "."} == {"MOLONARI1D"}
    assert all(r["private"] is False and r["writable"] is False for r in doc.repos)
    assert all(r["nested_config"] == "disabled" for r in doc.repos)
    assert all(r["fallback_branch"] == "main" for r in doc.repos)
    assert "ComplexGraphSync" not in {r["project_name"] for r in doc.repos}


def test_beta_frontier_is_not_an_implicit_repo_or_new_cgs_parser_fields():
    data = _fixture()
    assert data["prime_g"]["EDGE"] == "TWIN"
    assert data["prime_g"]["OP"] == "CGS-BETA"
    for name in ("molonari.cgs", "molonari-light.cgs"):
        doc = CgsDocument.from_toml(_EXAMPLES / name)
        normalized = doc.to_dict()
        assert "prime_g" not in normalized
        assert "opfrontier" not in normalized

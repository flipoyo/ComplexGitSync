"""ProjectAlias: the candidates, the claim and the shape a name may not have (AnonymousProject §2, §4)."""

from __future__ import annotations

from itertools import islice

import pytest

from ComplexGitSync.git_repo.project_alias import ProjectAlias
from ComplexGitSync.operating_space.operating_space import OperatingSpace


def _digest(name: str) -> str:
    return OperatingSpace.id_of(name)


def test_the_hash_alias_of_patrimoine_is_the_six_digits_the_ticket_names():
    assert ProjectAlias.first(_digest("PATRIMOINE"), "hash") == "293f93"


def test_a_collision_adds_a_suffix_in_order():
    assert list(islice(ProjectAlias.candidates(_digest("PATRIMOINE"), "hash"), 3)) == ["293f93", "293f93-1", "293f93-2"]


def test_the_sequence_aliases_are_numbered_from_one():
    assert list(islice(ProjectAlias.candidates(_digest("anything"), "sequence"), 3)) == ["anonymous-1", "anonymous-2", "anonymous-3"]


def test_the_sequence_aliases_do_not_depend_on_the_name():
    assert list(islice(ProjectAlias.candidates(_digest("A"), "sequence"), 2)) == list(islice(ProjectAlias.candidates(_digest("B"), "sequence"), 2))


def test_the_claim_a_branch_carries_is_the_os_id_so_it_is_hashed_once_where_hashes_are_made():
    claim = _digest("PATRIMOINE")
    assert len(claim) == 64 and claim.startswith("293f93")
    assert _digest("patrimoine") != claim  # case is kept


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="hash.*sequence"):
        next(ProjectAlias.candidates(_digest("x"), "random"))


@pytest.mark.parametrize("name", ["anonymous-1", "anonymous-42", "293f93", "293f93-2", "abcdef"])
def test_a_name_with_the_shape_of_an_alias_is_one(name):
    assert ProjectAlias.looks_like_alias(name)


@pytest.mark.parametrize("name", ["PATRIMOINE", "anonymous", "anonymous-x", "29f93", "293f93x", "ABCDEF", "demo-1"])
def test_an_ordinary_name_is_not(name):
    assert not ProjectAlias.looks_like_alias(name)

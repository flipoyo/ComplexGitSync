"""BranchSpelling: a branch name nothing holds is a misspelling only when it is close to one the tree has (CheckoutBareTip CD3)."""

from __future__ import annotations

import pytest

from ComplexGitSync.git_repo.branch_spelling import BranchSpelling

OWNERS = ("ComplexGitSync",)
KNOWN = ("main", "HT-BETA", "data-encrypt", "ComplexGitSync_data-encrypt", "ComplexGitSync_ComplexGitSync_HT-BETA", "hotfix-1")


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("data-encryupt", ("data-encrypt",)),  # the owner's slip of 2026-11-27: one letter too many
        ("data-encrpyt", ("data-encrypt",)),  # a swap of two neighbouring letters is one edit
        ("HT-BTEA", ("HT-BETA",)),
        ("ht-beta", ("HT-BETA",)),  # case alone is a slip
        ("mian", ("main",)),  # five characters or fewer: one edit, and a swap is one
        ("daat-encrypt", ("data-encrypt",)),
    ],
)
def test_a_slip_names_the_branch_it_may_have_meant(typed, expected):
    assert BranchSpelling.close_to(typed, KNOWN, OWNERS) == expected


@pytest.mark.parametrize("typed", ["data-encrypt", "HT-BETA", "main", "hotfix-1"])
def test_a_name_the_tree_holds_is_not_a_misspelling(typed):
    assert BranchSpelling.close_to(typed, KNOWN, OWNERS) == ()


def test_a_derived_name_is_the_branch_its_user_types():
    """``ComplexGitSync_data-encrypt`` is held, so typing ``data-encrypt`` is no slip even with no bare branch."""
    assert BranchSpelling.close_to("data-encrypt", ("ComplexGitSync_data-encrypt",), OWNERS) == ()


@pytest.mark.parametrize("typed", ["feature-x", "wholly-new", "release", "hotfix-2", "v2"])
def test_a_new_name_close_to_nothing_is_created_as_before(typed):
    assert BranchSpelling.close_to(typed, KNOWN, OWNERS) == ()


def test_numbered_siblings_are_never_a_typo():
    assert BranchSpelling.close_to("hotfix-2", ("hotfix-1",)) == ()
    assert BranchSpelling.close_to("hotfix-12", ("hotfix-1",)) == ()


def test_a_short_name_tolerates_one_edit_and_a_long_one_two():
    assert BranchSpelling.close_to("dex", ("dev",)) == ("dev",)
    assert BranchSpelling.close_to("dxx", ("dev",)) == ()  # two edits on a three-letter name
    assert BranchSpelling.close_to("stabel-line", ("stable-line",)) == ("stable-line",)
    assert BranchSpelling.close_to("stbl-line", ("stable-line",)) == ("stable-line",)  # two edits on a long one


def test_distance_counts_a_swap_as_one_edit():
    assert BranchSpelling.distance("ab", "ba") == 1
    assert BranchSpelling.distance("kitten", "sitting") == 3
    assert BranchSpelling.distance("", "abc") == 3


def test_only_the_trees_own_prefixes_are_stripped():
    """A legacy branch keeps its underscore: ``release_1`` must not make a one-letter new name a slip for ``1``."""
    assert BranchSpelling.typed("release_1", OWNERS) == "release_1"
    assert BranchSpelling.close_to("x", ("release_1",), OWNERS) == ()
    assert BranchSpelling.typed("lib_demo_dev", ("lib", "demo")) == "dev"  # a nested Parent's: <Parent>_<project>_<branch>

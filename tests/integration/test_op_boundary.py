"""OP, the one door through which an operation on G enters ComplexGitSync (OpBoundary).

CGS-ALPHA is ComplexGitSync: the same operation, asked by G (internal) or GX4G (external), at each Proof-of-Existence
level, on a real tree. ``isPoE(OP)`` is ``OP*``: the operation's name, ``*NAME`` and ``SHA256(G.MerkleRoot)``, bounded
by four bits, ``.PUBLIC = {1,1,1,1}`` the invariant. An external request is never exposed more than an internal one.
"""

from __future__ import annotations

import pytest
from test_close_branch import _git, _loaded, _two_repo_workspace

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.merkle import MerkleRoot
from ComplexGitSync.omniscience.omniscience_record import OmniscienceRecord
from ComplexGitSync.operating_space.operating_space import OperatingSpace
from ComplexGitSync.orchestre.op_boundary import (
    DEV,
    OPX,
    PUBLIC,
    USER,
    Op,
    Origin,
    PoE,
    RoutedClient,
)
from ComplexGitSync.tree.git_tree import TreeProfile


def _star(tree, *, level=None, origin=Origin.INTERNAL, operation="branch", args=("feature-x",)):
    client = _loaded(tree["snapshot"])
    op = Op(client, operation, args, origin=origin, level=level)
    op.run()
    return client, op.is_poe()


def test_the_four_levels_are_four_bits_and_public_is_the_invariant():
    assert (PUBLIC.sigma, USER.sigma, DEV.sigma, OPX.sigma) == (4, 2, 1, 0)
    assert PUBLIC == PoE(True, True, True, True)
    assert PoE.of_profile(TreeProfile.USER) == USER and PoE.of_profile(TreeProfile.DEV) == DEV


def test_a_routed_operation_runs_on_the_gittree(tmp_path):
    tree = _two_repo_workspace(tmp_path)

    _star(tree)

    assert "feature-x" in _git(tree["root"], "branch", "--list", "feature-x")


def test_op_star_keeps_the_name_and_the_merkle_root_hash_never_branches_or_paths(tmp_path):
    tree = _two_repo_workspace(tmp_path)

    client, star = _star(tree, level=PUBLIC)

    root = OperatingSpace(client.get_dependency_registry()).gittree_root
    assert star.name == "*demo"
    assert star.merkle_root_hash == MerkleRoot.content_digest(root.encode("utf-8"))
    flat = str(star.poe)
    assert "feature-x" not in flat and str(tmp_path) not in flat


@pytest.mark.parametrize(
    ("level", "parts"),
    [(PUBLIC, {"name", "merkle_root_sha256", "nodes", "edges"}), (USER, {"name", "merkle_root_sha256", "nodes"}), (DEV, {"name", "merkle_root_sha256"}), (OPX, set())],
)
def test_the_level_bounds_what_op_star_proves(tmp_path, level, parts):
    tree = _two_repo_workspace(tmp_path)

    _, star = _star(tree, level=level)

    assert set(star.poe) == parts
    assert star.sigma == level.sigma


def test_at_public_the_edges_are_starred_with_the_operation(tmp_path):
    tree = _two_repo_workspace(tmp_path)

    _, star = _star(tree, level=PUBLIC)

    assert star.poe["edges"] == [{"from": "demo", "to": "conf", "op": "branch"}]


def test_the_tree_profile_gives_the_level_until_memorymount_records_it(tmp_path):
    tree = _two_repo_workspace(tmp_path)  # it holds a private repository: a DEV tree

    _, star = _star(tree)

    assert star.level == DEV
    assert set(star.poe) == {"name", "merkle_root_sha256"}


def test_an_external_request_is_never_exposed_more_than_the_same_internal_one(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    inside = _two_repo_workspace(tmp_path / "in")
    outside = _two_repo_workspace(tmp_path / "out")

    _, internal = _star(inside, level=USER)
    _, external = _star(outside, level=USER, origin=Origin.EXTERNAL)

    assert set(external.poe) <= set(internal.poe)
    assert external.poe["nodes"] == internal.poe["nodes"]


def test_an_operation_that_neither_writes_nor_leaves_the_tree_does_not_go_through_op(tmp_path):
    client = _loaded(_two_repo_workspace(tmp_path)["snapshot"])

    with pytest.raises(GitSyncError, match="does not go through OP"):
        Op(client, "status").run()


def test_a_refusal_on_the_gittree_stays_a_refusal(tmp_path):
    client = _loaded(_two_repo_workspace(tmp_path)["snapshot"])

    with pytest.raises(GitSyncError):
        Op(client, "close_branch", ("main",)).run()  # the project's own default branch is never closed


def test_the_cli_enters_op_as_internal_and_every_other_attribute_is_the_client():
    logged: list[tuple[str, dict]] = []

    class Client:
        run_logger = None

        def _log_event(self, event, **fields):
            logged.append((event, fields))

        def fetch(self, *, private=False):
            return ("fetched", private)

        def status(self):
            return "read"

        def verify(self, cgshome, *, repair=False):
            return ("verified", repair)

    client = Client()
    routed = RoutedClient(client)

    assert routed.fetch(private=True) == ("fetched", True)
    assert routed.status() == "read"
    assert routed.verify("ws") == ("verified", False)  # a read stays direct (owner, Q2)
    assert routed.verify("ws", repair=True) == ("verified", True)
    routed.run_logger = "set through"
    assert client.run_logger == "set through"
    assert logged == [
        ("op", {"operation": "fetch", "origin": "internal"}),
        ("op", {"operation": "verify", "origin": "internal"}),
    ]


def _record(**extra):
    return OmniscienceRecord(
        project="*demo", os_id="a" * 64, project_branch="main", state_id=f"state({'b' * 64})", gittree_root="c" * 64,
        local_ledger_tip=f"sha256:{'d' * 64}", local_memory_git_commit="e" * 40, memory_repository="github:o/memory",
        memory_mount=".cgitsync/.memory", memory_ref="refs/heads/demo", memory_commit="e" * 40, **extra,
    )


def test_sigma_is_shown_in_the_record_and_committed_only_when_present():
    without, with_sigma = _record(), _record(sigma=2)

    assert "sigma" not in without.preimage() and "sigma" not in without.to_dict()["record"]
    assert with_sigma.preimage()["sigma"] == 2 and with_sigma.to_dict()["record"]["sigma"] == 2
    assert with_sigma.record_hash != without.record_hash
    assert OmniscienceRecord.from_dict(with_sigma.to_dict()) == with_sigma
    assert OmniscienceRecord.from_dict(without.to_dict()).record_hash == without.record_hash

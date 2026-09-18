"""Tree-wide merge preflight over real Git repositories.

The unit tests in ``tests/unit/test_git_runner.py`` pin down one repository's
answer; this file pins down what a *tree* does with those answers. Both halves
exist because of the same field failure: ``cgitsync merge apoub`` crashed
inside the conflict check with a ``UnicodeDecodeError`` raised on the content
of a tracked PDF, so no repository was checked and none was merged — but the
user could not tell that from the traceback. See
``.localSpec/DevTickets/archive/20260910_MergeOutputDecoding_DevPlanTicket.md``.

Real repositories and a real ``GitRunner`` throughout: the guarantee under
test is that ComplexGitSync agrees with what ``git merge`` would actually do,
and a fake could only ever agree with itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_repo import (
    AccessProtocol,
    DiscoveryState,
    GitProvider,
    NodeType,
    RefKind,
    RepoLifecycleState,
    SyncState,
    WorkingRepo,
)
from ComplexGitSync.git_runner import GitRunner
from ComplexGitSync.git_tree import WorkingGitTree
from ComplexGitSync.operations import merge_tree, merge_tree_one_at_a_time

# Invalid UTF-8, starting with the byte from the reported traceback.
_INVALID_UTF8 = b"\xdb!\xfe\xff"

_MERGE_BRANCH = "apoub"


def _git(repo_path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo_path, check=True, capture_output=True)


def _init_repo(repo_path: Path, *, conflicting: bool) -> None:
    """A repository with an ``apoub`` branch, conflicting or not.

    The file both branches touch holds bytes that are not valid UTF-8, so the
    conflict check has to reach its answer from inside content it cannot
    decode — the situation that used to raise instead of answering.
    """
    repo_path.mkdir(parents=True, exist_ok=True)
    target = repo_path / "payload.bin"

    _git(repo_path, "init", "-b", "main")
    _git(repo_path, "config", "user.email", "t@example.com")
    _git(repo_path, "config", "user.name", "Test")
    target.write_bytes(b"base\n" + _INVALID_UTF8 + b"\n")
    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-m", "base")

    _git(repo_path, "checkout", "-b", _MERGE_BRANCH)
    target.write_bytes(b"from-apoub\n" + _INVALID_UTF8 + b"\n")
    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-m", "apoub change")

    _git(repo_path, "checkout", "main")
    if conflicting:
        target.write_bytes(b"from-main\n" + _INVALID_UTF8 + b"\n")
        _git(repo_path, "add", "-A")
        _git(repo_path, "commit", "-m", "main change")

    # The merge preflight requires a real 'origin'; a local bare repository
    # keeps the whole fixture offline.
    remote_path = repo_path.parent / f"{repo_path.name}-remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote_path)], check=True, capture_output=True
    )
    _git(repo_path, "remote", "add", "origin", str(remote_path))
    _git(repo_path, "push", "-u", "origin", "main")
    _git(repo_path, "push", "origin", _MERGE_BRANCH)


def _entry(repo_id, name, node_type, parent_id, absolute_path, root_path) -> WorkingRepo:
    return WorkingRepo(
        repo_id=repo_id,
        name=name,
        node_type=node_type,
        parent_id=parent_id,
        absolute_path=absolute_path,
        relative_path=(
            Path(".") if parent_id is None else absolute_path.relative_to(root_path)
        ),
        current_ref_kind=RefKind.BRANCH,
        current_ref_name="main",
        target_ref_kind=RefKind.BRANCH,
        target_ref_name="main",
        resolved_ref_kind=RefKind.BRANCH,
        resolved_ref_name="main",
        commit_sha=GitRunner().rev_parse_head(absolute_path),
        repo_lifecycle_state=RepoLifecycleState.READY,
        sync_state=SyncState.ALIGNED,
        discovery_state=DiscoveryState.RESOLVED,
        is_reachable=True,
        gitprovider=GitProvider.GITHUB,
        project_owner_name="owner",
        project_name=name,
        access_protocol=AccessProtocol.SSH,
        default_branch="main",
        remote_name="origin",
    )


@pytest.fixture
def tree_with_one_blocked_repo(tmp_path):
    """A two-repository tree where only the leaf can merge cleanly.

    Leaf-first order means the leaf is the one a partial merge would have
    already written before the root's conflict was discovered — which is
    exactly what must not happen.
    """
    root_path = tmp_path / "project"
    leaf_path = root_path / "deps" / "leaf"
    _init_repo(root_path, conflicting=True)
    _init_repo(leaf_path, conflicting=False)
    # The leaf lives inside the root's worktree; keep the root's own status
    # clean so the merge preflight has only the conflict to complain about.
    (root_path / ".gitignore").write_text("deps/\n", encoding="utf-8")
    _git(root_path, "add", "-A")
    _git(root_path, "commit", "-m", "ignore nested repo")

    tree = WorkingGitTree()
    tree.add(_entry("root", "project", NodeType.ROOT, None, root_path, root_path))
    tree.add(
        _entry("root:deps/leaf", "leaf", NodeType.LEAF, "root", leaf_path, root_path)
    )
    tree.recompute_tree_state()
    assert tree.is_ready()
    return tree


def _snapshot(runner: GitRunner, tree: WorkingGitTree) -> dict[str, tuple]:
    return {
        entry.name: (
            runner.rev_parse_head(entry.absolute_path),
            (entry.absolute_path / "payload.bin").read_bytes(),
            runner.has_unresolved_merge(entry.absolute_path),
            runner.has_staged_changes(entry.absolute_path),
        )
        for entry in tree.values()
    }


def test_a_blocked_repository_prevents_every_merge_in_the_tree(tree_with_one_blocked_repo):
    """All checks precede all merges, so one conflict merges nothing anywhere."""
    tree = tree_with_one_blocked_repo
    runner = GitRunner()
    before = _snapshot(runner, tree)

    with pytest.raises(GitSyncError, match="no repository was merged"):
        merge_tree(tree, runner, _MERGE_BRANCH)

    assert _snapshot(runner, tree) == before


def test_the_error_names_the_repository_that_blocked_it(tree_with_one_blocked_repo):
    """The outcome the user gets instead of the decoding traceback."""
    with pytest.raises(GitSyncError) as excinfo:
        merge_tree(tree_with_one_blocked_repo, GitRunner(), _MERGE_BRANCH)

    assert "project" in str(excinfo.value)


def test_the_error_names_the_file_that_blocked_it(tree_with_one_blocked_repo):
    """Naming the repository is not enough: say which file, on a real tree."""
    with pytest.raises(GitSyncError) as excinfo:
        merge_tree(tree_with_one_blocked_repo, GitRunner(), _MERGE_BRANCH)

    assert "payload.bin" in str(excinfo.value)


def test_resolve_merges_the_clean_leaf_and_stops_at_the_conflict(
    tree_with_one_blocked_repo,
):
    """--resolve trades the all-or-nothing guarantee for real progress."""
    tree = tree_with_one_blocked_repo
    runner = GitRunner()
    leaf = tree.get("root:deps/leaf")
    leaf_before = runner.rev_parse_head(leaf.absolute_path)

    outcome = merge_tree_one_at_a_time(tree, runner, _MERGE_BRANCH)

    assert outcome.stopped_at == "project"
    assert Path("payload.bin") in outcome.stopped_paths
    assert runner.rev_parse_head(leaf.absolute_path) != leaf_before, (
        "the clean leaf must actually have merged"
    )
    assert runner.has_unresolved_merge(tree.get("root").absolute_path), (
        "the conflict must be left in the worktree for a merge tool"
    )


def test_resolve_reports_the_id_open_merge_tool_can_actually_use(
    tree_with_one_blocked_repo,
):
    """`stopped_at` is a display name; a merge tool is opened by id instead.

    Real field failure: `merge --resolve` stopping at `.memory` crashed with
    a bare `KeyError('.memory')` — `open_merge_tool` looked the repository
    up in the registry by its *name*, which is not always its *id* (this
    fixture's root has `repo_id="root"`, `name="project"`, same as `.memory`
    never having `repo_id=".memory"`). `stopped_at_id` is what fixes it.
    """
    tree = tree_with_one_blocked_repo

    outcome = merge_tree_one_at_a_time(tree, GitRunner(), _MERGE_BRANCH)

    assert outcome.stopped_at == "project"
    assert outcome.stopped_at_id == "root"
    assert tree.get(outcome.stopped_at_id) is tree.get("root")
    with pytest.raises(KeyError):
        tree.get(outcome.stopped_at)


def test_client_open_merge_tool_accepts_the_id_and_refuses_the_name(
    tree_with_one_blocked_repo, monkeypatch
):
    """The exact call `cli/expert.py` makes after a `--resolve` stop.

    A missing merge tool is the common case in a test environment, so this
    only has to prove the lookup itself succeeds — not that a tool opens.
    """
    from ComplexGitSync.errors import GitSyncError
    from ComplexGitSync.orchestre import ComplexGitSyncClient

    tree = tree_with_one_blocked_repo
    client = ComplexGitSyncClient(git_runner=GitRunner())
    client.registry = tree
    monkeypatch.setattr(ComplexGitSyncClient, "_resolve_merge_tool", lambda self, path: (None, None))
    outcome = merge_tree_one_at_a_time(tree, GitRunner(), _MERGE_BRANCH)

    manual = client.open_merge_tool(outcome.stopped_at_id)

    assert manual is not None  # no tool configured: the "resolve by hand" command
    with pytest.raises(GitSyncError, match="not a repository in this tree"):
        client.open_merge_tool(outcome.stopped_at)


def test_the_check_is_read_only_even_when_it_cannot_decode(tree_with_one_blocked_repo):
    """HEAD, index and worktree survive a preflight over undecodable content."""
    tree = tree_with_one_blocked_repo
    runner = GitRunner()
    before = _snapshot(runner, tree)

    for entry in tree.values():
        runner.can_merge_cleanly(entry.absolute_path, _MERGE_BRANCH)

    assert _snapshot(runner, tree) == before


def test_a_tree_that_can_merge_actually_merges(tmp_path):
    """The other half: nothing above turned a clean tree into a blocked one."""
    root_path = tmp_path / "project"
    leaf_path = root_path / "deps" / "leaf"
    _init_repo(root_path, conflicting=False)
    _init_repo(leaf_path, conflicting=False)
    (root_path / ".gitignore").write_text("deps/\n", encoding="utf-8")
    _git(root_path, "add", "-A")
    _git(root_path, "commit", "-m", "ignore nested repo")

    tree = WorkingGitTree()
    tree.add(_entry("root", "project", NodeType.ROOT, None, root_path, root_path))
    tree.add(
        _entry("root:deps/leaf", "leaf", NodeType.LEAF, "root", leaf_path, root_path)
    )
    tree.recompute_tree_state()

    merged = merge_tree(tree, GitRunner(), _MERGE_BRANCH)

    assert {name for name, _ in merged} == {"project", "leaf"}
    for entry in tree.values():
        content = (entry.absolute_path / "payload.bin").read_bytes()
        assert content.startswith(b"from-apoub")

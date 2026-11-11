"""A tree-wide merge carries a twin's memory, or refuses before anything moves (TwinMemoryMerge).

A bootstrap twin files its memory under ``ComplexGitSync_<project>``, and the memory nests ``.self-history``. The merge used to
derive their branches without the twin rule, found nothing, called the memory "skipped" and ended ``ok``, which left the tree
split and the next ``push`` refused. Real Git throughout: the project (`main`/`feature`), a configuration repository
(`demo`/`demo_feature`), the memory at `.cgitsync/.memory` and its `.self-history` (`ComplexGitSync_demo`/
`ComplexGitSync_demo_feature`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.tree.git_tree_branch import GitTreeBranches

TARGET, SOURCE = "ComplexGitSync_demo", "ComplexGitSync_demo_feature"
REPOS = (  # key, name, relative path, parent key, (target, source)
    ("root", "demo", ".", None, ("main", "feature")),
    ("conf", "conf", ".conf", "root", ("demo", "demo_feature")),
    ("memory", ".memory", ".cgitsync/.memory", "root", (TARGET, SOURCE)),
    ("history", ".self-history", ".cgitsync/.memory/.self-history", "memory", (TARGET, SOURCE)),
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()


def _identify(repo: Path) -> None:
    _git(repo, "config", "user.email", "t@e.st")
    _git(repo, "config", "user.name", "Test")


def _seeded_remote(tmp_path: Path, name: str, branch: str) -> Path:
    remote = tmp_path / f"{name}.git"
    _git(tmp_path, "init", "--bare", "-b", branch, remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", branch)
    _identify(seed)
    (seed / "README.md").write_text("initial\n", encoding="utf-8")
    (seed / ".gitignore").write_text(".cgitsync/\n.conf/\n.self-history/\n", encoding="utf-8")
    _git(seed, "add", "README.md", ".gitignore")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "-u", "origin", branch)
    return remote


def _commit(repo: Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", f"{name}: {text}")


def _repo_state(root: Path, name: str, relative: str, parent: Path | None, branch: str, sha: str) -> str:
    absolute = root if relative == "." else root / relative
    private = relative != "."
    parent_line = f'parent_absolute_path = "{(parent or root).as_posix()}"\n' if private else ""
    return f"""
[[repo_state]]
name = "{name}"
node_type = "{'leaf' if private else 'root'}"
absolute_path = "{absolute.as_posix()}"
{parent_line}relative_path = "{relative}"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "{branch}"
target_ref_kind = "branch"
target_ref_name = "{branch}"
resolved_ref_kind = "branch"
resolved_ref_name = "{branch}"
commit_sha = "{sha}"
{'default_branch = "demo"' if private else ''}
project_owner_name = "owner"
project_name = "{name.strip('.')}"
gitprovider = "github"
{'private = true' if private else ''}
{'writable = true' if private else ''}
""".strip() + "\n"


def _write_snapshot(tree: dict[str, Path], *, on: str) -> None:
    root = tree["root"]
    states = "".join(
        _repo_state(root, name, relative, tree.get(parent) if parent else None, branches[0 if on == "target" else 1], _git(tree[key], "rev-parse", "HEAD"))
        for key, name, relative, parent, branches in REPOS
    )
    tree["snapshot"].write_text(
        f"""
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "{root.as_posix()}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

""".lstrip() + states,
        encoding="utf-8",
    )


def _tree(tmp_path: Path, *, memory: str = "behind") -> dict[str, Path]:
    """The tree sits on its sources. *memory*: ``behind`` (the target's memory is an ancestor of the source's) or ``diverged``."""
    root = tmp_path / "demo"
    paths = {"root": root, "conf": root / ".conf", "memory": root / ".cgitsync" / ".memory"}
    paths["history"] = paths["memory"] / ".self-history"
    _git(tmp_path, "clone", "-b", "main", _seeded_remote(tmp_path, "demo", "main").as_posix(), root.as_posix())
    _identify(root)
    for key, _name, _relative, _parent, (target, _) in REPOS[1:]:
        paths[key].parent.mkdir(parents=True, exist_ok=True)
        _git(tmp_path, "clone", "-b", target, _seeded_remote(tmp_path, f"{key}-remote", target).as_posix(), paths[key].as_posix())
        _identify(paths[key])
    _git(root, "checkout", "-b", "feature")
    _commit(root, "work.txt", "feature")
    for key in ("conf", "memory", "history"):
        repo, (target, source) = paths[key], {k: b for k, _, _, _, b in REPOS}[key]
        _commit(repo, "entries/1.txt", "one")
        _git(repo, "checkout", "-b", source)
        _commit(repo, "entries/2.txt", "feature")
        if memory == "diverged" and key != "conf":
            _git(repo, "checkout", target)
            # The ledger collides on a number; `.self-history` is append-only, its records named by their own hash.
            _commit(repo, "entries/2.txt" if key == "memory" else "entries/other.txt", "main only")
            _git(repo, "checkout", source)
    tree = {**paths, "snapshot": tmp_path / "demo.gts"}
    _write_snapshot(tree, on="source")
    return tree


def _loaded(tree: dict[str, Path]) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    client.load_gts(tree["snapshot"])
    return client


def _tip(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", ref)


def test_the_resolver_the_merge_uses_names_a_twin_memory_and_what_it_nests_by_the_twin_rule(tmp_path):
    tree = _tree(tmp_path)
    branches = GitTreeBranches(_loaded(tree).registry)
    by_name = {repo.name: repo for repo in _loaded(tree).registry.values()}

    assert branches.target(by_name[".memory"], "main").name == TARGET
    assert branches.target(by_name[".self-history"], "feature").name == SOURCE
    assert branches.target(by_name["conf"], "feature").name == "demo_feature"  # no other repository is ever a twin


def test_the_dry_run_names_the_twin_branches_for_both_memory_repositories_and_the_merge_agrees(tmp_path, capsys):
    tree = _tree(tmp_path)

    cli_main(["merge", "feature", "--into", "main", "--all", "--dry-run", "--gts", str(tree["snapshot"])])
    out = capsys.readouterr().out

    assert f".memory: {TARGET} <- {SOURCE}" in out and f".self-history: {TARGET} <- {SOURCE}" in out
    assert "no such branch" not in out and "refused" not in out


def test_a_memory_behind_the_source_fast_forwards_and_the_tree_is_not_split(tmp_path):
    tree = _tree(tmp_path)
    source_tips = {key: _tip(tree[key], SOURCE) for key in ("memory", "history")}

    outcomes = {row.name: row.status for row in _loaded(tree).merge_into("feature", "main", all_writable=True)}

    assert outcomes[".memory"] == outcomes[".self-history"] == "fast-forward"
    for key in ("memory", "history"):
        assert _git(tree[key], "branch", "--show-current") == TARGET
        assert _tip(tree[key], "HEAD") == source_tips[key]  # moved forward, nothing merged file by file
    assert _git(tree["root"], "branch", "--show-current") == "main"
    assert (tree["root"] / "work.txt").is_file()


def test_a_memory_with_commits_of_its_own_keeps_its_own_side_whole_and_the_chain_stays_valid(tmp_path):
    tree = _tree(tmp_path, memory="diverged")
    before, source_tip = _git(tree["memory"], "rev-parse", f"{TARGET}^{{tree}}"), _tip(tree["memory"], SOURCE)

    outcomes = {row.name: row.status for row in _loaded(tree).merge_into("feature", "main", all_writable=True)}

    assert outcomes[".memory"] == "kept"
    assert _git(tree["memory"], "rev-parse", "HEAD^{tree}") == before
    assert subprocess.run(["git", "merge-base", "--is-ancestor", source_tip, "HEAD"], cwd=tree["memory"]).returncode == 0


def test_a_memory_with_no_branch_to_follow_on_refuses_before_anything_moves(tmp_path):
    tree = _tree(tmp_path)
    _git(tree["memory"], "checkout", "-b", "ComplexGitSync_demo_stray")  # the memory is elsewhere, and its source is gone
    _git(tree["memory"], "branch", "-D", SOURCE)
    _write_snapshot(tree, on="source")

    with pytest.raises(GitSyncError, match=r"memory cannot follow.*\.memory.*memory branch --project-branch feature"):
        _loaded(tree).merge_into("feature", "main", all_writable=True)

    assert _git(tree["root"], "branch", "--show-current") == "feature"  # nothing was checked out
    assert _git(tree["memory"], "branch", "--show-current") == "ComplexGitSync_demo_stray"


def test_a_memory_already_on_the_target_with_no_source_is_not_a_refusal(tmp_path):
    tree = _tree(tmp_path)
    _git(tree["memory"], "checkout", TARGET)
    _git(tree["memory"], "branch", "-D", SOURCE)
    _write_snapshot(tree, on="source")

    with pytest.warns(UserWarning, match="no branch"):
        outcomes = {row.name: row.status for row in _loaded(tree).merge_into("feature", "main", all_writable=True)}

    assert outcomes[".memory"] == "no-source"
    assert _git(tree["root"], "branch", "--show-current") == "main"


def test_the_plain_merge_derives_the_twin_names_too(tmp_path, capsys):
    tree = _tree(tmp_path)
    for key, _name, _relative, _parent, branches in REPOS:
        _git(tree[key], "checkout", branches[0])
    _write_snapshot(tree, on="target")

    cli_main(["merge", "feature", "--all", "--dry-run", "--gts", str(tree["snapshot"])])
    out = capsys.readouterr().out

    assert f".memory <- {SOURCE}" in out and f".self-history <- {SOURCE}" in out
    assert "no such branch" not in out


def test_after_the_merge_the_push_preflight_passes_because_nothing_is_split(tmp_path):
    """The owner's sequence of 2026-11-11: `merge --into main --all`, then `push --all`, which was refused."""
    tree = _tree(tmp_path)
    client = _loaded(tree)

    client.merge_into("feature", "main", all_writable=True)
    client.push(all_writable=True)  # a preflight refusal ("branch misalignment") would raise

    for key in ("memory", "history"):
        assert _git(tree[key], "rev-parse", "HEAD") == _git(tree[key], "rev-parse", f"origin/{TARGET}")


def test_pull_private_names_the_twin_memory_the_same_way_the_merge_does(tmp_path):
    """`pull --private` already resolved through `GitTreeBranches.target`; the merge now agrees with it by construction."""
    from ComplexGitSync.tree.operations import merge_source_ref

    registry = _loaded(_tree(tmp_path)).registry
    by_name = {repo.name: repo for repo in registry.values()}

    for name in (".memory", ".self-history"):
        assert merge_source_ref(by_name[name], "main", tree=registry) == GitTreeBranches(registry).target(by_name[name], "main").name == TARGET

"""A tree-wide merge never merges the memory file by file, and says what it did.

The owner's sequence of 2026-10-08, replayed (UnrelatedHistoryMerge, ``main_1-1``):
after `memory reboot` on a project branch, `merge --all` reported the memory as
"(conflicts — would block the merge)" with "(no file named)", offered
`--resolve`, and blocked every other private repository. A ledger is a hash
chain of numbered files, so two memories collide or interleave however they
are related; the memory keeps its own side whole and the other becomes history.

Real Git throughout; the tree is three repositories: the project (`main`/
`feature`), a configuration repository (`demo`/`demo_feature`) and the memory
at `.cgitsync/.memory` (`demo`/`demo_feature`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.orchestre import ComplexGitSyncClient

REPOS = (  # name, relative path, target branch, source branch
    ("demo", ".", "main", "feature"),
    ("conf", ".conf", "demo", "demo_feature"),
    (".memory", ".cgitsync/.memory", "demo", "demo_feature"),
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
    (seed / ".gitignore").write_text(".cgitsync/\n.conf/\n", encoding="utf-8")
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


def _repo_state(root: Path, name: str, relative: str, branch: str, sha: str) -> str:
    absolute = root if relative == "." else root / relative
    private = relative != "."
    parent = "" if relative == "." else f'parent_absolute_path = "{root.as_posix()}"\n'
    return f"""
[[repo_state]]
name = "{name}"
node_type = "{'root' if relative == '.' else 'leaf'}"
absolute_path = "{absolute.as_posix()}"
{parent}relative_path = "{relative}"
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


def _tree(tmp_path: Path, *, memory: str = "diverged", conf: str = "ok") -> dict[str, Path]:
    """The tree sits on its targets (`main`, `demo`, `demo`); the sources are local branches.

    *memory* is how the memory's two branches relate: ``diverged`` (the same
    ledger number holds a different entry on each) or ``orphan`` (the source was
    started afresh, as `memory reboot` leaves it). *conf* is ``ok`` or ``orphan``.
    """
    root = tmp_path / "demo"
    paths = {"root": root, "conf": root / ".conf", "memory": root / ".cgitsync" / ".memory"}
    remotes = {"root": ("demo", "main"), "conf": ("conf", "demo"), "memory": ("mem", "demo")}
    _git(tmp_path, "clone", "-b", "main", _seeded_remote(tmp_path, *remotes["root"]).as_posix(), root.as_posix())
    _identify(root)
    for key in ("conf", "memory"):
        paths[key].parent.mkdir(parents=True, exist_ok=True)
        name, branch = remotes[key]
        _git(tmp_path, "clone", "-b", branch, _seeded_remote(tmp_path, name, branch).as_posix(), paths[key].as_posix())
        _identify(paths[key])

    _git(root, "checkout", "-b", "feature")
    _commit(root, "work.txt", "feature")
    _git(root, "checkout", "main")
    for key, orphan in (("conf", conf == "orphan"), ("memory", memory == "orphan")):
        repo = paths[key]
        # Only the memory is a ledger: its branches hold *different* entries under the same
        # number, which collides however they are related. The configuration repository
        # just gains different files on each side, which merges cleanly.
        feature_file, target_file = ("entries/2.txt",) * 2 if key == "memory" else ("feature.txt", "main.txt")
        _commit(repo, "entries/1.txt", "one")
        _git(repo, "checkout", "-b", "demo_feature")
        if orphan:  # the source started afresh, as `memory reboot` leaves it
            _git(repo, "checkout", "--orphan", "fresh")
            _git(repo, "rm", "-rfq", ".")
            _commit(repo, "entries/1.txt", "fresh")
            _git(repo, "branch", "-D", "demo_feature")
            _git(repo, "branch", "-m", "demo_feature")
        else:
            _commit(repo, feature_file, "feature")
        _git(repo, "checkout", "demo")
        _commit(repo, target_file, "main")

    tree = {**paths, "snapshot": tmp_path / "demo.gts"}
    _write_snapshot(tree, on="target")
    return tree


def _on(tree: dict[str, Path], side: str) -> None:
    """Check every repository out on its *side* (``"target"`` or ``"source"``) and record that."""
    for key, (_, _, target, source) in zip(("root", "conf", "memory"), REPOS, strict=True):
        _git(tree[key], "checkout", target if side == "target" else source)
    _write_snapshot(tree, on=side)


def _write_snapshot(tree: dict[str, Path], *, on: str) -> None:
    root = tree["root"]
    states = "".join(
        _repo_state(root, name, relative, target if on == "target" else source, _git(tree[key], "rev-parse", "HEAD"))
        for (name, relative, target, source), key in zip(REPOS, ("root", "conf", "memory"), strict=True)
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


def _loaded(tree: dict[str, Path]) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    client.load_gts(tree["snapshot"])
    return client


def _tree_of(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", f"{ref}^{{tree}}")


def _reachable(repo: Path, ancestor: str, of: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, of], cwd=repo).returncode == 0


@pytest.mark.parametrize("memory", ["diverged", "orphan"])
def test_merge_all_keeps_the_target_memory_whole_and_still_merges_the_other_repositories(tmp_path, memory):
    tree = _tree(tmp_path, memory=memory)
    before_tree, source_tip = _tree_of(tree["memory"], "demo"), _git(tree["memory"], "rev-parse", "demo_feature")

    merged = dict(_loaded(tree).merge("feature", all_writable=True))

    assert set(merged) == {"demo", "conf", ".memory"}
    assert (tree["root"] / "work.txt").is_file()  # the project merged
    assert (tree["conf"] / "feature.txt").is_file() and (tree["conf"] / "main.txt").is_file()  # the config repository merged
    assert _tree_of(tree["memory"], "HEAD") == before_tree  # the memory: its own side, byte for byte
    assert _reachable(tree["memory"], source_tip, "HEAD")  # the other side kept as history
    assert _git(tree["memory"], "status", "--porcelain") == ""


def test_the_preview_says_kept_never_conflicts_and_never_offers_resolve(tmp_path, capsys):
    tree = _tree(tmp_path, memory="orphan")

    exit_code = cli_main(["merge", "feature", "--all", "--dry-run", "--gts", str(tree["snapshot"])])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert ".memory <- demo_feature (memory — its own side is kept whole, the source stays as history)" in out
    for forbidden in ("conflicts", "no file named", "--resolve"):
        assert forbidden not in out


def test_the_real_run_reports_the_memory_as_kept_not_merged(tmp_path, capsys):
    tree = _tree(tmp_path, memory="diverged")

    exit_code = cli_main(["merge", "feature", "--all", "--gts", str(tree["snapshot"])])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "kept .memory: its own memory stays whole; demo_feature is kept as history" in out
    assert "merged demo <- feature" in out and "merged .memory" not in out


def test_a_memory_that_is_already_part_of_the_target_is_up_to_date_not_blocking(tmp_path):
    tree = _tree(tmp_path, memory="diverged")
    client = _loaded(tree)
    client.merge("feature", all_writable=True)
    tip = _git(tree["memory"], "rev-parse", "HEAD")

    plan = {name: status for name, _, status, _ in _loaded(tree).merge_plan("feature", private=True)}

    assert plan[".memory"] == "up-to-date"
    assert _git(tree["memory"], "rev-parse", "HEAD") == tip


def test_another_repository_with_no_common_commit_refuses_the_tree_by_name_without_resolve(tmp_path):
    tree = _tree(tmp_path, conf="orphan")
    root_tip = _git(tree["root"], "rev-parse", "HEAD")
    memory_tip = _git(tree["memory"], "rev-parse", "HEAD")

    with pytest.raises(GitSyncError) as refused:
        _loaded(tree).merge("feature", all_writable=True)

    message = str(refused.value)
    assert "conf: 'demo_feature' and 'demo' share no commit; Git will not merge unrelated histories" in message
    assert "--resolve" not in message and "conflicts" not in message
    assert _git(tree["root"], "rev-parse", "HEAD") == root_tip and _git(tree["memory"], "rev-parse", "HEAD") == memory_tip


def test_the_preview_names_an_unrelated_repository_without_offering_resolve(tmp_path, capsys):
    tree = _tree(tmp_path, conf="orphan")

    exit_code = cli_main(["merge", "feature", "--all", "--dry-run", "--gts", str(tree["snapshot"])])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "conf <- demo_feature (no commit in common — would block the merge)" in out
    assert "unrelated=true" in out and "conf: share no commit" in out
    assert "--resolve" not in out and "(no file named)" not in out


def test_merge_into_checks_out_the_target_and_keeps_its_memory(tmp_path):
    tree = _tree(tmp_path, memory="orphan")
    _on(tree, "source")
    before_tree, source_tip = _tree_of(tree["memory"], "demo"), _git(tree["memory"], "rev-parse", "demo_feature")

    outcomes = _loaded(tree).merge_into("feature", "main", all_writable=True)

    assert {outcome.name: outcome.status for outcome in outcomes}[".memory"] == "kept"
    assert _git(tree["memory"], "branch", "--show-current") == "demo"
    assert _tree_of(tree["memory"], "HEAD") == before_tree and _reachable(tree["memory"], source_tip, "HEAD")


def test_pull_private_keeps_the_feature_memory_when_the_base_was_started_afresh(tmp_path):
    """`pull --private` refreshes a feature memory from its base; it too must not file-merge."""
    tree = _tree(tmp_path, memory="orphan")
    memory = tree["memory"]
    _on(tree, "source")
    _git(memory, "push", "origin", "demo", "demo_feature")
    before_tree, base_tip = _tree_of(memory, "demo_feature"), _git(memory, "rev-parse", "demo")

    refreshed = dict(_loaded(tree).refresh_private())

    assert refreshed.get(".memory") == "demo"
    assert _tree_of(memory, "HEAD") == before_tree and _reachable(memory, base_tip, "HEAD")


def test_pull_private_refuses_an_unrelated_repository_by_name_and_merges_nothing(tmp_path):
    tree = _tree(tmp_path, conf="orphan")
    _on(tree, "source")
    _git(tree["conf"], "push", "origin", "demo", "demo_feature")
    memory_tip = _git(tree["memory"], "rev-parse", "HEAD")

    with pytest.raises(GitSyncError, match="pull --private refused.*conf: 'origin/demo' and 'demo_feature' share no commit"):
        _loaded(tree).refresh_private()

    assert _git(tree["memory"], "rev-parse", "HEAD") == memory_tip


def test_after_the_merge_the_source_memory_holds_nothing_the_target_lacks_so_it_is_safe_to_delete(tmp_path):
    """The owner's `branch delete` found the memory's entries "only here"; once kept as history they are not."""
    tree = _tree(tmp_path, memory="diverged")
    client = _loaded(tree)
    before = {answer.name: answer.verdict for answer in client.branch_ancestry("feature", private=True)}
    assert before[".memory"] == "needs ancestor"  # nothing else holds the source memory's entries

    client.merge("feature", all_writable=True)

    after = {answer.name: answer.verdict for answer in _loaded(tree).branch_ancestry("feature", private=True)}
    assert after[".memory"] == "safe"

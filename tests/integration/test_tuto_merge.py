"""Tutorial 7 (`tutorials/07_merge.md`) replayed: the commands it shows, in its order, in a local sandbox.

`branch close`/`branch delete` (§7) are not replayed here: they need a tree
whose project declares its default branch, and `test_close_branch.py` covers them.
"""

from __future__ import annotations

from test_merge_resolve import _conflict_in_conf, _merging
from test_tree_merge_memory import _git, _on, _reachable, _tree

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.orchestre import ComplexGitSyncClient


def _cli(tree, capsys, *args):
    code = cli_main([*args, "--gts", str(tree["snapshot"])])
    return code, capsys.readouterr().out


def test_sections_2_3_5_and_7_look_merge_into_keep_the_memory_and_check(tmp_path, capsys):
    tree = _tree(tmp_path, memory="diverged")
    target_tip = _git(tree["memory"], "rev-parse", "demo")

    # §2 look before you merge: a plan, nothing moved
    code, out = _cli(tree, capsys, "merge", "feature", "--all", "--dry-run")
    assert code == 0 and "plan_order=" in out
    assert "its own side is kept whole, the source stays as history" in out
    assert _git(tree["memory"], "rev-parse", "HEAD") == target_tip

    # §1: from the source branch, `--into` checks out the target and merges the project
    _on(tree, "source")
    code, out = _cli(tree, capsys, "merge", "feature", "--into", "main")
    assert code == 0 and "demo: main <- feature" in out
    assert _git(tree["root"], "branch", "--show-current") == "main"

    # §5 choose the other memory: --theirs keeps feature's, main's stays reachable
    code = cli_main(["memory", "merge", "feature", "--into", "main", "--theirs", "--search-dir", str(tree["root"])])
    captured = capsys.readouterr()
    assert code == 0, captured.err[-600:]
    # feature's chain continues on main (its entry 2 wins; anything recorded since sits on top) ...
    assert _git(tree["memory"], "show", "demo:entries/2.txt") == "feature"
    assert _reachable(tree["memory"], _git(tree["memory"], "rev-parse", "demo_feature"), "demo")
    assert _reachable(tree["memory"], target_tip, "demo")  # ... and main's own is kept as history

    # §3 --all: the configuration repository merges its derived branch; the memory has nothing left
    for key in ("conf", "memory"):
        _git(tree[key], "checkout", "demo")
    code, out = _cli(tree, capsys, "merge", "feature", "--all")
    assert code == 0, out
    assert "merged conf <- demo_feature" in out and "merged .memory" not in out
    assert (tree["conf"] / "feature.txt").is_file() and (tree["conf"] / "main.txt").is_file()

    # §7 check the tree
    code, out = _cli(tree, capsys, "status")
    assert code == 0 and "errors=0" in out


def test_section_4_all_conflicts_goes_on_once_the_tool_resolved_the_text_conflict(tmp_path, capsys, monkeypatch):
    tree = _tree(tmp_path)
    _conflict_in_conf(tree, "notes.txt", "ours\n", "theirs\n")

    def tool(self, repo_id):  # the person resolves the file in the merge tool
        (tree["conf"] / "notes.txt").write_text("both\n", encoding="utf-8")
        _git(tree["conf"], "add", "notes.txt")

    monkeypatch.setattr(ComplexGitSyncClient, "open_merge_tool", tool)

    code, out = _cli(tree, capsys, "merge", "feature", "--all", "--resolve", "--all-conflicts")

    assert code == 0 and "stopped at" not in out
    assert "No file is resolved or staged for you" in out
    assert (tree["root"] / "work.txt").is_file() and not _merging(tree["conf"])


def test_section_4_resolve_stops_at_the_conflict_and_leaves_what_was_merged(tmp_path, capsys, monkeypatch):
    tree = _tree(tmp_path)
    _conflict_in_conf(tree, "notes.txt", "ours\n", "theirs\n")
    monkeypatch.setattr(ComplexGitSyncClient, "open_merge_tool", lambda self, repo_id: "cd .conf && git mergetool")

    code, out = _cli(tree, capsys, "merge", "feature", "--all", "--resolve")

    assert code == 1
    assert "stopped at conf: notes.txt" in out and "Resolve by hand" in out
    assert (tree["root"] / "work.txt").is_file() and _merging(tree["conf"])


def test_section_6_no_common_commit(tmp_path, capsys):
    tree = _tree(tmp_path, conf="orphan")

    code, out = _cli(tree, capsys, "merge", "feature", "--all", "--dry-run")
    assert code == 0 and "no commit in common" in out

    code, out = _cli(tree, capsys, "merge", "feature", "--all", "--resolve", "--all-conflicts")
    assert code == 1 and "stopped at conf: its branches share no commit" in out
    assert "Check the branch name, or leave conf out of the scope." in out

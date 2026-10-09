"""`merge --resolve` and `--all-conflicts` keep the memory, stop by name, and resolve nothing in silence.

MergeErgonomics. Reuses the three-repository tree of ``test_tree_merge_memory``:
the project, a configuration repository ``conf`` and the memory.
"""

from __future__ import annotations

from pathlib import Path

from test_tree_merge_memory import _git, _loaded, _reachable, _tree, _tree_of

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.orchestre import ComplexGitSyncClient


def _conflict_in_conf(tree: dict[str, Path], name: str, ours: str | bytes, theirs: str | bytes) -> None:
    conf = tree["conf"]
    for branch, content in (("demo_feature", theirs), ("demo", ours)):
        _git(conf, "checkout", branch)
        path = conf / name
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        _git(conf, "add", name)
        _git(conf, "commit", "-m", f"{name} on {branch}")


def _merging(repo: Path) -> bool:
    return (repo / ".git" / "MERGE_HEAD").exists()


def test_resolve_keeps_a_diverged_memory_and_never_stops_there(tmp_path):
    tree = _tree(tmp_path, memory="diverged")
    before, source = _tree_of(tree["memory"], "demo"), _git(tree["memory"], "rev-parse", "demo_feature")

    outcome = _loaded(tree).merge_resolve("feature", all_writable=True)

    assert outcome.stopped_at is None
    assert _tree_of(tree["memory"], "HEAD") == before and _reachable(tree["memory"], source, "HEAD")
    assert not _merging(tree["memory"])


def test_all_conflicts_ends_on_an_unrelated_repository_without_touching_it(tmp_path, monkeypatch):
    tree = _tree(tmp_path, conf="orphan")
    opened: list[str] = []
    monkeypatch.setattr(ComplexGitSyncClient, "open_merge_tool", lambda self, repo_id: opened.append(repo_id))
    conf_tip = _git(tree["conf"], "rev-parse", "HEAD")

    outcome = _loaded(tree).merge_resolve_all("feature", all_writable=True)

    assert outcome.stopped_at == "conf" and outcome.stopped_status == "unrelated"
    assert opened == [] and not _merging(tree["conf"])
    assert _git(tree["conf"], "rev-parse", "HEAD") == conf_tip


def test_the_cli_names_the_unrelated_repository_and_exits_nonzero(tmp_path, capsys):
    tree = _tree(tmp_path, conf="orphan")

    code = cli_main(["merge", "feature", "--all", "--resolve", "--all-conflicts", "--gts", str(tree["snapshot"])])
    out = capsys.readouterr().out

    assert code == 1
    assert "stopped at conf: its branches share no commit" in out
    assert "regenerat" not in out and "no file named" not in out


def test_all_conflicts_goes_on_after_the_merge_tool_resolves_a_text_conflict(tmp_path, monkeypatch):
    tree = _tree(tmp_path)
    _conflict_in_conf(tree, "notes.txt", "ours\n", "theirs\n")
    opened: list[str] = []

    def fake_tool(self, repo_id):  # the person resolves the file in the tool
        opened.append(repo_id)
        path = tree["conf"] / "notes.txt"
        path.write_text("both\n", encoding="utf-8")
        _git(tree["conf"], "add", "notes.txt")

    monkeypatch.setattr(ComplexGitSyncClient, "open_merge_tool", fake_tool)

    outcome = _loaded(tree).merge_resolve_all("feature", all_writable=True)

    assert outcome.stopped_at is None and len(opened) == 1
    assert (tree["conf"] / "notes.txt").read_text(encoding="utf-8") == "both\n"
    assert not _merging(tree["conf"])
    # every pass is reported, the merge the run committed itself included
    merged = dict(outcome.merged)
    assert merged["demo"] == "feature" and merged["conf"] == "demo_feature" and ".memory" in merged
    assert _git(tree["conf"], "log", "-1", "--format=%s") == "Merge branch 'demo_feature'"


def test_the_cli_lists_the_merge_the_run_committed(tmp_path, monkeypatch, capsys):
    tree = _tree(tmp_path)
    _conflict_in_conf(tree, "notes.txt", "ours\n", "theirs\n")

    def fake_tool(self, repo_id):
        (tree["conf"] / "notes.txt").write_text("both\n", encoding="utf-8")
        _git(tree["conf"], "add", "notes.txt")

    monkeypatch.setattr(ComplexGitSyncClient, "open_merge_tool", fake_tool)

    code = cli_main(["merge", "feature", "--all", "--resolve", "--all-conflicts", "--gts", str(tree["snapshot"])])
    out = capsys.readouterr().out

    assert code == 0
    assert "conf" in out and "demo_feature" in out and "stopped at" not in out


def test_with_no_merge_tool_all_conflicts_prints_the_command_to_run_by_hand(tmp_path, monkeypatch, capsys):
    tree = _tree(tmp_path)
    _conflict_in_conf(tree, "notes.txt", "ours\n", "theirs\n")
    monkeypatch.setattr(ComplexGitSyncClient, "open_merge_tool", lambda self, repo_id: "cd conf && git mergetool")

    code = cli_main(["merge", "feature", "--all", "--resolve", "--all-conflicts", "--gts", str(tree["snapshot"])])
    out = capsys.readouterr().out

    assert code == 1
    assert "no merge tool available. Resolve by hand:\n  cd conf && git mergetool" in out
    assert _merging(tree["conf"])


def test_all_conflicts_stops_when_the_tool_leaves_a_file_unresolved(tmp_path, monkeypatch):
    tree = _tree(tmp_path)
    _conflict_in_conf(tree, "notes.txt", "ours\n", "theirs\n")
    monkeypatch.setattr(ComplexGitSyncClient, "open_merge_tool", lambda self, repo_id: None)

    outcome = _loaded(tree).merge_resolve_all("feature", all_writable=True)

    assert outcome.stopped_at == "conf" and _merging(tree["conf"])


def test_a_binary_conflict_is_named_and_never_staged(tmp_path, monkeypatch, capsys):
    tree = _tree(tmp_path)
    _conflict_in_conf(tree, "logo.bin", b"ours\0\x01", b"theirs\0\x02")
    monkeypatch.setattr(
        ComplexGitSyncClient, "open_merge_tool", lambda self, repo_id: (_ for _ in ()).throw(AssertionError("tool opened"))
    )

    code = cli_main(["merge", "feature", "--all", "--resolve", "--all-conflicts", "--gts", str(tree["snapshot"])])
    out = capsys.readouterr().out

    assert code == 1
    assert "binary file logo.bin: Git left this branch's version in place; the other version is on 'demo_feature'" in out
    assert _git(tree["conf"], "diff", "--name-only", "--diff-filter=U") == "logo.bin"


def test_all_conflicts_across_clean_repositories_and_one_conflict(tmp_path, monkeypatch):
    tree = _tree(tmp_path)
    _conflict_in_conf(tree, "notes.txt", "ours\n", "theirs\n")
    monkeypatch.setattr(ComplexGitSyncClient, "open_merge_tool", lambda self, repo_id: "by hand")

    outcome = _loaded(tree).merge_resolve_all("feature", all_writable=True)

    assert [name for name, _ in outcome.merged] == ["demo"]  # the project merged before the stop
    assert outcome.stopped_at == "conf" and outcome.stopped_source == "demo_feature"
    assert outcome.hand_command == "by hand" and (tree["root"] / "work.txt").is_file()

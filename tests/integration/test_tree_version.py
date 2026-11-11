"""`cgitsync version diagnose | init | bump` (TreeVersion): every repository's version, through Pixi.

A tree of small real repositories, each with the manifest the case needs; Pixi runs for real, offline, because a
dependency-free task needs no solve.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.orchestre import ComplexGitSyncClient

PY = Path(sys.executable).as_posix()
PLATFORMS = '["linux-64", "osx-64", "osx-arm64", "win-64"]'
BUMP = '''import re, sys
level = sys.argv[1]
text = open("pixi.toml").read()
old = re.search(r'^version = "([^"]+)"', text, re.M).group(1)
a, b, c = map(int, old.split("."))
new = {"patch": f"{a}.{b}.{c+1}", "minor": f"{a}.{b+1}.0", "major": f"{a+1}.0.0"}[level]
if "--dry-run" not in sys.argv:
    open("pixi.toml", "w").write(text.replace(f'version = "{old}"', f'version = "{new}"', 1))
print(f"{old} -> {new}")
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()


def _manifest(name: str, version: str | None, task: str | None = None) -> str:
    head = f'[workspace]\nname = "{name}"\nchannels = ["conda-forge"]\nplatforms = {PLATFORMS}\n'
    return head + (f'version = "{version}"\n' if version else "") + (f'\n[tasks]\nbump-version = "{task}"\n' if task else "")


def _repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@e.st")
    _git(path, "config", "user.name", "Test")
    for name, text in {".gitignore": ".cgitsync/\n", **files}.items():
        (path / name).write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "initial")
    return _git(path, "rev-parse", "HEAD")


def _entry(name: str, path: Path, relative: str, sha: str, *, root: Path | None, private: bool, repo_name: str | None = None) -> str:
    leaf = "" if root is None else f'parent_absolute_path = "{root.as_posix()}"\n'
    flags = "private = true\nwritable = true\n" if private else ""
    return f"""
[[repo_state]]
name = "{name}"
node_type = "{'leaf' if root else 'root'}"
absolute_path = "{path.as_posix()}"
{leaf}relative_path = "{relative}"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "{sha}"
project_owner_name = "owner"
project_name = "{repo_name or name}"
gitprovider = "github"
{flags}"""


def _tree(tmp_path: Path, *, private: bool = True, extra: dict[str, dict[str, str]] | None = None) -> dict[str, Path]:
    """root `demo` (own task), `tool` (SemVer, no task), `bare` (no version), `plain` (no manifest), `cal` (2026.07),
    `ComplexGitSync` (another project's tool), and a private writable `.conf`."""
    root = tmp_path / "demo"
    task = f"{PY} bump.py"
    nested = ("tool", "bare", "plain", "cal", "ComplexGitSync", ".conf", *(extra or {}))
    repos = {
        "demo": (".", {"pixi.toml": _manifest("demo", "1.0.0", task), "bump.py": BUMP, "VERSION.txt": "1.0.0\n", ".gitignore": ".cgitsync/\n" + "".join(f"{n}/\n" for n in nested)}),
        "tool": ("tool", {"pixi.toml": _manifest("tool", "2.3.4")}),
        "bare": ("bare", {"pixi.toml": _manifest("bare", None)}),
        "plain": ("plain", {"README.md": "no manifest\n"}),
        "cal": ("cal", {"pixi.toml": _manifest("cal", "2026.07")}),
        "ComplexGitSync": ("ComplexGitSync", {"pixi.toml": _manifest("ComplexGitSync", "8.8.2")}),
        ".conf": (".conf", {"notes.md": "private\n"}),
        **{k: (k, v) for k, v in (extra or {}).items()},
    }
    out, entries = {}, []
    for name, (relative, files) in repos.items():
        path = root if relative == "." else root / relative
        if relative != ".":
            path.parent.mkdir(parents=True, exist_ok=True)
        # The root's own repository first, so a nested one is created inside a directory that exists.
        sha = _repo(path, files)
        out[name] = path
        entries.append((name, path, relative, sha))
    root_sha = entries[0][3]
    body = "".join(
        _entry(name, path, relative, sha, root=None if name == "demo" else root, private=(name == ".conf") and private, repo_name=name)
        for name, path, relative, sha in entries
    )
    snapshot = tmp_path / "demo.gts"
    snapshot.write_text(
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
""".strip()
        + "\n"
        + body,
        encoding="utf-8",
    )
    out["snapshot"], out["root_sha"] = snapshot, root_sha  # type: ignore[assignment]
    return out


def _loaded(tree: dict) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    client.load_gts(tree["snapshot"])
    return client


def _rows(client: ComplexGitSyncClient) -> dict[str, object]:
    return {row.name: row for row in client.version_diagnose()}


def _dirty(path: Path) -> None:
    (path / "work.txt").write_text("change\n", encoding="utf-8")


def _version(path: Path) -> str:
    import re

    return re.search(r'^version = "([^"]+)"', (path / "pixi.toml").read_text(encoding="utf-8"), re.M).group(1)


def test_diagnose_tells_every_case_apart_and_writes_nothing(tmp_path):
    tree = _tree(tmp_path)
    client = _loaded(tree)

    rows = _rows(client)

    assert (rows["demo"].verdict, rows["demo"].bump, rows["demo"].scheme) == ("ready", "task", "semver")
    assert (rows["tool"].verdict, rows["tool"].bump, rows["tool"].mirrors) == ("ready", "pixi", 0)
    assert rows["bare"].verdict == "init"
    assert rows["plain"].verdict == "not versioned"
    assert (rows["cal"].verdict, rows["cal"].scheme) == ("task needed", "calendar")
    assert rows["ComplexGitSync"].verdict == "other project"
    assert rows[".conf"].verdict == "private"
    assert _git(tree["demo"], "status", "--porcelain") == ""


def test_a_version_copied_elsewhere_needs_a_task(tmp_path):
    tree = _tree(tmp_path, extra={"mirrored": {"pixi.toml": _manifest("mirrored", "3.1.4"), "README.md": "v3.1.4\n"}})

    row = _rows(_loaded(tree))["mirrored"]

    assert (row.verdict, row.mirrors) == ("task needed", 1)
    assert "bump-version task" in row.detail


def test_a_task_whose_script_is_missing_is_named(tmp_path):
    tree = _tree(tmp_path, extra={"ghost": {"pixi.toml": _manifest("ghost", "1.0.0", f"{PY} nowhere.py")}})

    row = _rows(_loaded(tree))["ghost"]

    assert row.verdict == "task needed" and "nowhere.py" in row.detail


def test_init_installs_a_version_with_pixi_and_only_where_it_is_missing(tmp_path):
    tree = _tree(tmp_path)
    client = _loaded(tree)

    [move] = client.version_init(version="0.1.0")

    assert (move.name, move.new) == ("bare", "0.1.0")
    assert _version(tree["bare"]) == "0.1.0"
    assert _rows(client)["bare"].verdict == "ready"
    assert not (tree["plain"] / "pixi.toml").exists() and not (tree[".conf"] / "pixi.toml").exists()


def test_init_refuses_a_name_that_is_not_waiting_for_a_version(tmp_path):
    tree = _tree(tmp_path)

    with pytest.raises(GitSyncError, match="plain is not versioned"):
        _loaded(tree).version_init(["plain"])
    with pytest.raises(GitSyncError, match="not SemVer"):
        _loaded(tree).version_init(version="2026.07")


@pytest.mark.parametrize(("level", "expected_root", "expected_tool"), [("patch", "1.0.1", "2.3.5"), ("minor", "1.1.0", "2.4.0"), ("major", "2.0.0", "3.0.0")])
def test_bump_moves_the_root_and_each_modified_leaf_by_the_level(tmp_path, level, expected_root, expected_tool):
    tree = _tree(tmp_path)
    _dirty(tree["tool"])

    moves = _loaded(tree).version_bump(level)

    assert [m.name for m in moves] == ["tool", "demo"]  # leaf first, root last
    assert _version(tree["demo"]) == expected_root and _version(tree["tool"]) == expected_tool
    assert (moves[0].route, moves[1].route) == ("pixi", "task")


def test_bump_with_no_level_is_a_patch(tmp_path):
    tree = _tree(tmp_path)

    [root] = _loaded(tree).version_bump()

    assert (root.old, root.new) == ("1.0.0", "1.0.1")


def test_only_modified_leaves_move_and_the_root_always_does(tmp_path):
    tree = _tree(tmp_path)
    _dirty(tree["tool"])

    names = [m.name for m in _loaded(tree).version_bump("patch")]

    assert names == ["tool", "demo"]
    assert (tree["tool"] / "pixi.toml").read_text(encoding="utf-8").count("2.3.5") == 1


def test_a_modified_repository_that_is_not_versioned_is_skipped_without_stopping_the_bump(tmp_path):
    tree = _tree(tmp_path)
    for name in ("plain", "ComplexGitSync", ".conf"):
        _dirty(tree[name])

    names = [m.name for m in _loaded(tree).version_bump("patch")]

    assert names == ["demo"]
    assert _version(tree["ComplexGitSync"]) == "8.8.2"


def test_a_modified_leaf_that_is_not_ready_refuses_the_whole_bump_before_any_write(tmp_path):
    tree = _tree(tmp_path)
    _dirty(tree["cal"])
    _dirty(tree["tool"])

    with pytest.raises(GitSyncError, match=r"cal .*task needed"):
        _loaded(tree).version_bump("minor")

    assert _version(tree["demo"]) == "1.0.0" and _version(tree["tool"]) == "2.3.4"
    assert _version(tree["cal"]) == "2026.07"  # never passed to Pixi


def test_a_task_whose_dry_run_fails_changes_nothing_anywhere(tmp_path):
    broken = f"{PY} -c \\\"import sys; sys.exit(3)\\\""
    tree = _tree(tmp_path, extra={"fails": {"pixi.toml": _manifest("fails", "1.2.3", broken)}})
    _dirty(tree["fails"])
    _dirty(tree["tool"])

    with pytest.raises(GitSyncError, match="fails"):
        _loaded(tree).version_bump("patch")

    assert _version(tree["demo"]) == "1.0.0" and _version(tree["tool"]) == "2.3.4" and _version(tree["fails"]) == "1.2.3"


def test_a_failure_during_the_apply_names_what_completed_and_what_never_ran(tmp_path):
    # Passes its dry run, then fails the real run: the one case the dry run cannot see.
    flaky = f"{PY} -c \\\"import sys; print('1.2.3 -> 1.2.4') if '--dry-run' in sys.argv else sys.exit(4)\\\""
    tree = _tree(tmp_path, extra={"flaky": {"pixi.toml": _manifest("flaky", "1.2.3", flaky)}})
    for name in ("tool", "flaky"):
        _dirty(tree[name])

    with pytest.raises(GitSyncError) as stopped:
        _loaded(tree).version_bump("patch")

    text = str(stopped.value)
    assert "stopped part-way" in text and "failed: flaky" in text and "not attempted: demo" in text
    assert _version(tree["demo"]) == "1.0.0"  # the root never ran
    assert _git(tree["flaky"], "status", "--porcelain")  # nothing rolled back, nothing committed


def test_a_root_that_would_move_by_another_level_is_refused(tmp_path):
    lying = f"{PY} -c \\\"import sys; print('1.0.0 -> 1.0.1' if sys.argv[1:2] == ['patch'] else '1.0.0 -> 1.5.0')\\\""
    tree = _tree(tmp_path)
    (tree["demo"] / "pixi.toml").write_text(_manifest("demo", "1.0.0", lying), encoding="utf-8")
    _git(tree["demo"], "add", "-A")
    _git(tree["demo"], "commit", "-m", "lying task")

    with pytest.raises(GitSyncError, match=r"not a major bump"):
        _loaded(tree).version_bump("major")


def test_a_dry_run_writes_nothing_and_says_what_would_move(tmp_path):
    tree = _tree(tmp_path)
    _dirty(tree["tool"])

    moves = _loaded(tree).version_bump("minor", dry_run=True)

    assert [(m.name, m.old, m.new, m.applied) for m in moves] == [("tool", "2.3.4", "2.4.0", False), ("demo", "1.0.0", "1.1.0", False)]
    assert _version(tree["demo"]) == "1.0.0" and _version(tree["tool"]) == "2.3.4"


def test_the_tool_of_another_project_is_never_bumped_but_its_own_tree_bumps_it(tmp_path):
    foreign = _tree(tmp_path)
    _dirty(foreign["ComplexGitSync"])
    assert [m.name for m in _loaded(foreign).version_bump("patch")] == ["demo"]


def test_nothing_is_committed_pushed_or_tagged(tmp_path):
    tree = _tree(tmp_path)
    before = _git(tree["demo"], "rev-parse", "HEAD")

    _loaded(tree).version_bump("patch")

    assert _git(tree["demo"], "rev-parse", "HEAD") == before
    assert _git(tree["demo"], "tag") == ""
    assert "M pixi.toml" in _git(tree["demo"], "status", "--short")


def test_every_subcommand_is_refused_in_a_user_tree_before_anything_is_read(tmp_path):
    tree = _tree(tmp_path, private=False)
    client = _loaded(tree)

    for call in (client.version_diagnose, client.version_init, client.version_bump):
        with pytest.raises(GitSyncError, match="user's tree"):
            call()
    assert _version(tree["demo"]) == "1.0.0"


# -- the command: what is printed, and what is asked ------------------------------------------------------------------


def _run(handler, tree, **args) -> None:
    import argparse

    from ComplexGitSync.cli import version_command

    namespace = argparse.Namespace(json=False, repositories=[], version="0.1.0", yes=False, level="patch", dry_run=False, **args)
    assert getattr(version_command, handler)(_loaded(tree), tree["snapshot"], namespace) == 0


def test_diagnose_prints_a_row_per_repository_a_summary_and_the_next_command(tmp_path, capsys):
    _run("_diagnose", _tree(tmp_path))

    out = capsys.readouterr().out
    assert "VERDICT" in out and "other project" in out and "not versioned" in out
    assert "summary ready=" in out and "init=1" in out and "other_project=1" in out and "private=1" in out
    assert "next: cgitsync version init" in out


def test_diagnose_json_is_the_rows(tmp_path, capsys):
    import argparse
    import json

    from ComplexGitSync.cli import version_command

    tree = _tree(tmp_path)
    version_command._diagnose(_loaded(tree), tree["snapshot"], argparse.Namespace(json=True))

    out = capsys.readouterr().out
    rows = {row["name"]: row for row in json.loads(out[out.index("[\n"):])}
    assert rows["demo"]["verdict"] == "ready" and rows["bare"]["verdict"] == "init" and rows["ComplexGitSync"]["verdict"] == "other project"


def test_init_asks_before_each_repository_and_takes_what_is_typed(tmp_path, monkeypatch, capsys):
    tree = _tree(tmp_path)
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "1.4.0")

    _run("_init", tree)

    assert len(asked) == 1 and "bare" in asked[0]
    assert _version(tree["bare"]) == "1.4.0"
    assert "installed 1.4.0 in bare" in capsys.readouterr().out


def test_init_skips_what_is_answered_n_and_asks_nothing_with_yes(tmp_path, monkeypatch):
    tree = _tree(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    _run("_init", tree)
    assert "version =" not in (tree["bare"] / "pixi.toml").read_text(encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("--yes asks nothing"))
    import argparse

    from ComplexGitSync.cli import version_command

    version_command._init(_loaded(tree), tree["snapshot"], argparse.Namespace(repositories=[], version="0.3.0", yes=True))
    assert _version(tree["bare"]) == "0.3.0"


def test_bump_prints_each_move_the_root_and_the_next_step(tmp_path, capsys):
    tree = _tree(tmp_path)
    _dirty(tree["tool"])

    _run("_bump", tree)

    out = capsys.readouterr().out
    assert "bumped tool (tool): 2.3.4 -> 2.3.5 via pixi" in out
    assert "root demo moved by patch: 1.0.0 -> 1.0.1" in out
    assert "nothing was committed, pushed or tagged" in out


def test_init_naming_a_repository_that_is_not_waiting_prints_the_refusal_not_nothing_to_do(tmp_path):
    import argparse

    from ComplexGitSync.cli import version_command

    tree = _tree(tmp_path)

    with pytest.raises(GitSyncError, match="tool is ready"):
        version_command._init(_loaded(tree), tree["snapshot"], argparse.Namespace(repositories=["tool"], version="0.1.0", yes=True))

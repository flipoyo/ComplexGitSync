"""`cgitsync memory status|list|show` — looking at what a workspace remembers.

Read-only, because this milestone ships before there is anywhere to push a
memory to. What it must get right is telling the truth about three things a
user cannot otherwise see: how much is remembered, which States are held,
and what produced each record.
"""

from __future__ import annotations

from pathlib import Path

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.orchestre import ComplexGitSyncClient

_CGS = """
project = "demo"

repos = [
  "github:owner/demo",
]
"""


def _used_workspace(root: Path, *, operations: int = 1) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    config = root / "project.cgs"
    config.write_text(_CGS, encoding="utf-8")
    client = ComplexGitSyncClient()
    for _ in range(operations):
        client.load(config)
    return root


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_counts_what_is_remembered(tmp_path, capsys):
    workspace = _used_workspace(tmp_path / "demo", operations=2)

    exit_code = cli_main(["memory", "status", "--search-dir", str(workspace)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "states=1" in captured.out  # one tree, seen twice
    assert "entries=2" in captured.out
    assert "verification=verified" in captured.out


def test_status_prints_the_toolchain_the_records_carry(tmp_path, capsys):
    workspace = _used_workspace(tmp_path / "demo")

    cli_main(["memory", "status", "--search-dir", str(workspace)])
    captured = capsys.readouterr()

    for tool in ("cgitsync", "git", "pixi", "dvc", "git-lfs"):
        assert tool in captured.out
    # A tool that is not installed prints the word, never a blank column.
    assert "none" in captured.out


def test_status_on_a_workspace_that_has_recorded_nothing(tmp_path, capsys):
    workspace = tmp_path / "empty"
    (workspace / ".cgitsync").mkdir(parents=True)

    exit_code = cli_main(["memory", "status", "--search-dir", str(workspace)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "entries=0" in captured.out
    assert "verification=no-history" in captured.out
    assert "nothing has been recorded here yet" in captured.out


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_names_every_state_with_what_recorded_it(tmp_path, capsys):
    workspace = _used_workspace(tmp_path / "demo")
    [state] = sorted((workspace / ".cgitsync" / "state").glob("*.gts"))

    exit_code = cli_main(["memory", "list", "--search-dir", str(workspace)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert state.stem[:16] in captured.out
    assert "load" in captured.out


def test_list_shows_a_state_nobody_recorded(tmp_path, capsys):
    """History from before the ledger is listed, and said to be unrecorded."""
    workspace = _used_workspace(tmp_path / "demo")
    (workspace / ".cgitsync" / "state" / f"{'c' * 64}.gts").write_text("old\n", encoding="utf-8")

    cli_main(["memory", "list", "--search-dir", str(workspace)])
    captured = capsys.readouterr()

    assert "cccccccccccccccc" in captured.out
    assert "(no entry records it)" in captured.out


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_takes_a_prefix_and_prints_the_entries(tmp_path, capsys):
    workspace = _used_workspace(tmp_path / "demo", operations=2)
    [state] = sorted((workspace / ".cgitsync" / "state").glob("*.gts"))

    exit_code = cli_main(
        ["memory", "show", state.stem[:8], "--search-dir", str(workspace)]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert f"state={state.stem}" in captured.out
    assert "project=demo" in captured.out
    # Two operations over one tree: one State, two entries naming it.
    assert "seq=1" in captured.out
    assert "seq=2" in captured.out


def test_cli_show_state_prints_a_bare_environment_reference_and_the_tree(tmp_path, capsys):
    """`memory show state=<ref>` prints the environment as a bare reference
    (its full detail is `env=<ref>`'s job) followed by the `[tree]` section,
    drawn exactly the way `view-tree` draws it."""
    workspace = _used_workspace(tmp_path / "demo")
    [state] = sorted((workspace / ".cgitsync" / "state").glob("*.gts"))

    exit_code = cli_main(["memory", "show", state.stem, "--search-dir", str(workspace)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "environment=env(" in captured.out
    # The bare reference line names a path, not machine/tools/credentials
    # detail — that full record only ever appears under `env=<ref>`.
    assert "architecture" not in captured.out
    assert "[tree]" in captured.out
    assert "demo (root)" in captured.out


def test_cli_show_env_prints_the_full_record_as_a_tree(tmp_path, capsys):
    workspace = _used_workspace(tmp_path / "demo")
    client = ComplexGitSyncClient()
    [row] = client.memory_list(workspace)
    [environment] = client.memory_show(workspace, row["state"])["environments"]

    exit_code = cli_main(
        ["memory", "show", environment["id"], "--search-dir", str(workspace)]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert f"environment={environment['id']}" in captured.out
    assert "├── machine" in captured.out
    assert "architecture:" in captured.out
    assert "[tree]" not in captured.out


def test_show_refuses_a_prefix_that_matches_nothing(tmp_path, capsys):
    workspace = _used_workspace(tmp_path / "demo")

    exit_code = cli_main(["memory", "show", "deadbeef", "--search-dir", str(workspace)])
    captured = capsys.readouterr()

    # "the command ran and the answer is no", per the exit-code contract.
    assert exit_code == 1
    assert "no State" in captured.err
    assert "Traceback" not in captured.err


def test_show_refuses_an_ambiguous_prefix(tmp_path, capsys):
    workspace = _used_workspace(tmp_path / "demo")
    state_dir = workspace / ".cgitsync" / "state"
    (state_dir / f"{'a' * 64}.gts").write_text("one\n", encoding="utf-8")
    (state_dir / f"{'a' * 63}b.gts").write_text("two\n", encoding="utf-8")

    exit_code = cli_main(["memory", "show", "aaaa", "--search-dir", str(workspace)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "more than one State" in captured.err


# ---------------------------------------------------------------------------
# The Python side answers the same questions
# ---------------------------------------------------------------------------


def test_the_client_methods_mirror_the_commands(tmp_path):
    workspace = _used_workspace(tmp_path / "demo", operations=2)
    client = ComplexGitSyncClient()

    status = client.memory_status(workspace)
    assert status["entries"] == 2
    assert status["states"] == 1
    assert status["verification"] == "verified"

    [row] = client.memory_list(workspace)
    assert row["commands"] == ["load", "load"]

    shown = client.memory_show(workspace, row["state"][:8])
    assert shown["state"] == row["state"]

    # A real incident: `memory explore --timeline` prints each row as
    # `state=<hash>`, which reads exactly like the argument to paste back —
    # and a user did exactly that. `memory_show` strips the label rather
    # than treating "state=" as part of the prefix to match.
    assert client.memory_show(workspace, f"state={row['state'][:8]}")["state"] == row["state"]
    # Likewise the full `state(<hash>)` id, the form a ledger entry and
    # `self-history add --state-before` both use.
    assert client.memory_show(workspace, f"state({row['state']})")["state"] == row["state"]
    assert [entry["seq"] for entry in shown["entries"]] == [1, 2]
    assert set(shown["entries"][0]["toolchain"]) == {
        "cgitsync",
        "git",
        "pixi",
        "dvc",
        "git-lfs",
    }


def test_show_carries_this_states_own_tree_rendered_like_view_tree(tmp_path):
    """`memory show`'s ``tree`` is this State's own topology, drawn exactly
    the way `cgitsync view-tree` draws the live one (`format_view_tree`) —
    built from this `.gts` document, not whatever the client currently has
    loaded, so it answers "what did the tree look like then", not now."""
    workspace = _used_workspace(tmp_path / "demo")
    client = ComplexGitSyncClient()
    client.load(workspace / "project.cgs")
    [row] = client.memory_list(workspace)

    shown = client.memory_show(workspace, row["state"])

    assert shown["tree"] == client.view_tree()


def test_show_environment_reads_the_full_record_by_its_own_hash(tmp_path):
    """`memory show`'s own environment reference (a bare `env(<hash>)`
    citation, never the full record — that stays `memory show env=<ref>`'s
    job) resolves back to the real record through `memory_show_environment`,
    by the full id, a bare prefix, or the `env=` form its own CLI argument
    is spelled with."""
    workspace = _used_workspace(tmp_path / "demo")
    client = ComplexGitSyncClient()
    [row] = client.memory_list(workspace)
    shown = client.memory_show(workspace, row["state"])
    [environment] = shown["environments"]

    full = client.memory_show_environment(workspace, environment["id"])
    assert full["record"] == environment["record"]
    assert full["path"] == environment["path"]

    env_hash = environment["id"][len("env(") : -1]
    assert client.memory_show_environment(workspace, env_hash[:8])["id"] == environment["id"]
    assert (
        client.memory_show_environment(workspace, f"env={env_hash[:8]}")["id"]
        == environment["id"]
    )

"""What was committed, and whether anybody else has ever seen it.

A ledger entry said a `commit` ran and which State it produced. It never
said what was written, and the message is the part a person recognises the
work by — and the part that disappears first, when a branch is deleted or a
repository is archived.

Real Git throughout, and a bare repository standing in for a remote: the
difference between a commit that has been published and one that has not is
exactly the difference a fake cannot have.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import tomli_w

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.memory.integrity import Finding, HistoryState
from ComplexGitSync.memory.ledger_store import read_all_entries
from ComplexGitSync.orchestre import ComplexGitSyncClient


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def _identify(repo: Path) -> None:
    _git(repo, "config", "user.email", "integration@complexgitsync.test")
    _git(repo, "config", "user.name", "ComplexGitSync Integration")


def _remote_and_clone(
    tmp_path: Path,
    name: str,
    branch: str,
    into: Path,
    *,
    ignore: tuple[str, ...] = (),
) -> Path:
    """A bare remote holding one commit, cloned to *into*."""
    remote = tmp_path / f"{name}.git"
    _git(tmp_path, "init", "--bare", "-b", branch, remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", branch)
    _identify(seed)
    (seed / "README.md").write_text("initial\n", encoding="utf-8")
    # What `sync_gitignore` writes into a real tree: the memory, and every
    # repository mounted inside this one, are not part of this repository's
    # work — so a `commit` that finds only those finds nothing.
    (seed / ".gitignore").write_text(
        "".join(f"{line}\n" for line in (".cgitsync/", *ignore)), encoding="utf-8"
    )
    _git(seed, "add", "README.md", ".gitignore")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "-u", "origin", branch)
    into.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "clone", "-b", branch, remote.as_posix(), into.as_posix())
    _identify(into)
    return remote


def _snapshot(root: Path, config: Path, *, root_sha: str, config_sha: str) -> str:
    return f"""
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

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "{root.as_posix()}"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "{root_sha}"
project_owner_name = "owner"
project_name = "demo"
gitprovider = "github"

[[repo_state]]
name = "conf"
node_type = "leaf"
absolute_path = "{config.as_posix()}"
parent_absolute_path = "{root.as_posix()}"
relative_path = ".conf"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "demo"
target_ref_kind = "branch"
target_ref_name = "demo"
resolved_ref_kind = "branch"
resolved_ref_name = "demo"
commit_sha = "{config_sha}"
default_branch = "demo"
project_owner_name = "owner"
project_name = "conf"
gitprovider = "github"
private = true
writable = true
""".strip() + "\n"


def _workspace(tmp_path: Path) -> dict[str, Path]:
    """A two-repository tree, both real clones of real remotes."""
    root = tmp_path / "demo"
    config = root / ".conf"
    project_remote = _remote_and_clone(
        tmp_path, "demo", "main", root, ignore=(".conf/",)
    )
    config_remote = _remote_and_clone(tmp_path, "conf", "demo", config)
    snapshot = tmp_path / "demo.gts"
    snapshot.write_text(
        _snapshot(
            root,
            config,
            root_sha=_git(root, "rev-parse", "HEAD"),
            config_sha=_git(config, "rev-parse", "HEAD"),
        ),
        encoding="utf-8",
    )
    return {
        "root": root,
        "config": config,
        "snapshot": snapshot,
        "project_remote": project_remote,
        "config_remote": config_remote,
    }


def _loaded(snapshot: Path) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    client.load_gts(snapshot)
    return client


def _change(repo: Path, text: str) -> None:
    (repo / "work.txt").write_text(text, encoding="utf-8")


def _logs(root: Path) -> dict[str, dict]:
    """Every commit log the workspace holds, by the State it belongs to."""
    directory = root / ".cgitsync" / "commit-logs"
    return {
        path.stem: tomllib.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.toml"))
    }


def _rows(root: Path) -> list[dict]:
    return [row for log in _logs(root).values() for row in log.get("commit", [])]


def _published(root: Path) -> list[dict]:
    return [row for log in _logs(root).values() for row in log.get("published", [])]


# ---------------------------------------------------------------------------
# What a commit leaves behind
# ---------------------------------------------------------------------------


def test_a_commit_is_remembered_with_its_message(tmp_path):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")

    _loaded(tree["snapshot"]).commit("the tool now remembers what it committed")

    [row] = _rows(tree["root"])
    assert row["message"] == "the tool now remembers what it committed"
    assert row["repository"] == "demo"
    assert row["scope"] == "project"
    assert row["branch"] == "main"
    assert row["sha"] == _git(tree["root"], "rev-parse", "HEAD")
    assert row["authored_at"].startswith("2")


def test_the_message_is_reachable_from_the_state_hash_alone(tmp_path):
    """No index, no search: the State's name *is* where the messages are."""
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    client = _loaded(tree["snapshot"])
    client.commit("a message filed under a State")

    [state_hash] = list(_logs(tree["root"]))
    assert (tree["root"] / ".cgitsync" / "state" / f"{state_hash}.gts").is_file()
    shown = client.memory_show(tree["root"], state_hash)
    assert shown["entries"][-1]["commits"][0]["message"] == "a message filed under a State"


def test_the_configuration_half_is_marked_private(tmp_path):
    tree = _workspace(tmp_path)
    _change(tree["config"], "settings")

    _loaded(tree["snapshot"]).commit("one story, both halves", private=True)

    [row] = _rows(tree["root"])
    assert row["repository"] == "conf"
    assert row["scope"] == "private"
    assert row["branch"] == "demo"


def test_a_commit_that_changes_nothing_records_nothing(tmp_path):
    tree = _workspace(tmp_path)

    _loaded(tree["snapshot"]).commit("nothing to say")

    assert _rows(tree["root"]) == []


# ---------------------------------------------------------------------------
# Published, or only ever seen here
# ---------------------------------------------------------------------------


def test_an_unpushed_commit_is_told_from_a_pushed_one(tmp_path):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    client = _loaded(tree["snapshot"])
    client.commit("work nobody has seen")

    assert _published(tree["root"]) == []

    client.push()

    [publication] = _published(tree["root"])
    assert publication["sha"] == _git(tree["root"], "rev-parse", "HEAD")


def test_a_push_records_the_remote_the_ref_and_the_entry_that_did_it(tmp_path):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    client = _loaded(tree["snapshot"])
    client.commit("work to publish")
    client.push()

    [publication] = _published(tree["root"])
    assert publication["remote"] == "github:owner/demo"
    assert publication["ref"] == "refs/heads/main"
    entries = read_all_entries(tree["root"] / ".cgitsync" / "lgr")
    pushes = [entry for entry in entries if entry.command == "push"]
    assert publication["entry"] == pushes[-1].seq


def test_a_push_publishes_every_commit_since_the_last_one(tmp_path):
    """Two commits, one push: both become public, and both say so."""
    tree = _workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "one")
    client.commit("first")
    _change(tree["root"], "two")
    client.commit("second")

    client.push()

    assert len(_rows(tree["root"])) == 2
    assert len(_published(tree["root"])) == 2


def test_a_commit_the_memory_never_saw_is_not_claimed_as_published(tmp_path):
    """Committed by hand, so nothing recorded it. A push invents no row."""
    tree = _workspace(tmp_path)
    _change(tree["root"], "by hand")
    _git(tree["root"], "add", "work.txt")
    _git(tree["root"], "commit", "-m", "not through cgitsync")

    _loaded(tree["snapshot"]).push()

    assert _published(tree["root"]) == []


# ---------------------------------------------------------------------------
# What verify makes of an edited log
# ---------------------------------------------------------------------------


def test_editing_a_commit_message_is_found_by_verify(tmp_path):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    _loaded(tree["snapshot"]).commit("what was really written")

    [(state_hash, log)] = _logs(tree["root"]).items()
    log["commit"][0]["message"] = "a nicer story"
    path = tree["root"] / ".cgitsync" / "commit-logs" / f"{state_hash}.toml"
    path.write_text(tomli_w.dumps(log), encoding="utf-8")

    report = ComplexGitSyncClient().verify(tree["root"])

    assert report.state is HistoryState.CORRUPT
    assert Finding.COMMIT_LOG_MISMATCH in {finding for _seq, finding, _detail in report.findings}


def test_a_row_added_after_the_fact_is_found_by_verify(tmp_path):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    _loaded(tree["snapshot"]).commit("what was really written")

    [(state_hash, log)] = _logs(tree["root"]).items()
    log["commit"].append({**log["commit"][0], "message": "work nobody did"})
    path = tree["root"] / ".cgitsync" / "commit-logs" / f"{state_hash}.toml"
    path.write_text(tomli_w.dumps(log), encoding="utf-8")

    report = ComplexGitSyncClient().verify(tree["root"])

    assert Finding.COMMIT_LOG_MISMATCH in {finding for _seq, finding, _detail in report.findings}


def test_a_commit_log_whose_state_is_gone_is_reported_and_kept(tmp_path):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    _loaded(tree["snapshot"]).commit("work whose State will vanish")
    [state_hash] = list(_logs(tree["root"]))

    (tree["root"] / ".cgitsync" / "state" / f"{state_hash}.gts").unlink()
    report = ComplexGitSyncClient().verify(tree["root"])

    findings = {finding for _seq, finding, _detail in report.findings}
    assert Finding.ORPHAN_COMMIT_LOG in findings
    # Reported, never repaired: the evidence is still there afterwards.
    assert (tree["root"] / ".cgitsync" / "commit-logs" / f"{state_hash}.toml").is_file()


def test_an_untouched_commit_log_verifies(tmp_path):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    client = _loaded(tree["snapshot"])
    client.commit("honest work")
    client.push()

    report = ComplexGitSyncClient().verify(tree["root"])

    assert report.state is HistoryState.VERIFIED
    assert report.findings == []


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------


def test_memory_show_prints_the_message_and_whether_it_was_published(tmp_path, capsys):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    _loaded(tree["snapshot"]).commit("a message worth reading back")
    [state_hash] = list(_logs(tree["root"]))

    exit_code = cli_main(
        ["memory", "show", state_hash, "--search-dir", str(tree["root"])]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "a message worth reading back" in captured.out
    assert "unpushed" in captured.out


def test_show_keeps_a_long_message_to_one_line_unless_asked(tmp_path, capsys):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    message = "a subject line\n\nand a body that the common case must not have to read"
    _loaded(tree["snapshot"]).commit(message)
    [state_hash] = list(_logs(tree["root"]))

    cli_main(["memory", "show", state_hash, "--search-dir", str(tree["root"])])
    short = capsys.readouterr().out
    cli_main(["memory", "show", state_hash, "--full", "--search-dir", str(tree["root"])])
    full = capsys.readouterr().out

    assert "and a body" not in short
    assert "and a body" in full


# ---------------------------------------------------------------------------
# G5 — what a commit log must never carry off the machine
# ---------------------------------------------------------------------------


def test_a_commit_log_holds_no_absolute_path_and_no_user_name(tmp_path):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    client = _loaded(tree["snapshot"])
    client.commit("nothing here says where this machine is")
    client.push()

    everything = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((tree["root"] / ".cgitsync" / "commit-logs").glob("*.toml"))
    )

    assert str(tmp_path) not in everything
    assert str(Path.home()) not in everything

"""`push`/`tag`/`freeze` fold and send this project's own memory first.

Before this ticket, crossing the frontier between `.cgitsync` ("what will
be") and `.cgitsync/.memory` ("what is") was a person remembering to type
`cgitsync memory push` on their own initiative — gated on nothing, tied to
nothing. `main_1-1_PushFoldsMemory_DevPlanTicket.md` makes every command
that already reaches a remote for an unrelated reason cross that frontier
automatically, first, before doing anything else.

Real Git throughout — a bare repository standing in for the project's own
remote and a second one for the memory's, exactly like
`test_memory_onboarding.py` and `test_commit_memory.py`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import ComplexGitSync as complexgitsync_pkg
from ComplexGitSync.memory.ledger_store import read_all_entries
from ComplexGitSync.orchestre import ComplexGitSyncClient


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def _identify(repo: Path) -> None:
    _git(repo, "config", "user.email", "integration@complexgitsync.test")
    _git(repo, "config", "user.name", "ComplexGitSync Integration")


def _bare_remote(path: Path, *, branch: str = "main", seed: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--bare", "-b", branch, str(path)], check=True, capture_output=True
    )
    if seed:
        seeded = path.parent / f"{path.stem}-seed"
        seeded.mkdir()
        _git(seeded, "init", "-b", branch)
        _identify(seeded)
        (seeded / "README.md").write_text("initial\n", encoding="utf-8")
        # `.cgitsync/` is never this project's own work — the same line a
        # real tree's `sync_gitignore` writes.
        (seeded / ".gitignore").write_text(".cgitsync/\n", encoding="utf-8")
        _git(seeded, "add", "README.md", ".gitignore")
        _git(seeded, "commit", "-m", "initial")
        _git(seeded, "remote", "add", "origin", str(path))
        _git(seeded, "push", "-u", "origin", branch)
    return path


def _root_snapshot(root: Path, *, sha: str) -> str:
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
commit_sha = "{sha}"
project_owner_name = "owner"
project_name = "demo"
gitprovider = "github"
""".strip() + "\n"


def _with_memory_entry(
    snapshot_text: str, *, mount: Path, root: Path, branch: str, sha: str
) -> str:
    """The root-only snapshot above, plus the memory mount as a real entry.

    A memory is only "declared" for `_fold_memory_before_push`'s purposes
    when the tree's own registry carries an entry at ``.cgitsync/.memory``
    — the same check `status`'s dirty-note and `_refresh_memory_mount_state`
    already make. `memory_adopt`/`memory_push` themselves need no such
    entry (they work straight against the workspace path), which is why
    the physical adopt-and-push below runs before this entry ever exists.
    """
    return snapshot_text + f"""
[[repo_state]]
name = "memory"
node_type = "leaf"
absolute_path = "{mount.as_posix()}"
parent_absolute_path = "{root.as_posix()}"
relative_path = ".cgitsync/.memory"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "{branch}"
target_ref_kind = "branch"
target_ref_name = "{branch}"
resolved_ref_kind = "branch"
resolved_ref_name = "{branch}"
commit_sha = "{sha}"
project_owner_name = "owner"
project_name = "memory"
gitprovider = "github"
private = true
writable = true
""".strip() + "\n"


def _memory_ready_workspace(tmp_path: Path) -> dict[str, Path]:
    """A READY, single-repo tree with a real, adopted, already-pushed memory."""
    project_remote = _bare_remote(tmp_path / "demo.git", branch="main")
    root = tmp_path / "demo"
    _git(tmp_path, "clone", "-b", "main", str(project_remote), str(root))
    _identify(root)
    (root / ".cgitsync").mkdir(parents=True)

    # `memory_adopt` needs a loaded project even when `remote=`/`branch=`
    # are given explicitly — `_memory_base_branch` proposes a `.cgs` entry
    # of its own regardless, purely to read a fallback-branch name off it.
    root_only = tmp_path / "root-only.gts"
    root_only.write_text(_root_snapshot(root, sha=_git(root, "rev-parse", "HEAD")), encoding="utf-8")
    client = ComplexGitSyncClient()
    client.load_gts(root_only)
    # A State + ledger entry to give the mount's very first push something
    # to commit — an empty repository has no HEAD for `memory_push` itself
    # to read back, exactly the ambiguous-HEAD failure a workspace with no
    # prior activity would also hit.
    client.write_gts_snapshot(command_origin="load")

    memory_remote = _bare_remote(tmp_path / "memory.git", branch="demo", seed=False)
    client.memory_adopt(root, remote=str(memory_remote), branch="demo")
    mount = root / ".cgitsync" / ".memory"
    _identify(mount)
    client.memory_push(root)  # the initial push: commits the one pending State

    snapshot = tmp_path / "demo.gts"
    snapshot.write_text(
        _with_memory_entry(
            _root_snapshot(root, sha=_git(root, "rev-parse", "HEAD")),
            mount=mount,
            root=root,
            branch="demo",
            sha=_git(mount, "rev-parse", "HEAD"),
        ),
        encoding="utf-8",
    )
    return {
        "root": root,
        "mount": mount,
        "snapshot": snapshot,
        "project_remote": project_remote,
        "memory_remote": memory_remote,
    }


def _loaded(snapshot: Path) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    client.load_gts(snapshot)
    return client


def _change(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")


def _memory_head(memory_remote: Path, *, branch: str) -> str:
    """The memory's HEAD as the remote itself sees it — a bare repo, so a
    direct ``rev-parse`` on the branch ref, no clone needed."""
    return _git(memory_remote, "rev-parse", branch)


# ---------------------------------------------------------------------------
# A plain push folds and sends the memory — no --private, no separate
# `memory push`, needed.
# ---------------------------------------------------------------------------


def test_a_plain_push_folds_and_sends_pending_memory(tmp_path):
    tree = _memory_ready_workspace(tmp_path)
    before = _memory_head(tree["memory_remote"], branch="demo")
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")
    client.commit("work worth remembering")

    client.push()

    after = _memory_head(tree["memory_remote"], branch="demo")
    assert after != before  # the fold really reached the remote
    assert client.last_memory_fold is not None
    assert client.last_memory_fold["committed"] is True
    assert client.last_memory_fold["recorded"] >= 1


def test_a_plain_push_reaches_the_project_remote_as_before(tmp_path):
    """The fold is additional behaviour, not a replacement for the push."""
    tree = _memory_ready_workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")
    client.commit("ordinary project work")

    client.push()

    other = tmp_path / "verify-clone"
    _git(tmp_path, "clone", "-b", "main", str(tree["project_remote"]), str(other))
    assert (other / "work.txt").read_text(encoding="utf-8") == "one"


def test_a_tree_with_no_memory_mounted_sees_no_change(tmp_path):
    """No mount at all — most trees — and `push` behaves exactly as before."""
    project_remote = _bare_remote(tmp_path / "demo.git", branch="main")
    root = tmp_path / "demo"
    _git(tmp_path, "clone", "-b", "main", str(project_remote), str(root))
    _identify(root)
    snapshot = tmp_path / "demo.gts"
    snapshot.write_text(_root_snapshot(root, sha=_git(root, "rev-parse", "HEAD")), encoding="utf-8")

    _change(root, "work.txt", "one")
    client = _loaded(snapshot)
    client.commit("no memory here")

    client.push()  # must not raise, must not warn, must not touch anything memory-shaped

    assert client.last_memory_fold is None
    assert not (root / ".cgitsync" / ".memory").exists()


def test_a_declared_but_not_yet_adopted_memory_warns_and_the_push_still_succeeds(
    tmp_path, monkeypatch
):
    """D2: declared in the tree, but `memory adopt` was never run.

    `_fold_memory_before_push` treats "not adopted" the same as any other
    `memory_push` failure (D3) — caught, warned, never raised — so this is
    exercised the same way: a real, adopted memory (so the rest of the
    tree stays healthy — a declared-but-missing mount fails a tree's own
    preflight for reasons that have nothing to do with this ticket) with
    `memory_push` swapped for the exact refusal an unadopted one raises.
    """
    tree = _memory_ready_workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")
    client.commit("declared, not adopted")

    def _not_adopted(self, cgshome, *, message=None):
        from ComplexGitSync.errors import GitSyncError

        raise GitSyncError(
            f"{tree['mount']} is not a repository yet. Run 'cgitsync memory init' "
            "for the entry that mounts one, then 'cgitsync memory clone'."
        )

    monkeypatch.setattr(ComplexGitSyncClient, "memory_push", _not_adopted)

    with pytest.warns(UserWarning, match="memory not folded"):
        client.push()

    assert client.last_memory_fold is None
    other = tmp_path / "verify-clone"
    _git(tmp_path, "clone", "-b", "main", str(tree["project_remote"]), str(other))
    assert (other / "work.txt").read_text(encoding="utf-8") == "one"  # push still succeeded


def test_a_memory_push_failure_for_another_reason_warns_and_the_push_still_succeeds(
    tmp_path, monkeypatch
):
    """D3: an adopted memory that cannot reach its remote is its own
    failure, not the project's — it warns, and the command it was folding
    for still completes."""
    tree = _memory_ready_workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")
    client.commit("this must not be blocked by the memory")

    def _broken_memory_push(self, cgshome, *, message=None):
        from ComplexGitSync.errors import GitSyncError

        raise GitSyncError("the memory's remote refused the connection")

    monkeypatch.setattr(ComplexGitSyncClient, "memory_push", _broken_memory_push)

    with pytest.warns(UserWarning, match="memory not folded"):
        client.push()

    assert client.last_memory_fold is None
    other = tmp_path / "verify-clone"
    _git(tmp_path, "clone", "-b", "main", str(tree["project_remote"]), str(other))
    assert (other / "work.txt").read_text(encoding="utf-8") == "one"


# ---------------------------------------------------------------------------
# tag and freeze do it too, each on their own — not only when combined
# with a push.
# ---------------------------------------------------------------------------


def test_tag_folds_and_sends_pending_memory_on_its_own(tmp_path):
    tree = _memory_ready_workspace(tmp_path)
    before = _memory_head(tree["memory_remote"], branch="demo")
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")
    client.commit("work worth tagging")

    client.tag("v1")

    after = _memory_head(tree["memory_remote"], branch="demo")
    assert after != before
    assert client.last_memory_fold is not None
    assert client.last_memory_fold["committed"] is True


def test_freeze_folds_and_sends_pending_memory_on_its_own(tmp_path):
    tree = _memory_ready_workspace(tmp_path)
    before = _memory_head(tree["memory_remote"], branch="demo")
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")
    client.commit("work worth freezing")  # a prior State, pending by the time freeze folds

    client.freeze("release-1")

    after = _memory_head(tree["memory_remote"], branch="demo")
    assert after != before
    assert client.last_memory_fold is not None


def test_freeze_release_folds_via_its_own_push_and_its_own_freeze_harmlessly(tmp_path):
    """`freeze_release` calls `self.push()` then `self.freeze()` — both now
    fold the memory; the second fold has nothing new pending and is a
    cheap no-op, never a duplicate worth guarding against."""
    tree = _memory_ready_workspace(tmp_path)
    before = _memory_head(tree["memory_remote"], branch="demo")
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")

    client.freeze_release("v-release-1")

    after = _memory_head(tree["memory_remote"], branch="demo")
    assert after != before
    # Two folds happened (push's, then freeze's); the mount ends up with
    # no local changes left uncommitted either way.
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tree["mount"], check=True,
        text=True, capture_output=True,
    ).stdout
    assert status == ""

    # The entry freeze_release() wrote carries a release row — the real
    # end-to-end path, not just the unit-level plumbing. It lands in the
    # pending half (`.cgitsync/lgr`): the memory fold happens before the
    # freeze step writes this entry, so it is not folded in yet.
    entries = read_all_entries(tree["root"] / ".cgitsync" / "lgr")
    release_entry = entries[-1]
    assert release_entry.command == "freeze_release"
    release = dict(release_entry.release)
    assert release == {
        "semver": complexgitsync_pkg.__version__,
        "git_tag": "v-release-1",
        "artefact:src": complexgitsync_pkg.__build__,
    }

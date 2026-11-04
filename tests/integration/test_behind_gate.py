"""BehindGate: the branches of a `yyyy-xxx.cgs` tree, on real repositories.

A level tree (`demo-opx.cgs`: project `demo`, branch `opx`, falling back to `CGS-ALPHA`) whose `opx` exists nowhere
yet. Q3: every private branch derives from the level (`demo_opx`), never from the fallback the root waits on. Q1 (c):
the root keeps `opx`; ComplexGitSync, mounted behind the gate, takes `demo_opx`. Q2: `branch create opx` then
`checkout opx` creates every one of them, locally. See the BehindGate ticket.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ComplexGitSync import ComplexGitSyncClient
from ComplexGitSync.orchestre.installer import Installer
from ComplexGitSync.tree.git_tree_branch import GitTreeBranches
from ComplexGitSync.tree.settings import UseCase

MEMORY = ".memory"


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _seed(tmp_path: Path, name: str, branches: tuple[str, ...]) -> Path:
    remote = tmp_path / f"{name}-remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    (seed / "README.md").write_text(f"{name}\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "-c", "user.email=t@example.test", "-c", "user.name=Tester", "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    for branch in branches:
        _git(seed, "push", "origin", f"main:refs/heads/{branch}")
    return remote


def _level_tree(tmp_path: Path, monkeypatch, *, name: str = "demo-opx.cgs", project: str = 'name = "demo", default_branch = "opx", fallback_branch = "CGS-ALPHA"'):
    remotes = {
        "demo": _seed(tmp_path, "demo", ("main", "CGS-ALPHA")),
        "ComplexGitSync": _seed(tmp_path, "tool", ("main", "CGS-ALPHA")),
        ".localSpec": _seed(tmp_path, "localspec", ("main",)),
        MEMORY: _seed(tmp_path, "memory", ("main",)),
    }
    cgs = tmp_path / name
    cgs.write_text(
        f"""
project = {{ {project} }}
repos = [
    {{ repository = "github:owner/demo", relative_path = ".", nested_config = "disabled" }},
    {{ repository = "github:flipoyo/ComplexGitSync", fallback_branch = "CGS-ALPHA", nested_config = "disabled" }},
    {{ repository = "github:owner/.localSpec", relative_path = ".agent/.local/.localSpec", fallback_branch = "main", private = true, writable = true }},
    {{ repository = "github:owner/{MEMORY}", relative_path = ".cgitsync/.memory", fallback_branch = "main", private = true, writable = true, nested_config = "disabled" }},
]
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(Installer, "_use_case_of", lambda self, cgshome: UseCase.STANDALONE)
    client = ComplexGitSyncClient()
    monkeypatch.setattr(client, "_build_remote_url", lambda entry: str(remotes[entry.project_name]))
    registry = client.bootstrap(cgs, "demo", cgs_path=tmp_path / "one")
    return client, registry, tmp_path / "one" / "demo", remotes


def _by_name(registry):
    return {repo.name: repo for repo in registry.values()}


def _on(root: Path, path: str) -> str:
    return _git(root / path, "branch", "--show-current")


def test_every_private_branch_derives_from_the_level_and_the_tool_takes_the_projects(tmp_path, monkeypatch):
    client, registry, root, _ = _level_tree(tmp_path, monkeypatch)
    repos = _by_name(registry)

    assert (repos["demo"].target_ref_name, _on(root, ".")) == ("opx", "CGS-ALPHA")
    assert (repos["ComplexGitSync"].target_ref_name, _on(root, "ComplexGitSync")) == ("demo_opx", "CGS-ALPHA")
    for name, wanted in ((".localSpec", "demo_opx"), (MEMORY, "ComplexGitSync_demo_opx")):
        assert (repos[name].default_branch, repos[name].target_ref_name) == (wanted, wanted)
    expected = {d.repo.name: d.expected for d in GitTreeBranches(registry, client.git_runner).deviations()}
    assert expected == {"ComplexGitSync": "demo_opx", ".localSpec": "demo_opx", MEMORY: "ComplexGitSync_demo_opx"}


def test_the_install_names_branch_create_then_checkout_and_both_make_every_branch_locally(tmp_path, monkeypatch):
    client, registry, root, remotes = _level_tree(tmp_path, monkeypatch)
    commands = {m["repo"]: m["command"] for m in client.missing_private_branches}

    assert commands["demo"] == commands["ComplexGitSync"] == commands[".localSpec"] == "cgitsync branch create opx && cgitsync checkout opx"
    assert commands[MEMORY] == "cgitsync memory branch --project-branch opx"

    client.branch("opx")
    registry = client.checkout("opx")

    assert [_on(root, p) for p in (".", "ComplexGitSync", ".agent/.local/.localSpec", ".cgitsync/.memory")] == [
        "opx", "demo_opx", "demo_opx", "ComplexGitSync_demo_opx",
    ]
    assert GitTreeBranches(registry, client.git_runner).deviations() == ()
    for remote in remotes.values():
        assert not any("opx" in b for b in _git(remote, "branch", "--list").split())


def test_a_pull_while_the_root_waits_keeps_the_level(tmp_path, monkeypatch):
    client, _, root, _ = _level_tree(tmp_path, monkeypatch)
    state = sorted((root / ".cgitsync" / "state").glob("*.gts"), key=lambda p: p.stat().st_mtime)[-1]

    registry = client.pull(state)

    repos = _by_name(registry)
    assert repos["demo"].target_ref_name == "opx"
    assert repos[".localSpec"].target_ref_name == "demo_opx"
    assert GitTreeBranches(registry, client.git_runner).level == "opx"


def test_a_tree_that_is_not_a_level_keeps_the_tool_on_the_project_branch(tmp_path, monkeypatch):
    _, registry, root, _ = _level_tree(tmp_path, monkeypatch, name="demo.cgs", project='name = "demo", default_branch = "CGS-ALPHA"')

    repos = _by_name(registry)
    assert (repos["ComplexGitSync"].target_ref_name, _on(root, "ComplexGitSync")) == ("CGS-ALPHA", "CGS-ALPHA")
    assert repos[".localSpec"].target_ref_name == "demo_CGS-ALPHA"

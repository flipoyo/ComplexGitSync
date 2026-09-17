"""`pull` on a `.cgs` that does not sit at the tree's own root.

`examples/complexgitsync4dev.cgs` — this project's own developer spec —
deliberately does not sit at CGSHOME (`CLAUDE.md`, *Layout*: "where a `.cgs`
lives never affects the tree it describes"). `pull`, called with that exact
file on this exact project, broke on two separate bugs the day this
project's own memory was first mounted — main_1-1_PullOutsideRoot. Both are
reproduced here, on a synthetic tree shaped the same way, rather than only
asserted against this project's own history.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ComplexGitSync.orchestre import ComplexGitSyncClient


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


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
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "-u", "origin", branch)
    return remote


def _cgshome_with_a_cgs_not_at_its_root(tmp_path: Path) -> dict[str, Path]:
    """A real, cloned two-repo tree, with its `.cgs` filed away from the root.

    ``root/specs/topology.cgs`` — not ``root/topology.cgs`` — is the layout
    `examples/complexgitsync4dev.cgs` uses for real: a developer spec that
    describes the tree it sits *inside*, not the directory that holds it.
    """
    root = tmp_path / "cgshome"
    project_remote = _seeded_remote(tmp_path, "demo", "main")
    memory_remote = _seeded_remote(tmp_path, "memory", "demo")

    _git(tmp_path, "clone", "-b", "main", project_remote.as_posix(), root.as_posix())
    _identify(root)

    mount = root / ".mount"
    _git(tmp_path, "clone", "-b", "demo", memory_remote.as_posix(), mount.as_posix())
    _identify(mount)
    # A directory named exactly like the glob "auto" nested-config discovery
    # uses -- .cgitsync/.cgs/ in the real memory, reproduced here under a
    # generic mount so the test does not depend on the memory package at
    # all, only on the shape that broke it.
    (mount / ".cgs").mkdir()

    content = (
        'project = { name = "demo", default_branch = "main" }\n'
        "\n"
        "repos = [\n"
        '    "github:owner/demo",\n'
        '    { repository = "github:owner/memory", relative_path = ".mount", '
        'default_branch = "demo", fallback_branch = "main", '
        "private = true, writable = true },\n"
        "]\n"
    )

    specs_dir = root / "specs"
    specs_dir.mkdir()
    cgs_path = specs_dir / "topology.cgs"
    cgs_path.write_text(content, encoding="utf-8")

    # .cgitsync is established the way a real workspace's is: a .cgs sitting
    # AT the root it describes, the ordinary case load_cgs's own default
    # already serves. The non-root copy above is the one this test's `pull`
    # calls are actually about -- the same shape examples/complexgitsync4dev.cgs
    # has to this project's own CGSHOME.
    root_cgs = root / "topology.cgs"
    root_cgs.write_text(content, encoding="utf-8")
    ComplexGitSyncClient().load(root_cgs, discover_nested=True)
    root_cgs.unlink()

    return {"root": root, "cgs": cgs_path, "mount": mount}


def test_pull_resolves_the_root_from_the_workspace_not_the_cgs_file(tmp_path, monkeypatch):
    """The registry's root is CGSHOME, not the `.cgs` file's own directory."""
    tree = _cgshome_with_a_cgs_not_at_its_root(tmp_path)
    monkeypatch.chdir(tree["root"])

    client = ComplexGitSyncClient()
    registry = client.pull(tree["cgs"])

    assert registry.get("root").absolute_path == tree["root"].resolve()
    assert registry.get("root").absolute_path != tree["cgs"].parent.resolve()


def test_pull_does_not_crash_on_a_directory_shaped_like_a_cgs_file(tmp_path, monkeypatch):
    """The second bug: a `<name>.cgs` directory beside the mount must not
    be read as that repository's own nested config."""
    tree = _cgshome_with_a_cgs_not_at_its_root(tmp_path)
    monkeypatch.chdir(tree["root"])

    client = ComplexGitSyncClient()
    registry = client.pull(tree["cgs"])

    mount_entry = next(e for e in registry.values() if e.name == "memory")
    assert mount_entry.absolute_path == tree["mount"].resolve()

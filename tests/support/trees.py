"""trees — a READY three-repository tree on real Git, with a bare remote each.

``alpha`` and ``beta`` are leaves under the root ``demo``. A leaf-first sweep visits them in
``VISIT_ORDER``: beta, alpha, then the root demo. Each repository has one commit its remote does not, which is what
a ``push`` has to send. The snapshot is the ``.gts`` a client loads.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ComplexGitSync.orchestre import ComplexGitSyncClient

ORDER = ("alpha", "beta", "demo")
VISIT_ORDER = ("beta", "alpha", "demo")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()


def _remote(tmp_path: Path, name: str) -> Path:
    remote = tmp_path / f"{name}.git"
    git(tmp_path, "init", "--bare", "-b", "main", remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    git(seed, "init", "-b", "main")
    git(seed, "config", "user.email", "t@e.st")
    git(seed, "config", "user.name", "Test")
    (seed / "README.md").write_text("initial\n", encoding="utf-8")
    (seed / ".gitignore").write_text(".cgitsync/\n.alpha/\n.beta/\n", encoding="utf-8")
    git(seed, "add", "README.md", ".gitignore")
    git(seed, "commit", "-m", "initial")
    git(seed, "remote", "add", "origin", remote.as_posix())
    git(seed, "push", "-u", "origin", "main")
    return remote


def _entry(name: str, path: Path, root: Path, sha: str) -> str:
    leaf = name != "demo"
    parent = f'parent_absolute_path = "{root.as_posix()}"\nrelative_path = ".{name}"\n' if leaf else 'relative_path = "."\n'
    return f"""
[[repo_state]]
name = "{name}"
node_type = "{"leaf" if leaf else "root"}"
absolute_path = "{path.as_posix()}"
{parent}repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "{sha}"
project_owner_name = "owner"
project_name = "{name}"
gitprovider = "github"
"""


def three_repo_tree(tmp_path: Path, *, ahead: bool = True) -> dict[str, Path]:
    """Clone three repositories, give each one unpushed commit, and write the ``.gts`` that loads them."""
    root = tmp_path / "demo"
    paths = {"demo": root, "alpha": root / ".alpha", "beta": root / ".beta"}
    remotes = {name: _remote(tmp_path, name) for name in ORDER}
    git(tmp_path, "clone", "-b", "main", remotes["demo"].as_posix(), root.as_posix())
    (root / ".cgitsync").mkdir()  # a workspace that has been used, so its writing commands take the lock
    for name in ("alpha", "beta"):
        git(tmp_path, "clone", "-b", "main", remotes[name].as_posix(), paths[name].as_posix())
    for name, path in paths.items():
        git(path, "config", "user.email", "t@e.st")
        git(path, "config", "user.name", "Test")
        if ahead:
            (path / "work.txt").write_text(f"{name}\n", encoding="utf-8")
            git(path, "add", "work.txt")
            git(path, "commit", "-m", f"work in {name}")
    header = f"""
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
"""
    snapshot = tmp_path / "demo.gts"
    snapshot.write_text(
        (header + "".join(_entry(name, paths[name], root, git(paths[name], "rev-parse", "HEAD")) for name in ("demo", "beta", "alpha"))).strip() + "\n",
        encoding="utf-8",
    )
    return {"root": root, "snapshot": snapshot, **{f"remote_{n}": remotes[n] for n in ORDER}, **{f"path_{n}": paths[n] for n in ORDER}}


def loaded(snapshot: Path, runner: object | None = None) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    if runner is not None:
        client.git_runner = runner  # type: ignore[assignment]
    client.load_gts(snapshot)
    return client


def remote_head(remote: Path) -> str:
    return git(remote, "rev-parse", "main")


def remote_tags(remote: Path) -> list[str]:
    return git(remote, "tag", "--list").split()

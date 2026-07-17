"""End-to-end demonstration of the CGSil1 external Memory cycle."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_tree import TreeLifecycleState
from ComplexGitSync.orchestre import (
    CgsDocument,
    ComplexGitSyncClient,
    GitRunner,
    MemoryBinding,
)


def test_cgsil1_external_memory_cycle_demo(tmp_path: Path, monkeypatch) -> None:
    """MEM-DEMO-001: persisted external Memory reloads to the same State."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    _patch_git_identity(monkeypatch)
    sandbox = _build_cgsil1_sandbox(tmp_path)
    _patch_remote_urls(monkeypatch, sandbox)

    # 1. Start from local CGSil1.
    output_path = tmp_path / "workspace"
    project_root = _prepare_existing_root(output_path, sandbox)
    assert project_root.name == "CGSil1"

    # 2-4. Run ComplexGitSync, discover CGSil1.cgs, and discover the local Git address.
    runner = _Forge43MemoryHybridRunner(tmp_path / "forge43" / "CGSil1.git")
    client = ComplexGitSyncClient(git_runner=runner)
    cgs_path = sandbox["cgs_path"]
    cgs_document = CgsDocument.from_toml(cgs_path)
    assert cgs_document.project_name == "CGSil1"
    assert _run_git(project_root, "remote", "get-url", "origin") == str(
        sandbox["CGSil1_remote"]
    )

    # 5-7. Inspect the FileSystem, construct the DAG, and generate the GitTree.
    assert (project_root / "README.md").is_file()
    registry = client.initialise_cgs(cgs_path, output_path=output_path)
    assert registry.lifecycle_state == TreeLifecycleState.READY
    assert {entry.name for entry in registry.values()} == {"CGSil1", "CGSil2", "CGSih1"}
    assert (project_root / "CGSil2").is_dir()
    assert (project_root / "CGSih1").is_dir()

    # 8-10. Obtain public hash(@), locate current_memory_path, and finalize MemoryFS.
    initial_snapshot_path = client.loaded_snapshot_path
    assert initial_snapshot_path is not None
    initial_memory_path = initial_snapshot_path.parent
    initial_state_hash, initial_state_order = _parse_state_directory(initial_memory_path)
    assert re.fullmatch(r"[0-9a-f]{64}", initial_state_hash)
    assert initial_state_order == 0
    assert "@" not in initial_memory_path.name
    _assert_complete_cgsil1_state(initial_memory_path)

    # 11-12. Execute @CGSil1.remember and resolve @forge43@CGSil1.
    remember_result = client.remember(cgs_path, output_path=output_path)
    assert remember_result.binding.alias == "@forge43@CGSil1"
    assert remember_result.binding.remote_url == "git@forge43.io:/srv/git/CGSil1.git"
    assert remember_result.remote_validated is True

    # 13-15. Successful push triggers @CGSil1.memorize and verifies the remote commit.
    (project_root / "memory-demo.txt").write_text("external memory demo\n", encoding="utf-8")
    client.commit("demo: external memory cycle")
    client.push()
    memory_result = client.last_memory_result
    assert memory_result is not None
    assert memory_result.binding.alias == "@forge43@CGSil1"
    assert memory_result.commit_created is True
    assert memory_result.pushed is True
    assert memory_result.verified is True
    assert memory_result.local_ref == memory_result.remote_ref == runner.memory_remote_ref
    assert runner.memory_commits == [
        f"memory(CGSil1): persist state {memory_result.state_hash[:8]} "
        f"iteration {memory_result.state_order}"
    ]
    assert len(runner.memory_pushes) == 1

    current_memory_path = memory_result.current_memory_path
    state_hash, state_order = _parse_state_directory(current_memory_path)
    assert state_hash == memory_result.state_hash
    assert state_order == memory_result.state_order
    assert "@" not in current_memory_path.name
    _assert_complete_cgsil1_state(current_memory_path)

    persisted_state_digest = _hash_tree(current_memory_path)
    persisted_artifacts = _read_cgsil1_artifacts(current_memory_path)
    remote_state_path = runner.memory_remote_store / ".cgitsync" / current_memory_path.name
    assert remote_state_path.is_dir()
    assert _hash_tree(remote_state_path) == persisted_state_digest

    # 16. Remove or isolate the local Memory repository.
    isolated_root = tmp_path / "isolated" / "CGSil1"
    isolated_root.parent.mkdir()
    shutil.move(str(project_root), str(isolated_root))
    original_memory_repo = memory_result.memory_repository_path
    isolated_memory_repo = tmp_path / "isolated" / "memory-repository"
    if original_memory_repo.exists():
        shutil.move(str(original_memory_repo), str(isolated_memory_repo))
    assert not project_root.exists()
    assert not original_memory_repo.exists()

    # 17-18. Execute @CGSil1.retrieve and verify the retrieved Memory.
    retrieve_client = ComplexGitSyncClient(git_runner=runner)
    retrieve_result = retrieve_client.retrieve("CGSil1", output_path=tmp_path / "retrieved")
    assert retrieve_result.binding == MemoryBinding.for_name("CGSil1")
    assert retrieve_result.status == "retrieved"
    assert retrieve_result.verified is True
    retrieved_state_path = retrieve_result.cgitsync_path / current_memory_path.name
    assert retrieved_state_path.is_dir()
    assert _hash_tree(retrieved_state_path) == persisted_state_digest
    _assert_complete_cgsil1_state(retrieved_state_path)

    # 19-20. Execute @CGSil1.reload and compare reloaded State with persisted State.
    reload_client = ComplexGitSyncClient(git_runner=runner)
    reload_result = reload_client.reload("CGSil1", output_path=tmp_path / "reloaded")
    assert reload_result.status == "reloaded"
    assert reload_result.retrieve_result.verified is True
    assert reload_result.state_path.name == current_memory_path.name
    assert _hash_tree(reload_result.state_path) == persisted_state_digest
    assert _read_cgsil1_artifacts(reload_result.state_path) == persisted_artifacts
    assert reload_result.registry.is_ready()
    assert reload_result.registry.get("root").absolute_path == (
        tmp_path / "reloaded" / "CGSil1"
    ).resolve()


class _Forge43MemoryHybridRunner(GitRunner):
    """Use real local Git for project repos and simulated Git for forge43 Memory."""

    def __init__(self, memory_remote_store: Path) -> None:
        super().__init__()
        self.memory_remote_store = memory_remote_store
        self.memory_remote_url = "git@forge43.io:/srv/git/CGSil1.git"
        self.memory_remote_ref: str | None = None
        self.memory_remote_tree_hash: str | None = None
        self.memory_repositories: set[Path] = set()
        self.memory_local_refs: dict[Path, str | None] = {}
        self.memory_staged_hashes: dict[Path, str | None] = {}
        self.memory_commits: list[str] = []
        self.memory_pushes: list[tuple[Path, str, str | None]] = []

    def validate_memory_remote(self, remote_url: str) -> str:
        if remote_url == self.memory_remote_url:
            self.memory_remote_store.mkdir(parents=True, exist_ok=True)
            return self.memory_remote_ref or ""
        return super().validate_memory_remote(remote_url)

    def remote_head(self, remote_url: str, branch: str = "main") -> str | None:
        if remote_url == self.memory_remote_url:
            return self.memory_remote_ref
        return super().remote_head(remote_url, branch)

    def init_repository(self, repo_path: Path | str) -> None:
        repo = Path(repo_path).resolve()
        repo.mkdir(parents=True, exist_ok=True)
        self.memory_repositories.add(repo)
        self.memory_local_refs.setdefault(repo, None)

    def is_git_repository(self, repo_path: Path | str) -> bool:
        repo = Path(repo_path).resolve()
        if repo in self.memory_repositories:
            return repo.is_dir()
        return super().is_git_repository(repo_path)

    def checkout_branch(self, repo_path: Path | str, branch: str) -> None:
        if self._is_memory_repo(repo_path):
            return
        super().checkout_branch(repo_path, branch)

    def configure_remote(self, repo_path: Path | str, remote_name: str, remote_url: str) -> None:
        if self._is_memory_repo(repo_path):
            return
        super().configure_remote(repo_path, remote_name, remote_url)

    def fetch_branch(self, repo_path: Path | str, remote_name: str, branch: str) -> None:
        if self._is_memory_repo(repo_path):
            return
        super().fetch_branch(repo_path, remote_name, branch)

    def reset_to_fetch_head(self, repo_path: Path | str) -> None:
        if self._is_memory_repo(repo_path):
            repo = Path(repo_path).resolve()
            self.memory_local_refs[repo] = self.memory_remote_ref
            self._copy_remote_memory_to_repo(repo)
            return
        super().reset_to_fetch_head(repo_path)

    def fsck_full(self, repo_path: Path | str) -> None:
        if self._is_memory_repo(repo_path):
            if not (Path(repo_path).resolve() / ".cgitsync").is_dir():
                raise GitSyncError("git fsck --full failed")
            return
        super().fsck_full(repo_path)

    def clone(self, remote_url: str, destination: Path | str, *, branch: str) -> None:
        if remote_url == self.memory_remote_url:
            repo = Path(destination).resolve()
            if repo.exists() and (not repo.is_dir() or any(repo.iterdir())):
                raise GitSyncError(f"Clone destination already exists and is not empty: {repo}")
            repo.mkdir(parents=True, exist_ok=True)
            self.memory_repositories.add(repo)
            self.memory_local_refs[repo] = self.memory_remote_ref
            self._copy_remote_memory_to_repo(repo)
            return
        super().clone(remote_url, destination, branch=branch)

    def stage_all(self, repo_path: Path | str) -> None:
        if self._is_memory_repo(repo_path):
            repo = Path(repo_path).resolve()
            self.memory_staged_hashes[repo] = _hash_tree(repo / ".cgitsync")
            return
        super().stage_all(repo_path)

    def has_staged_changes(self, repo_path: Path | str) -> bool:
        if self._is_memory_repo(repo_path):
            repo = Path(repo_path).resolve()
            return self.memory_staged_hashes.get(repo) != self.memory_remote_tree_hash
        return super().has_staged_changes(repo_path)

    def commit(self, repo_path: Path | str, message: str) -> None:
        if self._is_memory_repo(repo_path):
            repo = Path(repo_path).resolve()
            self.memory_commits.append(message)
            self.memory_local_refs[repo] = f"{len(self.memory_commits):040x}"
            return
        super().commit(repo_path, message)

    def push(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
        set_upstream: bool = False,
    ) -> None:
        if self._is_memory_repo(repo_path):
            repo = Path(repo_path).resolve()
            self.memory_pushes.append((repo, remote, ref_name))
            self.memory_remote_ref = self.memory_local_refs[repo]
            self.memory_remote_tree_hash = self.memory_staged_hashes[repo]
            self._copy_repo_memory_to_remote(repo)
            return
        super().push(
            repo_path,
            remote=remote,
            ref_name=ref_name,
            set_upstream=set_upstream,
        )

    def rev_parse_head(self, repo_path: Path | str) -> str:
        if self._is_memory_repo(repo_path):
            repo = Path(repo_path).resolve()
            local_ref = self.memory_local_refs.get(repo)
            if local_ref is None:
                raise GitSyncError("No local Memory commit")
            return local_ref
        return super().rev_parse_head(repo_path)

    def _is_memory_repo(self, repo_path: Path | str) -> bool:
        return Path(repo_path).resolve() in self.memory_repositories

    def _copy_remote_memory_to_repo(self, repo: Path) -> None:
        source = self.memory_remote_store / ".cgitsync"
        target = repo / ".cgitsync"
        if target.exists():
            shutil.rmtree(target)
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git"))

    def _copy_repo_memory_to_remote(self, repo: Path) -> None:
        source = repo / ".cgitsync"
        target = self.memory_remote_store / ".cgitsync"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git"))


def _build_cgsil1_sandbox(tmp_path: Path) -> dict[str, Path]:
    cgsi1_remote, _ = _seed_remote_repo(tmp_path, "CGSil1")
    cgsi2_remote, _ = _seed_remote_repo(tmp_path, "CGSil2")
    cgsih1_remote, _ = _seed_remote_repo(tmp_path, "CGSih1")
    cgs_path = tmp_path / "CGSil1.cgs"
    cgs_path.write_text(_cgsil1_cgs(), encoding="utf-8")
    return {
        "cgs_path": cgs_path,
        "CGSil1_remote": cgsi1_remote,
        "CGSil2_remote": cgsi2_remote,
        "CGSih1_remote": cgsih1_remote,
    }


def _seed_remote_repo(base: Path, name: str) -> tuple[Path, Path]:
    remote = base / f"{name}.git"
    subprocess.run(["git", "init", "--bare", remote.as_posix()], check=True, capture_output=True)
    seed = base / f"{name}-seed"
    seed.mkdir()
    _run_git(seed, "init", "-b", "main")
    _run_git(seed, "config", "user.email", "memory-demo@complexgitsync.test")
    _run_git(seed, "config", "user.name", "Memory Demo")
    (seed / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    _run_git(seed, "add", "README.md")
    _run_git(seed, "commit", "-m", "initial")
    _run_git(seed, "remote", "add", "origin", remote.as_posix())
    _run_git(seed, "push", "-u", "origin", "main")
    _run_git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    return remote, seed


def _cgsil1_cgs() -> str:
    return """\
[document]
format_version = "1.0"

[project]
name = "CGSil1"
default_branch = "main"

[[repos]]
gitprovider = "gitlab"
project_owner_name = "CGS_test"
project_name = "CGSil1"
default_branch = "main"
fallback_branch = "main"
access_protocol = "ssh"
relative_path = "."

[[repos]]
gitprovider = "gitlab"
project_owner_name = "CGS_test"
project_name = "CGSil2"
default_branch = "main"
fallback_branch = "main"
access_protocol = "ssh"
relative_path = "CGSil2"
nested_config = "disabled"

[[repos]]
gitprovider = "github"
project_owner_name = "CGS_test"
project_name = "CGSih1"
default_branch = "main"
fallback_branch = "main"
access_protocol = "ssh"
relative_path = "CGSih1"
nested_config = "disabled"
"""


def _patch_remote_urls(monkeypatch: pytest.MonkeyPatch, sandbox: dict[str, Path]) -> None:
    remote_map = {
        "CGSil1": str(sandbox["CGSil1_remote"]),
        "CGSil2": str(sandbox["CGSil2_remote"]),
        "CGSih1": str(sandbox["CGSih1_remote"]),
    }
    monkeypatch.setattr(
        "ComplexGitSync.orchestre.ComplexGitSyncClient._build_remote_url",
        lambda self, entry: remote_map[entry.name],
    )


def _prepare_existing_root(output_path: Path, sandbox: dict[str, Path]) -> Path:
    output_path.mkdir(parents=True, exist_ok=True)
    project_root = output_path / "CGSil1"
    subprocess.run(
        ["git", "clone", str(sandbox["CGSil1_remote"]), str(project_root)],
        check=True,
        capture_output=True,
    )
    return project_root


def _patch_git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "memory-demo@complexgitsync.test")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Memory Demo")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "memory-demo@complexgitsync.test")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Memory Demo")


def _run_git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _parse_state_directory(state_path: Path) -> tuple[str, int]:
    match = re.fullmatch(r"state\(([0-9a-f]{64})\)_(\d+)", state_path.name)
    if match is None:
        raise AssertionError(f"Expected canonical Memory State directory: {state_path.name}")
    return match.group(1), int(match.group(2))


def _assert_complete_cgsil1_state(state_path: Path) -> None:
    assert state_path.is_dir()
    for suffix in ("cgs", "gts", "log", "lgr"):
        assert (state_path / f"CGSil1.{suffix}").is_file()


def _read_cgsil1_artifacts(state_path: Path) -> dict[str, bytes]:
    return {
        suffix: (state_path / f"CGSil1.{suffix}").read_bytes()
        for suffix in ("cgs", "gts", "log", "lgr")
    }


def _hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root)
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()

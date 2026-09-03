"""Integration coverage: discovery has no default depth bound.

See ``AgentSpec/archive/20260904_MaxDepthAutodetect_DevPlanTicket.md``.
``_walk_git_repositories`` is exhaustively unit-tested for the unbounded
walk itself (``tests/unit/test_walk_git_repositories.py``); this file
proves the same holds through the real client methods end to end, against
real git — no ``--max-depth`` passed, a repository well past the old
default of 5 levels is still found and adopted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ComplexGitSync.orchestre import ComplexGitSyncClient

# One deeper than the old default of 5, to prove there is no longer any
# implicit bound — not chosen to be dramatic, just past the old ceiling.
DEEP_SEGMENTS = ("a", "b", "c", "d", "e", "f", "g")


def _git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args],
        cwd=repo_path,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _seed_remote(tmp_path: Path, name: str) -> Path:
    remote = tmp_path / f"{name}.git"
    _git(tmp_path, "init", "--bare", "-q", "-b", "main", remote.as_posix())

    seed = tmp_path / f"seed-{name}"
    seed.mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", seed.as_posix())
    _git(seed, "config", "user.email", "integration@complexgitsync.test")
    _git(seed, "config", "user.name", "ComplexGitSync Integration")
    (seed / "README.md").write_text(f"{name}\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-qm", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "-q", "-u", "origin", "main")
    return remote


class TestDiscoverHasNoDefaultBound:
    def test_finds_a_repository_seven_levels_down_with_no_max_depth_flag(self, tmp_path):
        root_remote = _seed_remote(tmp_path, "root")
        deep_remote = _seed_remote(tmp_path, "deep-child")

        root = tmp_path / "root"
        _git(tmp_path, "clone", "-q", root_remote.as_posix(), root.as_posix())

        deep_path = root
        for segment in DEEP_SEGMENTS:
            deep_path = deep_path / segment
        deep_path.mkdir(parents=True)
        _git(tmp_path, "clone", "-q", deep_remote.as_posix(), deep_path.as_posix())

        report = ComplexGitSyncClient().discover_repos(root)

        # Local file:// remotes never resolve to a known provider, so a
        # warning about *that* is expected — what matters here is that the
        # walk itself reached this deep without a --max-depth flag: no
        # "scan stopped at --max-depth" warning, and the path was found.
        found_paths = {repo.absolute_path for repo in report.repos}
        assert deep_path in found_paths
        assert not any("stopped at --max-depth" in warning for warning in report.warnings)

    def test_max_depth_three_misses_the_same_repository(self, tmp_path):
        root_remote = _seed_remote(tmp_path, "root")
        deep_remote = _seed_remote(tmp_path, "deep-child")

        root = tmp_path / "root"
        _git(tmp_path, "clone", "-q", root_remote.as_posix(), root.as_posix())

        deep_path = root
        for segment in DEEP_SEGMENTS:
            deep_path = deep_path / segment
        deep_path.mkdir(parents=True)
        _git(tmp_path, "clone", "-q", deep_remote.as_posix(), deep_path.as_posix())

        report = ComplexGitSyncClient().discover_repos(root, max_depth=3)

        found_paths = {repo.absolute_path for repo in report.repos}
        assert deep_path not in found_paths
        assert any("max-depth" in warning for warning in report.warnings)


class TestInitFromSubmodulesHasNoDefaultBound:
    def test_adopts_a_submodule_declared_seven_levels_down(self, tmp_path, monkeypatch):
        root_remote = _seed_remote(tmp_path, "root")
        child_remote = _seed_remote(tmp_path, "deep-child")

        submodule_path = "/".join(DEEP_SEGMENTS)
        seed_root = tmp_path / "seed-root"
        _git(seed_root, "submodule", "add", "-q", child_remote.as_posix(), submodule_path)
        _git(seed_root, "commit", "-qm", "add deep submodule")
        _git(seed_root, "push", "-q", "origin", "main")

        work = tmp_path / "work"
        work.mkdir()
        root = work / "root"
        _git(work, "clone", "-q", root_remote.as_posix(), root.as_posix())
        _git(root, "submodule", "update", "--init", "--recursive", "-q")
        for repo in (root, root / submodule_path):
            _git(repo, "config", "user.email", "integration@complexgitsync.test")
            _git(repo, "config", "user.name", "ComplexGitSync Integration")

        from ComplexGitSync import orchestre

        def _fake_identifier(url: str) -> str:
            name = Path(url).name.removesuffix(".git")
            return f"github:test/{name}" if name in ("root", "deep-child") else url

        monkeypatch.setattr(orchestre, "_url_to_repo_identifier", _fake_identifier)
        client = ComplexGitSyncClient()
        by_name = {"root": root_remote, "deep-child": child_remote}
        monkeypatch.setattr(client, "_build_remote_url", lambda entry: by_name[entry.name].as_posix())
        monkeypatch.chdir(tmp_path)

        report = client.init_from_submodules(root)

        assert report.tree is not None and report.tree.is_ready()
        assert report.import_report is not None
        assert [sub.path for sub in report.import_report.submodules] == [submodule_path]
        assert not (root / ".gitmodules").is_file()
        assert (root / submodule_path / ".git").is_dir()

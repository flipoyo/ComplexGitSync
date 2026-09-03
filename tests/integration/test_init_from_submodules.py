"""Integration tests for ``init-from-submodules`` — Tutorial 3's steps 3-5.

The fixture builds the same shape as ``cawaqsviz``: a root repository, a
submodule inside it, and a submodule inside *that* one. Every remote is a
local bare repository, so nothing here touches the network.

Two seams translate between those local remotes and the
``provider:owner/repository`` addresses a ``.cgs`` speaks, since no local
path can ever map to a real provider:

* ``_url_to_repo_identifier`` — what ``discover`` reads *from* each
  checkout's ``origin``;
* ``_build_remote_url`` — what the clone step writes *to* git.

Everything between them (the ordering, the conversion, the ``.gitignore``
lifecycle, the resulting tree) is the real code path against real git.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync import GitSyncError
from ComplexGitSync.orchestre import ComplexGitSyncClient

REPO_NAMES = ("root", "hta", "twin")


def _git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        # protocol.file.allow is required from git 2.38 on for a submodule
        # whose URL is a local path, which is the only kind a hermetic test
        # can use.
        ["git", "-c", "protocol.file.allow=always", *args],
        cwd=repo_path,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _seed(tmp_path: Path, name: str) -> Path:
    """Create a bare remote for *name* holding one commit on ``main``."""
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


@pytest.fixture()
def submodule_tree(tmp_path: Path) -> dict[str, Path]:
    """A two-level submodule tree, checked out the way Tutorial 3 step 2 leaves it."""
    remotes = {name: _seed(tmp_path, name) for name in REPO_NAMES}

    _git(tmp_path / "seed-hta", "submodule", "add", "-q", remotes["twin"].as_posix(), "docs/twin")
    _git(tmp_path / "seed-hta", "commit", "-qm", "add twin submodule")
    _git(tmp_path / "seed-hta", "push", "-q", "origin", "main")

    _git(tmp_path / "seed-root", "submodule", "add", "-q", remotes["hta"].as_posix(), "external/HTA")
    _git(tmp_path / "seed-root", "commit", "-qm", "add hta submodule")
    _git(tmp_path / "seed-root", "push", "-q", "origin", "main")

    # Tutorial 3 steps 1-2: the user's own clone, submodules checked out.
    work = tmp_path / "work"
    work.mkdir()
    root = work / "root"
    _git(work, "clone", "-q", remotes["root"].as_posix(), root.as_posix())
    _git(root, "submodule", "update", "--init", "--recursive", "-q")
    for repo in (root, root / "external/HTA", root / "external/HTA/docs/twin"):
        _git(repo, "config", "user.email", "integration@complexgitsync.test")
        _git(repo, "config", "user.name", "ComplexGitSync Integration")

    return {"root": root, "tmp_path": tmp_path, **{f"{n}_remote": remotes[n] for n in REPO_NAMES}}


@pytest.fixture()
def client(submodule_tree, monkeypatch) -> ComplexGitSyncClient:
    """A client whose provider addresses resolve to the fixture's local remotes."""
    tmp_path = submodule_tree["tmp_path"]
    by_name = {name: submodule_tree[f"{name}_remote"] for name in REPO_NAMES}

    def _fake_identifier(url: str) -> str:
        name = Path(url).name.removesuffix(".git")
        return f"github:test/{name}" if name in by_name else url

    from ComplexGitSync import orchestre

    monkeypatch.setattr(orchestre, "_url_to_repo_identifier", _fake_identifier)
    instance = ComplexGitSyncClient()
    monkeypatch.setattr(
        instance,
        "_build_remote_url",
        lambda entry: by_name[entry.name].as_posix(),
    )
    monkeypatch.chdir(tmp_path)
    return instance


def _gitmodules_under(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob(".gitmodules") if ".cgitsync" not in p.parts)


def _tracked_at(repo: Path, path: str) -> str:
    return _git(repo, "ls-files", "-s", path)


class TestInitFromSubmodules:
    def test_dry_run_changes_nothing(self, submodule_tree, client):
        root = submodule_tree["root"]

        report = client.init_from_submodules(root, dry_run=True)

        assert report.dry_run is True
        assert report.cgs_written is False
        assert report.tree is None
        # Both levels are reported...
        assert {sub.path for sub in report.import_report.submodules} == {"external/HTA", "docs/twin"}
        # ...and nothing on disk moved.
        assert not list(root.glob("*.cgs"))
        assert len(_gitmodules_under(root)) == 2

    def test_adopts_the_tree_and_converts_both_levels(self, submodule_tree, client):
        root = submodule_tree["root"]

        report = client.init_from_submodules(root)

        assert report.cgs_written is True
        assert report.cgs_path == root / "root.cgs"
        assert report.tree is not None and report.tree.is_ready()

        # 1. No .gitmodules survives anywhere in the tree.
        assert _gitmodules_under(root) == []

        # 2. No gitlink is tracked at either level any more.
        hta = root / "external/HTA"
        assert _tracked_at(root, "external/HTA") == ""
        assert _tracked_at(hta, "docs/twin") == ""

        # 3. Each holder ignores its own child instead.
        assert "external/HTA" in (root / ".gitignore").read_text(encoding="utf-8")
        assert "docs/twin" in (hta / ".gitignore").read_text(encoding="utf-8")

        # 4. Both children are real, independent clones with their own history.
        assert (hta / ".git").is_dir()
        assert (hta / "docs/twin/.git").is_dir()

    def test_conversion_is_staged_in_every_holder(self, submodule_tree, client):
        root = submodule_tree["root"]

        client.init_from_submodules(root)

        for repo in (root, root / "external/HTA"):
            staged = _git(repo, "status", "--porcelain")
            assert "D  .gitmodules" in staged, staged

    def test_tree_records_the_nested_parent(self, submodule_tree, client):
        root = submodule_tree["root"]

        report = client.init_from_submodules(root)

        names = {entry.name: entry for entry in report.tree.values()}
        assert names["twin"].parent_id == names["hta"].repo_id
        assert names["twin"].absolute_path == root / "external/HTA/docs/twin"

    def test_supplied_cgs_is_reused_instead_of_written(self, submodule_tree, client):
        root = submodule_tree["root"]
        spec = submodule_tree["tmp_path"] / "handwritten.cgs"
        spec.write_text(
            'project = "root"\n'
            "repos = [\n"
            '    "github:test/root",\n'
            '    { repository = "github:test/hta", relative_path = "external/HTA" },\n'
            '    { repository = "github:test/twin", relative_path = "external/HTA/docs/twin" },\n'
            "]\n",
            encoding="utf-8",
        )

        report = client.init_from_submodules(root, cgs_path=spec)

        assert report.cgs_written is False
        assert report.cgs_path == spec
        assert not list(root.glob("*.cgs"))
        assert _gitmodules_under(root) == []

    def test_second_run_is_refused_rather_than_reclonig_children(self, submodule_tree, client):
        root = submodule_tree["root"]
        client.init_from_submodules(root)

        # A file only the local clone has: if the guard let the run through,
        # the child would be deleted and re-cloned, taking this with it.
        scratch = root / "external/HTA/uncommitted.txt"
        scratch.write_text("work in progress\n", encoding="utf-8")

        with pytest.raises(GitSyncError, match="nothing to convert"):
            client.init_from_submodules(root)

        assert scratch.is_file()

    def test_directory_name_mismatch_is_refused_before_anything_is_written(
        self, submodule_tree, client
    ):
        root = submodule_tree["root"]
        renamed = root.parent / "not-root"
        root.rename(renamed)

        with pytest.raises(GitSyncError, match="directory is named 'not-root'"):
            client.init_from_submodules(renamed)

        assert not list(renamed.glob("*.cgs"))
        assert len(_gitmodules_under(renamed)) == 2


class TestOrderingRegression:
    """Why the conversion must come *after* initialise, not before.

    Both halves of the ticket's §0, proved against real git rather than
    argued: converting first is undone by the clone step, and a second
    conversion pass cannot repair it.
    """

    def test_converting_before_initialise_is_undone_by_the_clone_step(
        self, submodule_tree, client
    ):
        root = submodule_tree["root"]
        hta = root / "external/HTA"

        # Tutorial 3's original order: convert first...
        client.import_submodules(root, apply=True, recursive=True)
        assert _gitmodules_under(root) == []

        # ...then initialise, which re-clones every non-root repository.
        client.discover_repos(root, max_depth=5, output=root / "root.cgs")
        client.initialise_cgs(root / "root.cgs", output_path=root.parent)

        # HTA came back from its remote with its submodule wiring intact.
        assert (hta / ".gitmodules").is_file()
        assert _tracked_at(hta, "docs/twin") != ""

    def test_a_second_conversion_pass_cannot_reach_the_deeper_level(
        self, submodule_tree, client
    ):
        root = submodule_tree["root"]
        hta = root / "external/HTA"

        client.import_submodules(root, apply=True, recursive=True)
        client.discover_repos(root, max_depth=5, output=root / "root.cgs")
        client.initialise_cgs(root / "root.cgs", output_path=root.parent)

        # The root has no .gitmodules any more, so the recursive walk has
        # no submodule graph to follow and never descends into HTA.
        repair = client.import_submodules(root, apply=True, recursive=True)

        assert repair.submodules == ()
        assert (hta / ".gitmodules").is_file()


"""The install frontier: ``initialise`` is the nested install, ``bootstrap`` the standalone one.

Each test builds real repositories on disk (bare "remotes" and their seeds) and
routes the client's remote URLs to them, so what is exercised is the real clone,
checkout and refusal path — never a fake runner. See ``AdditionalSpecs.md``,
*The install frontier*.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync import ComplexGitSyncClient
from ComplexGitSync.errors import InstallFrontierError
from ComplexGitSync.settings import Settings, UseCase


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _seed(tmp_path: Path, name: str, *, branches: tuple[str, ...] = ("main",)) -> tuple[Path, Path]:
    """A bare remote called *name* and the seed checkout that fed it."""
    remote = tmp_path / f"{name}-remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.email", "t@example.test")
    _git(seed, "config", "user.name", "Tester")
    (seed / "README.md").write_text(f"{name}\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "-u", "origin", "main")
    for branch in branches:
        if branch != "main":
            _git(seed, "push", "origin", f"main:refs/heads/{branch}")
    return remote, seed


def _write_cgs(path: Path, *, project: str = "demo", private_dependency: bool = False) -> Path:
    private = ", private = true, writable = true" if private_dependency else ""
    path.write_text(
        f"""
project = {{ name = "{project}", default_branch = "main" }}
repos = [
    {{ repository = "github:owner/{project}", relative_path = "." }},
    {{ repository = "github:owner/leaf", relative_path = "deps/leaf"{private} }},
]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _client(monkeypatch, remotes: dict[str, Path]) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    monkeypatch.setattr(client, "_build_remote_url", lambda entry: str(remotes[entry.project_name]))
    return client


def _checkout_root(cgshome: Path, remote: Path) -> None:
    """Stand in for the checkout a nested install starts from."""
    cgshome.parent.mkdir(parents=True, exist_ok=True)
    _git(cgshome.parent, "clone", remote.as_posix(), cgshome.as_posix())


def _states(root: Path) -> list[Path]:
    return sorted((root / ".cgitsync" / "state").rglob("*"))


# ---------------------------------------------------------------------------
# WP1 — refuse before touching the disk
# ---------------------------------------------------------------------------


def test_initialise_refuses_a_root_that_is_not_a_checkout_and_clones_nothing(tmp_path, monkeypatch):
    root_remote, _ = _seed(tmp_path, "demo")
    leaf_remote, _ = _seed(tmp_path, "leaf")
    cgs = _write_cgs(tmp_path / "demo.cgs")
    cgshome = tmp_path / "ws" / "demo"
    cgshome.mkdir(parents=True)
    client = _client(monkeypatch, {"demo": root_remote, "leaf": leaf_remote})

    with pytest.raises(InstallFrontierError, match="bootstrap") as refused:
        client.initialise_cgs(cgs, output_path=tmp_path / "ws")

    assert "is not a git repository" in str(refused.value)
    assert not (cgshome / "deps").exists()
    assert sorted(cgshome.iterdir()) == []


def test_initialise_still_accepts_a_root_on_a_detached_head(tmp_path, monkeypatch):
    root_remote, _ = _seed(tmp_path, "demo")
    leaf_remote, _ = _seed(tmp_path, "leaf")
    cgs = _write_cgs(tmp_path / "demo.cgs")
    cgshome = tmp_path / "ws" / "demo"
    _checkout_root(cgshome, root_remote)
    _git(cgshome, "checkout", "--detach")
    client = _client(monkeypatch, {"demo": root_remote, "leaf": leaf_remote})

    registry = client.initialise_cgs(cgs, output_path=tmp_path / "ws")

    assert registry.is_ready()
    assert (cgshome / "deps" / "leaf" / ".git").exists()


@pytest.mark.real_use_case
def test_initialise_from_a_standalone_installation_refuses_and_names_bootstrap(tmp_path, monkeypatch):
    root_remote, _ = _seed(tmp_path, "demo")
    leaf_remote, _ = _seed(tmp_path, "leaf")
    cgs = _write_cgs(tmp_path / "demo.cgs")
    cgshome = tmp_path / "ws" / "demo"
    _checkout_root(cgshome, root_remote)
    client = _client(monkeypatch, {"demo": root_remote, "leaf": leaf_remote})

    with pytest.raises(InstallFrontierError, match="bootstrap"):
        client.initialise_cgs(cgs, output_path=tmp_path / "ws")

    assert not (cgshome / "deps").exists()


def test_a_nested_installation_is_the_one_settings_recognises(tmp_path):
    workspace = tmp_path / "ws" / "demo"
    installation = workspace / "ComplexGitSync" / "src" / "ComplexGitSync"
    installation.mkdir(parents=True)

    assert Settings.resolve_use_case(workspace, installation=installation) is UseCase.NESTED
    assert Settings.resolve_use_case(tmp_path / "elsewhere", installation=installation) is UseCase.STANDALONE


def test_bootstrap_refuses_a_target_that_already_holds_a_checkout(tmp_path, monkeypatch):
    root_remote, _ = _seed(tmp_path, "demo")
    leaf_remote, _ = _seed(tmp_path, "leaf")
    cgs = _write_cgs(tmp_path / "demo.cgs")
    target = tmp_path / "cgspath" / "demo"
    _checkout_root(target, root_remote)
    client = _client(monkeypatch, {"demo": root_remote, "leaf": leaf_remote})

    with pytest.raises(InstallFrontierError, match="initialise"):
        client.bootstrap(cgs, "demo", cgs_path=tmp_path / "cgspath")

    assert not (target / "deps").exists()


def test_bootstrap_refuses_a_target_that_is_not_empty_and_is_not_a_checkout(tmp_path, monkeypatch):
    root_remote, _ = _seed(tmp_path, "demo")
    leaf_remote, _ = _seed(tmp_path, "leaf")
    cgs = _write_cgs(tmp_path / "demo.cgs")
    target = tmp_path / "cgspath" / "demo"
    (target / "docs").mkdir(parents=True)
    client = _client(monkeypatch, {"demo": root_remote, "leaf": leaf_remote})

    with pytest.raises(InstallFrontierError, match="not empty"):
        client.bootstrap(cgs, "demo", cgs_path=tmp_path / "cgspath")

    assert [path.name for path in target.iterdir()] == ["docs"]


# ---------------------------------------------------------------------------
# WP3 / WP4 — one branch rule, applied before the first clone
# ---------------------------------------------------------------------------


def test_a_private_writable_dependency_clones_with_no_project_branch_nested(tmp_path, monkeypatch):
    """The shared repository has no branch named for the project yet: it falls back to its own."""
    root_remote, _ = _seed(tmp_path, "demo")
    leaf_remote, _ = _seed(tmp_path, "leaf", branches=("trunk",))
    _git(leaf_remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
    _git(leaf_remote, "branch", "-D", "main")
    cgs = _write_cgs(tmp_path / "demo.cgs", private_dependency=True)
    cgshome = tmp_path / "ws" / "demo"
    _checkout_root(cgshome, root_remote)
    client = _client(monkeypatch, {"demo": root_remote, "leaf": leaf_remote})

    registry = client.initialise_cgs(cgs, output_path=tmp_path / "ws")

    assert registry.is_ready()
    assert _git(cgshome / "deps" / "leaf", "symbolic-ref", "--short", "HEAD") == "trunk"


def test_a_private_writable_dependency_clones_with_no_project_branch_standalone(tmp_path, monkeypatch):
    root_remote, _ = _seed(tmp_path, "demo")
    leaf_remote, _ = _seed(tmp_path, "leaf", branches=("trunk",))
    _git(leaf_remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
    _git(leaf_remote, "branch", "-D", "main")
    cgs = _write_cgs(tmp_path / "demo.cgs", private_dependency=True)
    client = _client(monkeypatch, {"demo": root_remote, "leaf": leaf_remote})

    registry = client.bootstrap(cgs, "demo", cgs_path=tmp_path / "cgspath")

    assert registry.is_ready()
    leaf = tmp_path / "cgspath" / "demo" / "deps" / "leaf"
    assert _git(leaf, "symbolic-ref", "--short", "HEAD") == "trunk"


def test_a_private_writable_dependency_takes_the_computed_branch_when_it_exists(tmp_path, monkeypatch):
    root_remote, _ = _seed(tmp_path, "demo")
    leaf_remote, _ = _seed(tmp_path, "leaf", branches=("demo",))
    cgs = _write_cgs(tmp_path / "demo.cgs", private_dependency=True)
    cgshome = tmp_path / "ws" / "demo"
    _checkout_root(cgshome, root_remote)
    client = _client(monkeypatch, {"demo": root_remote, "leaf": leaf_remote})

    client.initialise_cgs(cgs, output_path=tmp_path / "ws")

    assert _git(cgshome / "deps" / "leaf", "symbolic-ref", "--short", "HEAD") == "demo"


def test_a_hand_typed_branch_that_disagrees_with_the_computed_one_is_rejected(tmp_path):
    from ComplexGitSync.cgs_format import CgsDocument
    from ComplexGitSync.errors import ConfigValidationError

    cgs = tmp_path / "demo.cgs"
    cgs.write_text(
        """
project = { name = "demo", default_branch = "main" }
repos = [
    { repository = "github:owner/demo", relative_path = "." },
    { repository = "github:owner/hub", relative_path = "hub", default_branch = "someone-else", private = true, writable = true },
]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError, match="'demo'"):
        CgsDocument.from_toml(cgs)


# ---------------------------------------------------------------------------
# WP6 — a State is a .gts
# ---------------------------------------------------------------------------


def test_a_state_area_holds_gts_files_only(tmp_path, monkeypatch):
    root_remote, _ = _seed(tmp_path, "demo")
    leaf_remote, _ = _seed(tmp_path, "leaf")
    cgs = _write_cgs(tmp_path / "demo.cgs")
    client = _client(monkeypatch, {"demo": root_remote, "leaf": leaf_remote})

    client.bootstrap(cgs, "demo", cgs_path=tmp_path / "cgspath")

    root = tmp_path / "cgspath" / "demo"
    files = [path for path in _states(root) if path.is_file()]
    assert files, "a State should have been written"
    assert {path.suffix for path in files} == {".gts"}
    assert not (root / ".cgitsync" / ".cgs").exists()


# ---------------------------------------------------------------------------
# WP7 — a .gts is an input to both commands
# ---------------------------------------------------------------------------


def _snapshot_of(root: Path) -> Path:
    return sorted((root / ".cgitsync" / "state").rglob("*.gts"))[-1]


def test_bootstrap_from_a_gts_rebuilds_the_tree_with_the_same_state_name(tmp_path, monkeypatch):
    root_remote, _ = _seed(tmp_path, "demo")
    leaf_remote, leaf_seed = _seed(tmp_path, "leaf")
    cgs = _write_cgs(tmp_path / "demo.cgs")
    remotes = {"demo": root_remote, "leaf": leaf_remote}
    first = _client(monkeypatch, remotes)
    first.bootstrap(cgs, "demo", cgs_path=tmp_path / "one")
    original = _snapshot_of(tmp_path / "one" / "demo")
    recorded = _git(tmp_path / "one" / "demo" / "deps" / "leaf", "rev-parse", "HEAD")

    # The remote moves on; the snapshot must still rebuild the tree as it was.
    (leaf_seed / "later.txt").write_text("later\n", encoding="utf-8")
    _git(leaf_seed, "add", "later.txt")
    _git(leaf_seed, "commit", "-m", "later")
    _git(leaf_seed, "push", "origin", "main")

    second = _client(monkeypatch, remotes)
    registry = second.bootstrap(original, "demo", cgs_path=tmp_path / "two")

    assert registry.is_ready()
    rebuilt_leaf = tmp_path / "two" / "demo" / "deps" / "leaf"
    assert _git(rebuilt_leaf, "rev-parse", "HEAD") == recorded
    assert _snapshot_of(tmp_path / "two" / "demo").name == original.name


def test_initialise_from_a_gts_pins_each_dependency_to_its_recorded_commit(tmp_path, monkeypatch):
    root_remote, _ = _seed(tmp_path, "demo")
    leaf_remote, leaf_seed = _seed(tmp_path, "leaf")
    cgs = _write_cgs(tmp_path / "demo.cgs")
    remotes = {"demo": root_remote, "leaf": leaf_remote}
    first = _client(monkeypatch, remotes)
    first.bootstrap(cgs, "demo", cgs_path=tmp_path / "one")
    original = _snapshot_of(tmp_path / "one" / "demo")
    recorded = _git(tmp_path / "one" / "demo" / "deps" / "leaf", "rev-parse", "HEAD")

    (leaf_seed / "later.txt").write_text("later\n", encoding="utf-8")
    _git(leaf_seed, "add", "later.txt")
    _git(leaf_seed, "commit", "-m", "later")
    _git(leaf_seed, "push", "origin", "main")

    cgshome = tmp_path / "ws" / "demo"
    _checkout_root(cgshome, root_remote)
    nested = _client(monkeypatch, remotes)

    registry = nested.initialise(original, output_path=tmp_path / "ws")

    assert registry.is_ready()
    assert _git(cgshome / "deps" / "leaf", "rev-parse", "HEAD") == recorded
    assert _snapshot_of(cgshome).name == original.name


def test_a_gts_whose_commit_the_remote_no_longer_holds_is_refused_before_cloning(tmp_path, monkeypatch):
    root_remote, _ = _seed(tmp_path, "demo")
    leaf_remote, leaf_seed = _seed(tmp_path, "leaf")
    cgs = _write_cgs(tmp_path / "demo.cgs")
    remotes = {"demo": root_remote, "leaf": leaf_remote}
    first = _client(monkeypatch, remotes)
    first.bootstrap(cgs, "demo", cgs_path=tmp_path / "one")
    original = _snapshot_of(tmp_path / "one" / "demo")

    # Rewrite the leaf's history: the recorded commit is gone from the remote.
    _git(leaf_seed, "commit", "--amend", "-m", "rewritten")
    _git(leaf_seed, "push", "--force", "origin", "main")
    _git(leaf_remote, "gc", "--prune=now")

    second = _client(monkeypatch, remotes)
    with pytest.raises(InstallFrontierError, match="leaf"):
        second.bootstrap(original, "demo", cgs_path=tmp_path / "two")

    assert not (tmp_path / "two" / "demo").exists()


# ---------------------------------------------------------------------------
# A standalone install administers a workspace that holds a nested ComplexGitSync
# ---------------------------------------------------------------------------


@pytest.mark.real_use_case
def test_a_standalone_install_treats_a_nested_complexgitsync_as_one_repository(tmp_path, monkeypatch):
    root_remote, _ = _seed(tmp_path, "demo")
    nested_remote, _ = _seed(tmp_path, "ComplexGitSync")
    cgs = tmp_path / "demo.cgs"
    cgs.write_text(
        """
project = { name = "demo", default_branch = "main" }
repos = [
    { repository = "github:owner/demo", relative_path = "." },
    { repository = "github:flipoyo/ComplexGitSync", relative_path = "ComplexGitSync" },
]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    client = _client(monkeypatch, {"demo": root_remote, "ComplexGitSync": nested_remote})
    installation = Path(__import__("ComplexGitSync").__file__).resolve().parent
    before = subprocess.run(
        ["git", "status", "--porcelain", "--", str(installation)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout

    registry = client.bootstrap(cgs, "demo", cgs_path=tmp_path / "cgspath")

    workspace = tmp_path / "cgspath" / "demo"
    assert registry.is_ready()
    assert Settings.resolve_use_case(workspace) is UseCase.STANDALONE
    assert (workspace / "ComplexGitSync" / ".git").exists()
    assert "ComplexGitSync" in {entry.name for entry in registry.values()}
    # Neither installation writes into the other's.
    after = subprocess.run(
        ["git", "status", "--porcelain", "--", str(installation)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert after == before


def test_verify_is_quiet_about_a_spec_copy_an_older_memory_left_beside_a_state(tmp_path, monkeypatch):
    root_remote, _ = _seed(tmp_path, "demo")
    leaf_remote, _ = _seed(tmp_path, "leaf")
    cgs = _write_cgs(tmp_path / "demo.cgs")
    client = _client(monkeypatch, {"demo": root_remote, "leaf": leaf_remote})
    client.bootstrap(cgs, "demo", cgs_path=tmp_path / "cgspath")
    root = tmp_path / "cgspath" / "demo"
    baseline = client.verify(root)
    state = _snapshot_of(root)
    state.with_suffix(".cgs").write_text(cgs.read_text(encoding="utf-8"), encoding="utf-8")
    (root / ".cgitsync" / ".cgs").mkdir()
    (root / ".cgitsync" / ".cgs" / "demo-main.cgs").write_text("old\n", encoding="utf-8")

    report = ComplexGitSyncClient().verify(root)

    assert report.findings == baseline.findings

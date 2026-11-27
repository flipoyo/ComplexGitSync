"""MergeDataEncrypt: the features of the `data-encrypt` line and of `HT-BETA` that were never built against each other.

AnonymousProject (an alias in place of the project's name), EncryptedRepos (the `CRYPT` column) and the work of
`HT-BETA` (RootBareBranch's `<project>_<branch>`, BranchMissingEventName, DataSchema's integrity schema 2,
CheckoutBareTip) on real repositories and local bare remotes. Nothing here needs the `git-crypt` program: a repository
is *encrypted* when its `.gitattributes` asks for it, and *locked* until the filter is configured.
"""

from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path

import pytest
from test_anonymous_project import SECRET, _git, _holds, _seed, _World

from ComplexGitSync import ComplexGitSyncClient
from ComplexGitSync.orchestre.installer import Installer
from ComplexGitSync.tree.cgs_format import CgsDocument
from ComplexGitSync.tree.gts_document import GtsDocument
from ComplexGitSync.tree.settings import UseCase

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Tester")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@example.test")


def _seed_with(tmp_path: Path, name: str, *, files: dict[str, str] | None = None, dev_commit: bool = False) -> Path:
    """A bare remote on `main`, with *files* in it, and a `dev` branch of its own when *dev_commit*."""
    remote = tmp_path / f"{name}-remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    for path, text in {"README.md": f"{name}\n", **(files or {})}.items():
        (seed / path).parent.mkdir(parents=True, exist_ok=True)
        (seed / path).write_text(text, encoding="utf-8")
        _git(seed, "add", path)
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "origin", "main")
    if dev_commit:
        _git(seed, "checkout", "-q", "-b", "dev")
        (seed / "dev.txt").write_text("dev\n", encoding="utf-8")
        _git(seed, "add", "dev.txt")
        _git(seed, "commit", "-m", "work on dev")
        _git(seed, "push", "origin", "dev")
        _git(seed, "checkout", "-q", "main")
    return remote


def _bootstrap(world: _World, monkeypatch, name: str, cgs_text: str):
    cgs = world.tmp_path / f"{name}.cgs"
    cgs.write_text(cgs_text, encoding="utf-8")
    monkeypatch.setattr(Installer, "_use_case_of", lambda self, cgshome: UseCase.STANDALONE)
    client = ComplexGitSyncClient()
    monkeypatch.setattr(client, "_build_remote_url", lambda entry: str(world.remotes[entry.project_name]))
    registry = client.bootstrap(cgs, name, cgs_path=world.tmp_path / f"ws-{name}")
    return client, registry, world.tmp_path / f"ws-{name}" / name


def _project(name: str, *, anonymous: bool, extra: str = "", lib_extra: str = "") -> str:
    option = ', anonymous = "sequence"' if anonymous else ""
    return f"""
project = {{ name = "{name}", default_branch = "main"{option} }}
repos = [
    {{ repository = "github:owner/{name}", relative_path = ".", nested_config = "disabled" }},
    {{ repository = "github:owner/lib", relative_path = "lib", fallback_branch = "main", nested_config = "disabled"{lib_extra} }},
    {{ repository = "github:owner/.localSpec", relative_path = ".agent/.local/.localSpec", fallback_branch = "main", private = true, writable = true }},
    {{ repository = "github:owner/.memory", relative_path = ".cgitsync/.memory", fallback_branch = "main", private = true, writable = true, nested_config = "disabled" }},{extra}
]
""".lstrip()


def _world(tmp_path: Path, root: str, *, lib_files: dict[str, str] | None = None, spec_dev: bool = False, lib_dev: bool = True) -> _World:
    world = _World(tmp_path, None)
    world.remotes[root] = _seed(tmp_path, "root")
    world.remotes["lib"] = _seed_with(tmp_path, "lib", files=lib_files, dev_commit=lib_dev)
    world.remotes[".localSpec"] = _seed_with(tmp_path, "spec", dev_commit=spec_dev)
    return world


def _latest_state(tmp_path: Path) -> dict:
    latest = max(tmp_path.rglob("*.gts"), key=lambda path: path.stat().st_mtime)
    return tomllib.loads(latest.read_text(encoding="utf-8"))


def test_an_anonymous_trees_shared_repositories_take_the_alias_on_every_branch_rule(tmp_path, monkeypatch):
    """§2.1: the library is on `<alias>_dev` at the bare tip, the private mount too with the claim, the name nowhere."""
    world = _world(tmp_path, SECRET, spec_dev=True)
    client, registry, root = _bootstrap(world, monkeypatch, SECRET, _project(SECRET, anonymous=True))
    monkeypatch.chdir(root)

    client.branch("dev")
    client.checkout("dev")

    assert registry.project_name == "anonymous-1"
    assert _git(root / "lib", "branch", "--show-current") == "anonymous-1_dev"
    assert _git(root / "lib", "rev-parse", "HEAD") == _git(world.remotes["lib"], "rev-parse", "dev")  # RootBareBranch + CheckoutBareTip
    spec = root / ".agent/.local/.localSpec"
    assert _git(spec, "branch", "--show-current") == "anonymous-1_dev"
    assert _git(spec, "show", "HEAD:.cgitsync-alias") != ""  # a branch begun at the bare tip carries the claim too
    assert _git(spec, "merge-base", "--is-ancestor", "origin/dev", "HEAD") == ""  # it began at the bare `dev`
    for repo in (root / "lib", spec, root / ".cgitsync/.memory"):
        assert _holds(repo, SECRET) == [], repo


def test_the_branch_missing_report_names_the_alias_never_the_name(tmp_path, monkeypatch):
    """§2.1 again: a library with no `dev` waits for it, and what the report holds names the alias only."""
    world = _world(tmp_path, SECRET, lib_dev=False)
    client, registry, _ = _bootstrap(world, monkeypatch, SECRET, _project(SECRET, anonymous=True))

    waiting = {entry["repo"]: entry for entry in client.missing_branches}

    assert registry.project_name == "anonymous-1"
    assert "lib" in waiting and waiting["lib"]["wanted"] == "anonymous-1"
    # `path` is the user's own workspace folder, which may carry any name; what is reported to a register never does.
    assert all(SECRET not in str(value) for entry in waiting.values() for key, value in entry.items() if key != "path")


def test_a_status_shows_the_crypt_column_beside_a_repository_waiting_for_its_branch(tmp_path, monkeypatch):
    """§2.3: `CRYPT` (EncryptedRepos) and the waiting-branches report (BranchMissingEventName) in one tree."""
    attributes = {".gitattributes": "data/** filter=git-crypt diff=git-crypt\n", "data/keep.csv": "synthetic\n"}
    world = _world(tmp_path, "demo", lib_files=attributes, lib_dev=False)  # `main` only: `demo_dev` is missing in lib
    client, _, root = _bootstrap(world, monkeypatch, "demo", _project("demo", anonymous=False).replace('default_branch = "main"', 'default_branch = "dev"'))

    text = client.status()
    rows = json.loads(client.status_json())

    assert "CRYPT" in text
    assert any(entry["repo"] == "lib" and entry["wanted"] == "demo_dev" for entry in client.missing_branches)
    flat = json.dumps(rows)
    assert '"crypt": "locked"' in flat  # the JSON carries both the crypt field and the waiting branch
    assert "demo_dev" in flat or "missing" in flat


def test_an_anonymous_state_keeps_schema_one_and_its_new_fields_stay_outside_the_hash(tmp_path, monkeypatch):
    """§2.2: `project.anonymous` and `project.claim` are not in the State's name; only the alias is."""
    world = _world(tmp_path, SECRET)
    client, _, root = _bootstrap(world, monkeypatch, SECRET, _project(SECRET, anonymous=True))
    monkeypatch.chdir(root)
    client.branch("dev")
    client.checkout("dev")
    data = _latest_state(tmp_path)

    assert data["project"]["anonymous"] is True and data["project"]["claim"]
    assert data["document"]["integrity_schema"] == 1
    GtsDocument.from_dict(copy.deepcopy(data)).validate()
    for key in ("anonymous", "claim"):
        stripped = copy.deepcopy(data)
        stripped["project"].pop(key)
        GtsDocument.from_dict(stripped).validate()  # still valid: the field is not hashed
    renamed = copy.deepcopy(data)
    renamed["project"]["name"] = "other"
    with pytest.raises(Exception, match="snapshot_hash"):
        GtsDocument.from_dict(renamed).validate()  # the alias is


def test_an_anonymous_state_that_holds_data_is_schema_two_and_valid(tmp_path, monkeypatch):
    world = _world(tmp_path, SECRET)
    client, _, root = _bootstrap(world, monkeypatch, SECRET, _project(SECRET, anonymous=True, lib_extra=', data = { backend = "dvc" }'))
    monkeypatch.chdir(root)
    client.branch("dev")
    client.checkout("dev")
    data = _latest_state(tmp_path)

    assert data["project"]["anonymous"] is True
    assert data["document"]["integrity_schema"] == 2  # DataSchema: only a State that holds data
    GtsDocument.from_dict(copy.deepcopy(data)).validate()
    assert any(repo.get("data") == {"backend": "dvc"} for repo in data["repo_state"])


def _every_spec() -> list[Path]:
    found = [REPO_ROOT / "install.cgs", *sorted((REPO_ROOT / "examples").glob("*.cgs")), *sorted((REPO_ROOT / "tests/fixtures").rglob("*.cgs"))]
    return [path for path in found if path.is_file()]


@pytest.mark.parametrize("spec", _every_spec(), ids=lambda path: path.relative_to(REPO_ROOT).as_posix())
def test_every_spec_of_this_repository_still_validates_with_the_project_table_rule(spec):
    """§2.5: `validate` refuses an unknown `project` key; no spec this repository ships may use one."""
    CgsDocument.from_toml(spec)

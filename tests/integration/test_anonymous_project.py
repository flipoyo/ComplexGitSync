"""AnonymousProject: a project that names itself by an alias in the repositories it shares, on real repositories.

Local bare remotes: one root per project, and one `.localSpec` and one `.memory` that every project shares, which is the
whole point of the option. The real name is a string no shared repository may ever hold, in a branch, a tag, a commit
message or a file of any commit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync import ComplexGitSyncClient
from ComplexGitSync.errors import ConfigValidationError, GitSyncError
from ComplexGitSync.git_repo.project_alias import ProjectAlias
from ComplexGitSync.orchestre.installer import Installer
from ComplexGitSync.tree.settings import UseCase

SECRET = "ZQXSECRET"
OTHER = "NEIGHBOUR"
SHARED = (".localSpec", ".memory")


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Tester")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@example.test")


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _seed(tmp_path: Path, name: str) -> Path:
    remote = tmp_path / f"{name}-remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    (seed / "README.md").write_text(f"{name}\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "origin", "main")
    return remote


class _World:
    """The remotes every project shares, and a way to bootstrap one more project against them."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.tmp_path, self.monkeypatch = tmp_path, monkeypatch
        self.remotes = {".localSpec": _seed(tmp_path, "localspec"), ".memory": _seed(tmp_path, "memory"), "ComplexGitSync": _seed(tmp_path, "tool")}

    def project(self, name: str, *, anonymous: str | None, branch: str = "dev"):
        self.remotes[name] = _seed(self.tmp_path, name)
        option = f', anonymous = "{anonymous}"' if anonymous else ""
        cgs = self.tmp_path / f"{name}.cgs"
        cgs.write_text(
            f"""
project = {{ name = "{name}", default_branch = "main"{option} }}
repos = [
    {{ repository = "github:owner/{name}", relative_path = ".", nested_config = "disabled" }},
    {{ repository = "github:owner/.localSpec", relative_path = ".agent/.local/.localSpec", fallback_branch = "main", private = true, writable = true }},
    {{ repository = "github:owner/.memory", relative_path = ".cgitsync/.memory", fallback_branch = "main", private = true, writable = true, nested_config = "disabled" }},
]
""".lstrip(),
            encoding="utf-8",
        )
        self.monkeypatch.setattr(Installer, "_use_case_of", lambda self, cgshome: UseCase.STANDALONE)
        client = ComplexGitSyncClient()
        self.monkeypatch.setattr(client, "_build_remote_url", lambda entry: str(self.remotes[entry.project_name]))
        registry = client.bootstrap(cgs, name, cgs_path=self.tmp_path / f"ws-{name}")
        root = self.tmp_path / f"ws-{name}" / name
        self.monkeypatch.chdir(root)
        client.branch(branch)
        client.checkout(branch)
        client.push(private=True)
        client.memory_push(root)
        return client, registry, root

    def branches(self, repo: str) -> list[str]:
        return sorted(b.strip("* ") for b in _git(self.remotes[repo], "branch").splitlines())


def _holds(repo: Path, needle: str) -> list[str]:
    """Where *needle* shows in *repo*: branches, tags, messages, and the files of every commit."""
    found = [f"branch {b}" for b in _git(repo, "branch", "-a", "--format=%(refname:short)").split() if needle.lower() in b.lower()]
    found += [f"tag {t}" for t in _git(repo, "tag").split() if needle.lower() in t.lower()]
    found += ["message" for line in _git(repo, "log", "--all", "--format=%s%n%b").splitlines() if needle.lower() in line.lower()]
    revs = _git(repo, "rev-list", "--all").split()
    grep = subprocess.run(["git", "grep", "-l", "-i", needle, *revs], cwd=repo, capture_output=True, text=True).stdout
    return found + [f"file {line.split(':', 1)[1]}" for line in grep.splitlines() if ":" in line]


def test_an_anonymous_project_is_named_by_its_alias_and_no_shared_repository_holds_its_name(tmp_path, monkeypatch):
    world = _World(tmp_path, monkeypatch)

    client, registry, root = world.project(SECRET, anonymous="sequence")
    client.memory_push(root)

    assert registry.project_name == "anonymous-1"
    assert "anonymous-1_dev" in world.branches(".localSpec")
    assert "ComplexGitSync_anonymous-1_dev" in world.branches(".memory")
    for shared in (".agent/.local/.localSpec", ".cgitsync/.memory"):
        assert _holds(root / shared, SECRET) == [], shared
    for name in SHARED:
        assert _holds(world.remotes[name], SECRET) == [], name


def test_the_alias_branch_carries_its_claim(tmp_path, monkeypatch):
    world = _World(tmp_path, monkeypatch)
    world.project(SECRET, anonymous="sequence")

    claim = _git(world.remotes[".localSpec"], "show", f"anonymous-1_dev:{ProjectAlias.CLAIM_FILE}")

    assert len(claim) == 64


def test_a_second_project_takes_the_next_alias_and_the_first_finds_its_own_again(tmp_path, monkeypatch):
    world = _World(tmp_path, monkeypatch)
    first, _, _ = world.project(SECRET, anonymous="sequence")

    _, second, _ = world.project(OTHER, anonymous="sequence")
    assert second.project_name == "anonymous-2"

    (tmp_path / "again").mkdir()
    again = _World.__new__(_World)
    again.tmp_path, again.monkeypatch, again.remotes = tmp_path / "again", monkeypatch, world.remotes
    # The same project, installed a second time on another machine, resolves to the alias it already holds.
    remote_root = world.remotes.pop(SECRET)
    world.remotes[SECRET] = remote_root
    registry = _reinstall(world, SECRET, tmp_path)
    assert registry.project_name == "anonymous-1"
    assert first.registry.project_name == "anonymous-1"


def _reinstall(world: _World, name: str, tmp_path: Path):
    client = ComplexGitSyncClient()
    world.monkeypatch.setattr(Installer, "_use_case_of", lambda self, cgshome: UseCase.STANDALONE)
    world.monkeypatch.setattr(client, "_build_remote_url", lambda entry: str(world.remotes[entry.project_name]))
    return client.bootstrap(tmp_path / f"{name}.cgs", name, cgs_path=tmp_path / f"ws2-{name}")


def test_hash_aliases_that_collide_get_a_suffix_on_every_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(ProjectAlias, "truncation", staticmethod(lambda digest: "abc123"))
    world = _World(tmp_path, monkeypatch)

    _, first, _ = world.project(SECRET, anonymous="hash")
    _, second, _ = world.project(OTHER, anonymous="hash")

    assert (first.project_name, second.project_name) == ("abc123", "abc123-1")
    assert "abc123_dev" in world.branches(".localSpec") and "abc123-1_dev" in world.branches(".localSpec")


def test_a_project_that_is_not_anonymous_is_named_and_branched_as_ever(tmp_path, monkeypatch):
    world = _World(tmp_path, monkeypatch)

    _, registry, root = world.project(SECRET, anonymous=None)

    assert registry.project_name == SECRET and not registry.anonymous
    assert f"{SECRET}_dev" in world.branches(".localSpec")
    assert not (root / ".agent/.local/.localSpec" / ProjectAlias.CLAIM_FILE).exists()


def test_an_anonymous_project_may_not_be_called_like_an_alias_but_an_ordinary_project_may(tmp_path, monkeypatch):
    world = _World(tmp_path, monkeypatch)

    with pytest.raises(ConfigValidationError, match="alias"):
        world.project("anonymous-7", anonymous="sequence")
    _, registry, _ = world.project("facade", anonymous=None)  # six hex digits, and an ordinary word

    assert registry.project_name == "facade"



def _declare_anonymous(world: _World, name: str, mode: str) -> Path:
    cgs = world.tmp_path / f"{name}.cgs"
    cgs.write_text(cgs.read_text(encoding="utf-8").replace('default_branch = "main"', f'default_branch = "main", anonymous = "{mode}"'), encoding="utf-8")
    return cgs


def test_anonymise_lists_then_creates_the_alias_branches_and_keeps_the_old_ones(tmp_path, monkeypatch):
    world = _World(tmp_path, monkeypatch)
    client, _, root = world.project(SECRET, anonymous=None)
    cgs = _declare_anonymous(world, SECRET, "sequence")

    listed = client.anonymise(cgs, dry_run=True)

    assert listed.alias == "anonymous-1"
    assert {(m.old, m.new) for m in listed.moves} == {(f"{SECRET}_dev", "anonymous-1_dev"), (f"ComplexGitSync_{SECRET}_dev", "ComplexGitSync_anonymous-1_dev")}
    assert "anonymous-1_dev" not in world.branches(".localSpec")  # a dry run writes nothing

    done = client.anonymise(cgs, dry_run=False)

    assert len(done.created) == 2 and len(done.kept) == 2 and not done.deleted
    assert "anonymous-1_dev" in world.branches(".localSpec") and f"{SECRET}_dev" in world.branches(".localSpec")
    assert len(_git(world.remotes[".localSpec"], "show", f"anonymous-1_dev:{ProjectAlias.CLAIM_FILE}")) == 64
    assert "not rewritten" in done.note


def test_anonymise_deletes_an_old_branch_only_when_asked_and_confirmed_by_name(tmp_path, monkeypatch):
    world = _World(tmp_path, monkeypatch)
    client, _, _ = world.project(SECRET, anonymous=None)
    cgs = _declare_anonymous(world, SECRET, "sequence")
    asked: list[str] = []

    def confirm(move) -> bool:
        asked.append(move.old)
        return move.old == f"{SECRET}_dev"

    done = client.anonymise(cgs, dry_run=False, delete_old=True, confirm=confirm)

    assert sorted(asked) == sorted([f"{SECRET}_dev", f"ComplexGitSync_{SECRET}_dev"])
    assert [m.old for m in done.deleted] == [f"{SECRET}_dev"]
    assert f"{SECRET}_dev" not in world.branches(".localSpec")
    assert f"ComplexGitSync_{SECRET}_dev" in world.branches(".memory")


def test_anonymise_refuses_a_cgs_that_is_not_anonymous(tmp_path, monkeypatch):
    world = _World(tmp_path, monkeypatch)
    client, _, _ = world.project(SECRET, anonymous=None)

    with pytest.raises(GitSyncError, match="anonymous"):
        client.anonymise(tmp_path / f"{SECRET}.cgs", dry_run=True)


def test_anonymise_moves_a_branch_that_only_the_remote_holds(tmp_path, monkeypatch):
    world = _World(tmp_path, monkeypatch)
    client, _, root = world.project(SECRET, anonymous=None)
    spec = root / ".agent/.local/.localSpec"
    _git(spec, "push", "origin", f"{SECRET}_dev:refs/heads/{SECRET}_remote_only")
    _git(spec, "fetch", "origin")
    cgs = _declare_anonymous(world, SECRET, "sequence")

    client.anonymise(cgs, dry_run=False)

    assert "anonymous-1_remote_only" in world.branches(".localSpec")

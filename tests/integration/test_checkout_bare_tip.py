"""CheckoutBareTip: a derived branch begins at the bare branch's tip, a landed name never pins, a typo is caught.

The session of 2026-11-27 on real repositories and local bare remotes. A root `demo` on `dev` mounts `docs`, a
repository shared with other projects, so it is on `demo_<branch>`. `docs` holds a bare `feature` branch with a commit
of its own: `checkout feature` must begin `demo_feature` there, say so, and publish nothing.
"""

from __future__ import annotations

import subprocess
import warnings
from pathlib import Path

import pytest

from ComplexGitSync import ComplexGitSyncClient
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.orchestre.installer import Installer
from ComplexGitSync.tree.settings import UseCase

MEMORY = ".memory"


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _seed(tmp_path: Path, name: str, branches: tuple[str, ...], own_commit: tuple[str, ...] = ()) -> Path:
    """A bare remote with *branches* at one commit; those in *own_commit* hold one more, so their tips differ."""
    remote = tmp_path / f"{name}-remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    (seed / "README.md").write_text(f"{name}\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    ident = ("-c", "user.email=t@example.test", "-c", "user.name=Tester")
    _git(seed, *ident, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    for branch in branches:
        if branch in own_commit:
            _git(seed, "checkout", "-q", "-b", f"tmp-{branch}", "main")
            (seed / f"{branch}.txt").write_text(branch, encoding="utf-8")
            _git(seed, "add", f"{branch}.txt")
            _git(seed, *ident, "commit", "-m", f"work on {branch}")
            _git(seed, "push", "origin", f"tmp-{branch}:refs/heads/{branch}")
            _git(seed, "checkout", "-q", "main")
        else:
            _git(seed, "push", "origin", f"main:refs/heads/{branch}")
    return remote


def _install(tmp_path, monkeypatch):
    remotes = {
        "demo": _seed(tmp_path, "demo", ("main", "dev", "feature")),
        "docs": _seed(tmp_path, "docs", ("main", "feature", "hotfix-1"), own_commit=("feature",)),
        ".localSpec": _seed(tmp_path, "localspec", ("main",)),
        MEMORY: _seed(tmp_path, "memory", ("main",)),
    }
    cgs = tmp_path / "demo.cgs"
    cgs.write_text(
        f"""
project = {{ name = "demo", default_branch = "dev" }}
repos = [
    {{ repository = "github:owner/demo", relative_path = ".", nested_config = "disabled" }},
    {{ repository = "github:owner/docs", relative_path = "docs", fallback_branch = "main", nested_config = "disabled" }},
    {{ repository = "github:owner/.localSpec", relative_path = ".agent/.local/.localSpec", fallback_branch = "main", private = true, writable = true }},
    {{ repository = "github:owner/{MEMORY}", relative_path = ".cgitsync/.memory", fallback_branch = "main", private = true, writable = true, nested_config = "disabled" }},
]
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(Installer, "_use_case_of", lambda self, cgshome: UseCase.STANDALONE)
    client = ComplexGitSyncClient()
    monkeypatch.setattr(client, "_build_remote_url", lambda entry: str(remotes[entry.project_name]))
    client.bootstrap(cgs, "demo", cgs_path=tmp_path / "one")
    return client, remotes, tmp_path / "one" / "demo"


def _on(root: Path, path: str) -> str:
    return _git(root / path, "branch", "--show-current")


def _upstream(repo: Path, branch: str) -> str:
    completed = subprocess.run(["git", "config", f"branch.{branch}.remote"], cwd=repo, capture_output=True, text=True)
    return completed.stdout.strip()


def test_a_derived_branch_begins_at_the_bare_branch_tip_with_no_upstream_and_says_so(tmp_path, monkeypatch):
    client, remotes, root = _install(tmp_path, monkeypatch)
    tip = _git(remotes["docs"], "rev-parse", "feature")

    with pytest.warns(UserWarning, match=r"docs: demo_feature was created from feature's tip " + tip[:7]):
        client.checkout("feature")

    assert (_on(root, "."), _on(root, "docs"), _on(root, ".agent/.local/.localSpec")) == ("feature", "demo_feature", "demo_feature")
    assert _git(root / "docs", "rev-parse", "HEAD") == tip  # the bare branch's own commit, not the fallback's
    assert _upstream(root / "docs", "demo_feature") == ""  # a push creates demo_feature; it never writes to feature
    assert "demo_feature" not in _git(remotes["docs"], "branch", "--list")


def test_a_derived_branch_with_no_bare_branch_begins_at_head_and_is_not_reported(tmp_path, monkeypatch):
    client, remotes, root = _install(tmp_path, monkeypatch)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client.checkout("brand-new")

    assert not [w for w in caught if "was created from" in str(w.message)]
    assert _on(root, "docs") == "demo_brand-new"
    assert _git(root / "docs", "rev-parse", "HEAD") == _git(remotes["docs"], "rev-parse", "main")


def test_branch_create_says_the_same_and_checkout_then_finds_it_made(tmp_path, monkeypatch):
    client, remotes, root = _install(tmp_path, monkeypatch)

    with pytest.warns(UserWarning, match="docs: demo_feature was created from feature's tip"):
        client.branch("feature")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client.checkout("feature")

    assert not [w for w in caught if "was created from" in str(w.message)]  # already made, nothing more to say
    assert _on(root, "docs") == "demo_feature"
    assert _git(root / "docs", "rev-parse", "HEAD") == _git(remotes["docs"], "rev-parse", "feature")


def test_a_misspelt_name_is_refused_before_anything_moves(tmp_path, monkeypatch):
    client, _, root = _install(tmp_path, monkeypatch)
    before = [_on(root, p) for p in (".", "docs", ".agent/.local/.localSpec")]

    with pytest.raises(GitSyncError, match=r"no branch 'featur' exists.*misspelling of 'feature'.*cgitsync branch create featur"):
        client.checkout("featur")

    assert [_on(root, p) for p in (".", "docs", ".agent/.local/.localSpec")] == before
    for path in (".", "docs", ".agent/.local/.localSpec"):
        assert "featur\n" not in _git(root / path, "branch", "--list", "*featur*") + "\n"
        assert _git(root / path, "branch", "--list", "featur") == ""  # nothing created anywhere


def test_the_misspelt_name_is_made_on_purpose_with_branch_create_then_checkout(tmp_path, monkeypatch):
    client, _, root = _install(tmp_path, monkeypatch)

    with pytest.raises(GitSyncError):
        client.checkout("featur")
    client.branch("featur")
    client.checkout("featur")

    assert (_on(root, "."), _on(root, "docs")) == ("featur", "demo_featur")


def test_a_new_name_close_to_nothing_and_a_numbered_sibling_are_created_as_before(tmp_path, monkeypatch):
    client, _, root = _install(tmp_path, monkeypatch)

    client.checkout("release-candidate")
    client.checkout("hotfix-2")  # `hotfix-1` exists in docs: a numbered sibling, never a slip

    assert (_on(root, "."), _on(root, "docs")) == ("hotfix-2", "demo_hotfix-2")


def test_a_branch_only_the_remote_holds_is_not_a_misspelling(tmp_path, monkeypatch):
    """`featur` is close to `feature`, but the name is a real branch pushed by another clone and never fetched here."""
    client, remotes, root = _install(tmp_path, monkeypatch)
    other = tmp_path / "colleague"
    _git(tmp_path, "clone", "-q", remotes["demo"].as_posix(), other.as_posix())
    _git(other, "-c", "user.email=t@example.test", "-c", "user.name=Tester", "commit", "-q", "--allow-empty", "-m", "theirs")
    _git(other, "push", "-q", "origin", "HEAD:refs/heads/featur")

    client.checkout("featur")

    assert _on(root, ".") == "featur"

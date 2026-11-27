"""NestedInstall N2: `nested_config = "auto"` reads only a root `*.cgs`, and now says so when the spec is in `.config/`.

The shape of CaWaQS 4X mounted in HTBS (HtbsFirstSpecs D3): the repository keeps its specs in `.config/`, so the default
mounts it as a plain repository with none of its libraries, and said nothing. `auto` keeps its meaning (NestedInstall
D3); it warns at install and in `status`, names the files and the spelling that mounts one, and names a root spec it
did graft. Real repositories, local bare remotes.
"""

from __future__ import annotations

import subprocess
import warnings
from pathlib import Path

import pytest

from ComplexGitSync import ComplexGitSyncClient
from ComplexGitSync.orchestre.installer import Installer
from ComplexGitSync.tree.settings import UseCase

SPEC = """
project = {{ name = "{name}", default_branch = "4X" }}
repos = [
    {{ repository = "github:owner/{name}", relative_path = ".", nested_config = "disabled" }},
    {{ repository = "github:owner/lib1", relative_path = "src/lib1", default_branch = "4X", nested_config = "disabled" }},
]
""".lstrip()


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Tester")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@example.test")


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _seed(tmp_path: Path, name: str, *, files: dict[str, str] | None = None, branches: tuple[str, ...] = ("main", "4X")) -> Path:
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
    for branch in branches:
        _git(seed, "push", "origin", f"main:refs/heads/{branch}")
    return remote


def _tree(tmp_path: Path, monkeypatch, *, mounted: dict[str, str], entry_extra: str):
    """A project `demo` that mounts `cawaqs` (specs as *mounted*, path to text) and the library its spec declares."""
    remotes = {
        "demo": _seed(tmp_path, "demo"),
        "cawaqs": _seed(tmp_path, "cawaqs", files=mounted),
        "lib1": _seed(tmp_path, "lib1"),
    }
    cgs = tmp_path / "demo.cgs"
    cgs.write_text(
        f"""
project = {{ name = "demo", default_branch = "main" }}
repos = [
    {{ repository = "github:owner/demo", relative_path = ".", nested_config = "disabled" }},
    {{ repository = "github:owner/cawaqs", relative_path = "cawaqs", default_branch = "4X", private = true{entry_extra} }},
]
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(Installer, "_use_case_of", lambda self, cgshome: UseCase.STANDALONE)
    client = ComplexGitSyncClient()
    monkeypatch.setattr(client, "_build_remote_url", lambda entry: str(remotes[entry.project_name]))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        registry = client.bootstrap(cgs, "demo", cgs_path=tmp_path / "one")
    return client, registry, tmp_path / "one" / "demo", [str(w.message) for w in caught]


def _names(registry) -> set[str]:
    return {repo.name for repo in registry.values()}


def test_a_spec_kept_in_config_is_named_and_the_spelling_that_mounts_it_is_given(tmp_path, monkeypatch):
    client, registry, _, said = _tree(tmp_path, monkeypatch, mounted={".config/cawaqs.cgs": SPEC.format(name="cawaqs")}, entry_extra="")

    assert "lib1" not in _names(registry)  # `auto` keeps its meaning: a plain repository, as before
    hint = next(message for message in said if "was mounted as a plain repository" in message)
    assert hint.startswith("cawaqs ") and ".config/cawaqs.cgs" in hint
    assert 'nested_config = ".config/cawaqs.cgs"' in hint and 'nested_config = "disabled"' in hint


def test_two_specs_in_config_are_both_named(tmp_path, monkeypatch):
    mounted = {".config/cawaqs.cgs": SPEC.format(name="cawaqs"), ".config/cawaqs-dev-4X.cgs": SPEC.format(name="cawaqs")}
    _, _, _, said = _tree(tmp_path, monkeypatch, mounted=mounted, entry_extra="")

    hint = next(message for message in said if "was mounted as a plain repository" in message)
    assert ".config/cawaqs-dev-4X.cgs, .config/cawaqs.cgs" in hint


def test_a_spec_named_on_the_entry_mounts_the_libraries_on_the_branch_the_spec_declares(tmp_path, monkeypatch):
    """HtbsFirstSpecs §7.1 step 4: the libraries of a mounted read-only project are on the branch its spec declares, not `main`."""
    client, registry, root, said = _tree(
        tmp_path, monkeypatch, mounted={".config/cawaqs.cgs": SPEC.format(name="cawaqs")}, entry_extra=', nested_config = ".config/cawaqs.cgs"'
    )

    assert "lib1" in _names(registry)
    assert _git(root / "cawaqs" / "src" / "lib1", "branch", "--show-current") == "4X"
    assert not [message for message in said if "was mounted as a plain repository" in message]  # said nothing: it was named


def test_a_repository_meant_as_a_plain_one_stays_quiet_at_install_and_in_status(tmp_path, monkeypatch):
    client, _, _, said = _tree(
        tmp_path, monkeypatch, mounted={".config/cawaqs.cgs": SPEC.format(name="cawaqs")}, entry_extra=', nested_config = "disabled"'
    )

    assert not [message for message in said if "was mounted as a plain repository" in message]
    assert "was mounted as a plain repository" not in client.status()


def test_status_repeats_the_warning_for_a_repository_mounted_by_default(tmp_path, monkeypatch):
    client, _, _, _ = _tree(tmp_path, monkeypatch, mounted={".config/cawaqs.cgs": SPEC.format(name="cawaqs")}, entry_extra="")

    lines = [line for line in client.status().splitlines() if "was mounted as a plain repository" in line]

    assert len(lines) == 1 and lines[0].startswith("warning: cawaqs ") and ".config/cawaqs.cgs" in lines[0]


def test_a_root_spec_that_auto_grafts_is_named_with_its_repository_count(tmp_path, monkeypatch):
    """The HTAS case: its root still holds a spec, which `auto` grafts, and nothing said so."""
    _, registry, _, said = _tree(tmp_path, monkeypatch, mounted={"cawaqs.cgs": SPEC.format(name="cawaqs")}, entry_extra="")

    assert "lib1" in _names(registry)
    graft = next(message for message in said if "grafted" in message)
    assert graft.startswith("cawaqs: ") and "cawaqs.cgs" in graft and "bringing 2 repositories" in graft and 'nested_config = "disabled"' in graft

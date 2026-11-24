"""ParentBranch: a nested Parent is on `<project>_<branch>`, never on the project's bare branch, on real repositories.

A root `demo` on `dev` mounts `lib`, a repository that holds `lib.cgs` (a project of its own) and declares `libdoc`.
Another project on `dev` mounting the same `lib` must not share its branch, so `lib` and what its `.cgs` declares
target `demo_dev`. Until that branch exists they land on their fallback, and say so. No network: bare remotes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ComplexGitSync import ComplexGitSyncClient
from ComplexGitSync.orchestre.installer import Installer
from ComplexGitSync.tree.git_tree_branch import GitTreeBranches
from ComplexGitSync.tree.operations.preflight import Preflight
from ComplexGitSync.tree.settings import UseCase

MEMORY = ".memory"
CREATE = "cgitsync branch create dev && cgitsync checkout dev"


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _seed(tmp_path: Path, name: str, branches: tuple[str, ...], files: dict[str, str] | None = None) -> Path:
    remote = tmp_path / f"{name}-remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    for path, text in {"README.md": f"{name}\n", **(files or {})}.items():
        (seed / path).write_text(text, encoding="utf-8")
        _git(seed, "add", path)
    _git(seed, "-c", "user.email=t@example.test", "-c", "user.name=Tester", "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    for branch in branches:
        _git(seed, "push", "origin", f"main:refs/heads/{branch}")
    return remote


def _lib_cgs(project: str = "lib", extra: str = "", branch: str = "main") -> str:
    return f"""
project = {{ name = "{project}", default_branch = "{branch}" }}
repos = [
    {{ repository = "github:owner/lib", relative_path = ".", nested_config = "disabled" }},
    {{ repository = "github:owner/libdoc", relative_path = "libdoc", nested_config = "disabled" }},{extra}
]
""".lstrip()


def _remotes(tmp_path: Path, *, lib_cgs: str | None = None, lib_branches=("main", "dev"), root: str = "demo", branch: str = "dev") -> dict[str, Path]:
    return {
        root: _seed(tmp_path, root, ("main", branch)),
        "lib": _seed(tmp_path, "lib", lib_branches, {"lib.cgs": lib_cgs or _lib_cgs()}),
        "libdoc": _seed(tmp_path, "libdoc", ("main", "dev")),
        ".localSpec": _seed(tmp_path, "localspec", ("main",)),
        MEMORY: _seed(tmp_path, "memory", ("main",)),
        "inner": _seed(tmp_path, "inner", ("main", "dev")),
    }


def _install(tmp_path, monkeypatch, remotes, *, root="demo", branch="dev", lib_entry="", workdir="one", spec_name=None):
    cgs = tmp_path / (spec_name or f"{root}.cgs")
    cgs.write_text(
        f"""
project = {{ name = "{root}", default_branch = "{branch}" }}
repos = [
    {{ repository = "github:owner/{root}", relative_path = ".", nested_config = "disabled" }},
    {{ repository = "github:owner/lib", fallback_branch = "main"{lib_entry} }},
    {{ repository = "github:owner/.localSpec", relative_path = ".agent/.local/.localSpec", fallback_branch = "main", private = true, writable = true }},
    {{ repository = "github:owner/{MEMORY}", relative_path = ".cgitsync/.memory", fallback_branch = "main", private = true, writable = true, nested_config = "disabled" }},
]
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(Installer, "_use_case_of", lambda self, cgshome: UseCase.STANDALONE)
    client = ComplexGitSyncClient()
    monkeypatch.setattr(client, "_build_remote_url", lambda entry: str(remotes[entry.project_name]))
    registry = client.bootstrap(cgs, root, cgs_path=tmp_path / workdir)
    return client, registry, tmp_path / workdir / root


def _by_name(registry):
    return {repo.name: repo for repo in registry.values()}


def _on(root: Path, path: str) -> str:
    return _git(root / path, "branch", "--show-current")


def test_a_parent_and_what_it_declares_target_the_projects_branch_and_wait_on_their_fallback(tmp_path, monkeypatch):
    client, registry, root = _install(tmp_path, monkeypatch, _remotes(tmp_path))
    repos = _by_name(registry)

    for name in ("lib", "libdoc"):
        assert repos[name].target_ref_name == "demo_dev"
        assert (_on(root, name if name == "lib" else "lib/libdoc"), repos[name].fallback_applied) == ("main", True)
    assert (repos["demo"].target_ref_name, _on(root, ".")) == ("dev", "dev")
    assert repos[".localSpec"].target_ref_name == "demo_dev"
    waiting = {m["repo"]: m for m in client.missing_branches}
    assert {"lib", "libdoc"} <= set(waiting)
    assert waiting["lib"]["wanted"] == "demo_dev" and waiting["lib"]["landed"] == "main" and waiting["lib"]["command"] == CREATE
    expected = {d.repo.name: d.expected for d in GitTreeBranches(registry, client.git_runner).deviations()}
    assert expected.items() >= {"lib": "demo_dev", "libdoc": "demo_dev"}.items()


def test_branch_create_then_checkout_moves_the_parent_on_its_derived_name_and_pushes_nothing(tmp_path, monkeypatch):
    remotes = _remotes(tmp_path)
    client, _, root = _install(tmp_path, monkeypatch, remotes)

    client.branch("dev")
    registry = client.checkout("dev")

    assert [_on(root, p) for p in (".", "lib", "lib/libdoc")] == ["dev", "demo_dev", "demo_dev"]
    assert GitTreeBranches(registry, client.git_runner).deviations() == ()
    assert "demo_dev" not in _git(remotes["lib"], "branch", "--list")


def test_two_projects_on_one_branch_name_never_share_a_branch_of_the_parent(tmp_path, monkeypatch):
    remotes = _remotes(tmp_path)
    _, registry, _ = _install(tmp_path, monkeypatch, remotes)
    other_remotes = {**remotes, "other": _seed(tmp_path, "other", ("main", "dev"))}
    _, other, _ = _install(tmp_path, monkeypatch, other_remotes, root="other", workdir="two")

    assert _by_name(registry)["lib"].target_ref_name == "demo_dev"
    assert _by_name(other)["lib"].target_ref_name == "other_dev"


def test_a_nested_spec_naming_the_tree_itself_is_not_the_root_so_it_takes_the_derived_branch(tmp_path, monkeypatch):
    """RootBareBranch, Q3: one project split across repositories keeps only its root on the bare branch."""
    remotes = _remotes(tmp_path, lib_cgs=_lib_cgs("demo", branch="dev"))
    client, registry, _ = _install(tmp_path, monkeypatch, remotes)

    repos = _by_name(registry)
    assert (repos["lib"].target_ref_name, repos["libdoc"].target_ref_name) == ("demo_dev", "demo_dev")
    waiting = {m["repo"]: m for m in client.missing_branches}
    assert waiting["lib"]["wanted"] == "demo_dev" and waiting["libdoc"]["wanted"] == "demo_dev"  # no silent fallback


def test_nested_config_disabled_leaf_takes_the_derived_branch_not_the_projects(tmp_path, monkeypatch):
    """RootBareBranch: a plain leaf is not the root, whatever it holds."""
    client, registry, root = _install(tmp_path, monkeypatch, _remotes(tmp_path), lib_entry=', nested_config = "disabled"')

    assert _by_name(registry)["lib"].target_ref_name == "demo_dev"
    assert {m["repo"]: m["wanted"] for m in client.missing_branches}["lib"] == "demo_dev"
    assert _on(root, "lib") != "dev"


def test_a_parent_inside_a_parent_is_on_the_projects_branch_never_doubled(tmp_path, monkeypatch):
    inner = '\n    { repository = "github:owner/inner", relative_path = "inner", nested_config = "disabled" },'
    remotes = _remotes(tmp_path, lib_cgs=_lib_cgs(extra=inner))
    remotes["inner"] = _seed(tmp_path, "inner2", ("main", "dev"), {"inner.cgs": 'project = { name = "inner", default_branch = "main" }\nrepos = [{ repository = "github:owner/inner", relative_path = ".", nested_config = "disabled" }]\n'})
    client, registry, _ = _install(tmp_path, monkeypatch, remotes)

    inner_entry = _by_name(registry)["inner"]
    assert inner_entry.target_ref_name == "demo_dev"
    assert GitTreeBranches(registry, client.git_runner).target(inner_entry, "dev").name == "demo_dev"


def test_on_main_the_parent_targets_the_project_name(tmp_path, monkeypatch):
    client, registry, _ = _install(tmp_path, monkeypatch, _remotes(tmp_path, branch="main"), branch="main")

    assert GitTreeBranches(registry, client.git_runner).target(_by_name(registry)["lib"], "main").name == "demo"


def test_a_branch_the_roots_spec_types_on_the_parents_entry_wins(tmp_path, monkeypatch):
    client, registry, root = _install(tmp_path, monkeypatch, _remotes(tmp_path, lib_branches=("main", "dev", "stable")), lib_entry=', default_branch = "stable"')
    lib = _by_name(registry)["lib"]

    assert (lib.target_ref_name, _on(root, "lib")) == ("stable", "stable")
    assert GitTreeBranches(registry, client.git_runner).target(lib, "feature").name == "stable"


def test_a_parent_already_on_the_projects_branch_is_reported_off_target_and_never_moved(tmp_path, monkeypatch):
    client, registry, root = _install(tmp_path, monkeypatch, _remotes(tmp_path))
    _git(root / "lib", "fetch", "-q", "origin", "dev:refs/remotes/origin/dev")
    _git(root / "lib", "checkout", "-q", "-b", "dev", "origin/dev")  # what the rule before this one did

    branches = GitTreeBranches(registry, client.git_runner)
    off = {d.repo.name: (d.observed, d.expected) for d in branches.deviations()}

    assert off["lib"] == ("dev", "demo_dev")
    assert _on(root, "lib") == "dev"
    lines = client._reporting._branch_incoherence(registry, branches)
    assert "lib is on 'dev', expected 'demo_dev'" in lines
    assert lines[-1].endswith(CREATE)  # the command follows the list once (BranchMissingEventName)
    blocking = Preflight._collect_branch_alignment_diagnostics(registry, client.git_runner)
    assert any(d.repo_name == "lib" for d in blocking)


def test_a_private_repository_the_parent_declares_stays_under_the_parents_name(tmp_path, monkeypatch):
    private = '\n    { repository = "github:owner/inner", relative_path = "private", private = true, writable = true, fallback_branch = "main", nested_config = "disabled" },'
    client, registry, _ = _install(tmp_path, monkeypatch, _remotes(tmp_path, lib_cgs=_lib_cgs(extra=private)))
    repos = _by_name(registry)

    assert repos["inner"].target_ref_name == "lib_demo_dev"  # IntegrityMount: <Parent>_<Project>_<X>, as before
    assert repos[".localSpec"].target_ref_name == "demo_dev"
    assert GitTreeBranches(registry, client.git_runner).target(repos["lib"], "dev").name == "demo_dev"


def test_close_branch_renames_the_parents_derived_branch_and_leaves_the_bare_one_alone(tmp_path, monkeypatch):
    remotes = _remotes(tmp_path)
    client, _, root = _install(tmp_path, monkeypatch, remotes)
    client.branch("feature")
    client.checkout("feature")
    assert [_on(root, p) for p in (".", "lib")] == ["feature", "demo_feature"]
    client.checkout("dev")

    client.close_branch("feature")

    lib_branches = _git(root / "lib", "branch", "--list").replace("*", "").split()
    assert "closed/demo_feature" in lib_branches and "demo_feature" not in lib_branches
    assert "dev" not in lib_branches  # the bare branch other projects share was never made, so never touched


def test_merge_into_plans_the_parent_on_its_derived_names(tmp_path, monkeypatch):
    client, _, _ = _install(tmp_path, monkeypatch, _remotes(tmp_path))
    client.branch("feature")
    client.checkout("feature")

    rows = client.merge_into_plan("feature", "dev")

    assert {row.repo_name if hasattr(row, "repo_name") else str(row) for row in rows}
    assert any("demo_feature" in str(row) and "demo_dev" in str(row) for row in rows)

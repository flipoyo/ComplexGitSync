"""RootBareBranch: only the root is on the project's bare branch; a plain library takes `<project>_<branch>`, on real repositories.

A root `demo` on `dev` mounts `lib`, a library that holds no `.cgs`. Another project on `dev` mounting the same `lib`
must not share its branch, so `lib` targets `demo_dev`. Until that branch exists it lands on its fallback and says so.
No network: bare remotes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ComplexGitSync import ComplexGitSyncClient
from ComplexGitSync.orchestre.installer import Installer
from ComplexGitSync.tree.git_tree_branch import GitTreeBranches
from ComplexGitSync.tree.settings import UseCase


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _seed(tmp_path: Path, name: str, branches: tuple[str, ...]) -> Path:
    remote = tmp_path / f"{name}-remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    (seed / "README.md").write_text(f"{name}\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "-c", "user.email=t@example.test", "-c", "user.name=Tester", "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    for branch in branches:
        _git(seed, "push", "origin", f"main:refs/heads/{branch}")
    return remote


def _install(tmp_path, monkeypatch, *, root="demo", branch="dev", lib_branches=("main", "dev"), lib_entry="", private=True, workdir="one"):
    remotes = {
        root: _seed(tmp_path, root, ("main", branch)),
        "lib": _seed(tmp_path, f"lib-{workdir}", lib_branches),
        ".localSpec": _seed(tmp_path, f"localspec-{workdir}", ("main",)),
    }
    spec = tmp_path / f"{root}.cgs"
    private_entry = (
        '    { repository = "github:owner/.localSpec", relative_path = ".agent/.local/.localSpec", fallback_branch = "main", private = true, writable = true },\n'
        if private
        else ""
    )
    spec.write_text(
        f"""
project = {{ name = "{root}", default_branch = "{branch}" }}
repos = [
    {{ repository = "github:owner/{root}", relative_path = ".", nested_config = "disabled" }},
    {{ repository = "github:owner/lib", fallback_branch = "main", nested_config = "disabled"{lib_entry} }},
{private_entry}]
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(Installer, "_use_case_of", lambda self, cgshome: UseCase.STANDALONE)
    client = ComplexGitSyncClient()
    monkeypatch.setattr(client, "_build_remote_url", lambda entry: str(remotes[entry.project_name]))
    registry = client.bootstrap(spec, root, cgs_path=tmp_path / workdir)
    return client, registry, tmp_path / workdir / root


def _targets(registry) -> dict[str, str | None]:
    return {repo.name: repo.target_ref_name for repo in registry.values()}


def test_only_the_root_targets_the_bare_branch_and_a_plain_library_targets_the_derived_one(tmp_path, monkeypatch):
    _, registry, _ = _install(tmp_path, monkeypatch)

    assert _targets(registry) == {"demo": "dev", "lib": "demo_dev", ".localSpec": "demo_dev"}


def test_two_projects_on_one_branch_name_never_share_a_branch_of_a_plain_library(tmp_path, monkeypatch):
    _, first, _ = _install(tmp_path, monkeypatch)
    _, second, _ = _install(tmp_path, monkeypatch, root="other", workdir="two")

    assert (_targets(first)["lib"], _targets(second)["lib"]) == ("demo_dev", "other_dev")


def test_on_main_a_plain_library_targets_the_project_name(tmp_path, monkeypatch):
    _, registry, _ = _install(tmp_path, monkeypatch, branch="main")

    assert (_targets(registry)["demo"], _targets(registry)["lib"]) == ("main", "demo")


def test_a_branch_typed_on_the_librarys_entry_wins(tmp_path, monkeypatch):
    _, registry, _ = _install(tmp_path, monkeypatch, lib_branches=("main", "stable"), lib_entry=', default_branch = "stable"')

    assert _targets(registry)["lib"] == "stable"


def test_a_library_without_its_derived_branch_waits_on_its_fallback_and_is_named(tmp_path, monkeypatch):
    client, registry, root = _install(tmp_path, monkeypatch)

    assert _git(root / "lib", "branch", "--show-current") == "main"
    waiting = {m["repo"]: m for m in client.missing_branches}
    assert (waiting["lib"]["wanted"], waiting["lib"]["landed"]) == ("demo_dev", "main")
    assert waiting["lib"]["command"] == "cgitsync branch create dev && cgitsync checkout dev"
    assert {d.repo.name: d.expected for d in GitTreeBranches(registry, client.git_runner).deviations()}["lib"] == "demo_dev"


def test_the_command_a_library_already_on_the_bare_branch_is_given_starts_the_derived_branch_at_its_tip(tmp_path, monkeypatch):
    client, _, root = _install(tmp_path, monkeypatch)
    lib = root / "lib"
    _git(lib, "fetch", "-q", "origin", "dev:dev")
    _git(lib, "checkout", "-q", "dev")
    (lib / "work.txt").write_text("work\n", encoding="utf-8")
    _git(lib, "add", "work.txt")
    _git(lib, "-c", "user.email=t@example.test", "-c", "user.name=Tester", "commit", "-m", "work on dev")
    tip = _git(lib, "rev-parse", "dev")

    client.branch("dev")
    client.checkout("dev")

    assert (_git(lib, "branch", "--show-current"), _git(lib, "rev-parse", "demo_dev")) == ("demo_dev", tip)
    assert _git(lib, "rev-parse", "dev") == tip


def test_a_tree_without_a_private_repository_follows_the_same_rule(tmp_path, monkeypatch):
    _, registry, _ = _install(tmp_path, monkeypatch, private=False)

    assert _targets(registry) == {"demo": "dev", "lib": "demo_dev"}


def test_a_tree_without_a_private_repository_is_told_with_info_and_a_developer_tree_with_a_warning(tmp_path, monkeypatch, capsys):
    from ComplexGitSync.cli import memory_prompt

    monkeypatch.setattr(memory_prompt, "interactive", lambda: False)
    user, _, _ = _install(tmp_path, monkeypatch, private=False)
    memory_prompt.offer_missing_branches(user)
    said = capsys.readouterr().err
    dev, _, _ = _install(tmp_path, monkeypatch, root="other", workdir="two")
    memory_prompt.offer_missing_branches(dev)

    assert said.startswith("info: branch dev is new here: 1 repository has no branch of their own yet")
    assert "project: lib (wait for 'demo_dev', cloned on 'main')" in said
    assert "to create it, run:\n  cgitsync branch create dev && cgitsync checkout dev\n" in said
    assert "Do not commit" not in said and "cgitsync push" not in said  # a USER tree never commits (Q2)
    assert capsys.readouterr().err.startswith("warning: branch dev is new here: 2 repositories have no branch of their own yet")


def test_the_install_logs_branch_missing_with_the_scope_of_each_case(tmp_path, monkeypatch):
    """BranchMissingEventName, Q1/Q2: one event, one attribute, and a scope that says which case fired."""
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(ComplexGitSyncClient, "_log_event", lambda self, event, level=0, **fields: events.append((event, fields)))
    client, _, _ = _install(tmp_path, monkeypatch)

    logged = {fields["repo"]: fields for event, fields in events if event == "branch_missing"}
    assert "private_branch_missing" not in {event for event, _ in events}
    assert {repo: (m["scope"], m["root"], m["tree_branch"]) for repo, m in logged.items()} == {
        "lib": ("project", False, "dev"),
        ".localSpec": ("private/local", False, "dev"),
    }
    assert {m["repo"]: m["scope"] for m in client.missing_branches} == {"lib": "project", ".localSpec": "private/local"}
    assert not hasattr(client, "missing_private_branches")  # renamed, no alias (Q2)


def test_a_developer_tree_closes_with_one_block_and_one_command_that_also_pushes(tmp_path, monkeypatch, capsys):
    """BranchMissingEventName, Q3/Q4: grouped by scope, the bare branch in the command, printed once."""
    from ComplexGitSync.cli import memory_prompt

    monkeypatch.setattr(memory_prompt, "interactive", lambda: False)
    client, _, _ = _install(tmp_path, monkeypatch)
    memory_prompt.offer_missing_branches(client)
    err = capsys.readouterr().err

    assert err.count("warning:") == 1
    assert "This is expected on the first install of a branch." in err
    assert "  project: lib (wait for 'demo_dev', cloned on 'main')" in err
    assert "  private/local: .localSpec (wait for 'demo_dev', cloned on 'main')" in err
    assert err.endswith("run:\n  cgitsync branch create dev && cgitsync checkout dev && cgitsync push\n")
    assert err.count("cgitsync branch create") == 1


def test_status_names_the_creating_command_once_for_every_shared_repository(tmp_path, monkeypatch):
    """BranchMissingEventName: the deviations are listed, the command that fixes them all follows once."""
    client, registry, _ = _install(tmp_path, monkeypatch)

    lines = client._reporting._branch_incoherence(registry)

    assert "lib is on 'main', expected 'demo_dev'" in lines
    assert [line for line in lines if "branch create" in line] == [
        "to put every shared repository on its branch, run: cgitsync branch create dev && cgitsync checkout dev"
    ]

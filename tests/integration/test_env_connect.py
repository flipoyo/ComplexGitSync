"""EnvConnect: what GX4G needs from ComplexGitSync, on real repositories.

C2 — ``initialise`` takes a ``.cgs`` only, and ``release load`` takes a
State's name, found on this disk or in the memory, and refused unless the
content still names it. C8 — a standalone tree's memory is filed under
``ComplexGitSync_<project>``, the nested tree's under ``<project>``, and the
branch the memory is on remembers which. C7 — a DEV bootstrap whose memory
branch does not exist yet ends READY and names the command that creates it.

Each test builds bare "remotes" on disk and routes the client's remote URLs to
them, as ``test_install_frontier.py`` does. See ``AdditionalSpecs.md``, *The
install frontier*.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ComplexGitSync import ComplexGitSyncClient
from ComplexGitSync.errors import GitSyncError, InstallFrontierError
from ComplexGitSync.orchestre.installer import Installer
from ComplexGitSync.settings import UseCase

MEMORY = ".memory"


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _seed(tmp_path: Path, name: str, *, branches: tuple[str, ...] = ("main",)) -> tuple[Path, Path]:
    """A bare remote called *name*, holding *branches*, and the seed checkout that fed it."""
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


def _write_cgs(path: Path, *, memory_config: str = "disabled") -> Path:
    path.write_text(
        f"""
project = {{ name = "demo", default_branch = "main" }}
repos = [
    {{ repository = "github:owner/demo", relative_path = "." }},
    {{ repository = "github:owner/leaf", relative_path = "deps/leaf" }},
    {{ repository = "github:owner/{MEMORY}", relative_path = ".cgitsync/.memory", fallback_branch = "main", private = true, writable = true, nested_config = "{memory_config}" }},
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


def _standalone(monkeypatch) -> None:
    monkeypatch.setattr(Installer, "_use_case_of", lambda self, cgshome: UseCase.STANDALONE)


def _snapshot_of(root: Path) -> Path:
    return sorted((root / ".cgitsync" / "state").glob("*.gts"), key=lambda path: path.stat().st_mtime)[-1]


def _tree(tmp_path: Path, *, memory_branches: tuple[str, ...]) -> dict[str, Path]:
    remotes = {"demo": _seed(tmp_path, "demo")[0], "leaf": _seed(tmp_path, "leaf")[0]}
    remotes[MEMORY] = _seed(tmp_path, "memory", branches=memory_branches)[0]
    return remotes


# ---------------------------------------------------------------------------
# C2 — initialise takes a .cgs; release load takes a State's name
# ---------------------------------------------------------------------------


def test_initialise_refuses_a_gts_by_name_and_clones_nothing(tmp_path, monkeypatch):
    remotes = _tree(tmp_path, memory_branches=("main", "demo"))
    first = _client(monkeypatch, remotes)
    first.bootstrap(_write_cgs(tmp_path / "demo.cgs"), "demo", cgs_path=tmp_path / "one")
    original = _snapshot_of(tmp_path / "one" / "demo")
    cgshome = tmp_path / "ws" / "demo"
    cgshome.parent.mkdir(parents=True)
    _git(cgshome.parent, "clone", remotes["demo"].as_posix(), cgshome.as_posix())

    with pytest.raises(InstallFrontierError, match="release load"):
        _client(monkeypatch, remotes).initialise(original, output_path=tmp_path / "ws")

    assert not (cgshome / "deps").exists()


def _loaded_tree_moved_on(tmp_path: Path, monkeypatch) -> tuple[ComplexGitSyncClient, Path, str, str]:
    """A bootstrapped tree whose leaf has moved past the State it recorded first."""
    remotes = _tree(tmp_path, memory_branches=("main", "demo"))
    client = _client(monkeypatch, remotes)
    client.bootstrap(_write_cgs(tmp_path / "demo.cgs"), "demo", cgs_path=tmp_path / "one")
    root = tmp_path / "one" / "demo"
    state = _snapshot_of(root)
    leaf = root / "deps" / "leaf"
    recorded = _git(leaf, "rev-parse", "HEAD")
    _git(leaf, "-c", "user.email=t@example.test", "-c", "user.name=Tester", "commit", "--allow-empty", "-m", "later")
    client.load(state)
    return client, root, state.stem, recorded


def test_release_load_puts_the_tree_back_at_a_state_named_by_its_hash(tmp_path, monkeypatch):
    client, root, state_hash, recorded = _loaded_tree_moved_on(tmp_path, monkeypatch)

    client.load_release(state_hash)

    assert _git(root / "deps" / "leaf", "rev-parse", "HEAD") == recorded


def test_release_load_finds_a_state_only_the_memory_holds(tmp_path, monkeypatch):
    client, root, state_hash, recorded = _loaded_tree_moved_on(tmp_path, monkeypatch)
    memory = root / ".cgitsync" / ".memory"
    (memory / "state").mkdir()
    shutil.copy(root / ".cgitsync" / "state" / f"{state_hash}.gts", memory / "state" / f"{state_hash}.gts")
    _git(memory, "add", "state")
    _git(memory, "-c", "user.email=t@example.test", "-c", "user.name=Tester", "commit", "-m", "the State")
    _git(memory, "rm", "-r", "--cached", "--quiet", "state")
    shutil.rmtree(memory / "state")
    (root / ".cgitsync" / "state" / f"{state_hash}.gts").unlink()

    client.load_release(state_hash)

    assert _git(root / "deps" / "leaf", "rev-parse", "HEAD") == recorded


def test_release_load_refuses_a_file_whose_content_names_another_state(tmp_path, monkeypatch):
    client, root, state_hash, _ = _loaded_tree_moved_on(tmp_path, monkeypatch)
    forged = "a" * 64
    shutil.copy(root / ".cgitsync" / "state" / f"{state_hash}.gts", root / ".cgitsync" / "state" / f"{forged}.gts")
    before = _git(root / "deps" / "leaf", "rev-parse", "HEAD")

    with pytest.raises(GitSyncError, match=f"its content is the State {state_hash[:12]}"):
        client.load_release(forged)

    assert _git(root / "deps" / "leaf", "rev-parse", "HEAD") == before


def test_release_load_refuses_a_state_nobody_holds(tmp_path, monkeypatch):
    client, _, _, _ = _loaded_tree_moved_on(tmp_path, monkeypatch)

    with pytest.raises(GitSyncError, match=f"No State {'b' * 12} on this disk or in the memory"):
        client.load_release("b" * 64)


# ---------------------------------------------------------------------------
# C8 — whose memory: the project's, or ComplexGitSync's twin
# ---------------------------------------------------------------------------


def test_a_nested_and_a_standalone_tree_write_to_two_memory_branches(tmp_path, monkeypatch):
    remotes = _tree(tmp_path, memory_branches=("main", "demo", "ComplexGitSync_demo"))
    cgs = _write_cgs(tmp_path / "demo.cgs")
    nested_home = tmp_path / "ws" / "demo"
    nested_home.parent.mkdir(parents=True)
    _git(nested_home.parent, "clone", remotes["demo"].as_posix(), nested_home.as_posix())
    _client(monkeypatch, remotes).initialise_cgs(cgs, output_path=tmp_path / "ws")
    _standalone(monkeypatch)

    twin = _client(monkeypatch, remotes).bootstrap(cgs, "demo", cgs_path=tmp_path / "one")

    assert twin.memory_twin
    assert _git(nested_home / ".cgitsync" / ".memory", "branch", "--show-current") == "demo"
    assert _git(tmp_path / "one" / "demo" / ".cgitsync" / ".memory", "branch", "--show-current") == "ComplexGitSync_demo"


def test_the_branch_the_memory_is_on_remembers_a_twin_whoever_runs_next(tmp_path, monkeypatch):
    remotes = _tree(tmp_path, memory_branches=("main", "demo", "ComplexGitSync_demo"))
    cgs = _write_cgs(tmp_path / "demo.cgs")
    with monkeypatch.context() as standalone:
        _standalone(standalone)
        _client(monkeypatch, remotes).bootstrap(cgs, "demo", cgs_path=tmp_path / "one")
    root = tmp_path / "one" / "demo"
    shutil.copy(cgs, root / "demo.cgs")

    later = _client(monkeypatch, remotes)  # the suite's default: a nested ComplexGitSync
    registry = later.load_cgs(root / "demo.cgs")

    memory = next(repo for repo in registry.values() if repo.name == MEMORY)
    assert registry.memory_twin
    assert memory.target_ref_name == "ComplexGitSync_demo"
    assert later.memory_init(root)["branch"] == "ComplexGitSync_demo"


def test_a_standalone_bootstrap_from_a_nested_state_moves_the_memory_to_the_twin_branch(tmp_path, monkeypatch):
    remotes = _tree(tmp_path, memory_branches=("main", "demo", "ComplexGitSync_demo"))
    cgs = _write_cgs(tmp_path / "demo.cgs")
    _client(monkeypatch, remotes).bootstrap(cgs, "demo", cgs_path=tmp_path / "one")
    nested_state = _snapshot_of(tmp_path / "one" / "demo")
    _standalone(monkeypatch)

    _client(monkeypatch, remotes).bootstrap(nested_state, "demo", cgs_path=tmp_path / "two")

    assert _git(tmp_path / "two" / "demo" / ".cgitsync" / ".memory", "branch", "--show-current") == "ComplexGitSync_demo"


def test_a_project_or_branch_name_holding_an_underscore_is_refused_at_creation(tmp_path):
    client = ComplexGitSyncClient()

    with pytest.raises(Exception, match="may not contain '_'"):
        client.configure("my_project", ["github:owner/my_project"])
    with pytest.raises(GitSyncError, match="'feature_x' was not created"):
        client.branch("feature_x")
    # An existing repository's name, as discover --write reads it, is never refused.
    assert client.configure("my_project", ["github:owner/my_project"], new_name=False).project_name == "my_project"


# ---------------------------------------------------------------------------
# C7 — a DEV bootstrap whose memory branch does not exist yet
# ---------------------------------------------------------------------------


def test_a_dev_bootstrap_whose_memory_branch_is_missing_ends_ready_and_names_the_fix(tmp_path, monkeypatch):
    remotes = _tree(tmp_path, memory_branches=("main",))
    _standalone(monkeypatch)
    client = _client(monkeypatch, remotes)

    registry = client.bootstrap(_write_cgs(tmp_path / "demo.cgs", memory_config="config-memory.cgs"), "demo", cgs_path=tmp_path / "one")

    assert registry.is_ready()
    [missing] = client.missing_private_branches
    assert missing["repo"] == MEMORY
    assert missing["wanted"] == "ComplexGitSync_demo"
    assert missing["landed"] == "main"
    assert missing["command"] == "cgitsync memory branch --project-branch main"


def test_a_nested_config_missing_on_the_branch_really_targeted_is_still_refused(tmp_path, monkeypatch):
    remotes = _tree(tmp_path, memory_branches=("main", "demo"))

    with pytest.raises(GitSyncError, match="Nested configuration for .memory is not resolved"):
        _client(monkeypatch, remotes).bootstrap(
            _write_cgs(tmp_path / "demo.cgs", memory_config="config-memory.cgs"), "demo", cgs_path=tmp_path / "one"
        )


def _self_history_remote(tmp_path: Path, monkeypatch) -> Path:
    """A reachable .self-history remote, routed where the set-up asks for it, and a Git identity."""
    from ComplexGitSync.orchestre import memory_commands

    remote = _seed(tmp_path, "self-history")[0]
    monkeypatch.setattr(memory_commands.MemoryFacts, "remote_url", staticmethod(lambda _identifier: remote.as_posix()))
    for variable in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(variable, "Tester")
    for variable in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(variable, "t@example.test")
    return remote


def _c7_twin(tmp_path: Path, monkeypatch, remotes: dict[str, Path]) -> tuple[ComplexGitSyncClient, Path]:
    _standalone(monkeypatch)
    client = _client(monkeypatch, remotes)
    client.bootstrap(_write_cgs(tmp_path / "demo.cgs", memory_config="config-memory.cgs"), "demo", cgs_path=tmp_path / "one")
    return client, tmp_path / "one" / "demo"


def test_the_command_c7_names_sets_the_branch_up_and_a_later_install_finds_it(tmp_path, monkeypatch):
    remotes = _tree(tmp_path, memory_branches=("main",))
    remotes[".self-history"] = _self_history_remote(tmp_path, monkeypatch)
    client, root = _c7_twin(tmp_path, monkeypatch, remotes)

    answer = client.memory_branch(root, client.missing_private_branches[0]["project_branch"])

    assert answer["branch"] == "ComplexGitSync_demo" and answer["set_up"] and answer["pushed"]
    assert "config-memory.cgs" in _git(remotes[MEMORY], "ls-tree", "-r", "--name-only", "ComplexGitSync_demo")
    again = _client(monkeypatch, remotes)
    again.bootstrap(tmp_path / "demo.cgs", "demo", cgs_path=tmp_path / "two")
    nested = tmp_path / "two" / "demo" / ".cgitsync" / ".memory" / ".self-history"
    assert _git(nested, "branch", "--show-current") == "ComplexGitSync_demo"
    assert again.missing_private_branches == []


def test_a_set_up_that_cannot_run_creates_nothing_and_can_be_run_again(tmp_path, monkeypatch):
    remotes = _tree(tmp_path, memory_branches=("main",))
    client, root = _c7_twin(tmp_path, monkeypatch, remotes)
    memory = root / ".cgitsync" / ".memory"

    with pytest.raises(GitSyncError, match="self-history"):
        client.memory_branch(root, "main")

    assert _git(memory, "branch", "--show-current") == "main"
    assert "ComplexGitSync_demo" not in _git(memory, "branch", "--list")
    _self_history_remote(tmp_path, monkeypatch)
    assert client.memory_branch(root, "main")["set_up"]


def test_a_memory_branch_only_the_remote_holds_is_not_created_again(tmp_path, monkeypatch):
    remotes = _tree(tmp_path, memory_branches=("main", "demo", "demo_feature"))
    client = _client(monkeypatch, remotes)
    client.bootstrap(_write_cgs(tmp_path / "demo.cgs"), "demo", cgs_path=tmp_path / "one")

    with pytest.raises(GitSyncError, match="'demo_feature' already exists, so it is not created again"):
        client.memory_branch(tmp_path / "one" / "demo", "feature")


def test_every_writable_private_repository_left_on_a_fallback_branch_is_reported(tmp_path, monkeypatch):
    remotes = _tree(tmp_path, memory_branches=("main", "ComplexGitSync_demo"))
    _standalone(monkeypatch)
    cgs = _write_cgs(tmp_path / "demo.cgs")
    cgs.write_text(
        cgs.read_text(encoding="utf-8").replace(
            "]\n", '    { repository = "github:owner/settings", relative_path = "settings", private = true, writable = true },\n]\n'
        ),
        encoding="utf-8",
    )
    remotes["settings"] = _seed(tmp_path, "settings")[0]
    client = _client(monkeypatch, remotes)

    client.bootstrap(cgs, "demo", cgs_path=tmp_path / "one")

    [missing] = client.missing_private_branches
    assert (missing["repo"], missing["wanted"], missing["landed"], missing["config"], missing["command"]) == (
        "settings", "demo", "main", None, None,
    )


def test_the_cli_names_the_repository_warns_and_prints_the_command(tmp_path, monkeypatch, capsys):
    from ComplexGitSync.cli import memory_prompt

    client, _ = _c7_twin(tmp_path, monkeypatch, _tree(tmp_path, memory_branches=("main",)))
    monkeypatch.setattr(memory_prompt, "interactive", lambda: False)

    memory_prompt.offer_missing_branches(client)

    err = capsys.readouterr().err
    assert "warning: .memory has no branch 'ComplexGitSync_demo' yet, so it was cloned on 'main'" in err
    assert "Do not commit to it there" in err
    assert "to create it, run: cgitsync memory branch --project-branch main" in err


def test_os_id_survives_every_way_the_tree_is_materialised(tmp_path, monkeypatch):
    """Omniscience §10, item 1: one project name, one os_id, however the tree is installed or restored."""
    from ComplexGitSync.operating_space import OperatingSpace

    remotes = _tree(tmp_path, memory_branches=("main", "demo", "ComplexGitSync_demo"))
    cgs = _write_cgs(tmp_path / "demo.cgs")
    expected = OperatingSpace.id_of("demo")
    ids = {"bootstrap .cgs": OperatingSpace(_client(monkeypatch, remotes).bootstrap(cgs, "demo", cgs_path=tmp_path / "one")).os_id}
    state = _snapshot_of(tmp_path / "one" / "demo")
    ids["bootstrap .gts"] = OperatingSpace(_client(monkeypatch, remotes).bootstrap(state, "demo", cgs_path=tmp_path / "two")).os_id
    nested_home = tmp_path / "ws" / "demo"
    nested_home.parent.mkdir(parents=True)
    _git(nested_home.parent, "clone", remotes["demo"].as_posix(), nested_home.as_posix())
    ids["initialise .cgs"] = OperatingSpace(_client(monkeypatch, remotes).initialise_cgs(cgs, output_path=tmp_path / "ws")).os_id
    loader = _client(monkeypatch, remotes)
    loader.load(state)
    ids["release load <State hash>"] = OperatingSpace(loader.load_release(state.stem)).os_id
    with monkeypatch.context() as standalone:
        _standalone(standalone)
        ids["twin bootstrap"] = OperatingSpace(_client(monkeypatch, remotes).bootstrap(cgs, "demo", cgs_path=tmp_path / "three")).os_id

    assert ids == dict.fromkeys(ids, expected)

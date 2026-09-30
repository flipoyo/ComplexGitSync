"""A tree is USER or DEV by what it holds, and a DEV tree without a memory is offered one.

The UserDevProfile ticket: a tree whose `.cgs` holds no private repository is
a user's, and its memory stays on the disk; one holding any private
repository is a developer's, and its memory is meant to be synced. When a
DEV tree declares no memory, the work is still recorded locally, and
cgitsync offers — in a terminal, once — to create, declare and adopt one;
everywhere else it warns. Every provider tool here is faked: no network.
"""

from __future__ import annotations

import json
import subprocess
import warnings
from pathlib import Path

import pytest

from ComplexGitSync import orchestre
from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.cli import memory_prompt
from ComplexGitSync.git_runner import GitRunner
from ComplexGitSync.git_tree import TreeProfile
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.orchestre.memory_setup import MemorySetup, MemorySetupWarning

REPO_ROOT = Path(__file__).resolve().parents[2]

_DEV_NO_MEMORY_CGS = """# A developer's project, with a comment nobody may lose.
project = "demo"

repos = [
  # The project's own repository.
  "github:flipoyo/demo",
  { repository = "github:someone/conf", relative_path = "conf", private = true },
]
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    for key, value in (("GIT_AUTHOR_NAME", "Test"), ("GIT_COMMITTER_NAME", "Test"),
                       ("GIT_AUTHOR_EMAIL", "t@example.com"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(key, value)


def _loaded(root: Path, cgs: str) -> ComplexGitSyncClient:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.cgs").write_text(cgs, encoding="utf-8")
    client = ComplexGitSyncClient()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MemorySetupWarning)
        client.load(root / "project.cgs")
    return client


def _cgs(*entries: str, owner: str = "flipoyo") -> str:
    lines = ",\n".join(f"  {entry}" for entry in (f'"github:{owner}/demo"', *entries))
    return f'project = "demo"\n\nrepos = [\n{lines},\n]\n'


# ---------------------------------------------------------------------------
# WP1 — the rule
# ---------------------------------------------------------------------------


def test_a_tree_from_install_cgs_is_user(tmp_path):
    client = _loaded(tmp_path / "u", (REPO_ROOT / "install.cgs").read_text(encoding="utf-8"))

    assert client.registry.profile is TreeProfile.USER


def test_this_projects_developer_tree_is_dev(tmp_path):
    client = _loaded(tmp_path / "d", (REPO_ROOT / "examples" / "complexgitsync4dev.cgs").read_text(encoding="utf-8"))

    assert client.registry.profile is TreeProfile.DEV
    assert client.memory_setup_proposal() is None  # it declares its memory
    assert client.memory_setup_due is False


def test_a_read_only_private_repository_alone_makes_a_tree_dev(tmp_path):
    client = _loaded(tmp_path / "d", _cgs('{ repository = "github:x/conf", relative_path = "conf", private = true }'))

    assert client.registry.profile is TreeProfile.DEV


def test_a_repository_private_only_through_its_parent_counts(tmp_path):
    client = _loaded(tmp_path / "d", _cgs(
        '{ repository = "github:x/conf", relative_path = "conf", private = true }',
        '{ repository = "github:x/inner", relative_path = "conf/inner" }',
    ))
    inner = next(repo for repo in client.registry.values() if repo.name == "inner")

    assert inner.private is False and inner.effective_private is True
    assert client.registry.profile is TreeProfile.DEV


# ---------------------------------------------------------------------------
# WP2 — status says which
# ---------------------------------------------------------------------------


def test_status_and_its_json_carry_the_profile(tmp_path, capsys):
    workspace = tmp_path / "u"
    _loaded(workspace, _cgs())

    cli_main(["status", "--search-dir", str(workspace)])
    table = capsys.readouterr().out
    cli_main(["status", "--json", "--search-dir", str(workspace)])
    payload = json.loads(capsys.readouterr().out)

    assert "profile=user " in table
    assert payload["profile"] == "user"
    assert payload["schema_version"] == 1


# ---------------------------------------------------------------------------
# WP3a — the proposal, and the owner guess (D2's three cases)
# ---------------------------------------------------------------------------


def test_a_user_tree_is_proposed_nothing(tmp_path):
    client = _loaded(tmp_path / "u", _cgs())

    assert client.memory_setup_proposal() is None
    assert client.memory_setup_due is False


def test_the_owner_is_the_most_frequent_private_owner(tmp_path):
    client = _loaded(tmp_path / "d", _cgs(
        '{ repository = "github:alice/a", relative_path = "a", private = true }',
        '{ repository = "github:alice/b", relative_path = "b", private = true }',
        '{ repository = "github:bob/c", relative_path = "c", private = true }',
    ))

    assert MemorySetup.guess_owner(client.registry) == "alice"


def test_a_tie_falls_back_to_the_root_owner(tmp_path):
    client = _loaded(tmp_path / "d", _cgs(
        '{ repository = "github:alice/a", relative_path = "a", private = true }',
        '{ repository = "github:bob/c", relative_path = "c", private = true }',
    ))

    assert MemorySetup.guess_owner(client.registry) == "flipoyo"


def test_with_no_owner_anywhere_none_is_offered(tmp_path):
    client = _loaded(tmp_path / "d", _cgs('{ repository = "github:x/conf", relative_path = "conf", private = true }'))
    for repo in client.registry.values():
        repo.project_owner_name = None

    assert MemorySetup.guess_owner(client.registry) is None
    proposal = client.memory_setup_proposal()
    assert proposal["owner"] is None and proposal["entry"] is None
    assert "--owner <owner>" in proposal["warning"]


def test_the_proposal_is_data_with_every_default(tmp_path):
    workspace = tmp_path / "d"
    client = _loaded(workspace, _DEV_NO_MEMORY_CGS)

    proposal = client.memory_setup_proposal()

    assert proposal["profile"] == "dev"
    assert (proposal["provider"], proposal["owner"], proposal["name"]) == ("github", "someone", ".memory")
    assert proposal["repository"] == "github:someone/.memory"
    assert proposal["create_with"] == "gh repo create someone/.memory --private"
    assert proposal["cgs"] == str(workspace / "project.cgs")
    assert proposal["declined"] is False
    gitlab = client.memory_setup_proposal(provider="gitlab", owner="grp", name="mem")
    assert gitlab["create_with"].startswith("glab repo create grp/mem")
    assert client.memory_setup_proposal(provider="codeberg", owner="o")["create_with"].startswith("tea repo create")


# ---------------------------------------------------------------------------
# WP3d — a Python caller is warned, never refused, and the record is kept
# ---------------------------------------------------------------------------


def test_a_python_caller_is_warned_and_the_work_still_recorded(tmp_path):
    workspace = tmp_path / "d"
    workspace.mkdir()
    (workspace / "project.cgs").write_text(_DEV_NO_MEMORY_CGS, encoding="utf-8")
    client = ComplexGitSyncClient()

    with pytest.warns(MemorySetupWarning, match="no memory back-up and no global ledger record"):
        client.load(workspace / "project.cgs")

    assert client.memory_setup_due is True
    assert (workspace / ".cgitsync" / ".memory" / ".git").is_dir()  # the local default, as before
    assert "no memory back-up" in client.memory_status(workspace)["notice"]


# ---------------------------------------------------------------------------
# WP3b — the fix, as one call
# ---------------------------------------------------------------------------


class _ToolRun:
    def __init__(self, *, ran: bool = True, ok: bool = True, message: str = "") -> None:
        self.ran, self.ok, self.message = ran, ok, message


@pytest.fixture
def fake_provider(tmp_path, monkeypatch):
    """`gh` creates a bare repository on disk, and every identifier points there."""
    remote = tmp_path / "remote" / "memory.git"
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(orchestre, "_remote_url_for_identifier", lambda identifier: str(remote))

    def run_tool(self, tool, *argv):
        if argv[:2] != ("repo", "create"):
            return _ToolRun(ran=False)  # the toolchain probes (`ssh -V`, `gh auth status`) see no tool
        calls.append((tool, *argv))
        remote.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
        return _ToolRun()

    monkeypatch.setattr(GitRunner, "run_tool", run_tool)
    return remote, calls


def test_accepting_creates_declares_and_adopts_keeping_every_record(tmp_path, fake_provider):
    remote, calls = fake_provider
    workspace = tmp_path / "d"
    client = _loaded(workspace, _DEV_NO_MEMORY_CGS)
    states_before = sorted(p.name for p in (workspace / ".cgitsync" / "state").glob("*.gts"))

    result = client.memory_setup()

    assert result["failed"] is None and result["done"] == ["create", "declare", "adopt"]
    assert calls == [("gh", "repo", "create", "someone/.memory", "--private")]
    spec = (workspace / "project.cgs").read_text(encoding="utf-8")
    assert "# A developer's project, with a comment nobody may lose." in spec
    assert "# The project's own repository." in spec
    assert 'repository = "github:someone/.memory"' in spec
    mount = workspace / ".cgitsync" / ".memory"
    assert not (mount / ".git" / "cgitsync-defaulted").exists()
    assert _git(mount, "remote", "get-url", "origin") == str(remote)
    assert sorted(p.name for p in (workspace / ".cgitsync" / "state").glob("*.gts")) == states_before

    pushed = client.memory_push(workspace)

    assert pushed["pushed"] is True
    assert "refs/heads/demo" in _git(remote, "for-each-ref", "--format=%(refname)")


def test_a_failed_step_stops_the_rest_and_says_which(tmp_path, monkeypatch):
    monkeypatch.setattr(GitRunner, "remote_reachable", lambda self, url: False)
    monkeypatch.setattr(GitRunner, "run_tool", lambda self, *a: _ToolRun(ran=False))
    workspace = tmp_path / "d"
    client = _loaded(workspace, _DEV_NO_MEMORY_CGS)

    result = client.memory_setup()

    assert result["failed"] == "create" and result["done"] == []
    assert "gh is not installed" in result["error"]
    assert (workspace / "project.cgs").read_text(encoding="utf-8") == _DEV_NO_MEMORY_CGS
    assert (workspace / ".cgitsync" / ".memory" / ".git" / "cgitsync-defaulted").exists()


# ---------------------------------------------------------------------------
# WP3c — asked in a terminal, once; warned everywhere else
# ---------------------------------------------------------------------------


def _no_questions(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("no question may be asked here")
    monkeypatch.setattr("builtins.input", refuse)


def test_without_a_terminal_nothing_is_asked_and_the_warning_names_the_fix(tmp_path, monkeypatch, capsys):
    _no_questions(monkeypatch)
    client = _loaded(tmp_path / "d", _DEV_NO_MEMORY_CGS)

    memory_prompt.offer_after_command(client)

    err = capsys.readouterr().err
    assert "no memory back-up and no global ledger record" in err
    assert "cgitsync memory setup --provider github --name .memory --owner someone" in err


def test_declining_is_asked_once_then_only_warned(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "d"
    client = _loaded(workspace, _DEV_NO_MEMORY_CGS)
    monkeypatch.setattr(memory_prompt, "interactive", lambda: True)
    answers = iter(["", "", "", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    memory_prompt.offer_after_command(client)

    assert (workspace / ".cgitsync" / MemorySetup.DECLINED).is_file()
    assert "no memory back-up" in capsys.readouterr().err
    _no_questions(monkeypatch)
    again = _loaded(workspace, _DEV_NO_MEMORY_CGS)
    again.memory_setup_due = True

    memory_prompt.offer_after_command(again)

    assert "no memory back-up" in capsys.readouterr().err


def test_accepting_in_a_terminal_runs_the_fix(tmp_path, monkeypatch, capsys, fake_provider):
    workspace = tmp_path / "d"
    client = _loaded(workspace, _DEV_NO_MEMORY_CGS)
    monkeypatch.setattr(memory_prompt, "interactive", lambda: True)
    answers = iter(["", "", "mem", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    memory_prompt.offer_after_command(client)

    out = capsys.readouterr().out
    assert "gh repo create someone/mem --private" in out
    assert "done=create,declare,adopt" in out
    assert 'repository = "github:someone/mem"' in (workspace / "project.cgs").read_text(encoding="utf-8")


def test_memory_setup_is_a_command_of_its_own(tmp_path, monkeypatch, capsys, fake_provider):
    _no_questions(monkeypatch)
    workspace = tmp_path / "d"
    _loaded(workspace, _DEV_NO_MEMORY_CGS)

    exit_code = cli_main(["memory", "setup", "--owner", "me", "--search-dir", str(workspace)])

    assert exit_code == 0
    assert "done=create,declare,adopt" in capsys.readouterr().out
    assert 'repository = "github:me/.memory"' in (workspace / "project.cgs").read_text(encoding="utf-8")


def test_memory_status_warns_a_dev_tree_without_a_loaded_client(tmp_path, capsys):
    workspace = tmp_path / "d"
    _loaded(workspace, _DEV_NO_MEMORY_CGS)

    assert "no memory back-up" in ComplexGitSyncClient().memory_status(workspace)["notice"]
    cli_main(["memory", "status", "--search-dir", str(workspace)])
    assert "no memory back-up" in capsys.readouterr().out


def test_memory_status_on_a_user_tree_keeps_its_notice(tmp_path):
    workspace = tmp_path / "u"
    _loaded(workspace, _cgs())

    assert "local and unpublished" in ComplexGitSyncClient().memory_status(workspace)["notice"]


def test_an_unanswered_question_does_not_fail_the_command(tmp_path, monkeypatch, capsys):
    client = _loaded(tmp_path / "d", _DEV_NO_MEMORY_CGS)
    monkeypatch.setattr(memory_prompt, "interactive", lambda: True)

    def eof(_prompt):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)

    memory_prompt.offer_after_command(client)

    assert "no memory back-up" in capsys.readouterr().err

"""DataBackendContract — one dispatcher, one vocabulary, no DVC in the core.

The ticket's acceptance list (§6), against the fake backend: no Git, no DVC, no
process, no network. A Git-only tree never builds a backend; a ``dvc``
repository is ``UNSUPPORTED`` by name; every state is reachable; a dry run
writes nothing; a failure names its repository and phase; a backend's version
is asked once.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from support.fake_data_backend import WRITING, FakeDataBackend

import ComplexGitSync.tree.data_manager as data_manager_module
from ComplexGitSync import ComplexGitSyncClient
from ComplexGitSync.tree.cgs_format import CgsDocument
from ComplexGitSync.tree.data_command import DataCommand, DataContext, DataPhase
from ComplexGitSync.tree.data_manager import DataManager
from ComplexGitSync.tree.data_result import DataPlan, DataResult, DataStatus
from ComplexGitSync.tree.registry import RegistryTranslator

CGS = """
project = "twin"
repos = [
    { repository = "gitlab:owner/twin", relative_path = "." },
    { repository = "gitlab:owner/results", relative_path = "results", data = { backend = "dvc" } },
    { repository = "gitlab:owner/lib", relative_path = "lib" },
    { repository = "gitlab:owner/inputs", relative_path = "inputs", data = { backend = "dvc" } },
]
"""
GIT_ONLY_CGS = """
project = "demo"
repos = ["github:owner/demo", { repository = "github:owner/lib", relative_path = "lib" }]
"""


def _repos(tmp_path, text=CGS):
    path = tmp_path / "project.cgs"
    path.write_text(text, encoding="utf-8")
    return RegistryTranslator.from_cgs_document(CgsDocument.from_toml(path), path).values()


def _faked(fake=None):
    """A manager whose ``dvc`` is the fake, as the DvcBackend ticket will register the real one."""
    fake = fake or FakeDataBackend()
    built = []
    manager = DataManager({"dvc": lambda: built.append(fake) or fake})
    return manager, fake, built


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- a Git-only tree, and a backend nothing registered ---------------------------------------


def test_a_git_only_tree_never_constructs_a_backend(tmp_path):
    manager, fake, built = _faked()
    repos = _repos(tmp_path, GIT_ONLY_CGS)

    for command in DataCommand:
        assert manager.dispatch_all(command, repos) == ()
    assert built == [] and fake.calls == []
    assert manager.versions() == {} and fake.version_calls == 0
    assert not any("dvc" in name.lower() for name in sys.modules)


def test_the_manager_imports_no_backend():
    tree = ast.parse(Path(data_manager_module.__file__).read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported == {"__future__", "collections.abc", "dataclasses", "types", "typing", "data_command",
                        "data_result", "memory.ledger_store", "git_repo.git_repo"}
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    assert DataManager.BUILT_IN == {}


def test_a_dvc_repository_is_unsupported_by_name_with_no_process(tmp_path, monkeypatch):
    def no_process(*args, **kwargs):
        raise AssertionError("a process was started")

    monkeypatch.setattr(subprocess, "Popen", no_process)
    monkeypatch.setattr(subprocess, "run", no_process)
    answers = DataManager().dispatch_all(DataCommand.PULL, _repos(tmp_path))

    assert [(a.repo, a.state, a.backend, a.step) for a in answers] == [
        ("root:results", DataStatus.UNSUPPORTED, "dvc", "pre_git_change"),
        ("root:inputs", DataStatus.UNSUPPORTED, "dvc", "pre_git_change"),
    ]
    assert "root:results" in answers[0].describe() and "not available in this build" in answers[0].describe()
    assert not answers[0].ok and not answers[0].retry_safe


def test_the_client_holds_one_manager_with_no_backend():
    client = ComplexGitSyncClient()
    assert isinstance(client.orchestre.data_manager, DataManager)
    assert client.orchestre.data_manager.versions() == {}


# --- the vocabulary, and the routing --------------------------------------------------------


@pytest.mark.parametrize("state", list(DataStatus))
def test_every_state_is_reachable_through_the_fake(tmp_path, state):
    manager, _, _ = _faked(FakeDataBackend(state=state))
    answer = manager.dispatch(DataCommand.STATUS, _repos(tmp_path)[1])

    assert answer.state is state and answer.ok is (state is DataStatus.READY)


def test_unknown_never_reads_as_success():
    assert not DataResult(DataStatus.UNKNOWN).ok


@pytest.mark.parametrize("command", list(DataCommand))
def test_every_phase_of_every_command_reaches_its_backend_method(tmp_path, command):
    manager, fake, built = _faked()
    results = _repos(tmp_path)[1]

    for phase in command.phases:
        answer = manager.dispatch(command, results, DataContext(paths=("a.csv",)), phase=phase)
        assert (answer.repo, answer.step, answer.operation) == ("root:results", phase.value, command.value)
        assert fake.methods()[-1] == phase.value
    assert len(built) == 1


def test_add_and_rm_plan_before_they_stage(tmp_path):
    manager, fake, _ = _faked()
    results = _repos(tmp_path)[1]

    added = manager.dispatch(DataCommand.ADD, results, DataContext(paths=("x.nc",)))
    removed = manager.dispatch(DataCommand.RM, results, DataContext(paths=("y.nc",)))

    assert fake.methods() == ["plan_add", "prepare_staging", "plan_remove", "prepare_staging"]
    assert added.plan == DataPlan(("x.nc",), ("track x.nc",))
    assert removed.plan == DataPlan(("y.nc",), ("untrack y.nc",))


def test_a_phase_the_command_does_not_run_is_the_callers_mistake(tmp_path):
    manager, fake, _ = _faked()
    with pytest.raises(ValueError, match="push runs"):
        manager.dispatch(DataCommand.PUSH, _repos(tmp_path)[1], phase=DataPhase.MATERIALIZE)
    assert fake.calls == []


def test_the_network_is_reached_only_when_both_the_context_and_the_command_allow_it(tmp_path):
    manager, fake, _ = _faked()
    results = _repos(tmp_path)[1]
    online = DataContext(offline=False)

    manager.dispatch(DataCommand.PULL, results, phase=DataPhase.MATERIALIZE)
    manager.dispatch(DataCommand.PULL, results, online, phase=DataPhase.MATERIALIZE)
    manager.dispatch(DataCommand.CHECKOUT, results, online, phase=DataPhase.MATERIALIZE)
    manager.dispatch(DataCommand.STATUS, results, online)

    assert [args for _, _, args in fake.calls] == [
        {"allow_network": False}, {"allow_network": True}, {"allow_network": False}, {"offline": True}]


def test_pull_force_warns_its_backend_the_change_is_destructive(tmp_path):
    manager, fake, _ = _faked()
    results = _repos(tmp_path)[1]

    manager.dispatch(DataCommand.PULL, results)
    manager.dispatch(DataCommand.PULL_FORCE, results)
    manager.dispatch(DataCommand.MERGE, results, DataContext(destructive=True))

    assert [args["destructive"] for _, _, args in fake.calls] == [False, True, True]


def test_publish_commit_and_release_receive_their_arguments(tmp_path):
    manager, fake, _ = _faked()
    results = _repos(tmp_path)[1]
    context = DataContext(no_stage=True, revisions=("abc",), revision="v1")

    for phase in DataCommand.RELEASE_FREEZE.phases:
        manager.dispatch(DataCommand.RELEASE_FREEZE, results, context, phase=phase)

    assert [args for _, _, args in fake.calls] == [{"no_stage": True}, {"revisions": ("abc",)}, {"revision": "v1"}]


# --- a dry run, a failure, a secret ---------------------------------------------------------


@pytest.mark.parametrize("command", list(DataCommand))
def test_a_dry_run_writes_nothing_anywhere(tmp_path, command):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    manager, fake, _ = _faked(FakeDataBackend(scratch=scratch))
    repos = _repos(tmp_path)
    before = _snapshot(tmp_path)

    for phase in command.phases:
        answers = manager.dispatch_all(command, repos, DataContext(dry_run=True, offline=False), phase=phase)
        assert len(answers) == 2
        if phase.writes:
            assert all(f"dry run: {phase.value} not run" in a.diagnostics for a in answers)

    assert _snapshot(tmp_path) == before
    assert not set(fake.methods()) & set(WRITING)
    assert all(args.get("offline", True) for _, _, args in fake.calls)


def test_without_dry_run_the_fake_does_write(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    manager, _, _ = _faked(FakeDataBackend(scratch=scratch))

    manager.dispatch_all(DataCommand.PUSH, _repos(tmp_path))

    assert sorted(p.name for p in scratch.iterdir()) == ["publish-root_inputs", "publish-root_results"]


def test_a_failure_names_its_repository_and_phase_and_stops_no_other(tmp_path):
    manager, _, _ = _faked(FakeDataBackend(raises={"materialize": {"root:results"}}))

    answers = manager.dispatch_all(DataCommand.RELEASE_LOAD, _repos(tmp_path))

    assert [(a.repo, a.step, a.state) for a in answers] == [
        ("root:results", "materialize", DataStatus.FAILED), ("root:inputs", "materialize", DataStatus.READY)]
    assert answers[0].diagnostics == ("RuntimeError: materialize broke on root:results",)
    assert answers[0].describe().startswith("root:results: materialize for release load via dvc -> failed")


def test_an_answer_that_is_not_a_result_is_a_failure(tmp_path):
    class Wrong(FakeDataBackend):
        def publish(self, repo, *, revisions):
            return "done"

    manager = DataManager({"dvc": Wrong})
    answer = manager.dispatch(DataCommand.PUSH, _repos(tmp_path)[1])

    assert answer.state is DataStatus.FAILED and answer.diagnostics == ("the backend answered str",)


def test_diagnostics_are_scrubbed_of_secrets(tmp_path):
    fake = FakeDataBackend(diagnostics=("push to https://user:s3cret@host/store failed", "--token abc"))
    manager, _, _ = _faked(fake)

    answer = manager.dispatch(DataCommand.PUSH, _repos(tmp_path)[1])

    assert answer.diagnostics == ("push to https://***@host/store failed", "--token ***")
    assert "s3cret" not in answer.describe()


def test_a_backend_cannot_misname_what_it_answered(tmp_path):
    class Liar(FakeDataBackend):
        def inspect(self, repo, *, offline):
            return DataResult(DataStatus.READY, backend="git-lfs", repo="elsewhere", step="publish")

    answer = DataManager({"dvc": Liar}).dispatch(DataCommand.STATUS, _repos(tmp_path)[1])

    assert (answer.backend, answer.repo, answer.step) == ("dvc", "root:results", "inspect")


# --- version() ------------------------------------------------------------------------------


def test_a_backend_version_is_asked_once_and_reused(tmp_path):
    manager, fake, _ = _faked()
    repos = _repos(tmp_path)

    for command in DataCommand:
        manager.dispatch_all(command, repos)
        assert manager.versions() == {"dvc": "fake-1.0"}

    assert fake.version_calls == 1


def test_a_backend_that_cannot_say_its_version_has_none(tmp_path):
    manager, _, _ = _faked(FakeDataBackend(version_answer=OSError("not installed")))
    manager.dispatch(DataCommand.STATUS, _repos(tmp_path)[1])
    assert manager.versions() == {"dvc": "none"}

    empty, _, _ = _faked(FakeDataBackend(version_answer=""))
    empty.dispatch(DataCommand.STATUS, _repos(tmp_path)[1])
    assert empty.versions() == {"dvc": "none"}


def test_an_unregistered_backend_is_none_without_being_built(tmp_path):
    manager = DataManager()
    manager.dispatch(DataCommand.STATUS, _repos(tmp_path)[1])
    assert manager.versions() == {"dvc": "none"}


def test_registering_again_forgets_the_earlier_backend(tmp_path):
    manager, first, _ = _faked()
    results = _repos(tmp_path)[1]
    manager.dispatch(DataCommand.STATUS, results)
    manager.versions()

    second = FakeDataBackend(version_answer="fake-2.0")
    manager.register("dvc", lambda: second)
    manager.dispatch(DataCommand.STATUS, results)

    assert manager.versions() == {"dvc": "fake-2.0"} and len(second.calls) == 1 and len(first.calls) == 1


# --- the table ------------------------------------------------------------------------------


def test_every_command_runs_at_least_one_phase_and_only_checkout_and_reads_stay_offline():
    assert all(command.phases for command in DataCommand)
    assert {c for c in DataCommand if c.offline} == {
        DataCommand.VALIDATE, DataCommand.DISCOVER, DataCommand.STATUS, DataCommand.VIEW_TREE,
        DataCommand.CHECKOUT, DataCommand.VERIFY}
    assert {p for p in DataPhase if p.writes} == {
        DataPhase.PREPARE_STAGING, DataPhase.PRE_GIT_COMMIT, DataPhase.MATERIALIZE, DataPhase.PUBLISH}

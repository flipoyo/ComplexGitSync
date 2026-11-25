"""data_manager — one dispatcher between the tree and the data backends, and the contract they answer.

Ring: 2 (drives the backends, which will start processes; it starts none itself)
Contract: ``DataManager`` maps a repository's ``data.backend`` to a backend
    through a registry and runs the phase a ``DataCommand`` asks of it,
    answering one ``DataResult`` per data repository, stamped with the
    repository and the phase and scrubbed of secrets. A Git-only repository is
    never handed to a backend, and a backend nothing registered answers
    ``UNSUPPORTED`` without an import, an install attempt or a process.
    ``DataBackend`` is what a backend must answer.
Imports: data_command, data_result, ledger_store, git_repo

``DataManager`` never imports a backend (DataBackendContract D3, owner,
2026-11-25): this build registers none, so ``"dvc"`` is ``UNSUPPORTED`` until
the DvcBackend ticket registers one, and tests register a fake. A dry run calls
only ``inspect(offline=True)`` and the planning methods, never a phase that
writes. A backend's ``version()`` is asked at most once per manager, and only
for a backend a command actually touched; one that is not installed, or that
raises, is ``none`` (D6, owner, 2026-09-16).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from .data_command import DataCommand, DataContext, DataPhase
from .data_result import DataPlan, DataResult, DataStatus
from .memory.ledger_store import ArgvScrubber

if TYPE_CHECKING:
    from ..git_repo.git_repo import GitRepo


class DataBackend(Protocol):
    """What a data backend answers, one semantic phase per method (DataBackendContract §1).

    A phase answers a ``DataResult`` carrying only its state, ``retry_safe`` and
    diagnostics; the manager names the backend, repository, command and phase.
    ``inspect`` answers a ``DataResult`` too, so it can say why.
    """

    def inspect(self, repo: GitRepo, *, offline: bool) -> DataResult: ...
    def plan_add(self, repo: GitRepo, paths: tuple[str, ...]) -> DataPlan: ...
    def plan_remove(self, repo: GitRepo, paths: tuple[str, ...]) -> DataPlan: ...
    def prepare_staging(self, repo: GitRepo, plan: DataPlan) -> DataResult: ...
    def pre_git_commit(self, repo: GitRepo, *, no_stage: bool) -> DataResult: ...
    def pre_git_change(self, repo: GitRepo, *, destructive: bool) -> DataResult: ...
    def materialize(self, repo: GitRepo, *, allow_network: bool) -> DataResult: ...
    def publish(self, repo: GitRepo, *, revisions: tuple[str, ...]) -> DataResult: ...
    def verify_release(self, repo: GitRepo, *, revision: str | None) -> DataResult: ...
    def version(self) -> str: ...


class DataManager:
    """Dispatches each data repository's phase to its backend; holds the registry and the version cache."""

    #: The backends this build implements, by ``data.backend`` name: none yet (DvcBackend registers ``dvc``).
    BUILT_IN: Mapping[str, Callable[[], DataBackend]] = MappingProxyType({})
    #: A backend's version when it is not installed or cannot say (the owner's word, 2026-09-16).
    NO_VERSION = "none"

    def __init__(self, backends: Mapping[str, Callable[[], DataBackend]] | None = None) -> None:
        self._factories = dict(self.BUILT_IN if backends is None else backends)
        self._backends: dict[str, DataBackend] = {}
        self._versions: dict[str, str] = {}
        self._touched: list[str] = []

    def register(self, name: str, factory: Callable[[], DataBackend]) -> None:
        """Make *name* resolve to the backend *factory* builds, on first use; forgets an earlier one."""
        self._factories[name] = factory
        self._backends.pop(name, None)
        self._versions.pop(name, None)

    def backend_for(self, repo: GitRepo) -> DataBackend | None:
        """The backend of *repo*, built on first use; ``None`` for a Git-only repository or an unregistered backend."""
        if repo.data is None:
            return None
        name = repo.data.backend
        if name not in self._backends:
            factory = self._factories.get(name)
            if factory is None:
                return None
            self._backends[name] = factory()
        return self._backends[name]

    def dispatch(
        self, command: DataCommand, repo: GitRepo, context: DataContext | None = None, *,
        phase: DataPhase | None = None,
    ) -> DataResult | None:
        """Run *command*'s *phase* (its first by default) for *repo*; ``None`` for a Git-only repository.

        Never raises for a backend's failure: the answer is ``FAILED``, naming
        the repository and the phase. Raises ``ValueError`` only for a phase the
        command does not run, which is the caller's mistake.
        """
        if repo.data is None:
            return None
        phase = phase or command.phases[0]
        if phase not in command.phases:
            raise ValueError(f"{command.value} runs {[p.value for p in command.phases]}, not {phase.value}")
        context = context or DataContext()
        name = repo.data.backend
        if name not in self._touched:
            self._touched.append(name)
        backend = self.backend_for(repo)
        if backend is None:
            answer = DataResult(DataStatus.UNSUPPORTED, diagnostics=(
                f"the {name} backend is not available in this build; nothing was run",))
        else:
            answer = self._run(backend, command, phase, repo, context)
        # a WorkingRepo (what a tree holds) names itself by repo_id; a bare GitRepo by its repository name
        label = getattr(repo, "repo_id", "") or getattr(repo, "name", "") or repo.repo_name or repo.project_name
        return replace(answer, backend=name, repo=label, operation=command.value, step=phase.value,
                       diagnostics=tuple(self._scrub(line) for line in answer.diagnostics))

    def dispatch_all(
        self, command: DataCommand, repos: Iterable[GitRepo], context: DataContext | None = None, *,
        phase: DataPhase | None = None,
    ) -> tuple[DataResult, ...]:
        """One answer per data repository of *repos*, in order; a failure in one never stops the next."""
        answers = (self.dispatch(command, repo, context, phase=phase) for repo in repos)
        return tuple(answer for answer in answers if answer is not None)

    def versions(self) -> dict[str, str]:
        """The version of every backend a dispatch touched, each asked once; ``none`` when it cannot say."""
        for name in self._touched:
            if name not in self._versions:
                self._versions[name] = self._version_of(name)
        return {name: self._versions[name] for name in self._touched}

    def _version_of(self, name: str) -> str:
        backend = self._backends.get(name)
        if backend is None:
            return self.NO_VERSION
        try:
            return str(backend.version() or self.NO_VERSION)
        except Exception:  # a backend that cannot say its version has none, it does not stop the command
            return self.NO_VERSION

    @staticmethod
    def _run(
        backend: DataBackend, command: DataCommand, phase: DataPhase, repo: GitRepo, context: DataContext,
    ) -> DataResult:
        allow_network = not (context.offline or command.offline)
        try:
            plan = None
            if phase is DataPhase.PREPARE_STAGING:
                plan = (backend.plan_remove if command is DataCommand.RM else backend.plan_add)(repo, context.paths)
            if context.dry_run and phase.writes:
                seen = backend.inspect(repo, offline=True)
                return replace(seen, plan=plan, diagnostics=(*seen.diagnostics, f"dry run: {phase.value} not run"))
            calls = {
                DataPhase.INSPECT: lambda: backend.inspect(repo, offline=not allow_network),
                DataPhase.PREPARE_STAGING: lambda: backend.prepare_staging(repo, plan),
                DataPhase.PRE_GIT_COMMIT: lambda: backend.pre_git_commit(repo, no_stage=context.no_stage),
                DataPhase.PRE_GIT_CHANGE: lambda: backend.pre_git_change(
                    repo, destructive=context.destructive or command.destructive),
                DataPhase.MATERIALIZE: lambda: backend.materialize(repo, allow_network=allow_network),
                DataPhase.PUBLISH: lambda: backend.publish(repo, revisions=context.revisions),
                DataPhase.VERIFY_RELEASE: lambda: backend.verify_release(repo, revision=context.revision),
            }
            answer = calls[phase]()
        except Exception as exc:  # one repository's failure is its answer, never a traceback
            return DataResult(DataStatus.FAILED, diagnostics=(f"{type(exc).__name__}: {exc}",))
        if not isinstance(answer, DataResult):
            return DataResult(DataStatus.FAILED, diagnostics=(f"the backend answered {type(answer).__name__}",))
        return answer if plan is None or answer.plan is not None else replace(answer, plan=plan)

    @staticmethod
    def _scrub(line: str) -> str:
        """A diagnostic with credentials redacted and paths cut, by the ledger's own scrubber."""
        return " ".join(ArgvScrubber.scrub(line.split(" ")))


__all__ = ["DataBackend", "DataManager"]

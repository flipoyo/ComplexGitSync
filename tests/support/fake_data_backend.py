"""fake_data_backend — a data backend that records the phases it is asked for and imports nothing.

DataBackendContract §3: the orchestration tests pass against it, which is the
evidence that the dispatcher is backend-neutral rather than DVC-shaped. It can
be told which state each phase answers, which phase raises, and which
diagnostics to give, so every word of ``DataStatus`` is reachable in a test.
Given a ``scratch`` directory, a phase that writes leaves a file there, so a
dry run can be proven to write nothing by a snapshot of the disk.

DataAuthoring and DataMaterialisation test against it too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ComplexGitSync.tree.data_result import DataPlan, DataResult, DataStatus

#: The phases that may write, and so leave a file in ``scratch``.
WRITING = ("prepare_staging", "pre_git_commit", "materialize", "publish")


@dataclass
class FakeDataBackend:
    """Answers every phase of ``DataBackend`` from its settings and records each call."""

    state: DataStatus = DataStatus.READY
    #: A different state for one phase, by method name.
    states: dict[str, DataStatus] = field(default_factory=dict)
    #: Phases that raise, by method name; only for the repositories named, or all when the set is empty.
    raises: dict[str, set[str]] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()
    version_answer: Any = "fake-1.0"
    scratch: Path | None = None
    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    version_calls: int = 0

    def _answer(self, method: str, repo: Any, **arguments: Any) -> DataResult:
        label = getattr(repo, "repo_id", "") or getattr(repo, "name", "")
        self.calls.append((method, label, arguments))
        if method in self.raises and (not self.raises[method] or label in self.raises[method]):
            raise RuntimeError(f"{method} broke on {label}")
        if self.scratch is not None and method in WRITING:
            (self.scratch / f"{method}-{label.replace(':', '_').replace('/', '_')}").write_text("written")
        return DataResult(self.states.get(method, self.state), retry_safe=True, diagnostics=self.diagnostics)

    def inspect(self, repo, *, offline):
        return self._answer("inspect", repo, offline=offline)

    def plan_add(self, repo, paths):
        self.calls.append(("plan_add", getattr(repo, "repo_id", ""), {"paths": paths}))
        return DataPlan(paths=tuple(paths), steps=tuple(f"track {path}" for path in paths))

    def plan_remove(self, repo, paths):
        self.calls.append(("plan_remove", getattr(repo, "repo_id", ""), {"paths": paths}))
        return DataPlan(paths=tuple(paths), steps=tuple(f"untrack {path}" for path in paths))

    def prepare_staging(self, repo, plan):
        return self._answer("prepare_staging", repo, plan=plan)

    def pre_git_commit(self, repo, *, no_stage):
        return self._answer("pre_git_commit", repo, no_stage=no_stage)

    def pre_git_change(self, repo, *, destructive):
        return self._answer("pre_git_change", repo, destructive=destructive)

    def materialize(self, repo, *, allow_network):
        return self._answer("materialize", repo, allow_network=allow_network)

    def publish(self, repo, *, revisions):
        return self._answer("publish", repo, revisions=revisions)

    def verify_release(self, repo, *, revision):
        return self._answer("verify_release", repo, revision=revision)

    def version(self):
        self.version_calls += 1
        if isinstance(self.version_answer, BaseException):
            raise self.version_answer
        return self.version_answer

    def methods(self) -> list[str]:
        """The method names called, in order."""
        return [method for method, _, _ in self.calls]

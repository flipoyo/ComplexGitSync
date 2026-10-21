"""omniscience_observer — `cgitsync omniscience observe`: is each registered repository still there?

Ring: 3
Contract: probe every repository a project's records name (its ``.memory`` and
    each published repository) and append one observation per finding to the
    register, never removing or rewriting anything. Report, from the whole
    history of findings, whether the project is complete.
Imports: cgs_format, errors, memory, memory_facts, omniscience_appender, omniscience_completeness, omniscience_control, omniscience_observation, omniscience_reader, omniscience_record

It runs only when it is typed, never as a side effect of ``register`` (owner,
D3). A probe asks the host with ``ls-remote`` and sorts what Git printed
(`ObservationStatus.of_git_failure`): a host that says a repository is not
there is ``NOT_FOUND``, which is a warning and not a deletion. A branch the
ledger says was closed on purpose is ``CLOSED``, and only when the remote
answered and the ref is simply gone: a failed fetch never says "closed".
``DELETION_CONFIRMED`` is never probed: it is the owner's note, made by hand
with a reason, in a signed commit, and only about a target the host does say
is not there (owner, D2).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomli_w

from ..cgs_format import parse_repo_id
from ..errors import GitSyncError
from ..memory.ledger_store import LedgerStore
from ..memory.repository import MemoryRepository
from ..omniscience_completeness import CompletenessReport
from ..omniscience_observation import Observation, ObservationStatus
from ..omniscience_record import OmniscienceRecord
from .memory_facts import MemoryFacts
from .omniscience_appender import RegisterAppender
from .omniscience_control import IntegrityControl
from .omniscience_reader import RegisterReader

if TYPE_CHECKING:
    from .client import ComplexGitSyncClient

_URL_FORMS = ("https://", "ssh://", "git@")
_HEX_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class OmniscienceObserver:
    """Observes the repositories a project's records name."""

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def observe(
        self,
        cgshome: str | Path,
        project: str | None = None,
        *,
        confirm_deleted: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Probe *project*'s targets (default: this tree's) and append what was found.

        With *confirm_deleted* (a target's repository) and a *reason*, record
        only the owner's signed deletion note for that target, after asking
        the host once more and finding it still says the target is not there.
        Returns the new ``observations`` and the ``report`` over every
        finding the project has, its warnings and its permanent failures.
        """
        mount = IntegrityControl(self.client).mounted("omniscience observe", cgshome)
        git = self.client.git_runner
        name = project or self.client.get_dependency_registry().project_name
        if not name:
            raise GitSyncError("this tree has no project name, so there is nothing to observe; pass the project.")
        reader = RegisterReader(git, mount)
        git.fetch(mount)
        branches = reader.remote_branches()
        records = {
            f.value.record_hash: (f.branch, f.value)
            for branch, sha in sorted(branches.items())
            for f in reader.records(branch, sha)
            if isinstance(f.value, OmniscienceRecord) and f.value.project == name
        }
        if not records:
            raise GitSyncError(f"the register holds no record of project {name!r}, so there is nothing to observe.")
        if confirm_deleted is not None and not (reason or "").strip():
            raise GitSyncError("a deletion note needs a reason: pass --reason.")
        closed = self._declared_closes(Path(cgshome))
        now = f"{self.client.clock.now():%Y-%m-%dT%H:%M:%SZ}"
        observer = git.configured_email(mount) or ""
        made: dict[str, list[Observation]] = {}
        for (kind, repository, ref, commit), (branch, record) in sorted(self._targets(records).items()):
            if confirm_deleted is not None and repository != confirm_deleted:
                continue
            status, method, evidence = self._probe(repository, ref, commit, closed)
            if confirm_deleted is not None:
                if status is not ObservationStatus.NOT_FOUND:
                    raise GitSyncError(f"{repository} is not a deletion to confirm: the host answered {status.value}, not NOT_FOUND. Nothing was recorded.")
                status, method, evidence = ObservationStatus.DELETION_CONFIRMED, "owner-note", {"reason": str(reason).strip(), "host_answer": ObservationStatus.NOT_FOUND.value}
            made.setdefault(branch, []).append(
                Observation(project=name, os_id=record.os_id, target_kind=kind, target=repository, status=status, observed_at=now, method=method,
                            evidence=evidence, commit=commit, record_hash=record.record_hash, observer=observer)
            )
        if confirm_deleted is not None and not made:
            raise GitSyncError(f"no repository named {confirm_deleted!r} is registered for project {name!r}.")
        appender = RegisterAppender(self.client, mount)
        for branch, items in sorted(made.items()):
            files = {item.filename: tomli_w.dumps(item.to_dict()) for item in items}
            self._append(appender, branch, files, items, sign=confirm_deleted is not None)
        appender.settle(next(iter(sorted(made))))
        history = [
            f.value
            for branch, sha in sorted(self.reader_branches(reader).items())
            for f in reader.observations(branch, sha)
            if isinstance(f.value, Observation) and f.value.project == name
        ]
        report = CompletenessReport.of(history)
        self.client._log_event("omniscience_observe", project=name, observations=sum(len(v) for v in made.values()), verdict=report.verdict)
        return {"project": name, "observations": [item.to_dict() for items in made.values() for item in items], "report": report.to_dict()}

    @staticmethod
    def reader_branches(reader: RegisterReader) -> dict[str, str]:
        return {branch: sha for branch, sha in reader.remote_branches().items() if branch != "_global"}

    def _append(self, appender: RegisterAppender, branch: str, files: dict[str, str], items: list[Observation], *, sign: bool) -> None:
        message = f"observe {items[0].project}: {len(items)} finding(s)"
        try:
            appender.append(branch, lambda tip: files, message, sign=sign)
        except GitSyncError as exc:
            if sign:
                raise GitSyncError(f"the deletion note must be a signed commit, and Git could not sign it ({exc}). Nothing was recorded.") from exc
            raise

    # -- what to probe ------------------------------------------------------

    @staticmethod
    def _targets(records: dict[str, tuple[str, OmniscienceRecord]]) -> dict[tuple[str, str, str, str], tuple[str, OmniscienceRecord]]:
        """Every distinct (kind, repository, ref, commit) the records name, with the first record (by hash) that names it."""
        found: dict[tuple[str, str, str, str], tuple[str, OmniscienceRecord]] = {}
        for _, (branch, record) in sorted(records.items()):
            keys = [("memory", record.memory_repository, record.memory_ref, record.memory_commit)]
            keys += [("repository", str(row["repository"]), str(row["ref"]), str(row["commit"])) for row in record.published_rows]
            for key in keys:
                found.setdefault(key, (branch, record))
        return found

    def _declared_closes(self, cgshome: Path) -> dict[str, int]:
        """Every ``<repo>:<ref>`` the ledger says was kept on ``ancestors``, with the entry's ``seq``."""
        entries = LedgerStore(MemoryRepository(cgshome).mount_path() / "lgr").read_all_entries()
        return {r.origin.replace("/closed/", "/"): e.seq for e in entries for r in e.relocations}

    def _probe(self, repository: str, ref: str, commit: str, closed: dict[str, int]) -> tuple[ObservationStatus, str, dict[str, Any]]:
        git = self.client.git_runner
        url = self._address(repository)
        if url is None or not _HEX_COMMIT.fullmatch(commit) or ref.startswith("-"):
            return ObservationStatus.UNKNOWN, "none", {"said": "an address this build does not probe"}
        refs, said = git.remote_refs(url)
        if refs is None:
            return ObservationStatus.of_git_failure(said), "ls-remote", {"said": said.splitlines()[0] if said else ""}
        if refs.get(ref) == commit:
            return ObservationStatus.AVAILABLE, "ls-remote", {}
        if not git.remote_holds_commit(url, commit):
            return ObservationStatus.COMMIT_MISSING, "ls-remote+fetch", {"ref_present": ref in refs}
        if ref in refs:
            return ObservationStatus.AVAILABLE, "ls-remote", {"ref_moved_to": refs[ref]}
        name = self._repo_name(repository)
        seq = closed.get(f"{name}:{ref}") if name else None
        if seq is not None:
            return ObservationStatus.CLOSED, "ledger", {"ledger_seq": seq}
        return ObservationStatus.REF_MISSING, "ls-remote+fetch", {}

    @staticmethod
    def _repo_name(repository: str) -> str | None:
        try:
            return parse_repo_id(repository)["repo_name"]
        except (ValueError, KeyError):
            return None

    @staticmethod
    def _address(repository: str) -> str | None:
        """The URL to ask for *repository*, or ``None`` for a form this build will not hand to Git."""
        try:
            parse_repo_id(repository)
        except ValueError:
            return repository if repository.startswith(_URL_FORMS) else None  # never a local path: a record is not trusted to name one
        return MemoryFacts.remote_url(repository)


__all__ = ["OmniscienceObserver"]

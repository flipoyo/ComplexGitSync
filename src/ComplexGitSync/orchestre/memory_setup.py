"""memory_setup — The memory a DEV tree should have and does not declare, offered and set up.

Ring: 3
Contract: Say whether a loaded tree is a DEV tree with no declared memory, propose the repository and `.cgs` entry that fix it, run the fix through the three existing steps, remember a refusal, and word the warning shown when the fix cannot or may not run.
Imports: cgs_format, client, default_memory, errors, git_tree, memory, memory_facts, provider, snapshot_resolver
"""

from __future__ import annotations

import logging
import warnings
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..cgs_format import parse_repo_id
from ..errors import ComplexGitSyncError, GitSyncError
from ..git_tree import ROOT_REPO_ID, TreeProfile, WorkingGitTree
from ..memory.repository import DEFAULT_MEMORY_REPOSITORY, CgsEntryEditor, MemoryRepository
from ..provider import creation_plan
from ..snapshot_resolver import discover_gts_path
from .default_memory import DefaultMemory
from .memory_facts import MemoryFacts

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient


class MemorySetupWarning(UserWarning):
    """A DEV tree recorded work with no declared memory. `cli/` prints its own words for it instead."""


class MemorySetup:
    """Offer a DEV tree the memory repository its ``.cgs`` does not declare.

    A tree holding a private repository is a developer's (`WorkingGitTree.profile`),
    and a developer's memory is meant to be synced. When the ``.cgs`` says
    nowhere to sync it to, the work is still recorded — `DefaultMemory` makes
    a local one — but it has no back-up and no place in the project's shared
    ledger. This class proposes the fix as data, runs it as one call built
    from three existing steps (`repo_create`, `add_memory_repo_cgs`,
    `memory_adopt`), and words the warning for every case where it cannot or
    may not run. It never prompts: asking is `cli/`'s job.
    """

    #: The file under `.cgitsync/` saying the owner declined the proposal once.
    DECLINED = "memory-setup-declined"
    DEFAULT_PROVIDER = "github"

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    @staticmethod
    def due(registry: WorkingGitTree | None) -> bool:
        """Whether *registry* is a DEV tree whose ``.cgs`` declares no memory."""
        return (
            registry is not None
            and ROOT_REPO_ID in registry.repos
            and registry.profile is TreeProfile.DEV
            and not DefaultMemory.declared(registry)
        )

    @staticmethod
    def guess_owner(registry: WorkingGitTree) -> str | None:
        """Who the memory repository should belong to, when nobody said.

        The most frequent owner among the tree's private repositories; with
        none, or a tie, the owner of the project's root repository; with that
        unknown too, ``None`` — and the question has to be answered.
        """
        owners = Counter(
            repo.project_owner_name for repo in registry.values() if repo.effective_private and repo.project_owner_name
        ).most_common(2)
        if owners and (len(owners) == 1 or owners[0][1] > owners[1][1]):
            return owners[0][0]
        root = registry.repos.get(ROOT_REPO_ID)
        return (root.project_owner_name or None) if root is not None else None

    def workspace(self, cgshome: str | Path | None) -> Path:
        """*cgshome*, or the loaded tree's root when none is named."""
        if cgshome is not None:
            return Path(cgshome)
        registry = self.client.get_dependency_registry()
        return registry.get(ROOT_REPO_ID).absolute_path

    def declined_path(self, workspace: Path) -> Path:
        return Path(workspace) / ".cgitsync" / self.DECLINED

    def decline(self, cgshome: str | Path | None = None) -> Path:
        """Remember that the proposal was declined here, so it is asked once."""
        marker = self.declined_path(self.workspace(cgshome))
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("the memory setup proposal was declined; only the warning is shown now\n", encoding="utf-8")
        self.client._log_event("memory_setup_declined", marker=marker)
        return marker

    def warning(self, proposal: dict[str, Any] | None = None) -> str:
        """The plain-words warning for a DEV tree with no declared memory, with the command that fixes it."""
        command = "cgitsync memory setup"
        if proposal is not None:
            command += f" --provider {proposal['provider']} --name {proposal['name']}"
            command += f" --owner {proposal['owner']}" if proposal["owner"] else " --owner <owner>"
        return (
            "warning: this is a developer tree (it holds private repositories) but its .cgs declares no memory, "
            "so this work has no memory back-up and no global ledger record of the contribution. "
            f"To fix it, run: {command}"
        )

    def proposal(
        self,
        cgshome: str | Path | None = None,
        *,
        provider: str | None = None,
        owner: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any] | None:
        """What `memory setup` would do here, or ``None`` when there is nothing to set up.

        Pure: nothing is created, written or asked. ``owner`` is ``None`` when
        it cannot be guessed, and then ``entry``/``create_with`` are too.
        """
        registry = self.client.registry
        if not self.due(registry) or DefaultMemory(self.client).is_published(self.workspace(cgshome)):
            return None
        root = registry.get(ROOT_REPO_ID)
        provider = provider or self.DEFAULT_PROVIDER
        owner = owner or self.guess_owner(registry)
        name = name or DEFAULT_MEMORY_REPOSITORY
        entry = MemoryRepository.mount_entry(owner, root.name, provider=provider, name=name) if owner else None
        plan = creation_plan(parse_repo_id(str(entry["repository"]))) if entry else None
        source = root.source_cgs_path
        answer: dict[str, Any] = {
            "profile": registry.profile.value,
            "provider": provider,
            "owner": owner,
            "name": name,
            "repository": entry["repository"] if entry else None,
            "entry": entry,
            "line": CgsEntryEditor.format_entry(entry) if entry else None,
            "create_with": plan.command if plan else None,
            "cgs": str(source) if source is not None and Path(source).is_file() else None,
            "declined": self.declined_path(self.workspace(cgshome)).is_file(),
        }
        answer["warning"] = self.warning(answer)
        return answer

    def setup(
        self,
        cgshome: str | Path | None = None,
        *,
        provider: str | None = None,
        owner: str | None = None,
        name: str | None = None,
        cgs_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Create the repository, declare it in the ``.cgs``, adopt the local memory — in that order.

        Stops at the first step that fails and says which, and what was left
        done: ``failed`` is ``None`` on success, else ``create``, ``declare``
        or ``adopt``. No credential is read here — creating runs the
        provider's own tool, through `repo_create`.
        """
        proposal = self.proposal(cgshome, provider=provider, owner=owner, name=name)
        if proposal is None:
            raise GitSyncError(
                "nothing to set up: this tree declares a memory already, or holds no private "
                "repository, so its memory is local by design."
            )
        if proposal["owner"] is None:
            raise GitSyncError("no owner can be guessed for the memory repository. Pass one with --owner.")
        workspace = self.workspace(cgshome)
        result: dict[str, Any] = {**proposal, "done": [], "failed": None, "error": None}
        target = cgs_path or proposal["cgs"]
        steps = (
            ("create", lambda: self._create(proposal)),
            ("declare", lambda: self._declare(workspace, target, proposal)),
            ("adopt", lambda: self._adopt(workspace, proposal)),
        )
        for step, run in steps:
            try:
                run()
            except ComplexGitSyncError as exc:
                result.update(failed=step, error=str(exc))
                self.client._log_event("memory_setup", level=logging.WARNING, failed=step, error=str(exc))
                return result
            result["done"].append(step)
        self.declined_path(workspace).unlink(missing_ok=True)
        self.client._log_event("memory_setup", repository=proposal["repository"], failed=None)
        return result

    def _create(self, proposal: dict[str, Any]) -> None:
        answer = self.client.repo_create(str(proposal["repository"]), private=True)
        if answer["created"] == "unavailable":
            raise GitSyncError(f"{answer.get('reason', 'the provider tool is unavailable')}; run it yourself: {answer['command']}")

    def _declare(self, workspace: Path, target: str | Path | None, proposal: dict[str, Any]) -> None:
        if target is None:
            raise GitSyncError("this tree does not say which .cgs it was built from. Name one with --cgs.")
        self.client.add_memory_repo_cgs(target, cgshome=workspace, entry=proposal["entry"])

    def _adopt(self, workspace: Path, proposal: dict[str, Any]) -> None:
        branch = self.client.memory_init(workspace, owner=proposal["owner"])["branch"]
        remote = MemoryFacts.remote_url(str(proposal["repository"]))
        self.client.memory_adopt(workspace, branch=branch, remote=remote)

    def before_recording(self, registry: WorkingGitTree) -> None:
        """Run before every State is written: make the local memory, and flag a DEV tree that lacks one.

        The record is never lost — `DefaultMemory` still makes a local memory —
        and the flag is what lets `cli/` offer the fix after the command. A
        Python caller gets the same news as a :class:`MemorySetupWarning`.
        """
        DefaultMemory(self.client).ensure(registry)
        if self.due(registry):
            self.client.memory_setup_due = True
            self.client._log_event("memory_setup_due", level=logging.WARNING, message=self.warning())
            warnings.warn(self.warning(), MemorySetupWarning, stacklevel=2)

    def notice(self, workspace: Path) -> str:
        """What `memory status` says: the DEV warning on such a tree, else the default memory's notice.

        `memory status` loads no tree, so when this client holds none the
        workspace's latest State is read into a client of its own — never into
        this one — to tell a DEV tree from a USER tree.
        """
        if self.client.registry is None:
            peer = type(self.client)()
            try:
                peer.load_gts(discover_gts_path(str(workspace)))
            except (ComplexGitSyncError, OSError):
                return DefaultMemory(self.client).notice(workspace)
            return MemorySetup(peer).notice(workspace)
        proposal = self.proposal(workspace)
        if proposal is not None:
            return proposal["warning"]
        return DefaultMemory(self.client).notice(workspace)


__all__ = ["MemorySetup", "MemorySetupWarning"]

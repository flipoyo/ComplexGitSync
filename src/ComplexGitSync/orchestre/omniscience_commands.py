"""omniscience_commands — `cgitsync omniscience mount | record`: join the register, and see what would be registered.

Ring: 3
Contract: mount the shared private ``omniscience`` repository into a
    developer tree (declare it in the ``.cgs``, then clone the project's
    branch, or the index branch), and build, without writing anything, the
    registration record a State would make. Registering is OmniscienceAppend's;
    nothing here appends, pushes, or changes the memory.
Imports: cgs_format, errors, git_branch, git_repo, git_tree, git_tree_branch, gts_document, memory, memory_facts, omniscience_record, omniscience_repository, operating_space, registry

A record is built from the **committed** memory, because a reader must be
able to fetch what it names. So the State must be folded into the mounted
memory, the memory's worktree clean, and its HEAD on its remote: a State still
pending, a memory with uncommitted changes, or a commit nobody pushed is
refused by name, with the command that fixes it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..cgs_format import repo_identifier
from ..errors import GitSyncError
from ..git_branch import DEFAULT_BRANCH
from ..git_repo import RefKind
from ..git_tree import ROOT_REPO_ID, TreeProfile
from ..git_tree_branch import GitTreeBranches
from ..gts_document import GtsDocument
from ..memory.commit_log import CommitLog
from ..memory.ledger_store import LedgerStore
from ..memory.repository import MOUNT_PATH, MemoryRepository
from ..memory.states import STATE_DIR_NAME
from ..omniscience_record import OmniscienceRecord
from ..omniscience_repository import (
    DEFAULT_OMNISCIENCE_REPOSITORY,
    GLOBAL_BRANCH,
    OMNISCIENCE_MOUNT_PATH,
    OmniscienceRepository,
)
from ..operating_space import OperatingSpace
from ..registry import RegistryTranslator
from .memory_facts import MemoryFacts

if TYPE_CHECKING:
    from .client import ComplexGitSyncClient


class OmniscienceCommands:
    """The two commands that do not write to the register."""

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def mount(
        self,
        cgs_path: str | Path | None,
        cgshome: str | Path,
        *,
        repository: str = DEFAULT_OMNISCIENCE_REPOSITORY,
        contribute: bool = False,
    ) -> dict[str, Any]:
        """Declare the Omniscience repository in *cgs_path* and clone it at ``.omniscience``.

        *cgs_path* defaults to the ``.cgs`` the tree was built from. Refused in a
        USER tree, which holds no private repository and contributes nothing,
        before anything is touched. Running it twice
        changes nothing. The clone is the project's branch when the remote
        has one, else the index branch; a remote with neither is refused.
        """
        registry = self.client.get_dependency_registry()
        if registry.profile is not TreeProfile.DEV:
            raise GitSyncError(
                "omniscience mount was refused: this tree holds no private repository, so it is a user's tree, "
                "and Omniscience is for developer trees. Nothing was changed."
            )
        source = cgs_path or registry.get(ROOT_REPO_ID).source_cgs_path
        if source is None:
            raise GitSyncError("this tree was not built from a .cgs; pass the .cgs to edit with --cgs.")
        entry = OmniscienceRepository.mount_entry(repository, contribute=contribute, project_name=registry.project_name)
        declared = self.client.add_memory_repo_cgs(source, cgshome=cgshome, entry=entry)
        existing = next((e for e in registry.values() if e.relative_path == Path(OMNISCIENCE_MOUNT_PATH)), None)
        declared["mismatch"] = (
            f"the .cgs already declares {OMNISCIENCE_MOUNT_PATH} {'writable' if existing.writable else 'read-only'}; "
            "edit that entry to change it."
            if not declared["added"] and existing is not None and existing.writable != contribute
            else None
        )
        destination = Path(cgshome) / OMNISCIENCE_MOUNT_PATH
        git = self.client.git_runner
        if (destination / ".git").exists():
            return {**declared, "destination": str(destination), "cloned": False, "branch": git.current_branch(destination)}
        remote = MemoryFacts.remote_url(repository)
        project_branch = GitTreeBranches(registry, git).tree_branch or DEFAULT_BRANCH
        branch = next(
            (name for name in (OmniscienceRepository.branch(registry.project_name, project_branch), GLOBAL_BRANCH)
             if git.remote_branch_exists(remote, name)),
            None,
        )
        if branch is None:
            raise GitSyncError(
                f"{remote} has neither this project's branch nor {GLOBAL_BRANCH!r}: nothing has been registered "
                "there yet, so there is nothing to clone. The entry is declared in the .cgs."
            )
        git.clone(remote, destination, branch=branch)
        self.client._log_event("omniscience_mount", destination=destination, branch=branch, contribute=contribute)
        return {**declared, "destination": str(destination), "cloned": True, "branch": branch}

    def record(self, cgshome: str | Path, state: str) -> dict[str, Any]:
        """The record that registering *state* would write, and its hashes. Writes nothing.

        *state* is a State's hash or any unambiguous prefix. Everything is
        read from the committed memory, so the State must have been folded
        into it and pushed (``memory push``).
        """
        workspace = Path(cgshome)
        mount = MemoryRepository(workspace).mount_path()
        git = self.client.git_runner
        if not (mount / ".git").exists():
            raise GitSyncError(f"{mount} is not a repository: there is no committed memory to register. Run 'cgitsync memory setup'.")
        document, state_hash = self._committed_state(mount, MemoryFacts.normalise_state_argument(state))
        state_tree = RegistryTranslator.from_gts_document(document, tree_root=workspace)
        space = OperatingSpace(state_tree)
        if space.os_id is None:
            raise GitSyncError(f"this State's project name cannot be registered: {space.problem or 'it has none'}.")
        root = state_tree.get(ROOT_REPO_ID)
        if root.current_ref_kind is not RefKind.BRANCH or not root.current_ref_name:
            raise GitSyncError("this State was not recorded on a branch, so there is no branch head to register.")
        if git.has_uncommitted_changes(mount):
            raise GitSyncError(f"the memory at {mount} has uncommitted changes. Run 'cgitsync memory push' first.")
        head = LedgerStore(mount / "lgr").read_head()
        if head is None:
            raise GitSyncError(f"the memory at {mount} has no ledger entry, so there is no ledger tip to register.")
        commit, memory_branch = git.rev_parse_head(mount), git.current_branch(mount)
        ref = f"refs/heads/{memory_branch}"
        if git.remote_ref_commit(mount, "origin", ref) != commit:
            raise GitSyncError(f"the memory's commit {commit[:12]} is not on its remote, or the remote cannot be asked. Run 'cgitsync memory push' first.")
        entry = next((e for e in self.client.get_dependency_registry().values() if e.relative_path == Path(MOUNT_PATH)), None)
        if entry is None:
            raise GitSyncError("this tree declares no memory mount, so a memory repository cannot be named.")
        rows = CommitLog(mount).read(state_hash)["published"]
        record = OmniscienceRecord(
            project=state_tree.project_name or "",
            os_id=space.os_id,
            project_branch=root.current_ref_name,
            state_id=f"state({state_hash})",
            gittree_root=document.gittree_root or "",
            local_ledger_tip=head.entry_hash,
            local_memory_git_commit=commit,
            memory_repository=repo_identifier(entry),
            memory_mount=MOUNT_PATH,
            memory_ref=ref,
            memory_commit=commit,
            published_state_id=CommitLog.digest_of_rows([], rows) if rows else None,
            published_rows=tuple(_evidence(row) for row in rows),
            recorded_at=f"{self.client.clock.now():%Y-%m-%dT%H:%M:%SZ}",
            contributor=git.configured_email(root.absolute_path) or "",
        )
        return {
            "record": record.to_dict(),
            "record_hash": record.record_hash,
            "os_state_hash": record.os_state_hash,
            "filename": record.filename,
            "leaf": record.leaf_digest.hex(),
        }

    @staticmethod
    def _committed_state(mount: Path, prefix: str) -> tuple[GtsDocument, str]:
        found = sorted(path for path in (mount / STATE_DIR_NAME).glob("*.gts") if path.stem.startswith(prefix))
        if len(found) > 1:
            raise GitSyncError(f"{prefix!r} matches more than one State in the memory: {', '.join(p.stem[:12] for p in found)}.")
        if not found:
            raise GitSyncError(
                f"no State beginning with {prefix!r} is in the committed memory at {mount}. "
                "If it is recent, 'cgitsync memory push' folds it in; 'cgitsync memory list' shows what is there."
            )
        return GtsDocument.from_toml(found[0]), found[0].stem


def _evidence(row: dict[str, Any]) -> dict[str, Any]:
    """One publication row as the register's evidence: repository, ref, commit, and the remote's witness when there is one."""
    kept = {"repository": row["remote"], "ref": row["ref"], "commit": row["sha"]}
    kept.update({key: row[key] for key in ("remote_commit", "observed_at") if key in row})
    return kept


__all__ = ["OmniscienceCommands"]

"""gitignore_sync — Keep `.gitignore` coherent across the tree and commit what that changes.

Ring: 3
Contract: Keep `.gitignore` coherent across the tree and commit what that changes.
Imports: client, errors, git_tree, master, reports
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..errors import (
    GitSyncError,
)

if TYPE_CHECKING:
    pass
from ..git_tree import (
    cgitsync_managed_state_paths,
    iter_tree,
    sync_gitignore,
)
from ..master import MasterConfig
from .reports import (
    GitignoreSyncEntry,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient


class GitignoreSync:
    """Keep `.gitignore` coherent across the tree and commit what that changes.

    A collaborator of :class:`~ComplexGitSync.orchestre.client.ComplexGitSyncClient`,
    which keeps the state and exposes every method here unchanged. Everything
    that is not this class's own private helper is reached through the
    client, so a caller that patches a client method is still obeyed.
    """

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    # Pre-existing complexity debt from before C90 was enabled (P6,
    # .agent/.local/.localSpec/DevTickets/archive/20260828_Isolation_DevPlanTicket.md) — flagged, not fixed
    # under this ticket, since a real refactor of the .gitignore sync flow
    # risks behaviour change under time pressure. New code is enforced at
    # 12.
    def _sync_gitignore_lifecycle(  # noqa: C901
        self,
        *,
        pre_pull: bool = True,
        commit: bool = False,
    ) -> tuple[GitignoreSyncEntry, ...]:
        """Run the ``.gitignore`` lifecycle sync (DevPlanTicket Milestones 1-2).

        Every repo with children is safely pulled (parent-first, via
        :func:`iter_tree`) before its ``.gitignore`` is written, so the
        write starts from an up-to-date base. If the safe pull fails for
        any such repo, no ``.gitignore`` is written at all and this raises
        :exc:`~.errors.GitSyncError` immediately — no forcing, no silent
        degradation. (The ``--force-gitignore-sync`` fallback to
        ``pull-force`` was removed: run ``pull-force`` yourself if a pull
        cannot sync.)

        Returns one :class:`GitignoreSyncEntry` per repo whose
        ``.gitignore`` was actually created or modified, and also records
        them on :attr:`last_gitignore_sync` for the CLI to report.

        A repo whose HEAD is detached is skipped by the pre-pull: there is
        no branch to fast-forward, and pulling a branch guessed from the
        ``.cgs`` would move the checkout off the commit the caller asked
        for. Its ``.gitignore`` is still written.

        *pre_pull* can be set to ``False`` when the caller already pulled
        every repo in the tree immediately beforehand (e.g. ``restart()``'s
        own tree-wide pull already satisfies this step; repeating it here
        would just be a redundant no-op fast-forward per repo).

        *commit* gates Phase C (``--commit-gitignore``): when ``False``
        (the default), nothing is staged, committed, or pushed — the sync
        only writes the file and reports what changed. When ``True``, each
        changed repo has its ``.gitignore`` staged (and only that file),
        committed, and pushed — see :meth:`_commit_and_push_gitignore_sync`.
        """
        registry = self.client.registry
        assert registry is not None

        if pre_pull:
            for entry in iter_tree(registry):
                if not registry.children_of(entry.repo_id):
                    continue
                current_branch = self.client.git_runner.current_branch(entry.absolute_path)
                if current_branch is None:
                    # Detached HEAD: no branch to fast-forward. Guessing one
                    # from the .cgs would pull a branch the caller never asked
                    # for, moving the checkout off the exact commit under test
                    # — and a CI pull-request checkout is always detached, so
                    # this is the common case, not an edge case. The
                    # .gitignore is still written below; only the pull is
                    # skipped, because there is nothing it could safely do.
                    self.client._log_event(
                        "gitignore_pre_pull_skipped",
                        repo_name=entry.name,
                        absolute_path=str(entry.absolute_path),
                        reason="detached HEAD: no branch to fast-forward",
                    )
                    continue
                try:
                    self.client.git_runner.pull(entry.absolute_path, ref_name=current_branch)
                except GitSyncError as exc:
                    raise GitSyncError(
                        f"gitignore sync preflight failed: could not safely pull {entry.name!r} "
                        f"({entry.absolute_path}) before writing its .gitignore: {exc}"
                    ) from exc

        pending_paths: dict[str, tuple[str, ...]] = {}
        for entry in iter_tree(registry):
            children = registry.children_of(entry.repo_id)
            relative_paths = {
                child.absolute_path.relative_to(entry.absolute_path).as_posix() for child in children
            }
            if entry.parent_id is None:
                for managed_path in cgitsync_managed_state_paths(entry):
                    as_posix = managed_path.as_posix()
                    relative_paths.add(f"{as_posix}/" if as_posix == ".cgitsync" else as_posix)
            if not relative_paths:
                continue
            gitignore_path = entry.absolute_path / ".gitignore"
            try:
                existing_lines = gitignore_path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                existing_lines = []
            missing = tuple(sorted(path for path in relative_paths if path not in existing_lines))
            if missing:
                pending_paths[entry.repo_id] = missing

        changed_repo_ids = sync_gitignore(registry)

        synced_entries = tuple(
            GitignoreSyncEntry(
                repo_id=repo_id,
                name=registry.get(repo_id).name,
                absolute_path=registry.get(repo_id).absolute_path,
                added_paths=pending_paths.get(repo_id, ()),
                committed=commit,
            )
            for repo_id in changed_repo_ids
        )
        for record in synced_entries:
            self.client._log_event(
                "gitignore_sync_updated",
                repo_id=record.repo_id,
                repo_name=record.name,
                absolute_path=record.absolute_path,
                added_paths=record.added_paths,
            )
        if commit and synced_entries:
            self._commit_and_push_gitignore_sync(synced_entries)
        self.client.last_gitignore_sync = synced_entries
        return synced_entries

    def _commit_and_push_gitignore_sync(self, entries: tuple[GitignoreSyncEntry, ...]) -> None:
        """Phase C (DevPlanTicket Milestone 2, ``--commit-gitignore``).

        Only called once the caller has explicitly approved it. For each
        entry: stage ``.gitignore`` alone (never ``git add --all`` — this
        must not sweep in unrelated dirty work already in progress),
        commit with a message listing exactly which children were added,
        then push. Never force-pushes.
        """
        for record in entries:
            current_branch = self.client.git_runner.current_branch(record.absolute_path)
            self.client.git_runner.stage_path(record.absolute_path, ".gitignore")
            message_lines = [
                "chore(cgitsync): sync .gitignore for nested repo tree",
                "",
                "Added:",
            ]
            message_lines.extend(f"  {path}" for path in record.added_paths)
            user_name, user_email = MasterConfig.resolve_identity(record.absolute_path, self.client.git_runner)
            self.client.git_runner.commit(
                record.absolute_path,
                "\n".join(message_lines),
                user_name=user_name,
                user_email=user_email,
            )
            self.client.git_runner.push(record.absolute_path, ref_name=current_branch)
            self.client._log_event(
                "gitignore_sync_committed",
                repo_id=record.repo_id,
                repo_name=record.name,
                absolute_path=record.absolute_path,
                added_paths=record.added_paths,
            )


__all__ = ["GitignoreSync"]

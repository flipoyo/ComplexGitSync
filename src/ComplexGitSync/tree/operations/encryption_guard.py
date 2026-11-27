"""encryption_guard — Refuse a commit or a push that would send a file in clear where git-crypt is asked for.

Ring: 2
Contract: reads each repository in scope through :class:`~..git_repo.git_crypt.GitCrypt` and reports one blocking
    diagnostic per repository that holds a plaintext file under a path whose ``filter`` attribute is git-crypt
    (EncryptedRepos E1). Writes nothing and has no bypass (§5 Q1): a file that should be in clear belongs outside
    the encrypted pattern in ``.gitattributes``.
Imports: errors, git_repo, git_tree, preflight
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...errors import GitSyncError
from ...git_repo.git_crypt import GitCrypt
from ...git_repo.git_repo import RepoScope
from ..git_tree import WorkingGitTree, iter_tree_leaf_first
from .preflight import Preflight, PreflightDiagnostic, PreflightSeverity

if TYPE_CHECKING:
    from ...orchestre import GitRunner


class EncryptionGuard:
    """The plaintext check, for the index (``commit``) and for the commits a push would send (``push``)."""

    #: Paths named in a message before it says "and N more".
    SHOWN = 5

    @staticmethod
    def assert_encrypted(tree: WorkingGitTree, git_runner: GitRunner, *, operation_name: str, scope: RepoScope = RepoScope.ALL) -> None:
        """Raise when a file that should be encrypted would leave in clear.

        ``commit`` is checked after staging, on the index: that is the only
        place the blob a commit is about to keep exists. ``push`` is checked
        on the commits it would send, and ``freeze_release``, which commits
        and pushes, on both. Nothing moves anywhere when one repository is
        refused.
        """
        blocking: list[PreflightDiagnostic] = []
        if operation_name in ("commit", "freeze_release"):
            blocking += EncryptionGuard.diagnostics(tree, git_runner, scope=scope, staged=True)
        if operation_name in ("push", "freeze_release"):
            blocking += EncryptionGuard.diagnostics(tree, git_runner, scope=scope, staged=False)
        if blocking:
            raise GitSyncError(Preflight._format_preflight_error(operation_name, blocking, []))

    @staticmethod
    def diagnostics(tree: WorkingGitTree, git_runner: GitRunner, *, scope: RepoScope, staged: bool) -> list[PreflightDiagnostic]:
        """One blocking diagnostic per repository holding a plaintext file under a git-crypt path.

        *staged* reads the index; otherwise the commits a push would send.
        A clone that was never unlocked is the usual cause, and the message
        names both ways out.
        """
        crypt = GitCrypt(git_runner)
        found: list[PreflightDiagnostic] = []
        for repo in iter_tree_leaf_first(tree, scope):
            paths = crypt.staged_plaintext(repo.absolute_path) if staged else crypt.pushed_plaintext(repo.absolute_path)
            if paths:
                found.append(PreflightDiagnostic(PreflightSeverity.BLOCKING_ERROR, repo.name, EncryptionGuard._message(paths, staged)))
        return found

    @staticmethod
    def _message(paths: list[str], staged: bool) -> str:
        shown = ", ".join(paths[: EncryptionGuard.SHOWN])
        if len(paths) > EncryptionGuard.SHOWN:
            shown += f" and {len(paths) - EncryptionGuard.SHOWN} more"
        if staged:
            return (
                f"{len(paths)} file(s) under a git-crypt path would be committed in clear: {shown}. "
                "This clone is not unlocked. Run 'git-crypt unlock' with the key, or unstage them ('git restore --staged <path>')."
            )
        return (
            f"{len(paths)} file(s) under a git-crypt path are in clear in the commits to push: {shown}. "
            "They were committed in a clone that was not unlocked. Run 'git-crypt unlock' with the key "
            "and commit them again, or drop those commits, before pushing."
        )


__all__ = ["EncryptionGuard"]

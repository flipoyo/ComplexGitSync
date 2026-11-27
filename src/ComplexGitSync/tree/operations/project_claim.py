"""project_claim — mark a branch made for an anonymous project as that project's.

Ring: 2
Contract: after a branch named by an alias is created in a shared repository (``branch create``, ``memory branch``),
    commit one file at its root, ``.cgitsync-alias``, holding the claim: the SHA-256 of the project's real name.
    That is how a second machine, or a second project, tells an alias that is its own from one that is taken
    (AnonymousProject §2). The file says nothing an alias does not, in ``"hash"`` mode, and in ``"sequence"`` mode
    only what the owner chose (Q2). The repository is put back on the branch it was on, with nothing else moved.
Imports: git_repo
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ...git_repo.project_alias import ProjectAlias

if TYPE_CHECKING:
    from ...orchestre import GitRunner


class ProjectClaim:
    """The one writer of the claim file."""

    #: What the commit that carries the claim says; it names nothing about the project.
    MESSAGE = "cgitsync: claim of this alias"

    @staticmethod
    def stamp(git_runner: GitRunner, repo_path: Path | str, branch: str, claim: str | None) -> bool:
        """Commit the claim on *branch*, which already exists. ``False`` when there is no claim or it is already there."""
        if not claim:
            return False
        path = Path(repo_path)
        previous = git_runner.current_branch(path)
        if previous != branch:
            git_runner.checkout(path, branch)
        try:
            claim_file = path / ProjectAlias.CLAIM_FILE
            if claim_file.is_file() and claim_file.read_text(encoding="utf-8").strip() == claim:
                return False
            claim_file.write_text(claim + "\n", encoding="utf-8")
            git_runner.stage_path(path, ProjectAlias.CLAIM_FILE)
            git_runner.commit(path, ProjectClaim.MESSAGE)
            return True
        finally:
            if previous is not None and previous != branch:
                git_runner.checkout(path, previous)


__all__ = ["ProjectClaim"]

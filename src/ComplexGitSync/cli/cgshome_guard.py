"""A writing command refuses a workspace that ``$CGSHOME`` chose and the current directory is not in.

A shell that still exports an earlier workspace's ``CGSHOME`` makes every
command read and write that workspace, while its output looks exactly like the
tree the user is standing in (ticket CgshomeMismatch). Reads keep the warning
``_cgshome_warnings`` prints; a writing command is refused before anything is
touched. ``--search-dir``, an explicit snapshot and ``unset CGSHOME`` are the
ways out, and none of them is refused: only the *implicit* choice is.

The Python API is unchanged: a caller names its workspace, so there is no
hidden variable to guard against.
"""

from __future__ import annotations

from ..errors import GitSyncError
from ..tree.snapshot_resolver import CGSHOME_ORIGIN_ENVIRONMENT, CgshomeResolution


class CgshomeGuard:
    """Remembers the last workspace ``$CGSHOME`` chose that does not hold the current directory, and refuses a writing command on it."""

    #: The CLI names of the commands that write a workspace (``WRITING_METHODS``, as the CLI spells them).
    WRITING_COMMANDS = frozenset(
        {
            "add", "autofix", "branch-close", "branch-create", "branch-delete", "checkout", "commit", "fetch", "merge",
            "pull", "pull-force", "push", "release-freeze", "release-load", "rm", "submodules-import", "submodules-init",
            "tag", "verify-repair", "memory-adopt", "memory-branch", "memory-merge", "memory-migrate", "memory-push",
            "memory-reboot", "memory-setup", "self-history-add", "self-history-adopt",
        }
    )

    _chosen: CgshomeResolution | None = None

    @classmethod
    def note(cls, cgshome: CgshomeResolution) -> None:
        """Record *cgshome* when ``$CGSHOME`` chose it and the current directory is not inside it."""
        cls._chosen = cgshome if cgshome.origin == CGSHOME_ORIGIN_ENVIRONMENT and not cgshome.contains_cwd else None

    @classmethod
    def reset(cls) -> None:
        """Forget what an earlier command in this process noted."""
        cls._chosen = None

    @classmethod
    def check(cls, command_name: str) -> None:
        """Raise when *command_name* writes and the workspace it resolved was chosen by ``$CGSHOME`` alone."""
        chosen, cls._chosen = cls._chosen, None
        if chosen is None or command_name not in cls.WRITING_COMMANDS:
            return
        raise GitSyncError(
            f"{command_name} refused: $CGSHOME ({chosen.path}) does not contain the current directory, so this command would "
            f"write that workspace, not this one; nothing was changed. Run 'unset CGSHOME', or pass --search-dir "
            f"<the workspace you mean>, to say which."
        )


__all__ = ["CgshomeGuard"]

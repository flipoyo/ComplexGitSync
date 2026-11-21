"""encryption_notice — after an install, name the repositories that are encrypted and still locked.

Ring: 3
Contract: after ``bootstrap`` or ``initialise``, print one line to stderr naming each repository whose
    ``.gitattributes`` asks for git-crypt and whose clone has no filter configured (EncryptedRepos E2). Nothing is
    printed for a tree with none. cgitsync never unlocks: the line says only what the owner must do.
Imports: git_repo, git_tree
"""

from __future__ import annotations

import sys

from ..git_repo.git_crypt import GitCrypt
from ..orchestre import ComplexGitSyncClient
from ..tree.git_tree import iter_tree_leaf_first

#: The commands that leave a freshly cloned tree, which no one has unlocked yet.
INSTALLS = ("bootstrap", "initialise")


def announce(client: ComplexGitSyncClient, command_name: str) -> None:
    """Say which repositories are encrypted and locked, after an install."""
    if command_name not in INSTALLS or getattr(client, "registry", None) is None:
        return
    probe = GitCrypt(client.git_runner)
    locked = [repo.name for repo in iter_tree_leaf_first(client.get_dependency_registry()) if probe.state(repo.absolute_path) == GitCrypt.LOCKED]
    if locked:
        verb = "repository is" if len(locked) == 1 else "repositories are"
        print(f"{len(locked)} {verb} encrypted and locked: {', '.join(locked)}. Unlock with git-crypt before committing to {'it' if len(locked) == 1 else 'them'}.", file=sys.stderr)


__all__ = ["INSTALLS", "announce"]

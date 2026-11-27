"""nested_hint — say what ``nested_config = "auto"`` did, when it did less than a project that keeps its spec in ``.config/`` meant.

Ring: 1 (filesystem only, no subprocess)
Contract: ``auto`` reads only a ``*.cgs`` at the root of a repository (NestedInstall D3). A repository that keeps its
    spec in ``.config/`` therefore mounts as a plain repository, with none of what its own spec declares. ``NestedHint``
    names that case, the files and the spelling that mounts one, at install (``warn_leaf``) and in ``status``
    (``status_lines``), and names a root ``.cgs`` that ``auto`` did graft (``warn_graft``). It changes no rule: the
    mounted tree, and so every State, is what it was.
Imports: git_repo, git_tree
"""

from __future__ import annotations

import warnings
from pathlib import Path

from ..git_repo.git_repo import DiscoveryState, NodeType, WorkingRepo
from .git_tree import ROOT_REPO_ID, WorkingGitTree


class NestedHint:
    """Names what a repository's own spec would have added, and was not read."""

    #: Where a project that conforms to the agentic layout keeps its specs.
    CONFIG_DIR = ".config"

    @classmethod
    def config_files(cls, repo_root: Path) -> tuple[Path, ...]:
        """The ``*.cgs`` files in ``<repo_root>/.config``, sorted: what ``auto`` does not read."""
        return tuple(sorted(path for path in (repo_root / cls.CONFIG_DIR).glob("*.cgs") if path.is_file()))

    @classmethod
    def leaf_hint(cls, entry: WorkingRepo) -> str | None:
        """The sentence for *entry* if it holds specs in ``.config/`` that its mount did not read, else ``None``."""
        files = cls.config_files(entry.absolute_path)
        if not files:
            return None
        names = ", ".join(f"{cls.CONFIG_DIR}/{path.name}" for path in files)
        return (
            f"{entry.name} was mounted as a plain repository: nested_config = \"auto\" reads only a .cgs at the root of "
            f"a repository, and finds none, so what its own spec declares is not part of the tree. It holds {names}. "
            f"To mount one, write nested_config = \"{cls.CONFIG_DIR}/{files[0].name}\" on its entry in your .cgs; "
            "to say it is meant as a plain repository, write nested_config = \"disabled\"."
        )

    @classmethod
    def warn_leaf(cls, entry: WorkingRepo, nested_config: str) -> None:
        """At install: *entry* resolved to a leaf under ``auto``; say so when ``.config/`` held a spec."""
        hint = cls.leaf_hint(entry) if nested_config == "auto" else None
        if hint:
            warnings.warn(hint, stacklevel=3)

    @staticmethod
    def warn_graft(entry: WorkingRepo, nested_path: Path, count: int, nested_config: str) -> None:
        """At install: ``auto`` read *nested_path* from the root of *entry* and brought *count* repositories into the tree."""
        if nested_config == "auto":
            warnings.warn(
                f"{entry.name}: nested_config = \"auto\" grafted {nested_path.name} from its root, bringing {count} "
                f"repositor{'y' if count == 1 else 'ies'} into the tree. To keep them out, write nested_config = \"disabled\" "
                "on its entry; to mount another spec, name it.",
                stacklevel=3,
            )

    @classmethod
    def status_lines(cls, registry: WorkingGitTree) -> list[str]:
        """One ``warning:`` line per repository mounted as a plain one while its ``.config/`` holds a spec.

        A State does not record ``nested_config``, but it records ``discovery_state``: an entry the user wrote
        ``"disabled"`` for says so, and is left alone.
        """
        return [
            f"warning: {hint}"
            for entry in registry.values()
            if entry.repo_id != ROOT_REPO_ID
            and entry.node_type is NodeType.LEAF
            and entry.discovery_state is DiscoveryState.RESOLVED
            and entry.absolute_path is not None
            and (hint := cls.leaf_hint(entry))
        ]


__all__ = ["NestedHint"]

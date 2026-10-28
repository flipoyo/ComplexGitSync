"""legacy_paths — the import paths ``SrcLayout`` moved, kept answering for one release.

Ring: 0 (pure; installs an import hook, touches no file)
Contract: ``import ComplexGitSync.<old path>`` still gives the module that now
    lives at its new path — the *same* module object, so a name patched through
    one path is patched through both. Nothing here is used by the package
    itself: every internal import names the new path.
Imports: none

Why it exists
-------------
``SrcLayout`` split the flat package into ``git_repo/``, ``merkle/``,
``tree/``, ``omniscience/`` and ``operating_space/``. A caller outside this
repository wrote ``from ComplexGitSync.memory.store import ...`` or
``from ComplexGitSync.git_runner import GitRunner``; those lines keep working
for **one release** (owner, SrcLayout D3) and the user guide's *What changed*
names the new paths. The release after drops this module and the line that
installs it.

``ComplexGitSync.git_repo`` and ``ComplexGitSync.operating_space`` are not in the table: they are the new packages, which
re-exports what the old module held.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
from types import ModuleType

_PREFIX = "ComplexGitSync."

#: old path (below ``ComplexGitSync.``) -> new path. A package name stands for
#: every module under it.
MOVED_PATHS: dict[str, str] = {
    "autofix": "tree.autofix",
    "cgs_format": "tree.cgs_format",
    "clone_guard": "git_repo.clone_guard",
    "commit_message": "tree.commit_message",
    "config_document": "tree.config_document",
    "config_document_io": "tree.config_document_io",
    "discovery": "tree.discovery",
    "environment_spec": "tree.environment_spec",
    "git_branch": "git_repo.git_branch",
    "git_runner": "tree.git_runner",
    "git_tree": "tree.git_tree",
    "git_tree_branch": "tree.git_tree_branch",
    "global_index": "omniscience.global_index",
    "gts_document": "tree.gts_document",
    "master": "tree.master",
    "memory": "tree.memory",
    "omniscience_answer": "omniscience.omniscience_answer",
    "omniscience_completeness": "omniscience.omniscience_completeness",
    "omniscience_observation": "omniscience.omniscience_observation",
    "omniscience_record": "omniscience.omniscience_record",
    "omniscience_repository": "omniscience.omniscience_repository",
    "operations": "tree.operations",
    "orchestre.omniscience": "omniscience.omniscience",
    "orchestre.omniscience_appender": "omniscience.omniscience_appender",
    "orchestre.omniscience_checkpoints": "omniscience.omniscience_checkpoints",
    "orchestre.omniscience_commands": "omniscience.omniscience_commands",
    "orchestre.omniscience_control": "omniscience.omniscience_control",
    "orchestre.omniscience_memory": "omniscience.omniscience_memory",
    "orchestre.omniscience_observer": "omniscience.omniscience_observer",
    "orchestre.omniscience_reader": "omniscience.omniscience_reader",
    "orchestre.omniscience_view": "omniscience.omniscience_view",
    "paths": "tree.paths",
    "project_version": "tree.project_version",
    "provider": "git_repo.provider",
    "registry": "tree.registry",
    "settings": "tree.settings",
    "snapshot_resolver": "tree.snapshot_resolver",
    "ticket_gate": "tree.ticket_gate",
    "toolchain": "tree.toolchain",
    "tree_env": "tree.tree_env",
    "workspace_lock": "tree.workspace_lock",
}


def new_path(old: str) -> str | None:
    """The new path of *old* (a dotted path below the package), or None if it never moved."""
    parts = old.split(".")
    for end in range(len(parts), 0, -1):
        moved = MOVED_PATHS.get(".".join(parts[:end]))
        if moved is not None:
            return ".".join([moved, *parts[end:]])
    return None


class _AliasLoader(importlib.abc.Loader):
    """Hands back the module that lives at the new path."""

    def __init__(self, target: str) -> None:
        self._target = target
        self._real_spec: importlib.machinery.ModuleSpec | None = None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        module = importlib.import_module(_PREFIX + self._target)
        self._real_spec = module.__spec__
        return module

    def exec_module(self, module: ModuleType) -> None:
        # The import machinery has just stamped the old name on the module.
        module.__spec__ = self._real_spec


class LegacyPathFinder(importlib.abc.MetaPathFinder):
    """Answers ``import ComplexGitSync.<old path>`` with the module that moved."""

    def find_spec(self, fullname: str, path: object = None, target: object = None):
        if not fullname.startswith(_PREFIX):
            return None
        moved = new_path(fullname[len(_PREFIX):])
        if moved is None:
            return None
        return importlib.machinery.ModuleSpec(fullname, _AliasLoader(moved))


def install() -> None:
    """Put the finder first on ``sys.meta_path``, once."""
    if not any(isinstance(finder, LegacyPathFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, LegacyPathFinder())


__all__ = ["MOVED_PATHS", "LegacyPathFinder", "install", "new_path"]

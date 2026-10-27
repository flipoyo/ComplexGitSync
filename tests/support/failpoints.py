"""failpoints — the named places a write can fail, each mapped to the callable that is patched.

Built from the operation matrix in ``AdditionalSpecs.md`` (*How a tree-wide write fails*).
``test_failpoints_targets_exist`` fails loudly when a rename leaves one pointing at nothing,
so a failpoint can never silently stop testing what its name says.
"""

from __future__ import annotations

import importlib

#: name -> dotted path of the callable a fault is injected into.
FAILPOINTS: dict[str, str] = {
    "push.repo": "ComplexGitSync.git_runner.GitRunner.push",
    "tag.create": "ComplexGitSync.git_runner.GitRunner.create_tag",
    "tag.annotated": "ComplexGitSync.git_runner.GitRunner.create_annotated_tag",
    "commit.repo": "ComplexGitSync.git_runner.GitRunner.commit",
    "state.write": "ComplexGitSync.memory.states.MemoryStates.write_atomically",
    "ledger.entry": "ComplexGitSync.memory.ledger_store.LedgerStore.write_entry",
    "ledger.head": "ComplexGitSync.memory.ledger_store.LedgerStore.write_head",
}


def resolve(dotted: str) -> object:
    """The callable *dotted* names, or ``AttributeError`` / ``ImportError`` when it is gone."""
    module_path, _, attribute = dotted.rpartition(".")
    owner = module_path
    while True:
        try:
            holder = importlib.import_module(owner)
            break
        except ImportError:
            owner, _, _ = owner.rpartition(".")
    for part in module_path[len(owner) :].lstrip(".").split("."):
        if part:
            holder = getattr(holder, part)
    return getattr(holder, attribute)

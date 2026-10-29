"""op_levels — the Proof-of-Existence levels, the command each calls for, and the leaves ALPHA and BETA share.

Ring: 3 (reads the client's tree; calls no provider, writes nothing)
Contract: ``PoE``, a level's four-bit vector over ``{NAME, NODE, EDGE, OP}`` (``.PUBLIC = {1,1,1,1}`` the
    invariant) and the levels by their three-letter, case-sensitive code; ``LevelCommand``, the one
    ``cgitsync`` command a level calls for on ``YYYY.node``; ``Leaves``, whether a leaf of the tree is the leaf the
    other side holds. ``OP`` (``op_boundary.py``) is what uses them.
Imports: errors, merkle, git_tree, gts_document

BeforeCGS-BETA §6 and §7.0, owner 2026-10-29.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import GitSyncError
from ..merkle import MerkleRoot
from ..tree.git_tree import TreeProfile
from ..tree.gts_document import GtsDocument

if TYPE_CHECKING:
    from .client import ComplexGitSyncClient


@dataclass(frozen=True, slots=True)
class PoE:
    """A level's Proof-of-Existence vector over ``{NAME, NODE, EDGE, OP}``; four bits, ``.PUBLIC`` the invariant."""

    name: bool
    node: bool
    edge: bool
    op: bool

    @property
    def sigma(self) -> int:
        """How many of the four parts the level exposes."""
        return sum((self.name, self.node, self.edge, self.op))

    @staticmethod
    def of_profile(profile: TreeProfile) -> PoE:
        """The level a tree's profile stands for, until MemoryMount records it at install."""
        return USER if profile is TreeProfile.USER else DEV


PUBLIC = PoE(True, True, True, True)
USER = PoE(True, True, False, False)
DEV = PoE(True, False, False, False)
OPX = PoE(False, False, False, False)

#: The levels by their three-letter, case-sensitive code (owner, AD8): ``USR`` is ``USER``'s, as the protocol writes it.
LEVELS: Mapping[str, PoE] = {"OPX": OPX, "DEV": DEV, "USR": USER, "PUB": PUBLIC}


class LevelCommand:
    """The one ``cgitsync`` command a level calls for on ``YYYY.node`` (BeforeCGS-BETA §7.0). ``.cgs`` is the only way in."""

    @staticmethod
    def of(project: str, code: str) -> str | None:
        """``bootstrap yyyy-opx.cgs``, ``bootstrap yyyy-dev.cgs`` or ``initialise ../install.cgs``; ``None`` for ``PUB`` (``VOID``).

        *code* comes from the caller, which is CGS-BETA through a GX4G node: ``OP`` never computes a level of its own,
        and it only emits the command (AD7), never runs it, since a node may be another machine.
        """
        if code not in LEVELS:
            raise GitSyncError(f"{code!r} is not a level: one of {', '.join(LEVELS)}.")
        return {"OPX": f"cgitsync bootstrap {project}-opx.cgs", "DEV": f"cgitsync bootstrap {project}-dev.cgs", "USR": "cgitsync initialise ../install.cgs"}.get(code)


class Leaves:
    """GitRepo-ALPHA == GitRepo-BETA: whether a leaf of the tree is the leaf the other side holds (BeforeCGS-BETA §6)."""

    @staticmethod
    def of_tree(client: ComplexGitSyncClient) -> dict[str, str]:
        """ALPHA: each leaf's schema-1 hash, by the key a State orders it on (``relative_path``)."""
        from ..tree.registry import RegistryTranslator  # the writer builds on this layer

        registry = client.get_dependency_registry()
        return {str(t["relative_path"]): MerkleRoot.repo_leaf_hash(GtsDocument.repo_leaf(t)) for t in (RegistryTranslator.repo_state_dict(registry, e) for e in registry.values())}

    @staticmethod
    def of_state(document: GtsDocument) -> dict[str, str]:
        """BETA: the same, read from a registered State (``repo_hash``), which is what the register commits to."""
        return {str(r["relative_path"]): str(r["repo_hash"]) for r in document.repo_states if r.get("repo_hash")}

    @staticmethod
    def compare(alpha: Mapping[str, str], beta: Mapping[str, str] | None) -> dict[str, str]:
        """Per leaf: ``equal``, ``different``, ``absent-beta`` or ``absent-alpha``; ``unverified`` when BETA is not mounted.

        Hashes are compared and never shown (FD2): the verdict is all that leaves this class.
        """
        if beta is None:
            return {key: "unverified" for key in sorted(alpha)}
        verdict = {key: ("equal" if beta.get(key) == value else "different" if key in beta else "absent-beta") for key, value in alpha.items()}
        verdict.update({key: "absent-alpha" for key in beta if key not in alpha})
        return dict(sorted(verdict.items()))


__all__ = ["DEV", "LEVELS", "OPX", "PUBLIC", "USER", "LevelCommand", "Leaves", "PoE"]

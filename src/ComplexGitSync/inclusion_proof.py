"""inclusion_proof — prove one repository is in a GitTree without showing the others.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: an RFC 9162 §2.1.3 (formerly RFC 6962) audit path from one repository's
    leaf to ``H_GITTREE`` under ``integrity_schema = 1``, and its check
    against a trusted root. Private: no client method and no command uses it
    yet; OmniscienceQuery is where a proof reaches a user.
Imports: errors, gts_integrity

There is no second hashing implementation here. Every digest comes from
:class:`~.gts_integrity.GtsIntegrity`: a leaf's ``repo_leaf_hash``, an inner
node's ``node_hash``, and each sibling subtree's ``merkle_root`` over the same
ordered leaves, cut where ``merkle_root`` cuts them (the largest power of two
below the size). So a proof's root is ``merkle_root(leaves)`` by construction
and no schema-1 digest moves.

A proof is checked against a trusted root *and* its trusted size, which a
``.gts`` (its ``[[repo_state]]`` count) or a register record carries with it.
A leaf is found by its ``relative_path``, the key ``ordered_leaves`` sorts and
refuses duplicates on; a leaf carries no repository id. Siblings are listed
from the leaf up. A proof is stored as a TOML-ready table of hex strings
(:meth:`InclusionProof.to_dict`), the way a ``.gts`` stores its digests. Only
the GitTree level is proved: ``H_GITTREE`` to ``H_STATE`` is one hash, carried
by the record that holds the proof.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import ConfigValidationError
from .gts_integrity import GtsIntegrity


@dataclass(frozen=True, slots=True)
class InclusionProof:
    """One leaf's audit path to the root it was made against."""

    schema: int
    leaf_index: int
    tree_size: int
    siblings: tuple[str, ...]
    root: str

    @classmethod
    def of(cls, leaves: Sequence[Mapping[str, Any]], relative_path: str) -> InclusionProof:
        """The proof that the leaf at *relative_path* is in ``merkle_root(leaves)``."""
        ordered = GtsIntegrity.ordered_leaves(leaves)
        paths = [leaf["relative_path"] for leaf in ordered]
        if relative_path not in paths:
            raise ConfigValidationError(f"no repository at {relative_path!r} in this GitTree")
        index = paths.index(relative_path)
        return cls(
            schema=GtsIntegrity.SCHEMA,
            leaf_index=index,
            tree_size=len(ordered),
            siblings=tuple(cls._path(index, ordered)),
            root=GtsIntegrity.merkle_root(ordered),
        )

    @staticmethod
    def _path(index: int, ordered: Sequence[Mapping[str, Any]]) -> list[str]:
        """RFC 9162 §2.1.3.1 ``PATH(m, D[n])``, leaf first, each sibling a subtree root from ``merkle_root``."""
        if len(ordered) <= 1:
            return []
        split = 1
        while split * 2 < len(ordered):
            split *= 2
        if index < split:
            return [*InclusionProof._path(index, ordered[:split]), GtsIntegrity.merkle_root(ordered[split:])]
        return [*InclusionProof._path(index - split, ordered[split:]), GtsIntegrity.merkle_root(ordered[:split])]

    def verify(self, leaf_hash: str, trusted_root: str, trusted_size: int) -> bool:
        """Whether *leaf_hash* is in the tree of *trusted_size* leaves whose root is *trusted_root*.

        The size is trusted with the root, as RFC 9162 trusts a signed tree
        head: the audit path alone does not fix it (leaf 3 walks the same path
        in a tree of 5 and of 6). Dispatched on ``schema``: a later schema adds
        a branch and never reinterprets this one. A malformed proof is
        ``False``, never an error.
        """
        if self.schema == 1:
            return self._verify_schema_1(leaf_hash, trusted_root, trusted_size)
        return False

    def _verify_schema_1(self, leaf_hash: str, trusted_root: str, trusted_size: int) -> bool:
        """RFC 9162 §2.1.3.2, with ``GtsIntegrity.node_hash`` as the inner hash."""
        if self.root != trusted_root or self.tree_size != trusted_size:
            return False
        if not 0 <= self.leaf_index < self.tree_size:
            return False
        try:
            node = bytes.fromhex(leaf_hash)
            siblings = [bytes.fromhex(sibling) for sibling in self.siblings]
        except ValueError:
            return False
        index, last = self.leaf_index, self.tree_size - 1
        for sibling in siblings:
            if last == 0:
                return False
            if index & 1 or index == last:
                node = GtsIntegrity.node_hash(sibling, node)
                while not index & 1 and index != 0:
                    index, last = index >> 1, last >> 1
            else:
                node = GtsIntegrity.node_hash(node, sibling)
            index, last = index >> 1, last >> 1
        return last == 0 and node.hex() == trusted_root

    def to_dict(self) -> dict[str, Any]:
        """The proof as a TOML-ready table: integers and hex strings."""
        return {
            "schema": self.schema,
            "leaf_index": self.leaf_index,
            "tree_size": self.tree_size,
            "siblings": list(self.siblings),
            "root": self.root,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> InclusionProof:
        """A proof read back from :meth:`to_dict`'s table."""
        return cls(
            schema=int(data["schema"]),
            leaf_index=int(data["leaf_index"]),
            tree_size=int(data["tree_size"]),
            siblings=tuple(str(sibling) for sibling in data["siblings"]),
            root=str(data["root"]),
        )


__all__ = ["InclusionProof"]

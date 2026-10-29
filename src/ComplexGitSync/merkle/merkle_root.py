"""merkle_root — the one place a hash is computed, and the Merkle tree never leaves it.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: ``MerkleRoot`` computes every hash the project records or compares:
    a repository's leaf, the GitTree root, a State's name (``integrity_schema = 1``),
    the Omniscience register's hashes (``GLOBAL_SCHEMA = 1``), and the content
    hashes of a ledger entry, a commit log, an Environment and the other records.
    It takes plain values and gives back hex strings, names and verdicts. It never
    returns a tree, a level, a node or a sibling: an audit path is an inner node,
    so ``InclusionProof`` keeps its path private and says only whether it holds.
Imports: none

Why one door. A hash is an *immaterial frontier*: the same bytes must come out
whether it is computed from a structure in memory, from a ``.gts`` on disk or
from a repository on a remote (the test is LEFT = RIGHT, bit for bit). One class
that every caller goes through is where that test lives, and where a new hash
has to be added, instead of five modules that each know a little of the tree.

What it does not own: *which* fields make a repository's identity. The adapter
that knows a ``GitRepo`` (``GtsDocument.repo_leaf``) builds each leaf and hands
plain values in, so this folder imports nothing, not even ``GitRepo``.

**A released schema is immutable.** Any change to what is hashed or how is a new
schema, with the old one still verifiable. The golden vectors in
``tests/unit/test_merkle_root.py`` are the cross-platform contract, unchanged
since ``integrity_schema = 1`` and ``GLOBAL_SCHEMA = 1`` were written.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


class MerkleError(ValueError):
    """A set of leaves that cannot be a tree: no path, a path that is not POSIX, two leaves on one path."""


class MerkleRoot:
    """Every hash, as stateless class methods. See the module docstring."""

    #: The hash contract a new ``.gts`` declares in ``document.integrity_schema``.
    SCHEMA = 1
    #: The hash contract a register record declares in ``record.schema``.
    GLOBAL_SCHEMA = 1
    ALGORITHM = "sha256"
    #: The ``prev`` of a ledger chain's first entry: the root every chain starts from.
    GENESIS_PREV = "sha256:" + "0" * 64

    _REPO_TAG = b"CGS:REPO:v1\x00"
    _NODE_TAG = b"CGS:NODE:v1\x00"
    _STATE_TAG = b"CGS:STATE:v1\x00"
    _OS_STATE_TAG = b"CGS:OSSTATE:v1\x00"
    _RECORD_TAG = b"CGS:RECORD:v1\x00"
    _LEAF_TAG = b"CGS:GLEAF:v1\x00"
    _GNODE_TAG = b"CGS:GNODE:v1\x00"
    _CHECKPOINT_TAG = b"CGS:CHECKPOINT:v1\x00"
    _OBSERVATION_TAG = b"CGS:OBSERVATION:v1\x00"

    # -- content hashes: one record, one file, one name ------------------------

    @staticmethod
    def canonical_json(payload: Any) -> bytes:
        """The canonical bytes of *payload*: sorted keys, no whitespace, UTF-8."""
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @classmethod
    def digest(cls, payload: Any) -> str:
        """SHA-256 of the canonical bytes of *payload*, as hex, with no domain tag."""
        return hashlib.sha256(cls.canonical_json(payload)).hexdigest()

    @staticmethod
    def content_digest(data: bytes) -> str:
        """SHA-256 of raw *data*, as hex: a file's bytes, or a canonical name's."""
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def stream_digest(chunks: Iterable[bytes]) -> str:
        """SHA-256 over *chunks* in order, as hex: a file read piece by piece."""
        running = hashlib.sha256()
        for chunk in chunks:
            running.update(chunk)
        return running.hexdigest()

    @classmethod
    def prefixed(cls, hex_digest: str) -> str:
        """``sha256:<hex>``, how a ledger entry and a commit log write a digest."""
        return f"{cls.ALGORITHM}:{hex_digest}"

    # -- schema 1: the State -----------------------------------------------------

    @classmethod
    def repo_leaf_hash(cls, leaf: Mapping[str, Any]) -> str:
        """``H_REPO`` of one canonical repository leaf, as hex."""
        return hashlib.sha256(cls._REPO_TAG + cls.canonical_json(leaf)).hexdigest()

    @classmethod
    def gittree_root(cls, leaves: Sequence[Mapping[str, Any]]) -> str:
        """``H_GITTREE`` over *leaves*, in any input order, as hex."""
        digests = [bytes.fromhex(cls.repo_leaf_hash(leaf)) for leaf in cls._ordered(leaves)]
        return cls._subtree(digests, cls._NODE_TAG).hex()

    @classmethod
    def check_leaves(cls, leaves: Sequence[Mapping[str, Any]]) -> None:
        """Raise :class:`MerkleError` when *leaves* cannot be one tree; return nothing."""
        cls._ordered(leaves)

    @classmethod
    def state_hash(cls, state_payload: Mapping[str, Any]) -> str:
        """``H_STATE`` of the State payload (which carries ``gittree_root``), as hex."""
        return hashlib.sha256(cls._STATE_TAG + cls.canonical_json(state_payload)).hexdigest()

    # -- GLOBAL_SCHEMA 1: the register ----------------------------------------------

    @classmethod
    def _tagged(cls, tag: bytes, payload: Mapping[str, Any]) -> bytes:
        return hashlib.sha256(tag + cls.canonical_json(payload)).digest()

    @classmethod
    def os_state_hash(cls, payload: Mapping[str, Any]) -> str:
        """``H_OS_STATE`` of one Operating Space State commitment, as hex."""
        return cls._tagged(cls._OS_STATE_TAG, payload).hex()

    @classmethod
    def record_hash(cls, preimage: Mapping[str, Any]) -> str:
        """The name of a registration record: the hash of its committed fields, as hex."""
        return cls._tagged(cls._RECORD_TAG, preimage).hex()

    @classmethod
    def leaf_digest(cls, payload: Mapping[str, Any]) -> bytes:
        """One registration's global leaf, as raw bytes."""
        return cls._tagged(cls._LEAF_TAG, payload)

    @classmethod
    def global_root(cls, leaf_digests: Sequence[bytes]) -> str:
        """``H_GLOBAL`` over *leaf_digests*, already in leaf order, as hex; the empty tree has the RFC's empty root."""
        return cls._subtree(leaf_digests, cls._GNODE_TAG).hex()

    @classmethod
    def checkpoint_hash(cls, body: Mapping[str, Any]) -> str:
        """The name of a checkpoint: the hash of its body, which never contains its own name, as hex."""
        return cls._tagged(cls._CHECKPOINT_TAG, body).hex()

    @classmethod
    def observation_hash(cls, table: Mapping[str, Any]) -> str:
        """The name of an observation: the hash of everything it says, as hex. Not a leaf: no ``H_GLOBAL`` covers it."""
        return cls._tagged(cls._OBSERVATION_TAG, table).hex()

    # -- proofs ------------------------------------------------------------------

    @classmethod
    def inclusion_proof(cls, leaves: Sequence[Mapping[str, Any]], relative_path: str) -> InclusionProof:
        """The proof that the leaf at *relative_path* is in ``gittree_root(leaves)``."""
        ordered = cls._ordered(leaves)
        paths = [leaf["relative_path"] for leaf in ordered]
        if relative_path not in paths:
            raise MerkleError(f"no repository at {relative_path!r} in this GitTree")
        index = paths.index(relative_path)
        digests = [bytes.fromhex(cls.repo_leaf_hash(leaf)) for leaf in ordered]
        return InclusionProof(cls.SCHEMA, index, len(ordered), cls.gittree_root(ordered), tuple(cls._path(index, digests, cls._NODE_TAG)))

    @classmethod
    def global_inclusion_proof(cls, leaf_digests: Sequence[bytes], index: int) -> GlobalInclusionProof:
        """The proof that the digest at *index* is in ``global_root(leaf_digests)``, which must already be in leaf order."""
        if not 0 <= index < len(leaf_digests):
            raise MerkleError(f"no leaf at index {index} in an index of {len(leaf_digests)}")
        return GlobalInclusionProof(cls.GLOBAL_SCHEMA, index, len(leaf_digests), cls.global_root(leaf_digests), tuple(cls._path(index, leaf_digests, cls._GNODE_TAG)))

    # -- the tree, private --------------------------------------------------------

    @classmethod
    def _ordered(cls, leaves: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        """*leaves* in tree order: ``relative_path`` compared as UTF-8 bytes; a duplicate is an invalid State."""
        keyed: dict[bytes, Mapping[str, Any]] = {}
        for leaf in leaves:
            path = leaf.get("relative_path")
            if not isinstance(path, str) or not path:
                raise MerkleError(f"repository {leaf.get('name')!r} has no relative_path")
            if "\\" in path:
                raise MerkleError(f"relative_path {path!r} must use forward slashes")
            key = path.encode("utf-8")
            if key in keyed:
                raise MerkleError(f"duplicate relative_path {path!r} in one State")
            keyed[key] = leaf
        return [keyed[key] for key in sorted(keyed)]

    @classmethod
    def _inner(cls, tag: bytes, left: bytes, right: bytes) -> bytes:
        return hashlib.sha256(tag + left + right).digest()

    @classmethod
    def _subtree(cls, digests: Sequence[bytes], tag: bytes) -> bytes:
        """RFC 9162 §2.1.1 ``MTH``: no leaf is ever duplicated; the empty tree is the hash of nothing."""
        if not digests:
            return hashlib.sha256(b"").digest()
        if len(digests) == 1:
            return digests[0]
        split = 1
        while split * 2 < len(digests):
            split *= 2
        return cls._inner(tag, cls._subtree(digests[:split], tag), cls._subtree(digests[split:], tag))

    @classmethod
    def _path(cls, index: int, digests: Sequence[bytes], tag: bytes) -> list[str]:
        """RFC 9162 §2.1.3.1 ``PATH(m, D[n])``, leaf first, each sibling a subtree root."""
        if len(digests) <= 1:
            return []
        split = 1
        while split * 2 < len(digests):
            split *= 2
        if index < split:
            return [*cls._path(index, digests[:split], tag), cls._subtree(digests[split:], tag).hex()]
        return [*cls._path(index - split, digests[split:], tag), cls._subtree(digests[:split], tag).hex()]


class InclusionProof:
    """One leaf's proof that it is in a root. It says whether it holds and shows no node.

    The audit path is an inner node of the tree, so it is private: it is not in ``summary``,
    it has no accessor, and it is not stored. A caller learns *verified* or *invalid*.

    This is deliberately **not** a dataclass: ``dataclasses.asdict``, ``astuple`` and ``fields``
    refuse it, so nothing that walks a dataclass can carry the path out by accident. It has
    ``__slots__`` and no ``__dict__``, a ``repr`` that omits the path, and it is immutable.
    Python cannot make an attribute unreachable; the rule is that nothing outside this module
    reads ``_path``, and a test holds it.
    """

    __slots__ = ("schema", "leaf_index", "tree_size", "root", "_path")
    _TAG = MerkleRoot._NODE_TAG
    _SCHEMA = MerkleRoot.SCHEMA

    def __init__(self, schema: int, leaf_index: int, tree_size: int, root: str, path: tuple[str, ...]) -> None:
        for name, value in zip(InclusionProof.__slots__, (schema, leaf_index, tree_size, root, tuple(path))):
            object.__setattr__(self, name, value)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and all(getattr(self, n) == getattr(other, n) for n in InclusionProof.__slots__)

    def __hash__(self) -> int:
        return hash((type(self), self.schema, self.leaf_index, self.tree_size, self.root))

    def __repr__(self) -> str:
        return f"{type(self).__name__}(schema={self.schema}, leaf_index={self.leaf_index}, tree_size={self.tree_size}, root={self.root!r})"

    def verify(self, leaf_hash: str, trusted_root: str, trusted_size: int) -> bool:
        """Whether *leaf_hash* is in the tree of *trusted_size* leaves whose root is *trusted_root*.

        The size is trusted with the root, as RFC 9162 trusts a signed tree head: the path alone
        does not fix it (leaf 3 walks the same path in a tree of 5 and of 6). Dispatched on
        ``schema``: a later schema adds a branch and never reinterprets this one. A malformed
        proof is ``False``, never an error.
        """
        if self.schema != self._SCHEMA:
            return False
        if self.root != trusted_root or self.tree_size != trusted_size or not 0 <= self.leaf_index < self.tree_size:
            return False
        try:
            node = bytes.fromhex(leaf_hash)
            siblings = [bytes.fromhex(sibling) for sibling in self._path]
        except ValueError:
            return False
        index, last = self.leaf_index, self.tree_size - 1
        for sibling in siblings:
            if last == 0:
                return False
            if index & 1 or index == last:
                node = MerkleRoot._inner(self._TAG, sibling, node)
                while not index & 1 and index != 0:
                    index, last = index >> 1, last >> 1
            else:
                node = MerkleRoot._inner(self._TAG, node, sibling)
            index, last = index >> 1, last >> 1
        return last == 0 and node.hex() == trusted_root

    def summary(self) -> dict[str, Any]:
        """What may be shown: the schema, the leaf's index, the tree's size and the root. Never the path."""
        return {"schema": self.schema, "leaf_index": self.leaf_index, "tree_size": self.tree_size, "root": self.root}


class GlobalInclusionProof(InclusionProof):
    """One registration's proof against ``H_GLOBAL``: over digests the index already holds, in index order."""

    __slots__ = ()
    _TAG = MerkleRoot._GNODE_TAG
    _SCHEMA = MerkleRoot.GLOBAL_SCHEMA


__all__ = ["GlobalInclusionProof", "InclusionProof", "MerkleError", "MerkleRoot"]

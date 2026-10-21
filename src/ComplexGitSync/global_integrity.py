"""global_integrity — the hashes of the Omniscience register, a schema of their own.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: ``H_OS_STATE``, a registration's record hash, a global leaf, the
    global Merkle root ``H_GLOBAL`` and a checkpoint's name, each a
    domain-separated SHA-256 over canonical JSON (or, for an inner node, over
    two raw digests). Frozen as ``GLOBAL_SCHEMA = 1``.
Imports: gts_integrity

This is a new schema beside ``integrity_schema = 1``, never a reading of it:
every tag here differs from the tags of :class:`~.gts_integrity.GtsIntegrity`,
so a global digest can never be mistaken for a State, tree or repository
digest. What is shared is the machinery, not the contract: canonical JSON
(``_canonical_json``), the RFC 9162 tree shape (``_subtree``, whose inner
nodes here use ``NODE_TAG``) and the empty root, all inherited. The schema-1
entry points it also inherits are refused, so the two schemas cannot mix. A
change to anything hashed here is ``GLOBAL_SCHEMA = 2`` with schema 1 still
verifiable; the golden vectors in ``tests/unit/test_global_integrity.py`` are
the cross-platform contract.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from .gts_integrity import GtsIntegrity

#: The hash contract a record declares in ``record.schema``.
GLOBAL_SCHEMA = 1


class GlobalIntegrity(GtsIntegrity):
    """The pure hash functions of ``GLOBAL_SCHEMA = 1``. Stateless."""

    GLOBAL_SCHEMA = GLOBAL_SCHEMA
    OS_STATE_TAG = b"CGS:OSSTATE:v1\x00"
    RECORD_TAG = b"CGS:RECORD:v1\x00"
    LEAF_TAG = b"CGS:GLEAF:v1\x00"
    NODE_TAG = b"CGS:GNODE:v1\x00"
    CHECKPOINT_TAG = b"CGS:CHECKPOINT:v1\x00"

    @classmethod
    def merkle_root(cls, leaves: Sequence[Mapping[str, Any]]) -> str:
        """Refused: it would mix schema-1 leaf tags with the global node tag. Use :meth:`global_root`."""
        raise TypeError("a global tree is rooted with global_root over leaf digests, never merkle_root")

    @classmethod
    def repo_leaf_hash(cls, leaf: Mapping[str, Any]) -> str:
        """Refused: a schema-1 digest. Use ``GtsIntegrity.repo_leaf_hash``."""
        raise TypeError("repo_leaf_hash is schema 1's; use GtsIntegrity")

    @classmethod
    def state_hash(cls, state_payload: Mapping[str, Any]) -> str:
        """Refused: a schema-1 digest. Use ``GtsIntegrity.state_hash``."""
        raise TypeError("state_hash is schema 1's; use GtsIntegrity")

    @classmethod
    def _tagged(cls, tag: bytes, payload: Mapping[str, Any]) -> bytes:
        return hashlib.sha256(tag + cls._canonical_json(payload)).digest()

    @classmethod
    def os_state_hash(cls, payload: Mapping[str, Any]) -> str:
        """``H_OS_STATE`` of one Operating Space State commitment, as hex."""
        return cls._tagged(cls.OS_STATE_TAG, payload).hex()

    @classmethod
    def record_hash(cls, preimage: Mapping[str, Any]) -> str:
        """The name of a registration record: the hash of its committed fields, as hex."""
        return cls._tagged(cls.RECORD_TAG, preimage).hex()

    @classmethod
    def leaf_digest(cls, payload: Mapping[str, Any]) -> bytes:
        """One registration's global leaf, as raw bytes."""
        return cls._tagged(cls.LEAF_TAG, payload)

    @classmethod
    def global_root(cls, leaf_digests: Sequence[bytes]) -> str:
        """``H_GLOBAL`` over *leaf_digests*, already in leaf order, as hex; the empty tree has the RFC's empty root."""
        return cls._subtree(leaf_digests).hex()

    @classmethod
    def checkpoint_hash(cls, body: Mapping[str, Any]) -> str:
        """The name of a checkpoint: the hash of its body, which never contains its own name, as hex."""
        return cls._tagged(cls.CHECKPOINT_TAG, body).hex()


__all__ = ["GLOBAL_SCHEMA", "GlobalIntegrity"]

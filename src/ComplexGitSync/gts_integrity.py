"""gts_integrity — the three-level hash that names a State.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: given plain dicts, compute each repository's leaf hash, the
    GitTree Merkle root over them, and the State hash on top; frozen as
    ``INTEGRITY_SCHEMA = 1``.
Imports: errors

The three levels
----------------
``repo_hash`` is the integrity identity of one GitTree member,
``merkle_root`` that of the GitTree, ``snapshot_hash`` that of the complete
State. Each level is a domain-separated SHA-256 over canonical JSON (or, for
an inner tree node, over two raw 32-byte digests), so a mismatch can be
traced down to the one repository that changed instead of only "this State
is wrong".

The tree follows RFC 6962 §2.1: no leaf is ever duplicated, so an inclusion
proof could be added later without changing any digest. The specification
is the GtsHashRepoPrecision ticket, §3, and ``AdditionalSpecs.md``, *What a
State's name is computed from*.

**A released integrity schema is immutable.** Any change to what is hashed
or how is ``INTEGRITY_SCHEMA = 2``, with schema 1 still verifiable. The
golden vectors in ``tests/unit/test_gts_integrity.py`` are the
cross-platform contract.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from .errors import ConfigValidationError

#: The hash contract a new ``.gts`` declares in ``document.integrity_schema``.
INTEGRITY_SCHEMA = 1


class GtsIntegrity:
    """The pure hash functions of ``integrity_schema = 1``. Stateless."""

    SCHEMA = INTEGRITY_SCHEMA
    REPO_TAG = b"CGS:REPO:v1\x00"
    NODE_TAG = b"CGS:NODE:v1\x00"
    STATE_TAG = b"CGS:STATE:v1\x00"

    @staticmethod
    def _canonical_json(payload: Mapping[str, Any]) -> bytes:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    @classmethod
    def repo_leaf_hash(cls, leaf: Mapping[str, Any]) -> str:
        """``H_REPO`` of one canonical repository leaf, as hex."""
        return hashlib.sha256(cls.REPO_TAG + cls._canonical_json(leaf)).hexdigest()

    @classmethod
    def node_hash(cls, left: bytes, right: bytes) -> bytes:
        """``H_NODE`` over two raw 32-byte child digests, as raw bytes."""
        return hashlib.sha256(cls.NODE_TAG + left + right).digest()

    @classmethod
    def ordered_leaves(cls, leaves: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        """*leaves* in tree order: ``relative_path`` compared as UTF-8 bytes.

        Refuses a leaf without a POSIX ``relative_path`` and two leaves sharing one
        — a duplicate is an invalid State, never a tie broken by name.
        """
        keyed: dict[bytes, Mapping[str, Any]] = {}
        for leaf in leaves:
            path = leaf.get("relative_path")
            if not isinstance(path, str) or not path:
                raise ConfigValidationError(f"repository {leaf.get('name')!r} has no relative_path")
            if "\\" in path:
                raise ConfigValidationError(f"relative_path {path!r} must use forward slashes")
            key = path.encode("utf-8")
            if key in keyed:
                raise ConfigValidationError(f"duplicate relative_path {path!r} in one State")
            keyed[key] = leaf
        return [keyed[key] for key in sorted(keyed)]

    @classmethod
    def merkle_root(cls, leaves: Sequence[Mapping[str, Any]]) -> str:
        """``H_GITTREE`` over *leaves*, in any input order, as hex."""
        digests = [bytes.fromhex(cls.repo_leaf_hash(leaf)) for leaf in cls.ordered_leaves(leaves)]
        return cls._subtree(digests).hex()

    @classmethod
    def _subtree(cls, digests: Sequence[bytes]) -> bytes:
        if not digests:
            # RFC 6962 §2.1, MTH({}): only the default workspace's empty,
            # never-READY State has no repository (GtsDocument.validate).
            return hashlib.sha256(b"").digest()
        if len(digests) == 1:
            return digests[0]
        split = 1
        while split * 2 < len(digests):
            split *= 2
        return cls.node_hash(cls._subtree(digests[:split]), cls._subtree(digests[split:]))

    @classmethod
    def state_hash(cls, state_payload: Mapping[str, Any]) -> str:
        """``H_STATE`` of the State payload (which carries ``gittree_root``), as hex."""
        return hashlib.sha256(cls.STATE_TAG + cls._canonical_json(state_payload)).hexdigest()


__all__ = ["INTEGRITY_SCHEMA", "GtsIntegrity"]

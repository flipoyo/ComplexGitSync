"""global_index — the accepted registrations, and the one root over them.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: from a set of registration records, the ordered unique leaves of
    the global tree, ``H_GLOBAL`` over them, and the checkpoint body that
    names that root. Reading or writing any file of the ``_global`` branch is
    OmniscienceAppend's; this module only computes.
Imports: global_integrity, omniscience_record

Ordering and duplicates are frozen here. A leaf is one registered branch head
and one State commitment (never ``os_id`` alone), ordered by the UTF-8 bytes
of ``os_id``, ``project_branch``, ``state_id`` and the record hash, in that
order. The same record twice is one leaf (a registration is idempotent); two
different records for one State are two leaves, ordered by record hash. The
empty index has the RFC 9162 empty root, so an index with nothing accepted
has a defined root.

A checkpoint body names its parent checkpoint, the root and the record
hashes it covers. It never contains its own name, which is the hash of the
body, so a checkpoint is never hashed while it is being built.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .global_integrity import GLOBAL_SCHEMA, GlobalIntegrity
from .omniscience_record import OmniscienceRecord


@dataclass(frozen=True, slots=True)
class GlobalIndex:
    """The accepted registrations, one leaf each, in tree order."""

    records: tuple[OmniscienceRecord, ...]

    @classmethod
    def of(cls, records: Iterable[OmniscienceRecord]) -> GlobalIndex:
        """The index over *records*: duplicates (same record hash) collapse, the rest are ordered."""
        unique = {record.record_hash: record for record in records}
        return cls(tuple(sorted(unique.values(), key=lambda record: record.leaf_key)))

    @property
    def root(self) -> str:
        """``H_GLOBAL``, as hex."""
        return GlobalIntegrity.global_root([record.leaf_digest for record in self.records])

    def checkpoint_body(self, parent: str = "") -> dict[str, Any]:
        """What a checkpoint says: its schema, its parent's name (empty for the first), the root and the covered record hashes."""
        return {
            "schema": GLOBAL_SCHEMA,
            "parent": parent,
            "global_root": self.root,
            "records": sorted(record.record_hash for record in self.records),
        }

    def checkpoint_hash(self, parent: str = "") -> str:
        """The checkpoint's name, which is its file name under ``checkpoints/``."""
        return GlobalIntegrity.checkpoint_hash(self.checkpoint_body(parent))


__all__ = ["GlobalIndex"]

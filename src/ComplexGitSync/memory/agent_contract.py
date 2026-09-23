"""agent_contract — content-addressed AgentContract records.

Ring: 1 (filesystem only, no subprocess, no clock)
Contract: store and load one ``AgentContractRecord`` under
    ``<dev_sync_dir>/agent-contracts/<hash>.toml`` using its canonical
    digest and an atomic replace, plus a plain-text ``current`` pointer
    naming which record is in force. Decides nothing about what a
    provider's terms actually say — that is a project's own
    ``legalTerms/<provider>.md`` — and nothing about Git. The caller
    supplies every dated fact; this module reads no clock, per
    ``universal_clock.py``'s seam (see AdditionalSpecs.md, *Versioning*).
Imports: (stdlib only, plus tomli_w/tomllib for the same TOML the .cgs/.gts
    documents already use)

See ``.agent/.local/.localSpec/DevTickets/archive/20260923_AgentContract_DevPlanTicket.md``
§3 for why this lives beside, not inside, ``.cgitsync/``: the record is
signed once per provider and shared across every project the owner runs
with that provider, not scoped to one workspace's own state area the way
an Environment record (``environment.py``) is.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

AGENT_CONTRACTS_DIR_NAME = "agent-contracts"
CURRENT_POINTER_NAME = "current"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class AgentContractRecord:
    """One signed AgentContract: a provider, the terms in force, and when.

    ``legal_terms_sha256`` ties this record to the exact ``legalTerms``
    entry the owner assessed before signing — so editing that assessment
    without re-signing is detectable the same way editing this record's
    own fields is: the digest changes and an old citation stops matching.
    """

    provider: str
    terms_version: str
    date: str
    legal_terms_sha256: str
    attested_by: str

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "terms_version": self.terms_version,
            "date": self.date,
            "legal_terms_sha256": self.legal_terms_sha256,
            "attested_by": self.attested_by,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> AgentContractRecord:
        return cls(
            provider=str(value["provider"]),
            terms_version=str(value["terms_version"]),
            date=str(value["date"]),
            legal_terms_sha256=str(value["legal_terms_sha256"]),
            attested_by=str(value["attested_by"]),
        )

    def digest(self) -> str:
        canonical = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def contract_path(dev_sync_dir: Path, contract_hash: str) -> Path:
    """Return the canonical path for *contract_hash* under *dev_sync_dir*."""
    if not _HASH_RE.fullmatch(contract_hash):
        raise ValueError("AgentContract hash must be 64 lowercase hexadecimal characters")
    return dev_sync_dir / AGENT_CONTRACTS_DIR_NAME / f"{contract_hash}.toml"


def write_contract(dev_sync_dir: Path, record: AgentContractRecord) -> Path:
    """Atomically persist *record* under the name of its own digest and
    point ``current`` at it. A byte-identical record already on disk is
    left alone; a colliding hash with different content raises."""
    digest = record.digest()
    destination = contract_path(dev_sync_dir, digest)
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = tomli_w.dumps(record.to_dict()).encode("utf-8")
    if destination.is_file():
        if destination.read_bytes() != content:
            raise ValueError(f"AgentContract digest collision at {destination}")
    else:
        temporary = destination.with_name(f".{destination.name}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    current_path = dev_sync_dir / AGENT_CONTRACTS_DIR_NAME / CURRENT_POINTER_NAME
    current_path.write_text(digest + "\n", encoding="utf-8")
    return destination


def read_contract(path: Path) -> AgentContractRecord:
    """Load an AgentContract record and verify its name matches its content."""
    with path.open("rb") as handle:
        record = AgentContractRecord.from_dict(tomllib.load(handle))
    expected = path.stem
    if record.digest() != expected:
        raise ValueError(f"AgentContract record digest does not match its filename: {path}")
    return record


def read_current_contract(dev_sync_dir: Path) -> AgentContractRecord | None:
    """Return the record ``current`` points to, or ``None`` when nothing has
    been signed yet (D4: absent, not fatal — a caller reports the absence
    rather than blocking on it)."""
    current_path = dev_sync_dir / AGENT_CONTRACTS_DIR_NAME / CURRENT_POINTER_NAME
    if not current_path.is_file():
        return None
    contract_hash = current_path.read_text(encoding="utf-8").strip()
    if not contract_hash:
        return None
    return read_contract(contract_path(dev_sync_dir, contract_hash))

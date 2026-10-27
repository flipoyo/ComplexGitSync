"""omniscience_observation — one finding about whether a registered repository is still there.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: the statuses an observation can carry, how a failed Git answer is
    sorted into one of them, and the canonical form, name and TOML table of
    one observation. Probing and storing are the observer's
    (``orchestre/omniscience_observer.py``); this module only decides what a
    given answer means.
Imports: errors, merkle

An observation is an event, not a registration. It is appended, named by the
hash of everything it says (so two findings made at different moments are two
files), and never edited. It is **not** hashed into ``H_GLOBAL`` and never
removes or changes a leaf: a repository that vanishes is a fact recorded
beside the registration that named it.

The sorting of a failed Git answer is by what Git printed, because that is all
a client holding no provider credentials can see. A private repository seen
without access is answered ``404`` by some hosts, so ``NOT_FOUND`` means "the
host says it is not there", never "it was deleted": only ``DELETION_CONFIRMED``
says that, and it is never inferred (``AdditionalSpecs.md``, *The Omniscience
register*, Observations).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .errors import ConfigValidationError
from .merkle import MerkleRoot

#: What an observation's ``target.kind`` can be.
TARGET_KINDS = ("memory", "repository")

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

# Order matters: the first pattern that matches names the status.
_FAILURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ACCESS_DENIED", re.compile(r"permission denied|authentication failed|could not read username|access denied|\b403\b|publickey|forbidden|terminal prompts disabled", re.I)),
    ("NOT_FOUND", re.compile(r"repository not found|not found|does not exist|does not appear to be a git repository|no such file or directory|\b404\b", re.I)),
    ("UNREACHABLE", re.compile(r"could not resolve host|timed out|timeout|connection (?:refused|reset|closed)|network is unreachable|unable to access|temporary failure|ssl|early eof|broken pipe", re.I)),
)


class ObservationStatus(Enum):
    """What one probe found. The first seven are the ticket's; ``CLOSED`` and ``REF_MISSING`` split two cases it left to one."""

    AVAILABLE = "AVAILABLE"
    ACCESS_DENIED = "ACCESS_DENIED"
    UNREACHABLE = "UNREACHABLE"
    NOT_FOUND = "NOT_FOUND"
    COMMIT_MISSING = "COMMIT_MISSING"
    REF_MISSING = "REF_MISSING"  # the ref is gone, the commit is still handed out
    CLOSED = "CLOSED"  # a branch closed on purpose, known from the ledger and never from a failed fetch
    UNKNOWN = "UNKNOWN"
    DELETION_CONFIRMED = "DELETION_CONFIRMED"  # only ever the owner's signed note, never a probe

    @classmethod
    def of_git_failure(cls, text: str) -> ObservationStatus:
        """Sort what Git printed when it could not read a remote.

        Denied, then not found, then unreachable; anything else, or nothing at
        all, is ``UNKNOWN``, because guessing is how a network outage gets
        recorded as a deletion.
        """
        for name, pattern in _FAILURES:
            if pattern.search(text or ""):
                return cls[name]
        return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class Observation:
    """One finding about one target of one registered project."""

    project: str
    os_id: str
    target_kind: str
    target: str
    status: ObservationStatus
    observed_at: str
    method: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    commit: str = ""
    record_hash: str = ""
    observer: str = ""

    def __post_init__(self) -> None:
        if self.target_kind not in TARGET_KINDS:
            raise ConfigValidationError(f"observation target kind {self.target_kind!r} is not one of {', '.join(TARGET_KINDS)}")
        if not _ISO.fullmatch(self.observed_at):
            raise ConfigValidationError(f"observation time {self.observed_at!r} is not YYYY-MM-DDTHH:MM:SSZ")
        for name in ("project", "os_id", "target", "method"):
            if not getattr(self, name):
                raise ConfigValidationError(f"observation {name} must not be empty")

    def to_dict(self) -> dict[str, Any]:
        """The observation as a TOML-ready table."""
        data: dict[str, Any] = {
            "observation": {"schema": MerkleRoot.GLOBAL_SCHEMA, "project": self.project, "observed_at": self.observed_at, "observer": self.observer},
            "target": {"os_id": self.os_id, "kind": self.target_kind, "repository": self.target, "commit": self.commit, "record": self.record_hash},
            "finding": {"status": self.status.value, "method": self.method},
        }
        if self.evidence:
            data["finding"]["evidence"] = dict(self.evidence)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Observation:
        """An observation read back from :meth:`to_dict`'s table."""
        try:
            header, target, finding = data["observation"], data["target"], data["finding"]
            return cls(
                project=str(header["project"]),
                observed_at=str(header["observed_at"]),
                observer=str(header.get("observer", "")),
                os_id=str(target["os_id"]),
                target_kind=str(target["kind"]),
                target=str(target["repository"]),
                commit=str(target.get("commit", "")),
                record_hash=str(target.get("record", "")),
                status=ObservationStatus(str(finding["status"])),
                method=str(finding["method"]),
                evidence=dict(finding.get("evidence", {})),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigValidationError(f"not an observation: {exc!r}") from exc

    @property
    def observation_hash(self) -> str:
        """The observation's name: the hash of everything it says."""
        return MerkleRoot.observation_hash(self.to_dict())

    @property
    def filename(self) -> str:
        """Where it lives on the project's branch: ``observations/<hash>.toml``."""
        return f"observations/{self.observation_hash}.toml"


__all__ = ["TARGET_KINDS", "Observation", "ObservationStatus"]

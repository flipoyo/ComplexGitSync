"""omniscience_record — one registration, and what of it is committed.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: the canonical form of one registration of one Operating Space
    branch head (``GLOBAL_SCHEMA = 1``): which fields are hashed, which are
    addressing or claims and are not, ``H_OS_STATE``, the record's own hash
    (its file name), and its leaf in the global tree. Specified in
    ``AdditionalSpecs.md``, *The Omniscience register*.
Imports: errors, global_integrity

A registration commits to the project's name and ``os_id``, the branch, the
State (``state_id``, ``gittree_root``), the local ledger tip, and the
``.memory`` repository, ref and immutable commit that hold them, plus the
digest of the publication evidence that existed. It does **not** commit to
what can change after the fact or says who and when: ``recorded_at`` and
``contributor`` (a claim; a Git signature is the evidence), ``remote_locator``
and any URL alias, the individual publication rows (checkable against their
digest), and every access status. So the same registration made twice is the
same file, and an alias or a credential never rewrites a historical hash.

That ``os_id`` really is the id of ``project`` is checked where the id is
computed (``OperatingSpace``), not here: this module is pure.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import ConfigValidationError
from .global_integrity import GLOBAL_SCHEMA, GlobalIntegrity

_HEX64 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_STATE_ID = re.compile(r"state\([0-9a-f]{64}\)")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class OmniscienceRecord:
    """One registration of an Operating Space branch head."""

    project: str
    os_id: str
    project_branch: str
    state_id: str
    gittree_root: str
    local_ledger_tip: str
    local_memory_git_commit: str
    memory_repository: str
    memory_mount: str
    memory_ref: str
    memory_commit: str
    published_state_id: str | None = None
    # Not committed: claims, addressing and evidence (see the module docstring).
    recorded_at: str = ""
    contributor: str = ""
    remote_locator: str | None = None
    published_rows: tuple[Mapping[str, Any], ...] = field(default=(), compare=False)
    schema: int = GLOBAL_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != GLOBAL_SCHEMA:
            raise ConfigValidationError(f"record schema {self.schema} is not one this build reads (it reads {GLOBAL_SCHEMA})")
        for name, pattern in (
            ("os_id", _HEX64),
            ("gittree_root", _HEX64),
            ("state_id", _STATE_ID),
            ("local_ledger_tip", _DIGEST),
            ("local_memory_git_commit", _COMMIT),
            ("memory_commit", _COMMIT),
        ):
            if not pattern.fullmatch(str(getattr(self, name))):
                raise ConfigValidationError(f"record {name} {getattr(self, name)!r} is not a valid {name}")
        if self.published_state_id is not None and not _DIGEST.fullmatch(self.published_state_id):
            raise ConfigValidationError(f"record published_state id {self.published_state_id!r} is not a sha256 digest")
        for name in ("project", "project_branch", "memory_repository", "memory_mount", "memory_ref"):
            if not getattr(self, name):
                raise ConfigValidationError(f"record {name} must not be empty")
        # The rows are evidence outside the hash, so anyone can write them by hand; they reach Git commands later.
        for row in self.published_rows:
            ref, repository, commit = (str(row.get(key, "")) for key in ("ref", "repository", "commit"))
            if not _COMMIT.fullmatch(commit) or not ref or not repository or ref.startswith("-") or repository.startswith("-"):
                raise ConfigValidationError(f"record published row {dict(row)!r} needs a repository, a ref and a hex commit, none starting with '-'")

    # -- what is committed --------------------------------------------------

    def _operating_space(self) -> dict[str, str]:
        return {
            "os_id": self.os_id,
            "project_branch": self.project_branch,
            "state_id": self.state_id,
            "gittree_root": self.gittree_root,
            "local_ledger_tip": self.local_ledger_tip,
            "local_memory_git_commit": self.local_memory_git_commit,
        }

    def _memory(self) -> dict[str, str]:
        return {
            "repository": self.memory_repository,
            "mount": self.memory_mount,
            "ref": self.memory_ref,
            "commit": self.memory_commit,
        }

    def preimage(self) -> dict[str, Any]:
        """Exactly the fields the record's hash covers."""
        committed: dict[str, Any] = {
            "schema": self.schema,
            "project": self.project,
            "operating_space": self._operating_space(),
            "memory_repo": self._memory(),
        }
        if self.published_state_id is not None:
            committed["published_state_id"] = self.published_state_id
        return committed

    @property
    def os_state_hash(self) -> str:
        """``H_OS_STATE``: the Operating Space State commitment: os_id, branch, State, root, ledger tip, memory commit."""
        memory = {key: value for key, value in self._memory().items() if key != "mount"}
        return GlobalIntegrity.os_state_hash({**self._operating_space(), "memory_repo": memory})

    @property
    def record_hash(self) -> str:
        """The record's own hash, which is its file name."""
        return GlobalIntegrity.record_hash(self.preimage())

    @property
    def filename(self) -> str:
        """Where the record lives in the register: ``register/<record_hash>.toml``."""
        return f"register/{self.record_hash}.toml"

    # -- the global tree ----------------------------------------------------

    @property
    def leaf_key(self) -> tuple[bytes, ...]:
        """The order leaves take in the global tree: ``os_id``, branch, State, then the record hash, as UTF-8."""
        return tuple(part.encode("utf-8") for part in (self.os_id, self.project_branch, self.state_id, self.record_hash))

    @property
    def leaf_digest(self) -> bytes:
        """The registration's global leaf: one branch head, one State commitment, one record."""
        return GlobalIntegrity.leaf_digest(
            {
                "os_id": self.os_id,
                "project_branch": self.project_branch,
                "state_id": self.state_id,
                "os_state_hash": self.os_state_hash,
                "record_hash": self.record_hash,
            }
        )

    # -- the file -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The record as a TOML-ready table: committed fields, then claims, addressing and evidence."""
        memory: dict[str, Any] = self._memory()
        if self.remote_locator is not None:
            memory["remote_locator"] = self.remote_locator
        published: dict[str, Any] = {}
        if self.published_state_id is not None:
            published["id"] = self.published_state_id
            published["repo"] = [dict(row) for row in self.published_rows]
        data: dict[str, Any] = {
            "record": {
                "schema": self.schema,
                "project": self.project,
                "recorded_at": self.recorded_at,
                "contributor": self.contributor,
            },
            "operating_space": {**self._operating_space(), "os_state_hash": self.os_state_hash},
            "memory_repo": memory,
        }
        if published:
            data["published_state"] = published
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OmniscienceRecord:
        """A record read back from :meth:`to_dict`'s table; a stored ``os_state_hash`` that disagrees is refused."""
        try:
            header, space, memory = data["record"], data["operating_space"], data["memory_repo"]
            published = data.get("published_state", {})
            record = cls(
                schema=int(header["schema"]),
                project=str(header["project"]),
                recorded_at=str(header.get("recorded_at", "")),
                contributor=str(header.get("contributor", "")),
                os_id=str(space["os_id"]),
                project_branch=str(space["project_branch"]),
                state_id=str(space["state_id"]),
                gittree_root=str(space["gittree_root"]),
                local_ledger_tip=str(space["local_ledger_tip"]),
                local_memory_git_commit=str(space["local_memory_git_commit"]),
                memory_repository=str(memory["repository"]),
                memory_mount=str(memory["mount"]),
                memory_ref=str(memory["ref"]),
                memory_commit=str(memory["commit"]),
                remote_locator=str(memory["remote_locator"]) if "remote_locator" in memory else None,
                published_state_id=str(published["id"]) if "id" in published else None,
                published_rows=tuple(dict(row) for row in published.get("repo", ())),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigValidationError(f"not a registration record: {exc!r}") from exc
        stored = space.get("os_state_hash")
        if stored is not None and stored != record.os_state_hash:
            raise ConfigValidationError("the stored os_state_hash is not the one this record commits to")
        return record


__all__ = ["OmniscienceRecord"]

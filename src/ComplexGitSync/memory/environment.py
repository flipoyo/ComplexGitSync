"""environment — content-addressed persistence for Environment records.

Ring: 1 (filesystem only, no subprocess)
Contract: store and load one secret-free ``TreeEnvironment`` under
    ``.cgitsync/env/<hash>.toml`` using its canonical digest and an atomic
    replace; decide nothing about how the environment is observed.
Imports: environment_spec
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli_w

from ..environment_spec import TreeEnvironment

ENVIRONMENT_DIR_NAME = "env"
_ENVIRONMENT_ID_RE = re.compile(r"^env\(([0-9a-f]{64})\)$")


@dataclass(frozen=True)
class EnvironmentStore:
    """Content-addressed persistence for Environment records under ``env/``.

    Built on one state area; a record is stored under the name of its
    canonical digest, so the same environment is one file however often it is
    written.
    """

    cgitsync_dir: Path

    @staticmethod
    def format_id(environment_hash: str) -> str:
        """Return the ledger spelling for an Environment digest."""
        if not re.fullmatch(r"[0-9a-f]{64}", environment_hash):
            raise ValueError("environment hash must be 64 lowercase hexadecimal characters")
        return f"env({environment_hash})"

    @staticmethod
    def parse_hash(environment_id: str) -> str | None:
        """Return the digest inside ``env(<hash>)``, or ``None`` when invalid."""
        match = _ENVIRONMENT_ID_RE.fullmatch(environment_id)
        return match.group(1) if match else None

    def path(self, environment_hash: str) -> Path:
        """Return the canonical path for *environment_hash*."""
        EnvironmentStore.format_id(environment_hash)
        return self.cgitsync_dir / ENVIRONMENT_DIR_NAME / f"{environment_hash}.toml"

    def write(self, record: TreeEnvironment) -> Path:
        """Atomically persist *record* under the name of its canonical digest."""
        digest = record.digest()
        destination = self.path(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = tomli_w.dumps(record.to_dict()).encode("utf-8")
        if destination.is_file():
            if destination.read_bytes() != content:
                raise ValueError(f"Environment digest collision at {destination}")
            return destination

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
        return destination

    @staticmethod
    def read(path: Path) -> TreeEnvironment:
        """Load an Environment record and verify that its name matches its content."""
        with path.open("rb") as handle:
            record = TreeEnvironment.from_dict(tomllib.load(handle))
        expected = path.stem
        if record.digest() != expected:
            raise ValueError(f"Environment record digest does not match its filename: {path}")
        return record

    @staticmethod
    def resolve_references(
        memory_dirs: Iterable[Path], environment_ids: Iterable[str]
    ) -> list[dict[str, Any]]:
        """Resolve unique ledger references against pending and folded stores."""
        answer: list[dict[str, Any]] = []
        directories = tuple(memory_dirs)
        for environment_id in dict.fromkeys(environment_ids):
            digest = EnvironmentStore.parse_hash(environment_id)
            if digest is None:
                continue
            path = next(
                (
                    EnvironmentStore(directory).path(digest)
                    for directory in directories
                    if EnvironmentStore(directory).path(digest).is_file()
                ),
                None,
            )
            answer.append(
                {
                    "id": environment_id,
                    "path": str(path) if path is not None else None,
                    "record": EnvironmentStore.read(path).to_dict() if path else None,
                }
            )
        return answer


__all__ = [
    "ENVIRONMENT_DIR_NAME",
    "EnvironmentStore",
]

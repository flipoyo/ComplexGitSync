"""state_store — content-addressed .cgitsync/state(<hash>)_n/ directory allocation.

Ring: 1 (filesystem only, no subprocess)
Contract: given a ``.cgitsync`` directory and a state hash, format/parse the
    ``state(<hash>)`` identifier and ``state(<hash>)_n`` directory-name
    grammar, allocate the next free numbered directory for a given hash
    (collision-avoiding, via ``Path.exists()``), and enumerate existing
    state directories/artifacts already on disk (``.gts`` snapshots and
    named files) — no Git, no subprocess, no network.
Imports: none

Despite the ``MemoryStateDirectory``/``_resolve_memory_state_directory``
naming, this has **nothing to do with** the deleted Memory SSH-Git transport
(removed by ``CleanupPass2_DevPlanTicket.md`` D1). This is the general,
content-addressed directory allocator every lifecycle command (``initialise``,
``pull``, ``checkout``, ``push``, ``commit``, ``branch``, ``freeze``,
``freeze_release``, ``launch_release``) uses, via
``ComplexGitSyncClient.write_gts_snapshot()``, to allocate
``.cgitsync/state(<hash>)_<n>/`` directories — see that ticket's D1
"naming collision" section for the full history if this is confusing.

Extracted from ``orchestre/`` (module-level functions/class starting at
``_format_state_id``, plus the ``_SHA256_HEX_RE``/``_STATE_ID_RE``/
``_STATE_DIR_RE`` regex constants they depend on) as part of
``.agent/.local/.localSpec/DevTickets/archive/20260828_Isolation_DevPlanTicket.md`` Wave 2, work package
P5-state. That integration has landed: ``orchestre/`` imports six names
from here and keeps no copy of its own, so this module is the live
allocator every lifecycle command goes through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The directory every State is written into, flat: ``state/<hash>.gts``.
#:
#: The hash is the document's own content digest, so the same tree yields
#: the same file name on any machine. Nothing counts occurrences any more —
#: the same content is the same file, and being seen twice is two ledger
#: entries pointing at one name.
STATE_DIR_NAME = "state"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_STATE_ID_RE = re.compile(r"^state\(([0-9a-f]{64})\)$")
_STATE_DIR_RE = re.compile(r"^state\(([0-9a-f]{64})\)_(\d+)$")














@dataclass(frozen=True, slots=True)
class MemoryStateDirectory:
    state_hash: str
    state_order: int
    final_path: Path
    temporary_path: Path


@dataclass(frozen=True)
class MemoryStates:
    """The State files of one state area, and the grammar of their names.

    Built on a ``.cgitsync`` directory (folded or pending). A State is one
    file named by its content hash, ``state/<hash>.gts``; this class allocates
    and enumerates them, and formats and parses the ``state(<hash>)``
    identifier the ledger writes.
    """

    cgitsync_dir: Path

    @staticmethod
    def format_id(state_hash: str) -> str:
        if _SHA256_HEX_RE.fullmatch(state_hash) is None:
            raise ValueError("state_hash must be a lowercase hexadecimal SHA-256 digest")
        return f"state({state_hash})"

    @staticmethod
    def parse_hash(state_id: str) -> str | None:
        match = _STATE_ID_RE.fullmatch(state_id)
        return match.group(1) if match else None

    @staticmethod
    def directory_name(state_hash: str, state_order: int) -> str:
        if state_order < 0:
            raise ValueError("state_order must be non-negative")
        return f"{MemoryStates.format_id(state_hash)}_{state_order}"

    @staticmethod
    def temporary_directory_name(state_hash: str, state_order: int) -> str:
        if state_order < 0:
            raise ValueError("state_order must be non-negative")
        return f".tmp-{MemoryStates.directory_name(state_hash, state_order)}"

    @staticmethod
    def order_from_directory_name(name: str) -> int | None:
        match = _STATE_DIR_RE.fullmatch(name)
        return int(match.group(2)) if match else None

    def next_directory_order(self, state_hash: str) -> int:
        MemoryStates.format_id(state_hash)
        max_order = -1
        if self.cgitsync_dir.is_dir():
            for entry in self.cgitsync_dir.iterdir():
                if not entry.is_dir():
                    continue
                match = _STATE_DIR_RE.fullmatch(entry.name)
                if match is None or match.group(1) != state_hash:
                    continue
                max_order = max(max_order, int(match.group(2)))
        return max_order + 1

    def resolve_directory(self, state_hash: str) -> MemoryStateDirectory:
        state_order = self.next_directory_order(state_hash)
        while True:
            final_path = self.cgitsync_dir / MemoryStates.directory_name(state_hash, state_order)
            temporary_path = self.cgitsync_dir / MemoryStates.temporary_directory_name(state_hash, state_order)
            if not final_path.exists() and not temporary_path.exists():
                return MemoryStateDirectory(
                    state_hash=state_hash,
                    state_order=state_order,
                    final_path=final_path,
                    temporary_path=temporary_path,
                )
            state_order += 1

    def path(self, state_hash: str, suffix: str = ".gts") -> Path:
        """Where a State of *state_hash* is written: ``state/<hash><suffix>``.

        The one place that composes a State's path. ``.gts`` is the State
        itself, and the only thing written there: a State is the tree, not the
        spec that built it. Older memories may still hold a ``.cgs`` beside a
        State; nothing writes one, and nothing treats one as damage.
        """
        return self.cgitsync_dir / STATE_DIR_NAME / f"{state_hash}{suffix}"

    def snapshot_candidates(self) -> list[Path]:
        """Every ``.gts`` under *self.cgitsync_dir*, in both layouts.

        The flat ``state/<hash>.gts`` written today, and the older
        ``state(<hash>)_<n>/`` directories. A workspace that predates the flat
        layout keeps resolving without being rewritten: nothing here migrates
        anything, it only reads.
        """
        candidates: list[Path] = []
        if self.cgitsync_dir.is_dir():
            for state_dir in sorted(self.cgitsync_dir.iterdir(), key=lambda path: path.name):
                if not state_dir.is_dir() or _STATE_DIR_RE.fullmatch(state_dir.name) is None:
                    continue
                candidates.extend(sorted(state_dir.glob("*.gts")))
        flat_state_dir = self.cgitsync_dir / STATE_DIR_NAME
        if flat_state_dir.is_dir():
            candidates.extend(sorted(flat_state_dir.glob("*.gts")))
        return candidates

    def snapshot_candidates_for_id(self, state_id: str) -> list[Path]:
        state_hash = MemoryStates.parse_hash(state_id)
        if state_hash is None or not self.cgitsync_dir.is_dir():
            return []
        candidates: list[Path] = []
        for state_dir in sorted(self.cgitsync_dir.glob(f"{state_id}_*"), key=lambda path: path.name):
            if state_dir.is_dir() and _STATE_DIR_RE.fullmatch(state_dir.name) is not None:
                candidates.extend(sorted(state_dir.glob("*.gts")))
        return candidates

    def artifact_candidates(self, filename: str) -> list[Path]:
        candidates: list[Path] = []
        if not self.cgitsync_dir.is_dir():
            return candidates
        for state_dir in sorted(self.cgitsync_dir.iterdir(), key=lambda path: path.name):
            if not state_dir.is_dir() or _STATE_DIR_RE.fullmatch(state_dir.name) is None:
                continue
            candidate = state_dir / filename
            if candidate.is_file():
                candidates.append(candidate)
        return candidates

    def latest_artifact(self, filename: str) -> Path | None:
        candidates = self.artifact_candidates(filename)
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def write(self, state_hash: str, write: Any, *, suffix: str = ".gts") -> Path:
        """Write one State, atomically, at the path its content hash names.

        *write* is called with a temporary path in the same directory and must
        write the file; the rename that follows is what makes the State appear
        all at once. A crash leaves either the previous State or none, never a
        truncated one.

        The old layout got the same guarantee by building a whole directory and
        renaming it into place. A State is one file now, so it costs one rename.
        """
        destination = self.path(state_hash, suffix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        MemoryStates.write_atomically(destination, write)
        return destination

    @staticmethod
    def write_atomically(destination: Path, write: Any) -> None:
        """Write *destination* through a temporary file in the same directory.

        *write* is called with the temporary path and must write the file. A
        reader never sees a half-written file, and a crash leaves either the
        previous content or none, never a truncated one.
        """
        temporary = destination.with_name(f".{destination.name}.tmp")
        try:
            write(temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


__all__ = [
    "MemoryStateDirectory",
    "MemoryStates",
]

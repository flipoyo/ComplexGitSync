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

Extracted verbatim from ``orchestre.py`` (module-level functions/class
starting at ``_format_state_id``, plus the ``_SHA256_HEX_RE``/``_STATE_ID_RE``/
``_STATE_DIR_RE`` regex constants they depend on) as part of
``AgentSpec/20260828_Isolation_DevPlanTicket.md`` Wave 2, work package
P5-state. ``orchestre.py`` still carries its own copy of this code — a
later, separate Lane-B integration step re-points its callers at this module
and deletes the duplicate; this module is not wired in yet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_STATE_ID_RE = re.compile(r"^state\(([0-9a-f]{64})\)$")
_STATE_DIR_RE = re.compile(r"^state\(([0-9a-f]{64})\)_(\d+)$")


def _format_state_id(state_hash: str) -> str:
    if _SHA256_HEX_RE.fullmatch(state_hash) is None:
        raise ValueError("state_hash must be a lowercase hexadecimal SHA-256 digest")
    return f"state({state_hash})"


def _parse_state_hash(state_id: str) -> str | None:
    match = _STATE_ID_RE.fullmatch(state_id)
    return match.group(1) if match else None


def _state_directory_name(state_hash: str, state_order: int) -> str:
    if state_order < 0:
        raise ValueError("state_order must be non-negative")
    return f"{_format_state_id(state_hash)}_{state_order}"


def _temporary_state_directory_name(state_hash: str, state_order: int) -> str:
    if state_order < 0:
        raise ValueError("state_order must be non-negative")
    return f".tmp-{_state_directory_name(state_hash, state_order)}"


def _state_order_from_directory_name(name: str) -> int | None:
    match = _STATE_DIR_RE.fullmatch(name)
    return int(match.group(2)) if match else None


def _next_state_directory_order(cgitsync_dir: Path, state_hash: str) -> int:
    _format_state_id(state_hash)
    max_order = -1
    if cgitsync_dir.is_dir():
        for entry in cgitsync_dir.iterdir():
            if not entry.is_dir():
                continue
            match = _STATE_DIR_RE.fullmatch(entry.name)
            if match is None or match.group(1) != state_hash:
                continue
            max_order = max(max_order, int(match.group(2)))
    return max_order + 1


@dataclass(frozen=True, slots=True)
class MemoryStateDirectory:
    state_hash: str
    state_order: int
    final_path: Path
    temporary_path: Path


def _resolve_memory_state_directory(cgitsync_dir: Path, state_hash: str) -> MemoryStateDirectory:
    state_order = _next_state_directory_order(cgitsync_dir, state_hash)
    while True:
        final_path = cgitsync_dir / _state_directory_name(state_hash, state_order)
        temporary_path = cgitsync_dir / _temporary_state_directory_name(state_hash, state_order)
        if not final_path.exists() and not temporary_path.exists():
            return MemoryStateDirectory(
                state_hash=state_hash,
                state_order=state_order,
                final_path=final_path,
                temporary_path=temporary_path,
            )
        state_order += 1


def _state_snapshot_candidates(cgitsync_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    if cgitsync_dir.is_dir():
        for state_dir in sorted(cgitsync_dir.iterdir(), key=lambda path: path.name):
            if not state_dir.is_dir() or _STATE_DIR_RE.fullmatch(state_dir.name) is None:
                continue
            candidates.extend(sorted(state_dir.glob("*.gts")))
    legacy_state_dir = cgitsync_dir / "state"
    if legacy_state_dir.is_dir():
        candidates.extend(sorted(legacy_state_dir.glob("*.gts")))
    return candidates


def _state_snapshot_candidates_for_id(cgitsync_dir: Path, state_id: str) -> list[Path]:
    state_hash = _parse_state_hash(state_id)
    if state_hash is None or not cgitsync_dir.is_dir():
        return []
    candidates: list[Path] = []
    for state_dir in sorted(cgitsync_dir.glob(f"{state_id}_*"), key=lambda path: path.name):
        if state_dir.is_dir() and _STATE_DIR_RE.fullmatch(state_dir.name) is not None:
            candidates.extend(sorted(state_dir.glob("*.gts")))
    return candidates


def _state_artifact_candidates(cgitsync_dir: Path, filename: str) -> list[Path]:
    candidates: list[Path] = []
    if not cgitsync_dir.is_dir():
        return candidates
    for state_dir in sorted(cgitsync_dir.iterdir(), key=lambda path: path.name):
        if not state_dir.is_dir() or _STATE_DIR_RE.fullmatch(state_dir.name) is None:
            continue
        candidate = state_dir / filename
        if candidate.is_file():
            candidates.append(candidate)
    return candidates


def _latest_state_artifact(cgitsync_dir: Path, filename: str) -> Path | None:
    candidates = _state_artifact_candidates(cgitsync_dir, filename)
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)

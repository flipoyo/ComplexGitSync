"""snapshot_resolver — resolve which .gts snapshot the CLI should default to.

Ring: 1 (filesystem only, no subprocess)
Contract: given optional CLI arguments (an explicit path and/or a search
    directory), return the .gts snapshot path a command should use —
    resolving CGSHOME, preferring a register's recorded current snapshot,
    and otherwise falling back to the most recently modified snapshot on
    disk — or raise FileNotFoundError with an actionable message.
Imports: stdlib only (os, re, tomllib, pathlib)

Temporary duplication with ``state_store.py``
-----------------------------------------------
``_STATE_DIR_RE``, ``_state_order_from_directory_name``, and
``_state_snapshot_candidates`` below are self-contained copies of logic that
also lives in ``orchestre.py`` today and is, at the time this module was
authored, being extracted *in parallel* by a different work package
(P5-state, Wave 2 of ``AgentSpec/20260828_Isolation_DevPlanTicket.md``) into
its own ``state_store.py`` module — a general "content-addressed state
directory" abstraction this module does not need in full. This module only
needs the narrow slice of that family required to answer "which snapshot
does the CLI default to": recognising a canonical ``state(<hash>)_<n>``
directory name and listing the ``.gts`` files under such directories. Rather
than import a module that may not exist yet (or may land with an
incompatible shape), this module carries its own minimal copy, matching the
precedent already used successfully between ``ledger_entry.py`` and
``integrity.py`` in Wave 1. A later integration step should reconcile this
duplication — e.g. by having this module depend on ``state_store.py`` for
the directory-name parsing once both have landed — rather than each module
silently drifting apart.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

# ---------------------------------------------------------------------------
# Minimal, self-contained copy of the canonical state-directory naming
# scheme (see module docstring: duplicated from orchestre.py / state_store.py
# on purpose, pending a later reconciliation pass).
# ---------------------------------------------------------------------------

_STATE_DIR_RE = re.compile(r"^state\(([0-9a-f]{64})\)_(\d+)$")


def _state_order_from_directory_name(name: str) -> int | None:
    """Return the trailing order suffix of a canonical ``state(<hash>)_<n>``
    directory name, or ``None`` if *name* does not match that shape."""
    match = _STATE_DIR_RE.fullmatch(name)
    return int(match.group(2)) if match else None


def _state_snapshot_candidates(cgitsync_dir: Path) -> list[Path]:
    """Return every ``*.gts`` file under canonical state directories and the
    legacy ``state/`` directory beneath *cgitsync_dir*."""
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


# ---------------------------------------------------------------------------
# CLI-facing default-snapshot resolution.
# ---------------------------------------------------------------------------


def _state_lgr_candidates(cgshome: Path) -> list[Path]:
    """Return every ``*.lgr`` register file under canonical state
    directories beneath ``cgshome/.cgitsync``."""
    cgitsync_dir = cgshome / ".cgitsync"
    if not cgitsync_dir.is_dir():
        return []
    candidates: list[Path] = []
    for state_dir in sorted(cgitsync_dir.iterdir(), key=lambda path: path.name):
        if not state_dir.is_dir() or _state_order_from_directory_name(state_dir.name) is None:
            continue
        candidates.extend(sorted(state_dir.glob("*.lgr")))
    return candidates


def _discover_lgr_path(cgshome: Path) -> Path:
    """Return the single ``.lgr`` register to consult under *cgshome*.

    Prefers the most recently modified register found inside a canonical
    state directory; falls back to a lone ``*.lgr`` file directly under
    *cgshome*. Raises ``FileNotFoundError`` if none or more than one
    ambiguous candidate is found at the top level.
    """
    canonical_entries = _state_lgr_candidates(cgshome)
    if canonical_entries:
        canonical_entries.sort(key=lambda path: (path.stat().st_mtime, str(path)), reverse=True)
        return canonical_entries[0]

    lgr_entries = sorted(cgshome.glob("*.lgr"))
    if not lgr_entries:
        raise FileNotFoundError(f"No .lgr register found under CGSHOME: {cgshome}")
    if len(lgr_entries) > 1:
        names = ", ".join(path.name for path in lgr_entries)
        raise FileNotFoundError(f"Multiple .lgr registers found under {cgshome}: {names}")
    return lgr_entries[0]


def _expand_lgr_path(raw_path: str) -> Path:
    """Expand ``$HOME`` and other environment variables in a path recorded
    inside a ``.lgr`` register."""
    expanded = raw_path
    home = os.environ.get("HOME")
    if home:
        expanded = expanded.replace("$HOME", home)
    return Path(os.path.expandvars(expanded)).expanduser()


def discover_cgshome(search_dir: str | Path | None = None) -> Path:
    """Resolve and return CGSHOME.

    Resolution order:

    1. Walk up from ``search_dir`` when provided.
    2. Walk up from ``$CGSHOME`` when defined.
    3. Walk up from the current working directory.

    Raises
    ------
    FileNotFoundError
        If no ancestor contains a ``.cgitsync`` directory.
    """
    start_dir: Path
    search_origin: str
    if search_dir is not None:
        start_dir = Path(search_dir).expanduser().resolve()
        search_origin = f"--search-dir ({start_dir})"
    else:
        env_cgshome = os.environ.get("CGSHOME")
        if env_cgshome:
            start_dir = Path(env_cgshome).expanduser().resolve()
            search_origin = f"$CGSHOME ({start_dir})"
        else:
            start_dir = Path.cwd().resolve()
            search_origin = f"current working directory ({start_dir})"

    for candidate in (start_dir, *start_dir.parents):
        if (candidate / ".cgitsync").is_dir():
            return candidate.resolve()

    raise FileNotFoundError(
        "Unable to locate CGSHOME. "
        f"Checked {search_origin} and its parents for a .cgitsync directory."
    )


def discover_gts_path(search_dir: str | Path | None = None) -> Path:
    """Return the ``.gts`` snapshot a command should default to.

    Resolution order:

    1. Locate CGSHOME (see :func:`discover_cgshome`).
    2. If a ``.lgr`` register can be found and it names a
       ``current_snapshot_path`` that still exists on disk, use it.
    3. Otherwise fall back to the most recently modified ``.gts`` file
       found under canonical ``state(<hash>)_<n>`` directories (or the
       legacy ``state/`` directory) beneath ``CGSHOME/.cgitsync``.

    Parameters
    ----------
    search_dir:
        Optional directory whose ancestors are searched first when resolving
        CGSHOME.

    Raises
    ------
    FileNotFoundError
        If CGSHOME cannot be located, or if ``CGSHOME/.cgitsync``
        contains no ``.gts`` snapshots.
    """
    cgshome = discover_cgshome(search_dir)
    try:
        register_path = _discover_lgr_path(cgshome)
        data = tomllib.loads(register_path.read_text(encoding="utf-8"))
        current_snapshot_path = data.get("register", {}).get("current_snapshot_path")
        if isinstance(current_snapshot_path, str) and current_snapshot_path:
            resolved_current = _expand_lgr_path(current_snapshot_path).resolve()
            if resolved_current.is_file():
                return resolved_current
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        pass

    cgitsync_dir = cgshome / ".cgitsync"
    gts_entries = [(path, path.stat().st_mtime) for path in _state_snapshot_candidates(cgitsync_dir)]
    if gts_entries:
        gts_entries.sort(key=lambda x: x[1], reverse=True)
        return gts_entries[0][0].resolve()

    raise FileNotFoundError(
        f"No .gts snapshot found under CGSHOME/.cgitsync: {cgitsync_dir}. "
        "Run 'cgitsync initialise' first, or pass --gts FILE explicitly."
    )


def resolve_gts_path(gts: str | None, search_dir: str | None) -> Path:
    """Return the resolved .gts path, auto-discovering when *gts* is ``None``."""
    if gts is not None:
        return Path(gts)
    return discover_gts_path(search_dir)


def resolve_workspace_source(source: str | None, search_dir: str | None) -> Path:
    """Return a workspace source path for commands that accept optional input.

    Parameters
    ----------
    source:
        Explicit ``.cgs`` or ``.gts`` path supplied on the command line.
    search_dir:
        Optional directory whose ancestors are searched first when resolving
        CGSHOME during auto-discovery.

    Returns
    -------
    Path
        The explicit source path, or the latest workspace snapshot under
        ``CGSHOME/.cgitsync`` when *source* is omitted.

    Raises
    ------
    FileNotFoundError
        If auto-discovery is required and CGSHOME or a workspace snapshot
        cannot be located.
    """
    if source is not None:
        return Path(source)
    return discover_gts_path(search_dir)


def resolve_visualization_source(source: str | None, search_dir: str | None) -> Path:
    """Return the resolved source path for visualization commands.

    When *source* is provided it is returned as-is (may be .cgs or .gts).
    When *source* is ``None`` the latest .gts snapshot is discovered
    automatically via :func:`discover_gts_path`.
    """
    return resolve_workspace_source(source, search_dir)

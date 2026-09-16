"""snapshot_resolver — resolve which .gts snapshot the CLI should default to.

Ring: 1 (filesystem only, no subprocess)
Contract: given optional CLI arguments (an explicit path and/or a search
    directory), return the .gts snapshot path a command should use —
    resolving CGSHOME, preferring a register's recorded current snapshot,
    and otherwise falling back to the most recently modified snapshot on
    disk — or raise FileNotFoundError with an actionable message. The
    ``describe_*`` functions return the same answer wrapped in a
    ``CgshomeResolution``/``SnapshotResolution`` record naming *which input
    decided it*, so the CLI can report a workspace the user did not expect
    instead of silently acting on it. This module never prints.
Imports: settings, state_store

One state-path grammar, imported
-------------------------------
``_STATE_DIR_RE`` and the state-directory helpers used to be copied into
this module, because ``state_store.py`` was authored in parallel and might
have landed with an incompatible shape. Both have existed side by side for
weeks, so this module now imports them: one grammar, one place, and a
workspace laid out either way resolves the same.

"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .settings import default_workspace
from .state_store import _state_order_from_directory_name, _state_snapshot_candidates


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




# ---------------------------------------------------------------------------
# Resolution provenance — what was chosen, and on whose say-so.
#
# Auto-discovery answers "which workspace?" from three possible sources, and
# only one of them is visible in the command the user typed. When the answer
# is not the workspace the user is standing in, every later line the command
# prints describes a tree they cannot see. These records carry the *reason*
# alongside the path so the CLI (Ring 4) can say it out loud; nothing in this
# module prints.
# ---------------------------------------------------------------------------

CGSHOME_ORIGIN_SEARCH_DIR = "--search-dir"
CGSHOME_ORIGIN_ENVIRONMENT = "$CGSHOME"
CGSHOME_ORIGIN_CWD = "current directory"

#: The fourth and last answer: no input found a workspace, so the default one
#: was used. Named separately from the three inputs above because it is not an
#: input at all — nobody asked for it, and the CLI says so rather than letting
#: a user believe the tool found something of theirs.
CGSHOME_ORIGIN_DEFAULT = "default workspace"

SNAPSHOT_ORIGIN_EXPLICIT = "explicit path"
SNAPSHOT_ORIGIN_REGISTER = "register"
SNAPSHOT_ORIGIN_MOST_RECENT = "most recent snapshot"


@dataclass(frozen=True)
class CgshomeResolution:
    """Which CGSHOME a command resolved, from which starting point, and why.

    ``origin`` is one of the ``CGSHOME_ORIGIN_*`` constants — the input that
    decided the answer. ``start_dir`` is the directory the upward walk began
    at, which is the *value* of that input, not necessarily the workspace
    found (the walk climbs to the nearest ancestor holding ``.cgitsync``).
    """

    path: Path
    origin: str
    start_dir: Path

    @property
    def contains_cwd(self) -> bool:
        """Whether the current directory lies inside the resolved workspace.

        ``False`` is the one case a user cannot see coming: the command will
        read and write a tree somewhere else on disk while reporting it in
        terms that look exactly like the tree they are standing in.
        """
        cwd = Path.cwd().resolve()
        return cwd == self.path or self.path in cwd.parents


@dataclass(frozen=True)
class SnapshotResolution:
    """Which ``.gts`` snapshot a command resolved, and how it was chosen.

    ``cgshome`` is ``None`` when the caller passed an explicit path — no
    workspace discovery ran, so there is no provenance to report.
    """

    path: Path
    origin: str
    cgshome: CgshomeResolution | None = None
    register_path: Path | None = None

    @property
    def inside_cgshome(self) -> bool:
        """Whether the snapshot actually lives under the workspace it came from.

        A register may name a ``current_snapshot_path`` pointing anywhere;
        one pointing outside its own CGSHOME means the two disagree about
        which tree is current, which is worth saying rather than following
        in silence.
        """
        if self.cgshome is None:
            return True
        resolved = self.path.resolve()
        return resolved == self.cgshome.path or self.cgshome.path in resolved.parents


def describe_cgshome(search_dir: str | Path | None = None) -> CgshomeResolution:
    """Resolve CGSHOME and report which input decided it.

    Resolution order (the first three unchanged, and deliberately so — the
    documented bootstrap workflow tells users to export ``$CGSHOME``):

    1. Walk up from ``search_dir`` when provided.
    2. Walk up from ``$CGSHOME`` when defined.
    3. Walk up from the current working directory.
    4. Fall back to the default workspace, creating it once if it does not
       exist yet (``settings.default_workspace``).

    Step 4 is why this no longer raises for an ordinary run: a workspace
    that holds nothing is a valid answer, and the alternative was a
    traceback the first time anyone typed a command outside a tree. It is
    reported as :data:`CGSHOME_ORIGIN_DEFAULT` so the answer never
    masquerades as something the user pointed at.

    An explicit *search_dir* is never overridden by the default: the user
    named a directory, and silently working somewhere else would be the
    sharpest edge this project has.

    Raises
    ------
    FileNotFoundError
        If *search_dir* was given and no ancestor of it contains a
        ``.cgitsync`` directory, or if the default workspace cannot be
        created.
    """
    start_dir: Path
    origin: str
    if search_dir is not None:
        start_dir = Path(search_dir).expanduser().resolve()
        origin = CGSHOME_ORIGIN_SEARCH_DIR
    else:
        env_cgshome = os.environ.get("CGSHOME")
        if env_cgshome:
            start_dir = Path(env_cgshome).expanduser().resolve()
            origin = CGSHOME_ORIGIN_ENVIRONMENT
        else:
            start_dir = Path.cwd().resolve()
            origin = CGSHOME_ORIGIN_CWD

    for candidate in (start_dir, *start_dir.parents):
        if (candidate / ".cgitsync").is_dir():
            return CgshomeResolution(
                path=candidate.resolve(), origin=origin, start_dir=start_dir
            )

    if origin != CGSHOME_ORIGIN_SEARCH_DIR:
        fallback = default_workspace()
        if fallback is not None:
            return CgshomeResolution(
                path=fallback.resolve(),
                origin=CGSHOME_ORIGIN_DEFAULT,
                start_dir=start_dir,
            )

    raise FileNotFoundError(
        "Unable to locate CGSHOME. "
        f"Checked {origin} ({start_dir}) and its parents for a .cgitsync directory."
    )


def discover_cgshome(search_dir: str | Path | None = None) -> Path:
    """Resolve and return CGSHOME.

    The path only. Callers that need to tell a user *why* this workspace was
    chosen — every CLI command that auto-discovers one — should call
    :func:`describe_cgshome` instead.
    """
    return describe_cgshome(search_dir).path


def describe_gts_path(search_dir: str | Path | None = None) -> SnapshotResolution:
    """Return the ``.gts`` snapshot a command should default to, with provenance.

    Resolution order:

    1. Locate CGSHOME (see :func:`describe_cgshome`).
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
    cgshome = describe_cgshome(search_dir)
    try:
        register_path = _discover_lgr_path(cgshome.path)
        data = tomllib.loads(register_path.read_text(encoding="utf-8"))
        current_snapshot_path = data.get("register", {}).get("current_snapshot_path")
        if isinstance(current_snapshot_path, str) and current_snapshot_path:
            resolved_current = _expand_lgr_path(current_snapshot_path).resolve()
            if resolved_current.is_file():
                return SnapshotResolution(
                    path=resolved_current,
                    origin=SNAPSHOT_ORIGIN_REGISTER,
                    cgshome=cgshome,
                    register_path=register_path,
                )
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        pass

    cgitsync_dir = cgshome.path / ".cgitsync"
    gts_entries = [(path, path.stat().st_mtime) for path in _state_snapshot_candidates(cgitsync_dir)]
    if gts_entries:
        gts_entries.sort(key=lambda x: x[1], reverse=True)
        return SnapshotResolution(
            path=gts_entries[0][0].resolve(),
            origin=SNAPSHOT_ORIGIN_MOST_RECENT,
            cgshome=cgshome,
        )

    raise FileNotFoundError(
        f"No .gts snapshot found under CGSHOME/.cgitsync: {cgitsync_dir} "
        f"(CGSHOME came from {cgshome.origin}). "
        "Run 'cgitsync initialise' first, or pass --gts FILE explicitly."
    )


def discover_gts_path(search_dir: str | Path | None = None) -> Path:
    """Return the ``.gts`` snapshot a command should default to.

    The path only; see :func:`describe_gts_path` for the same answer with
    the provenance a CLI command needs in order to report it.
    """
    return describe_gts_path(search_dir).path


def describe_workspace_source(
    source: str | None, search_dir: str | None
) -> SnapshotResolution:
    """Resolve a command's source path, with provenance.

    An explicit *source* is returned verbatim (never resolved, matching
    :func:`resolve_workspace_source`) and carries no CGSHOME provenance —
    nothing was discovered, so there is nothing to explain. Otherwise this
    is :func:`describe_gts_path`.
    """
    if source is not None:
        return SnapshotResolution(path=Path(source), origin=SNAPSHOT_ORIGIN_EXPLICIT)
    return describe_gts_path(search_dir)


def resolve_gts_path(gts: str | None, search_dir: str | None) -> Path:
    """Return the resolved .gts path, auto-discovering when *gts* is ``None``."""
    return describe_workspace_source(gts, search_dir).path


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
    return describe_workspace_source(source, search_dir).path


def resolve_visualization_source(source: str | None, search_dir: str | None) -> Path:
    """Return the resolved source path for visualization commands.

    When *source* is provided it is returned as-is (may be .cgs or .gts).
    When *source* is ``None`` the latest .gts snapshot is discovered
    automatically via :func:`discover_gts_path`.
    """
    return resolve_workspace_source(source, search_dir)

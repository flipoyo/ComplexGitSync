"""paths — environment-marker path portability ($HOME/%USERPROFILE%/etc.) and CGSHOME/CGSPATH resolution.

Ring: 1 (reads os.environ, Path.cwd()/Path.home(), and — only in
    resolve_bootstrap_root's timestamp default — the clock, via
    universal_clock; no subprocess)
Contract: convert between absolute, machine-specific paths and the portable
    $HOME/%USERPROFILE%/%HOMEDRIVE%%HOMEPATH% marker tokens that .gts/.lgr
    documents record instead of raw absolute paths; and resolve where
    CGSHOME/CGSPATH live for load/initialise/clone/bootstrap, from an
    explicit override, the environment, or the current working directory.
Imports: cgs_format, errors, universal_clock

Extracted verbatim from ``orchestre.py`` (Wave 2, P5-paths of
``.agent/.local/.localSpec/DevTickets/archive/20260828_Isolation_DevPlanTicket.md``). ``orchestre.py`` still
carries its own copy of every function below until a later, separate
integration step deletes it there and re-points imports — this module does
not change that file.

The five env-marker functions (``_get_path_environment_markers`` through
``_preferred_path_separators``) are copied verbatim, unchanged, from
``orchestre.py``: pure string/``Path`` manipulation that only reads
environment-variable *values*, never mutates anything.

The four ``resolve_*`` functions mirror ``ComplexGitSyncClient``'s
``resolve_cgshome``/``resolve_initialise_cgshome``/``resolve_clone_root``/
``resolve_bootstrap_root`` methods. Reading those methods showed none of
them reference ``self`` for anything beyond calling another one of the same
four (or the private ``_resolve_project_root`` helper, also duplicated here
for the same reason) — every one is already a pure function of its explicit
arguments plus ``os.environ``/``Path.cwd()``/``Path.home()``/the clock, just
written as a bound method. They are extracted here as plain functions so
they are testable and reusable with no ``ComplexGitSyncClient`` instance;
the client methods are expected to become thin delegates to these in the
later integration step.
"""

from __future__ import annotations

import os
from pathlib import Path

from .cgs_format import CgsDocument
from .errors import ConfigValidationError, GitSyncError
from .universal_clock import ClockProtocol, SystemClock

# ============================================================
#  Environment-marker path portability
# ============================================================


def _get_path_environment_markers() -> tuple[tuple[str, Path], ...]:
    markers: list[tuple[str, Path]] = []
    seen_paths: set[str] = set()

    def add_marker(token: str, raw_value: str | None) -> None:
        if not raw_value:
            return
        resolved = Path(raw_value).expanduser().resolve()
        key = os.path.normcase(str(resolved))
        if key in seen_paths:
            return
        seen_paths.add(key)
        markers.append((token, resolved))

    add_marker("$HOME", os.environ.get("HOME"))
    add_marker("%USERPROFILE%", os.environ.get("USERPROFILE"))
    homedrive = os.environ.get("HOMEDRIVE")
    homepath = os.environ.get("HOMEPATH")
    if homedrive and homepath:
        add_marker("%HOMEDRIVE%%HOMEPATH%", f"{homedrive}{homepath}")
    return tuple(markers)


def _path_to_environment_marker(path: Path | str) -> str:
    resolved_path = Path(path).expanduser().resolve()
    for token, base_path in _get_path_environment_markers():
        try:
            relative = resolved_path.relative_to(base_path)
        except ValueError:
            continue
        if relative == Path("."):
            return token
        return f"{token}/{relative.as_posix()}"
    return str(resolved_path)


def _expand_environment_markers(raw_path: str) -> str:
    def _replace_prefixed_marker(value: str, marker: str, replacement: str) -> str:
        if value == marker:
            return replacement
        for separator in _preferred_path_separators():
            prefix = f"{marker}{separator}"
            if value.startswith(prefix):
                suffix = value[len(prefix):]
                return f"{replacement}{separator}{suffix}"
        return value

    expanded = raw_path
    home = os.environ.get("HOME")
    if home:
        expanded = _replace_prefixed_marker(expanded, "$HOME", home)
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        expanded = _replace_prefixed_marker(expanded, "%USERPROFILE%", userprofile)
    homedrive = os.environ.get("HOMEDRIVE")
    homepath = os.environ.get("HOMEPATH")
    if homedrive and homepath:
        expanded = _replace_prefixed_marker(
            expanded,
            "%HOMEDRIVE%%HOMEPATH%",
            f"{homedrive}{homepath}",
        )
    return expanded


#: What a State calls the tree it describes.
#:
#: Every path a State records sits inside the workspace, so every one of
#: them is written against this token and never against the machine. A
#: snapshot that said ``$HOME/work/demo/docs`` published one developer's
#: directory layout to everyone who could read the memory; ``$CGSTREE/docs``
#: says the same thing about the tree and nothing at all about the disk.
#:
#: It is deliberately **not** ``$CGSHOME``: that names an environment
#: variable which may be set, unset, or pointing at a different workspace
#: entirely, and a path that resolves differently depending on a shell is
#: exactly the trap this token exists to avoid. It is resolved against the
#: workspace the snapshot was found in, by the reader, always.
TREE_MARKER = "$CGSTREE"


def _path_against_tree(path: Path | str, tree_root: Path) -> str | None:
    """Write *path* as ``$CGSTREE/...``, or ``None`` if it is outside the tree.

    ``None`` is the honest answer for a path outside the workspace — a
    ``.cgs`` kept in a home directory, say. It cannot be expressed against
    the tree, it means nothing on another machine, and recording it would
    publish exactly the layout this function exists to keep out of a memory
    that gets pushed (MemoryRepoLocal's gate G5).
    """
    resolved = Path(path).expanduser().resolve()
    try:
        relative = resolved.relative_to(tree_root)
    except ValueError:
        return None
    return TREE_MARKER if relative == Path(".") else f"{TREE_MARKER}/{relative.as_posix()}"


def _path_from_tree(raw_path: str, tree_root: Path | None) -> Path:
    """Resolve a recorded path, expanding :data:`TREE_MARKER` against *tree_root*.

    Falls through to the older environment-marker handling for every
    snapshot written before the tree marker existed, which keeps reading
    them exactly as it always did.
    """
    if raw_path == TREE_MARKER or raw_path.startswith(f"{TREE_MARKER}/"):
        if tree_root is None:
            raise ConfigValidationError(
                f"this snapshot records paths against {TREE_MARKER} but no workspace "
                "was given to resolve them against."
            )
        suffix = raw_path[len(TREE_MARKER) :].lstrip("/")
        return (tree_root / suffix).resolve() if suffix else tree_root
    return _resolve_document_path(raw_path)


def _resolve_document_path(raw_path: str) -> Path:
    return Path(_expand_environment_markers(raw_path)).expanduser().resolve()


def _preferred_path_separators() -> tuple[str, ...]:
    separators: list[str] = []
    seen: set[str] = set()
    for separator in (os.sep, os.altsep, "/", "\\"):
        if separator and separator not in seen:
            seen.add(separator)
            separators.append(separator)
    return tuple(separators)


# ============================================================
#  CGSHOME / CGSPATH resolution
# ============================================================


def resolve_cgshome(
    document: CgsDocument,
    source_path: Path,
    *,
    output_path: str | Path | None = None,
) -> Path:
    """Resolve CGSHOME from CGSPATH, the environment, or CWD."""
    if output_path is not None:
        cgspath = Path(output_path).expanduser().resolve()
        return (cgspath / (document.project_name or source_path.stem)).resolve()
    env_cgshome = os.environ.get("CGSHOME")
    if env_cgshome:
        return Path(env_cgshome).expanduser().resolve()
    cgspath = (Path.cwd() / "../..").resolve()
    return (cgspath / (document.project_name or source_path.stem)).resolve()


def resolve_initialise_cgshome(
    config_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> Path:
    """Read a .cgs file and resolve the CGSHOME initialise will use."""
    source_path = Path(config_path).resolve()
    document = CgsDocument.from_toml(source_path)
    return resolve_cgshome(document, source_path, output_path=output_path)


def _resolve_project_root(
    document: CgsDocument,
    source_path: Path,
    target_dir: str | Path | None,
    output_path: str | Path | None = None,
) -> Path:
    if target_dir is not None:
        return Path(target_dir).resolve()

    base_dir = Path(output_path).resolve() if output_path is not None else Path.cwd()
    default_root = (base_dir / (document.project_name or source_path.stem)).resolve()
    if not default_root.exists() or (default_root.is_dir() and not any(default_root.iterdir())):
        return default_root
    raise GitSyncError(
        f"Clone destination already exists and is not empty: {default_root}\n"
        f"Choose a different --target-dir or ensure the directory is empty."
    )


def resolve_clone_root(
    config_path: str | Path,
    *,
    target_dir: str | Path | None = None,
    output_path: str | Path | None = None,
) -> Path:
    """Resolve the destination directory a clone_cgs run will clone into."""
    source_path = Path(config_path).resolve()
    document = CgsDocument.from_toml(source_path)
    return _resolve_project_root(document, source_path, target_dir, output_path)


def resolve_bootstrap_root(
    project_name: str,
    *,
    cgs_path: str | Path | None = None,
    clock: ClockProtocol | None = None,
) -> Path:
    """Resolve the isolated CGSHOME a bootstrap run will clone into.

    ``project_name`` always forms the final path segment, regardless of
    the ``.cgs`` document's own ``project_name`` field, so the
    destination is explicit rather than inferred. When *cgs_path* is
    omitted, it defaults to a fresh ``$HOME/.cgs/CGS<timestamp>/``
    directory (``$HOME/.cgs`` is created if missing) so a bootstrapped
    project never lands inside the ComplexGitSync clone itself — running
    ComplexGitSync standalone must never mix its own repo with the
    project state it manages. ``clock`` names that timestamp — real by
    default (:class:`~.universal_clock.SystemClock`); a caller that cares
    about the exact directory name can inject a fixed one instead.
    """
    if not project_name:
        raise ValueError("bootstrap requires a non-empty project_name.")
    if cgs_path is not None:
        cgspath = Path(cgs_path).expanduser().resolve()
    else:
        cgs_root = (Path.home() / ".cgs").expanduser().resolve()
        cgs_root.mkdir(parents=True, exist_ok=True)
        cgspath = cgs_root / f"CGS{(clock or SystemClock()).now():%Y%m%d%H%M%S}"
    return (cgspath / project_name).resolve()

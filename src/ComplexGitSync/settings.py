"""settings — the answers no workspace can give, because no workspace is open yet.

Ring: 1 (reads the environment and the filesystem; no subprocess)
Contract: where ComplexGitSync keeps its workspaces, which one it falls back
    to when nothing else resolves (creating it once, then reusing it), which
    other workspaces exist there, and whether this installation is running
    standalone or nested. Answers all of that before any `.gts` has been
    found, which is what separates it from `master.py`.
Imports: gts_document

Why this module exists
----------------------
Workspace discovery has three inputs — ``--search-dir``, ``$CGSHOME``, the
current directory — and every one of them is supplied by the user. When none
lands on a ``.cgitsync`` directory the tool has no state at all, and "no
state" used to be spelled as an unhandled ``FileNotFoundError``. A tool that
manages state should have a valid empty state: an empty tree is not an
error, it is a project that has not started.

So there is always a fourth answer: a real, empty workspace under the
default root, holding a valid `.gts` with no repositories in it.

**Minted once, then reused.** The first run that needs the default creates
``<root>/CGS<timestamp>/cgitsync`` and records it in the pointer file
``<root>/default``; every later run reads the pointer. Minting a fresh
timestamp per run would leave a user who mistyped a directory three times
owning three empty workspaces, indistinguishable by name from real ones.

**Not master.py.** That module holds the Git identity ComplexGitSync commits
under, persisted per workspace in ``.cgitsync/master.toml``. A per-workspace
file cannot be read before a workspace has been found, which is the whole
problem this module solves.

The public surface
------------------
    CGS_ROOT_ENV        The variable that overrides the default root
    UseCase             STANDALONE / NESTED — observed, never obeyed
    cgs_root            Where workspaces live: $CGSPATH, else $HOME/.cgs
    default_workspace   The fallback workspace, created once and reused
    other_workspaces    Every other workspace under the root — a hint only
    resolve_use_case    Whether this installation runs inside the workspace
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from .gts_document import GtsDocument

#: Overrides the root every workspace is created under. ``CGSPATH`` already
#: exists as a concept — the parent of ``CGSHOME`` — but only as the
#: ``--cgs-path``/``--output-path`` options. Reading it here makes the same
#: idea available to a user who never passes an option, and it stays out of
#: `.cgs`/`.gts` documents, which must remain machine-independent.
CGS_ROOT_ENV = "CGSPATH"

#: The project-name segment of the default workspace: no project chosen yet.
DEFAULT_WORKSPACE_NAME = "cgitsync"

#: Holds the path of the default workspace, so it is minted once.
POINTER_FILE_NAME = "default"

_STATE_DIR_NAME = ".cgitsync"


class UseCase(StrEnum):
    """Which of README §2's two ways of running is in force.

    **Observed, never obeyed.** It is printed so that a user who believes
    they are in one case and is in the other has something to correct them.
    Nothing branches on it: a flag that changes behaviour needs its own
    ticket and its own tests, and this one is worth having now precisely
    because getting it wrong costs nothing.
    """

    STANDALONE = "standalone"
    NESTED = "nested"


def cgs_root(environ: dict[str, str] | None = None) -> Path:
    """The directory every workspace lives under.

    ``$CGSPATH`` when set, otherwise ``$HOME/.cgs`` — the same root
    ``bootstrap`` has always written into. Reads the environment, so a test
    that moves ``HOME`` moves this with it.
    """
    env = environ if environ is not None else os.environ
    override = env.get(CGS_ROOT_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".cgs").expanduser().resolve()


def pointer_file(root: Path | None = None) -> Path:
    """Where the default workspace's path is recorded."""
    return (root if root is not None else cgs_root()) / POINTER_FILE_NAME


def read_default_workspace(root: Path | None = None) -> Path | None:
    """The recorded default workspace, or ``None`` if there is not one yet.

    ``None`` also covers a pointer naming a workspace that has since been
    deleted: a pointer to nothing is not an answer, and the caller mints a
    new workspace rather than resolving to a path that is gone.
    """
    pointer = pointer_file(root)
    try:
        recorded = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not recorded:
        return None
    candidate = Path(recorded).expanduser()
    return candidate if (candidate / _STATE_DIR_NAME).is_dir() else None


def default_workspace(root: Path | None = None, *, create: bool = True) -> Path | None:
    """The workspace to fall back on when nothing else resolves.

    Reuse before create: an existing pointer wins. With *create* false this
    only reports what is already there, which is what a caller that must not
    write to disk — a dry run, a test — needs.
    """
    base = root if root is not None else cgs_root()
    recorded = read_default_workspace(base)
    if recorded is not None:
        return recorded
    if not create:
        return None
    return _mint_default_workspace(base)


def other_workspaces(root: Path | None = None, *, exclude: Path | None = None) -> list[Path]:
    """Every workspace under *root*, except *exclude*, sorted by path.

    A **hint**, never an answer. "The tool never fails" and "the user
    probably meant one of these seven" are different problems, and merging
    them is how a command ends up acting on the wrong tree.
    """
    base = root if root is not None else cgs_root()
    excluded = exclude.resolve() if exclude is not None else None
    found: list[Path] = []
    try:
        candidates = sorted(base.glob(f"*/*/{_STATE_DIR_NAME}"))
    except OSError:
        return []
    for state_dir in candidates:
        workspace = state_dir.parent.resolve()
        if workspace != excluded and workspace not in found:
            found.append(workspace)
    return found


def resolve_use_case(cgshome: Path) -> UseCase:
    """Whether the running installation lives inside *cgshome*.

    **Nested** is the case where the ComplexGitSync being executed sits
    inside the workspace it is managing — the developer tree, where the tool
    manages itself. **Standalone** is every other case, the default
    workspace included, since that workspace contains nothing at all.

    Derived, never stored. Two callers in one process cannot disagree, and
    the answer cannot go stale when a later command resolves a different
    workspace.
    """
    installation = Path(__file__).resolve().parent
    workspace = Path(cgshome).expanduser().resolve()
    if workspace == installation or workspace in installation.parents:
        return UseCase.NESTED
    return UseCase.STANDALONE


def _mint_default_workspace(root: Path) -> Path:
    """Create the default workspace, record it, and return it."""
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / f"CGS{datetime.now(UTC):%Y%m%d%H%M%S}" / DEFAULT_WORKSPACE_NAME
    workspace.mkdir(parents=True, exist_ok=True)
    write_empty_snapshot(workspace)
    pointer_file(root).write_text(f"{workspace}\n", encoding="utf-8")
    return workspace


def write_empty_snapshot(workspace: Path) -> Path:
    """Write a valid `.gts` recording a workspace with no repositories.

    ``UNLOADED`` and ``is_ready = false``, because an empty tree must never
    claim to be ready — a command that trusted `ready=true` over zero
    repositories would report a workspace as good to go when nothing has
    been cloned into it.

    The state directory is named by the document's **content** hash, which
    is what a State's name is supposed to mean, and which a document with no
    repositories computes as well as any other.
    """
    document = GtsDocument(
        {
            "document": {
                "format_version": GtsDocument.CURRENT_SCHEMA_VERSION,
                "generated_at": f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}",
                "command_origin": "default-workspace",
            },
            "project": {
                "name": DEFAULT_WORKSPACE_NAME,
                "root_absolute_path": str(workspace),
            },
            "tree_state": {
                "lifecycle_state": "UNLOADED",
                "is_ready": False,
                "registry_complete": False,
            },
            "repo_state": [],
        }
    )
    digest = document.ensure_snapshot_hash()
    state_dir = workspace / _STATE_DIR_NAME / f"state({digest})_0"
    state_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = state_dir / f"{DEFAULT_WORKSPACE_NAME}.gts"
    document.to_toml(snapshot_path)
    return snapshot_path


__all__ = [
    "CGS_ROOT_ENV",
    "DEFAULT_WORKSPACE_NAME",
    "UseCase",
    "cgs_root",
    "default_workspace",
    "other_workspaces",
    "resolve_use_case",
    "write_empty_snapshot",
]

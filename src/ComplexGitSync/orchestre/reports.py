"""reports — The report values discovery and gitignore sync hand back.

Ring: 3
Contract: The report values discovery and gitignore sync hand back.
Imports: discovery, git_tree, status_render
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..discovery import (
    ImportSubmodulesReport,
)

if TYPE_CHECKING:
    pass
from ..git_tree import (
    ProjectTreeState,
    WorkingGitTree,
)
from ..status_render import (
    StatusCounts,
)


@dataclass(frozen=True, slots=True)
class DiscoveredRepo:
    """One git repository found on disk by :meth:`ComplexGitSyncClient.discover_repos`.

    Attributes
    ----------
    relative_path:
        Location relative to the scanned root — ``"."`` for the root
        repository itself. Taken directly from the filesystem walk, never
        inferred from a repository name.
    absolute_path:
        Resolved location on disk.
    remote_url:
        ``origin``'s URL, or ``None`` when the repository has no ``origin``.
    identifier:
        Canonical ``provider:owner/repository`` shorthand, or ``None`` when
        *remote_url* is missing or could not be parsed into one.
    branch:
        Currently checked-out branch, or ``None`` on a detached HEAD.
    has_cgs:
        ``True`` when the repository already contains its own ``*.cgs``.
        Informational only: the generated draft leaves ``nested_config``
        unset either way, since the default ``"auto"`` already resolves
        cleanly whether or not a nested ``.cgs`` is present.
    parent_relative_path:
        The scanned repository this one sits *inside*, as its own
        *relative_path*; ``None`` when it sits directly under the scanned
        root. A repository holding another one is a parent, not a leaf, and
        the drafted ``.cgs`` is read back that way — see
        ``registry.build_registry_from_cgs_document``.
    """

    relative_path: str
    absolute_path: Path
    remote_url: str | None
    identifier: str | None
    branch: str | None
    has_cgs: bool
    parent_relative_path: str | None = None

@dataclass(frozen=True, slots=True)
class DiscoverReport:
    """Result returned by :meth:`ComplexGitSyncClient.discover_repos`.

    Attributes
    ----------
    root:
        The scanned directory.
    repos:
        Every git repository found, root first, then children ordered by
        ``relative_path``.
    cgs_entries:
        Authoring-form ``repos`` tables for the repositories that could be
        fully resolved — ready to pass to
        :meth:`ComplexGitSyncClient.configure`.
    warnings:
        Human-readable notes about repositories that were found but could
        *not* be turned into a ``.cgs`` entry (no ``origin``, or a remote
        URL that is not a recognised ``provider:owner/repository``). These
        are reported for a human to resolve, never guessed at.
    project_name:
        Name proposed for the draft document, taken from the root
        repository's own name when it is resolvable, else the directory name.
    written_to:
        Where the draft was written — a relative ``output`` resolved inside
        ``root`` — or ``None`` when nothing was written.
    """

    root: Path
    repos: tuple[DiscoveredRepo, ...]
    cgs_entries: tuple[dict, ...]
    warnings: tuple[str, ...]
    project_name: str
    written_to: Path | None = None

@dataclass(frozen=True, slots=True)
class InitFromSubmodulesReport:
    """Result returned by :meth:`ComplexGitSyncClient.init_from_submodules`.

    Attributes
    ----------
    root:
        The checkout the command was pointed at, which is also CGSHOME
        once the tree is initialised.
    discover:
        The :class:`DiscoverReport` from step 1, kept so a caller can show
        the same tree and warnings ``discover`` itself would have printed.
    cgs_path:
        The ``.cgs`` used — the one written from the discovery, or the
        pre-existing file passed in.
    cgs_written:
        ``True`` when *cgs_path* was authored by this call, ``False`` when
        an existing file was reused.
    import_report:
        The :class:`~ComplexGitSync.discovery.ImportSubmodulesReport` from
        the conversion step, or ``None`` on a dry run that never reached it.
    tree:
        The ``READY`` tree ``initialise`` produced, or ``None`` on a dry run.
    dry_run:
        ``True`` when nothing was written, cloned, or converted.
    """

    root: Path
    discover: DiscoverReport
    cgs_path: Path
    cgs_written: bool
    import_report: ImportSubmodulesReport | None = None
    tree: WorkingGitTree | None = None
    dry_run: bool = False

@dataclass(frozen=True, slots=True)
class GitignoreSyncEntry:
    """One repo whose ``.gitignore`` was created or modified by ``sync_gitignore()``."""

    repo_id: str
    name: str
    absolute_path: Path
    added_paths: tuple[str, ...]
    committed: bool = False

@dataclass(frozen=True, slots=True)
class _StatusView:
    """What one ``status`` run observed, before anybody renders it.

    ``is_empty`` is the workspace with no repositories in it — a project
    that has not started. It is carried as its own fact rather than inferred
    from ``rows`` being empty, because the two renderings answer it in very
    different ways and neither should have to guess.
    """

    workspace: Path
    use_case: str
    branch_label: str
    rows: list[tuple[str, str, str, str, str, str, str, str, str]]
    counts: StatusCounts
    tree_state: ProjectTreeState
    incoherent: list[str]
    is_empty: bool
    # Whether the workspace's own memory mount has anything not yet swept
    # into `memory push`. False (not just absent) whenever there is no
    # memory mount at all, so `status()` can print the hint with no further
    # check of its own.
    memory_dirty: bool = False
    # USER or DEV, from `WorkingGitTree.profile`; an empty tree holds nothing private.
    profile: str = "user"


__all__ = [
    "DiscoverReport",
    "DiscoveredRepo",
    "GitignoreSyncEntry",
    "InitFromSubmodulesReport",
]

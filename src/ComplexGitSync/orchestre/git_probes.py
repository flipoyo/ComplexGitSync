"""git_probes — Read-only facts about repositories, asked before the client acts on them.

Ring: 3
Contract: Read-only facts about repositories, asked before the client acts on them.
Imports: errors, git_repo, git_tree, operations, orchestre
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ..errors import (
    GitSyncError,
)

if TYPE_CHECKING:
    pass
from ..git_repo import (
    RepoScope,
    WorkingRepo,
)
from ..git_tree import (
    WorkingGitTree,
)
from ..operations import (
    RepoOutcome,
)


class GitProbes:
    """Read-only facts about repositories, asked before the client acts on them."""

    @staticmethod
    def as_write_outcomes(result: object) -> tuple[RepoOutcome, ...]:
        """Normalise a tree-write result into the outcome tuple the CLI reports.

        Every real operation returns one. A caller-supplied stand-in for the
        ``GitTree.git`` command facade — a test double, an embedder's own
        implementation — may return nothing, and a missing report must not
        become a crash *after* the write already happened.
        """
        if isinstance(result, tuple | list):
            return tuple(result)
        return ()

    @staticmethod
    def local_status_from_porcelain(status_lines: list[str]) -> str:
        if not status_lines:
            return "clean"
        staged = any(line[:2] != "??" and line[0] != " " for line in status_lines)
        unstaged = any(line[:2] == "??" or (len(line) > 1 and line[1] != " ") for line in status_lines)
        if staged and unstaged:
            return "staged+dirty"
        if staged:
            return "staged"
        return "dirty"

    @staticmethod
    def short_sha(value: str | None) -> str:
        return value[:8] if value else "-"

    @staticmethod
    def unmanaged_gitlink_paths(
        registry: WorkingGitTree,
        entry: WorkingRepo,
        git_runner: Any,
    ) -> set[Path]:
        try:
            gitlinks = git_runner.tracked_gitlink_paths(entry.absolute_path)
        except (AttributeError, GitSyncError):
            return set()

        managed_children: set[Path] = set()
        for child in registry.children_of(entry.repo_id):
            try:
                managed_children.add(child.absolute_path.relative_to(entry.absolute_path))
            except ValueError:
                continue
        return {path for path in gitlinks if path not in managed_children}

    @staticmethod
    def identifier_for_url(url: str) -> str:
        """:meth:`url_to_repo_identifier`, looked up through the package.

        ``orchestre._url_to_repo_identifier`` is the name callers patch, so
        every caller in this package goes through the attribute at call time
        rather than binding the function.
        """
        from .. import orchestre

        return orchestre._url_to_repo_identifier(url)

    @staticmethod
    def url_to_repo_identifier(url: str) -> str:
        """Convert a git remote URL to a ComplexGitSync ``provider:owner/repo`` identifier.

        Supports HTTPS and SSH URL forms for GitHub and GitLab.  Custom-host
        URLs are passed through as-is (using the bare hostname as the provider
        token), which will fail :func:`~ComplexGitSync.cgs_format.parse_repo_id`
        validation downstream if the provider is not registered — the caller is
        responsible for handling that case.

        Examples
        --------
        >>> _url_to_repo_identifier("https://github.com/owner/repo.git")
        'github:owner/repo'
        >>> _url_to_repo_identifier("git@gitlab.com:group/sub/repo.git")
        'gitlab:group/sub/repo'
        """
        _PROVIDER_MAP = {
            "github.com": "github",
            "gitlab.com": "gitlab",
            "codeberg.org": "codeberg",
        }

        url = url.strip()
        if url.endswith(".git"):
            url = url[:-4]

        # SSH format: git@hostname:path/to/repo
        ssh_match = re.match(r"^git@([^:]+):(.+)$", url)
        if ssh_match:
            hostname = ssh_match.group(1).lower()
            path = ssh_match.group(2).strip("/")
            provider = _PROVIDER_MAP.get(hostname, hostname)
            return f"{provider}:{path}"

        # HTTPS/HTTP format: https://hostname/path/to/repo
        parsed = urlsplit(url)
        if parsed.scheme in ("https", "http") and parsed.netloc:
            hostname = parsed.netloc.lower()
            path = parsed.path.strip("/")
            provider = _PROVIDER_MAP.get(hostname, hostname)
            return f"{provider}:{path}"

        # Unknown format — return stripped URL and let downstream validation fail
        return url

    @staticmethod
    def blocking_worktree_dirt(status_lines: Sequence[str]) -> list[str]:
        """Filter ``git status --porcelain`` lines down to the ones that block a conversion.

        Everything blocks except the repository's own ``.gitignore``. That one
        file is written by ComplexGitSync itself, in every repository that
        holds a child (:func:`~ComplexGitSync.git_tree.sync_gitignore`), so it
        is routinely dirty in exactly the tree ``submodules import`` is asked
        to convert — ``initialise`` writes it moments before, and refusing over
        it would deadlock the one working order (see
        ``.agent/.local/.dev/DevTickets/archive/20260903_InitFromSubmodules_DevPlanTicket.md``). Exempting it is
        safe: the conversion only runs ``git rm --cached`` in the *holding*
        repository, which never touches the child's working tree at all. The
        check exists to protect real, unsaved work in a child, and it still
        catches every bit of that.
        """
        blocking: list[str] = []
        for line in status_lines:
            # "XY path", with the rename form "XY old -> new"; only the plain
            # single-path form can name a top-level .gitignore.
            path = line[3:].strip() if len(line) > 3 else ""
            if path == ".gitignore":
                continue
            blocking.append(line)
        return blocking

    @staticmethod
    def walk_git_repositories(
        root: Path, *, max_depth: int | None = None
    ) -> tuple[list[Path], bool]:
        """Return every directory under *root* that holds a ``.git``, root first.

        *max_depth* bounds the walk when given; ``None`` (the default) means
        unbounded — the walk runs until there is nothing left to look into.
        Returns the repositories found and whether a given *max_depth* stopped
        the walk early: reached while directories were still left to look
        into, meaning there may be more repositories below that were never
        seen. Unbounded, this is always ``False``.

        Used by :meth:`ComplexGitSyncClient.discover_repos`. Two rules matter:

        * A ``.git`` **file** counts, not just a directory. Git stores a
          submodule's real git directory under ``<parent>/.git/modules/<name>``
          and leaves a ``.git`` *file* in the working copy pointing at it.
        * ``.git`` is never descended into, so those ``modules/<name>``
          directories cannot be mistaken for a second copy of a repository that
          was already reported from its working-tree location.

        Depth is counted from *root* (itself depth 0). Nested repositories are
        reported in addition to their parent, not instead of it: a parent and
        its children are exactly the tree ComplexGitSync manages.

        Iterative, with an explicit stack rather than recursion: an unbounded
        walk has no ceiling on how deep a real filesystem can go, and Python's
        call stack does (~1000 frames) — a tree deeper than that would raise
        ``RecursionError`` if this called itself once per level.
        """
        found: list[Path] = []
        stopped_early = False

        def _subdirectories(directory: Path) -> list[Path]:
            try:
                children = sorted(directory.iterdir())
            except (PermissionError, OSError):
                return []
            return [
                child
                for child in children
                if child.is_dir() and not child.is_symlink() and child.name != ".git"
            ]

        # A stack visits depth-first, matching the previous recursive walk's
        # root-first, depth-first order; pushing each directory's children in
        # reverse means popping them back off in the original sorted order.
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            directory, depth = stack.pop()
            if (directory / ".git").exists():
                found.append(directory)
            children = _subdirectories(directory)
            if max_depth is not None and depth >= max_depth:
                stopped_early = stopped_early or bool(children)
                continue
            stack.extend((child, depth + 1) for child in reversed(children))

        return found, stopped_early

    @staticmethod
    def as_posix_or_none(path: Path | None) -> str | None:
        return None if path is None else path.as_posix()

    @staticmethod
    def scope_for(
        tree: WorkingGitTree,
        *,
        private: bool,
        command: str,
        default: RepoScope = RepoScope.ALL,
    ) -> RepoScope:
        """``--private`` narrows a command to the writable configuration repos.

        Without it, a command keeps whatever *default* it has always had — for
        the tree-wide readers and movers that is every repository, because a
        private/local one already resolves its own branch name. ``--private``
        is how a user acts on the configuration repositories alone, and it is
        refused rather than silently empty when the tree has none.
        """
        if not private:
            return default
        return GitProbes.resolve_command_scope(tree, private=True, command=command)

    @staticmethod
    def resolve_command_scope(
        tree: WorkingGitTree,
        *,
        private: bool,
        command: str,
        all_writable: bool = False,
    ) -> RepoScope:
        """Pick the scope a write command runs at, and refuse an empty one.

        Three forms, and the bare one has not moved. Without a flag a write
        command touches only the repositories this project owns. With
        ``--private``, only the **writable** configuration repos — the ones the
        ``.cgs`` declares ``private = true, writable = true``. With ``--all``,
        both in a single pass, sharing one commit message the way
        ``freeze-release`` already does.

        ``--private`` raises rather than silently doing nothing when no
        repository qualifies, since a command that quietly touched nothing is
        exactly the failure this whole mechanism exists to prevent. ``--all``
        does **not**: most trees declare no writable configuration repository at
        all, and "do the project half, there was no other half" is a complete
        and correct answer there rather than a mistake to report. Same rule,
        two callers, two right answers — see the CLI's scope note, which says
        when the private half was empty.

        ``--all`` maps to :attr:`RepoScope.WRITABLE`, not :attr:`RepoScope.ALL`.
        The enum's ``ALL`` is wider: it includes the read-only configuration
        repositories, which no form of this flag ever writes to.
        """
        if private and all_writable:
            raise GitSyncError(
                f"{command}: --all and --private are mutually exclusive. --all already"
                f" reaches the writable configuration repositories alongside this"
                f" project's own; --private reaches them instead of it."
            )
        if all_writable:
            return RepoScope.WRITABLE
        if not private:
            return RepoScope.PROJECT
        if any(RepoScope.PRIVATE.includes(repo) for repo in tree.values()):
            return RepoScope.PRIVATE
        read_only = sorted(repo.name for repo in tree.values() if repo.effective_private)
        detail = (
            f" The private repositories in this tree are read-only: {', '.join(read_only)}."
            if read_only
            else " This tree declares no private repositories at all."
        )
        raise GitSyncError(
            f"{command} --private: no writable configuration repository in this tree.{detail}"
            f" A private repository is read-only unless its .cgs entry also says"
            f" writable = true."
        )

    @staticmethod
    def is_dot_named_mount(relative_path: str) -> bool:
        """True when any segment of *relative_path* is a dot-named directory.

        Used only to pick ``discover``'s default for ``private``. Being dot-named
        is a habit, not the rule — ``private`` means "shared with other projects",
        and ``docs/DocSpec`` is private without being hidden at any level. The
        habit is reliable enough to make a *default* out of, which the author
        then sees in the drafted ``.cgs`` and can delete.
        """
        return any(segment.startswith(".") for segment in relative_path.split("/") if segment != ".")


__all__ = [
    "GitProbes",
]

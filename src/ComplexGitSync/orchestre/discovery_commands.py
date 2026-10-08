"""discovery_commands — Find repositories and submodules and turn them into specs.

Ring: 3
Contract: Find repositories and submodules and turn them into specs.
Imports: cgs_format, client, discovery, errors, git_probes, git_tree, git_tree_branch, memory_facts, provider, reports
"""

from __future__ import annotations

import configparser
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..cgs_format import CgsDocument, parse_repo_id
from ..discovery import (
    ImportSubmodulesReport,
    SubmoduleEntry,
    _parse_gitmodules,
    discover_nested_configs,
)
from ..errors import (
    GitSyncError,
)

if TYPE_CHECKING:
    pass
from ..git_tree import (
    _update_gitignore_file,
    innermost_containing_path,
)
from ..git_tree_branch import GitTreeBranches
from ..provider import (
    creation_plan,
    looks_like_already_exists,
    looks_like_not_signed_in,
)
from .git_probes import GitProbes
from .memory_facts import MemoryFacts
from .reports import (
    DiscoveredRepo,
    DiscoverReport,
    InitFromSubmodulesReport,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient


class DiscoveryCommands:
    """Find repositories and submodules and turn them into specs.

    A collaborator of :class:`~ComplexGitSync.orchestre.client.ComplexGitSyncClient`,
    which keeps the state and exposes every method here unchanged. Everything
    that is not this class's own private helper is reached through the
    client, so a caller that patches a client method is still obeyed.
    """

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def import_submodules(
        self,
        repo_root: str | Path,
        *,
        apply: bool = False,
        recursive: bool = False,
    ) -> ImportSubmodulesReport:
        """Report or convert git submodules in *repo_root* to plain nested clones.

        With *recursive* (default ``False``, matching ``git submodule
        update``'s own flag name and meaning): also converts any submodule
        that itself has its own checked-out ``.gitmodules``, at any depth,
        root first — the opposite of this codebase's usual leaf-first
        mutation order, and deliberately so: converting a submodule stages
        changes in its own working tree, and if a deeper level converted
        first, the parent level's own preflight (is the submodule's
        working tree clean?) would then reject its own conversion over
        dirt the deeper conversion itself just made. Converting parent
        first has no such problem — ``git rm --cached <path>`` only
        touches the parent's own index, never the submodule's working
        tree — see :meth:`_gitmodules_levels_root_first` for the full
        reasoning. A submodule path that was never checked out (``git
        submodule update --init`` not run for it) has no ``.git`` to read
        a nested ``.gitmodules`` from and is invisible either way — the
        same ceiling :meth:`discover_repos` already documents for itself.
        Without *recursive*, behavior is unchanged: exactly
        ``<repo_root>/.gitmodules``, one level.

        Parses ``<repo_root>/.gitmodules`` and, for each declared submodule:

        * **Dry-run** (``apply=False``, the default): returns an
          :class:`ImportSubmodulesReport` describing what would change —
          submodule names, paths, URLs, branches — without touching the
          repository.
        * **Apply** (``apply=True``): for each submodule in turn —

          1. Verifies the working tree at ``<repo_root>/<path>`` is clean
             (``git status --porcelain`` empty) and raises
             :exc:`~ComplexGitSync.errors.GitSyncError` if it is not —
             the same check the preflight machinery in ``operations/``
             performs for every mutation operation.
          2. Runs ``git rm --cached <path>`` in *repo_root*, dropping the
             gitlink from the index while preserving the child's working
             tree and ``.git`` directory (no re-clone, no local history
             lost).
          3. Removes the submodule's stanza from ``.gitmodules`` (deletes
             the file entirely when all stanzas are removed), then stages
             the updated file.
          4. Calls the existing :func:`~ComplexGitSync.git_tree._update_gitignore_file`
             helper (``git_tree.py``) to append ``<path>`` to
             ``<repo_root>/.gitignore`` — the same step the ``.gitignore``
             lifecycle sync performs for every parent-child relationship.

        This is the whole job: turning gitlinks into plain clones on disk.
        It does not author a ``.cgs`` — that would need the root's own
        identity too, which ``.gitmodules`` never records, and a project
        checkout worth importing already has one (:meth:`discover_repos`)
        or is worth writing by hand. Run :meth:`discover_repos` on the same
        checkout, before or after applying, to get one.

        Parameters
        ----------
        repo_root:
            Absolute (or resolvable) path to the local git repository that
            contains a ``.gitmodules`` file.
        apply:
            When ``False`` (default) the method is a pure read: it reports
            what would change without modifying anything. Set to ``True`` to
            perform the conversion.

        Returns
        -------
        ImportSubmodulesReport
            Always returned, whether or not *apply* was set. With
            *recursive*, one flat report combining every level — the same
            shape :meth:`discover_repos` already uses for its own report,
            regardless of how deep a repository was found.
        """
        root = Path(repo_root).resolve()
        if not recursive:
            return self._import_submodules_one_level(root, apply=apply)

        submodules: list[SubmoduleEntry] = []
        converted: list[str] = []
        applied_any = False
        for level_root in self._gitmodules_levels_root_first(root):
            level_report = self._import_submodules_one_level(level_root, apply=apply)
            submodules.extend(level_report.submodules)
            converted.extend(level_report.converted)
            applied_any = applied_any or level_report.applied
        return ImportSubmodulesReport(
            submodules=tuple(submodules),
            applied=applied_any,
            converted=tuple(converted),
            scan_root=root,
        )

    def _gitmodules_levels_root_first(
        self, level_root: Path, *, _visited: set[Path] | None = None
    ) -> list[Path]:
        """Every directory under *level_root* (itself included) with a
        checked-out ``.gitmodules``, root first.

        Root first, not leaf first, despite this codebase's usual
        leaf-first mutation order (``push``/``commit``/…): converting a
        submodule stages changes in *its own* working tree (the removed
        ``.gitmodules`` stanza, the new ``.gitignore`` line) — if a deeper
        level converted first, the *parent* level's own preflight (``is
        <submodule path> clean?``) would then see that staged dirt and
        reject its own conversion. Converting parent-first never has this
        problem: ``git rm --cached <path>`` only touches the parent's own
        index, never the submodule's working tree, so a still-unconverted
        child submodule is exactly as clean immediately after its parent
        converts as it was before.

        A submodule path with no ``.git`` (never ``git submodule update
        --init``'d) has nothing to recurse into and is skipped — matching
        :meth:`discover_repos`'s own "only what is checked out" ceiling.
        """
        visited = _visited if _visited is not None else set()
        resolved = level_root.resolve()
        if resolved in visited or not (resolved / ".gitmodules").is_file():
            return []
        visited.add(resolved)

        levels: list[Path] = [resolved]
        content = (resolved / ".gitmodules").read_text(encoding="utf-8")
        for sub in _parse_gitmodules(content):
            child_path = resolved / sub.path
            if not (child_path / ".git").exists():
                continue
            levels.extend(self._gitmodules_levels_root_first(child_path, _visited=visited))
        return levels

    # Pre-existing complexity debt from before C90 was enabled (P6,
    # .agent/.local/.dev/DevTickets/archive/20260828_Isolation_DevPlanTicket.md) — flagged, not fixed
    # under this ticket, since a real refactor of the submodule-conversion
    # flow risks behaviour change under time pressure. New code is enforced
    # at 12.
    def _import_submodules_one_level(  # noqa: C901
        self, root: Path, *, apply: bool
    ) -> ImportSubmodulesReport:
        """The single-level conversion :meth:`import_submodules` always did —
        the unit it composes over per level when *recursive* is set."""
        gitmodules_path = root / ".gitmodules"

        if not gitmodules_path.is_file():
            self.client._log_event(
                "import_submodules_no_gitmodules",
                repo_root=str(root),
                apply=apply,
            )
            return ImportSubmodulesReport(
                submodules=(),
                applied=False,
                converted=(),
                scan_root=root,
            )

        content = gitmodules_path.read_text(encoding="utf-8")
        # Record which repository declared each submodule. Its ``path`` is
        # written relative to that repository, so without this the entry
        # cannot be placed once several levels are reported together.
        submodules = tuple(
            replace(entry, owner_root=root) for entry in _parse_gitmodules(content)
        )

        self.client._log_event(
            "import_submodules_start",
            repo_root=str(root),
            submodule_count=len(submodules),
            apply=apply,
        )

        if not submodules:
            return ImportSubmodulesReport(
                submodules=(),
                applied=False,
                converted=(),
                scan_root=root,
            )

        if not apply:
            return ImportSubmodulesReport(
                submodules=submodules,
                applied=False,
                converted=(),
                scan_root=root,
            )

        # --- apply=True: perform the conversion ---

        # 1. Preflight: every child working tree must be clean.
        for sub in submodules:
            child_path = root / sub.path
            if not child_path.exists():
                continue
            dirty_lines = GitProbes.blocking_worktree_dirt(self.client.git_runner.status_porcelain(child_path))
            if dirty_lines:
                raise GitSyncError(
                    f"submodules import preflight failed: submodule '{sub.name}' "
                    f"at '{sub.path}' has uncommitted changes — stage or stash them first.\n"
                    + "\n".join(dirty_lines)
                )

        # 2. Per submodule: git rm --cached <path>, update .gitmodules, update .gitignore
        converted: list[str] = []
        for sub in submodules:
            self.client.git_runner.rm_cached(root, sub.path)
            converted.append(sub.name)
            _update_gitignore_file(root, [sub.path])
            self.client._log_event(
                "import_submodules_converted",
                repo_root=str(root),
                submodule_name=sub.name,
                submodule_path=sub.path,
                submodule_url=sub.url,
                submodule_branch=sub.branch,
            )

        # 3. Rewrite / remove .gitmodules — rebuild from remaining (unconverted)
        #    stanzas. Since we convert ALL submodules here, the file is removed.
        remaining_entries = [
            s for s in _parse_gitmodules(content) if s.name not in converted
        ]
        if remaining_entries:
            # Write back a .gitmodules with only the unconverted stanzas
            cfg = configparser.RawConfigParser()
            for sub in remaining_entries:
                section = f'submodule "{sub.name}"'
                cfg.add_section(section)
                cfg.set(section, "path", sub.path)
                cfg.set(section, "url", sub.url)
                # Git's own .gitmodules default, not git_branch.DEFAULT_BRANCH
                # — this omits the key only when it would say what Git already
                # assumes, and must not move when our .cgs default moves.
                if sub.branch != "main":
                    cfg.set(section, "branch", sub.branch)
            import io
            buf = io.StringIO()
            cfg.write(buf)
            gitmodules_path.write_text(buf.getvalue(), encoding="utf-8")
            self.client.git_runner.stage_path(root, ".gitmodules")
        else:
            # All submodules converted — remove .gitmodules entirely
            self.client.git_runner._run("rm", "--cached", ".gitmodules", cwd=root)
            gitmodules_path.unlink(missing_ok=True)

        return ImportSubmodulesReport(
            submodules=submodules,
            applied=True,
            converted=tuple(converted),
            scan_root=root,
        )

    def init_from_submodules(
        self,
        repo_root: str | Path,
        *,
        cgs_path: str | Path | None = None,
        max_depth: int | None = None,
        dry_run: bool = False,
        force: bool = False,
        force_access_protocol: str | None = None,
    ) -> InitFromSubmodulesReport:
        """Adopt a submodule-based checkout in one call: discover, initialise, convert.

        This is the whole of Tutorial 4's steps 3-5, in the one order that
        works. Point it at a checkout that was cloned and ``git submodule
        update --init --recursive``'d by hand, and it produces a ``READY``
        ComplexGitSync tree whose submodules have become plain nested
        clones, staged but not committed.

        The sequence, each step delegating to the method that already owns
        it:

        1. :meth:`discover_repos` on *repo_root* — a pure read that drafts
           the ``.cgs`` from what is checked out.
        2. Write that draft to ``<repo_root>/<project>.cgs``, unless
           *cgs_path* names a file that already exists, which is used
           as-is instead.
        3. :meth:`initialise_cgs` with ``output_path = repo_root.parent``,
           so CGSHOME resolves to *repo_root* itself.
        4. :meth:`import_submodules` with ``apply=True, recursive=True``
           on CGSHOME.

        **Why the conversion comes last.** :meth:`initialise_cgs` adopts
        the root in place but deletes and re-clones every *other*
        repository straight from its remote, and those remotes still
        declare submodules. Converting first would therefore be undone for
        every non-root repository the moment step 3 ran. Nor can a second
        conversion pass repair that: ``import_submodules(recursive=True)``
        walks the submodule graph declared by the root's own
        ``.gitmodules``, so once the root is converted, no deeper level is
        reachable any more.

        Committing is deliberately left to the caller. The conversion
        touches every repository holding a submodule, and some of them
        belong to other people — ``branch``/``checkout``/``add``/``commit``
        stay explicit, separate steps.

        Parameters
        ----------
        repo_root:
            The checkout to adopt. Its directory name must match the
            project name the discovery derives, since that is what makes
            CGSHOME resolve back to this same directory.
        cgs_path:
            Use this ``.cgs`` instead of writing one. When it does not
            exist, the draft is written there rather than to the default
            location.
        max_depth:
            Passed to :meth:`discover_repos`. ``None`` (the default) scans
            with no depth bound.
        dry_run:
            Report the plan — the discovery and the submodules that would
            be converted — without writing, cloning, or converting
            anything.
        force:
            Proceed even when *repo_root* has no ``.gitmodules`` of its
            own. Without it, that case is refused: there is nothing to
            convert, while step 3 would still delete and re-clone every
            non-root repository, destroying any uncommitted work in them.
        force_access_protocol:
            ``"ssh"`` or ``"https"``, forwarded to :meth:`initialise_cgs`
            for the clone step. The discovery step reads each repository's
            configured remote as it is and is unaffected.

        Returns
        -------
        InitFromSubmodulesReport
            The discovery, the ``.cgs`` used, the conversion report, and
            the resulting tree.
        """
        root = Path(repo_root).resolve()
        if not root.is_dir():
            raise GitSyncError(f"submodules init: not a directory: {root}")

        report = self.client.discover_repos(root, max_depth=max_depth)
        target_cgs = (
            Path(cgs_path).resolve()
            if cgs_path is not None
            else root / f"{report.project_name}.cgs"
        )
        reuse_existing = cgs_path is not None and target_cgs.is_file()
        # A supplied .cgs is the authority on the project name; the
        # discovery is then only a report of what is on disk.
        project_name = (
            CgsDocument.from_toml(target_cgs).project_name or root.name
            if reuse_existing
            else report.project_name
        )
        self.client._assert_adoptable(
            root,
            report,
            project_name=project_name,
            reuse_existing=reuse_existing,
            force=force,
        )

        self.client._log_event(
            "init_from_submodules_start",
            repo_root=str(root),
            project_name=report.project_name,
            repo_count=len(report.repos),
            cgs_path=str(target_cgs),
            reuse_existing_cgs=reuse_existing,
            dry_run=dry_run,
        )

        if dry_run:
            return InitFromSubmodulesReport(
                root=root,
                discover=report,
                cgs_path=target_cgs,
                cgs_written=False,
                import_report=self.client.import_submodules(root, apply=False, recursive=True),
                dry_run=True,
            )

        if not reuse_existing:
            self.client.configure(report.project_name, list(report.cgs_entries), output_path=target_cgs)

        tree = self.client.initialise_cgs(
            target_cgs,
            output_path=root.parent,
            force_access_protocol=force_access_protocol,
        )
        try:
            import_report = self.client.import_submodules(root, apply=True, recursive=True)
        except GitSyncError as exc:
            raise GitSyncError(
                f"{exc}\n"
                f"hint: the tree at {root} is initialised but its submodules are "
                f"not converted yet — every repository is exactly as its remote "
                f"declares it. Fix the cause above, then finish the job with "
                f"'cgitsync submodules import {root} --recursive'."
            ) from exc

        self.client._log_event(
            "init_from_submodules_done",
            repo_root=str(root),
            cgs_path=str(target_cgs),
            converted_count=len(import_report.converted),
        )
        return InitFromSubmodulesReport(
            root=root,
            discover=report,
            cgs_path=target_cgs,
            cgs_written=not reuse_existing,
            import_report=import_report,
            tree=tree,
            dry_run=False,
        )

    def _assert_adoptable(
        self,
        root: Path,
        report: DiscoverReport,
        *,
        project_name: str,
        reuse_existing: bool,
        force: bool,
    ) -> None:
        """Refuse an adoption that cannot work, before anything is written.

        Three ways :meth:`init_from_submodules` would otherwise fail
        halfway through, each cheaper to detect here:

        * The discovery resolved no repository at all, so there is no
          ``.cgs`` to write. Skipped when the caller supplied one
          (*reuse_existing*): the discovery is then only a report.
        * *project_name* and the directory name disagree, so ``initialise``
          would resolve CGSHOME to a sibling directory that does not exist
          — Tutorial 4's easiest mistake.
        * *root* has no ``.gitmodules``, meaning either an already-adopted
          tree or one that never used submodules. There would be nothing
          to convert, while the clone step would still re-clone (and so
          discard) every non-root repository. ``force`` overrides this one.
        """
        if not reuse_existing and not report.cgs_entries:
            raise GitSyncError(
                f"submodules init: no resolvable git repository found under "
                f"{root} — nothing to adopt."
            )
        if project_name != root.name:
            raise GitSyncError(
                f"submodules init: the project name is '{project_name}' but the "
                f"directory is named '{root.name}'. CGSHOME is resolved as "
                f"<parent>/<project name>, so these must match. Rename the directory "
                f"to '{root.parent / project_name}' and run this again."
            )
        if force or (root / ".gitmodules").is_file():
            return
        raise GitSyncError(
            f"submodules init: no .gitmodules in {root}, so there is nothing to "
            f"convert — this tree looks already adopted, or never used submodules. "
            f"Running anyway would still delete and re-clone every non-root "
            f"repository from its remote, losing any uncommitted work in them. "
            f"Pass --force if that is really what you want, or use 'initialise' for "
            f"a tree that already has a .cgs."
        )

    # Pre-existing complexity debt from before C90 was enabled (P6,
    # .agent/.local/.dev/DevTickets/archive/20260828_Isolation_DevPlanTicket.md) — flagged, not fixed
    # under this ticket, since a real refactor of the filesystem-walking
    # discovery flow risks behaviour change under time pressure. New code
    # is enforced at 12.
    def discover_repos(  # noqa: C901
        self,
        root_dir: str | Path | None = None,
        *,
        max_depth: int | None = None,
        output: str | Path | None = None,
    ) -> DiscoverReport:
        """Scan *root_dir* for git repositories and draft a ``.cgs`` from what is there.

        This is the entry point for adopting a project that exists on disk
        but has no ``.cgs`` describing it yet. It is a **pure read** of the
        filesystem and of each repository's git config: nothing is cloned,
        fetched, staged, or modified, and no network call is made.

        The walk descends from *root_dir* with no depth limit by default,
        treating every directory that contains a ``.git`` entry as a
        repository. Pass *max_depth* to bound it. It never descends *into*
        a ``.git`` directory — for a submodule the real git directory
        lives at ``<parent>/.git/modules/<name>`` while the child's own
        ``.git`` is a file, so walking into it would report the same
        repository twice.

        For each repository found, ``origin``'s URL is read and converted to
        the canonical ``provider:owner/repository`` shorthand through the
        *existing* :func:`~ComplexGitSync.cgs_format.parse_repo_id` grammar —
        this method adds no second parser. ``relative_path`` comes straight
        from the walk rather than from a repository name, so a child mounted
        at ``external/Thing`` is recorded there and not at ``Thing``.

        Repositories with no ``origin``, or whose remote URL does not resolve
        to a registered provider, are reported in ``warnings`` and left out
        of ``cgs_entries``. They are never guessed at: a draft that silently
        invented an address would be worse than one that says what it could
        not determine.

        Only what is **checked out at scan time** can be found. In particular
        a repository cloned without ``--recurse-submodules`` leaves its
        submodule paths as empty directories, and those are correctly not
        reported here; recovering them from git metadata instead is
        :meth:`import_submodules`' job.

        Parameters
        ----------
        root_dir:
            Directory to scan. Defaults to the current working directory.
        max_depth:
            Maximum directory depth to descend below *root_dir*. The root
            itself is depth 0. ``None`` (the default) scans with no bound.
        output:
            Optional path to write the drafted ``.cgs`` to; a relative path
            is inside *root_dir*, which the draft describes, not the current
            directory. When omitted, the draft is only returned — matching the "report first, write
            only when asked" posture of ``--commit-gitignore`` and
            ``submodules import``.

        Returns
        -------
        DiscoverReport
            The repositories found, the draft ``.cgs`` entries, and any
            warnings.
        """
        root = Path(root_dir).resolve() if root_dir is not None else Path.cwd().resolve()
        if not root.is_dir():
            raise GitSyncError(f"discover: not a directory: {root}")

        repos: list[DiscoveredRepo] = []
        warnings: list[str] = []

        found_paths, stopped_early = GitProbes.walk_git_repositories(root, max_depth=max_depth)
        if stopped_early:
            warnings.append(
                f"the scan stopped at --max-depth {max_depth} with directories left "
                f"to look into; any repository deeper than that was not seen. "
                f"Re-run with a larger --max-depth to be sure."
            )

        for repo_path in found_paths:
            relative = repo_path.relative_to(root).as_posix() if repo_path != root else "."
            remote_url = self.client.git_runner.remote_get_url(repo_path)
            try:
                branch = self.client.git_runner.current_branch(repo_path)
            except GitSyncError:
                # A repository with no commits yet has no resolvable HEAD.
                # That is a perfectly ordinary thing to find on disk, so it
                # must not abort the scan — report it as branch-less, the
                # same as a detached HEAD.
                branch = None
            has_cgs = any(repo_path.glob("*.cgs"))

            identifier: str | None = None
            if remote_url is None:
                # F5: naming what to add, not only what was missing — a
                # remote-less repository used to vanish from the draft with
                # one warning among the report's output, and the user's
                # next move (hand-write the entry) was the one thing the
                # warning did not say how to do.
                branch_hint = f', default_branch = "{branch}"' if branch else ""
                warnings.append(
                    f"{relative}: no 'origin' remote — cannot determine an address; "
                    f"add one, or add this entry by hand: "
                    f'{{ repository = "provider:owner/{Path(relative).name}", '
                    f'relative_path = "{relative}"{branch_hint} }}.'
                )
            else:
                candidate = GitProbes.identifier_for_url(remote_url)
                try:
                    parse_repo_id(candidate)
                except ValueError as exc:
                    warnings.append(
                        f"{relative}: remote {remote_url!r} does not map to a known "
                        f"provider:owner/repository ({exc}); add this repository to "
                        f"the .cgs by hand, or declare a custom provider for it."
                    )
                else:
                    identifier = candidate

            repos.append(
                DiscoveredRepo(
                    relative_path=relative,
                    absolute_path=repo_path,
                    remote_url=remote_url,
                    identifier=identifier,
                    branch=branch,
                    has_cgs=has_cgs,
                    # The walk is root-first, so anything holding this
                    # repository has already been seen.
                    parent_relative_path=GitProbes.as_posix_or_none(
                        innermost_containing_path((found.relative_path for found in repos), relative)
                    ),
                )
            )

        root_repo = next((r for r in repos if r.relative_path == "."), None)
        project_name = root.name
        if root_repo is not None and root_repo.identifier is not None:
            project_name = root_repo.identifier.rsplit("/", 1)[-1]
        # DiscoverRoundTrip D1/F4: the tree's own default branch is the
        # root's branch, drafted as `project.default_branch` — not only as
        # a `fallback_branch`, which `_select_clone_ref` only ever
        # consults when the *target* branch is absent from the remote.
        # Without this, a tree scanned entirely on `branch1` drafted a
        # `.cgs` that targeted `main` everywhere the remote happened to
        # have it, and the draft did not reproduce the tree it scanned.
        root_branch = root_repo.branch if root_repo is not None else None
        project: str | dict[str, str] = (
            {"name": project_name, "default_branch": root_branch} if root_branch else project_name
        )

        cgs_entries: list[dict] = []
        for repo in repos:
            if repo.identifier is None:
                continue
            entry: dict = {
                "repository": repo.identifier,
                "relative_path": repo.relative_path,
            }
            if repo.branch:
                entry["fallback_branch"] = repo.branch
                # A repository scanned on a different branch than the
                # tree's own default needs its own explicit target — left
                # unset when it matches, so the common case (everything on
                # one branch) drafts one `project.default_branch` rather
                # than repeating the same value on every entry.
                if repo.branch != root_branch:
                    entry["default_branch"] = repo.branch
            # A repository with no .cgs of its own resolves cleanly on the
            # default "auto" (zero matches -> RESOLVED), so it is left
            # unset here rather than private to "disabled".
            if GitProbes.is_dot_named_mount(repo.relative_path):
                # A dot-named mount (.agentSpec, .localSpec, .claude) is
                # almost always a config repository shared with other
                # projects, and "private" means exactly that: shared, so
                # tree-wide branch moves must leave it alone. Drafting it
                # private states the convention as a default the author can
                # see and delete, rather than hiding these repositories from
                # the scan — they are still found, still listed, and still
                # written out.
                entry["private"] = True
            cgs_entries.append(entry)

        written_to = (root / Path(output).expanduser()).resolve() if output is not None else None
        self.client._log_event(
            "discover_repos",
            root=str(root),
            repo_count=len(repos),
            entry_count=len(cgs_entries),
            warning_count=len(warnings),
            max_depth=max_depth,
            output=str(written_to) if written_to is not None else None,
        )

        if written_to is not None:
            if not cgs_entries:
                raise GitSyncError(
                    f"discover: no resolvable git repository found under {root} — "
                    f"nothing to write."
                )
            self.client.configure(project, cgs_entries, output_path=written_to)

        return DiscoverReport(
            root=root,
            repos=tuple(repos),
            cgs_entries=tuple(cgs_entries),
            warnings=tuple(warnings),
            project_name=project_name,
            written_to=written_to,
        )

    def repo_create(
        self,
        identifier: str,
        *,
        private: bool = True,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a repository on its provider, using the provider's own tool.

        *identifier* is the ordinary `.cgs` spelling —
        ``github:flipoyo/.memory``, ``gitlab:some/group/project`` — parsed by
        `parse_repo_id` and by nothing else, so the owner or the group comes
        from the same place here as in every spec.

        **No credential is read, stored or sent by this project.** It runs
        `gh`, `glab` or `tea`, which the user has already signed in to. When
        that tool is missing or signed out, this returns the command to run
        rather than pretending it could have done it.

        ``created`` says what happened, in one word:

        - ``created`` — the repository did not exist and now does.
        - ``exists`` — it was already there. That is the normal answer for
          anybody who created it by hand before running this, so it is an
          ordinary success and not a failure.
        - ``unavailable`` — the tool is absent or signed out. The answer
          carries the command and, when it applies, the sign-in command.
        """
        identity = parse_repo_id(identifier)
        plan = creation_plan(identity, private=private, description=description)
        if plan is None:
            # Asked before the URL is built: a provider this project cannot
            # create for may not be one it can spell a remote for either.
            raise GitSyncError(
                f"no repository-creation tool is known for provider "
                f"{identity.get('gitprovider', '?')!r}. Create {identifier} on its "
                "host, then carry on — every other command speaks plain Git."
            )
        remote_url = MemoryFacts.remote_url(identifier)
        answer: dict[str, Any] = {
            "repository": identifier,
            "remote_url": remote_url,
            "private": private,
            "command": plan.command,
            "sign_in": plan.sign_in,
        }
        # Asked before running anything: a tool that refuses because the
        # repository is already there says so in prose, and prose is a worse
        # thing to decide on than a ref listing.
        if self.client.git_runner.remote_reachable(remote_url):
            self.client._log_event("repo_create", repository=identifier, outcome="exists")
            return {**answer, "created": "exists"}

        run = self.client.git_runner.run_tool(plan.tool, *plan.argv)
        if not run.ran:
            self.client._log_event("repo_create", repository=identifier, outcome="no-tool")
            return {**answer, "created": "unavailable", "reason": f"{plan.tool} is not installed"}
        if run.ok:
            self.client._log_event("repo_create", repository=identifier, outcome="created")
            return {**answer, "created": "created"}
        if looks_like_already_exists(run.message):
            self.client._log_event("repo_create", repository=identifier, outcome="exists")
            return {**answer, "created": "exists"}
        if looks_like_not_signed_in(run.message):
            self.client._log_event("repo_create", repository=identifier, outcome="signed-out")
            return {**answer, "created": "unavailable", "reason": f"{plan.tool} is not signed in"}
        raise GitSyncError(f"{plan.command} failed: {run.message}")

    def discover_nested_configs(self) -> tuple[str, ...]:
        registry = self.client.get_dependency_registry()
        changes = discover_nested_configs(registry)
        # A nested `.cgs` names a project of its own; what a private/local
        # child targets is the *tree's* project's branch, which only a tree
        # (not a single document) can say.
        GitTreeBranches(registry).declare_targets()
        return changes


__all__ = ["DiscoveryCommands"]

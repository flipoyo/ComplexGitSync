"""version — how each repository of a tree is versioned, and how to put a version where there is none, and bump it.

Ring: 2
Contract: ``VersionOperation`` reads every repository's ``pixi.toml`` version (``diagnose``), installs one with Pixi where
    a repository has none (``init``), and runs each modified repository's own ``bump-version`` task, or Pixi's own bump,
    leaf first and the root last, checking the root moved by the level asked (``bump``). Writes no State, no ledger entry
    and no commit; refused outside a DEV tree before anything is read.
Imports: errors, git_branch, git_repo, git_tree, toolchain, version_scheme, preflight

TreeVersion (owner, 2026-11-11). A repository's own task comes first because it knows every file that mirrors the
version; Pixi's bump is only used for plain SemVer that nothing else copies. ``cgitsync`` never writes a manifest, a
``.tex`` or a README itself, and never passes a version that is not SemVer to Pixi (it would turn ``2026.07`` into
``2026.7``).
"""

from __future__ import annotations

import re
import shlex
import tomllib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ...errors import GitSyncError
from ...git_repo.git_branch import foreign_tool
from ...git_repo.git_repo import WorkingRepo
from ..git_tree import ROOT_REPO_ID, TreeProfile, WorkingGitTree, iter_tree, iter_tree_leaf_first
from ..toolchain import ABSENT, Toolchain
from ..version_scheme import VersionScheme
from .preflight import Preflight

if TYPE_CHECKING:
    from ...orchestre import GitRunner

__all__ = ["VersionMove", "VersionOperation", "VersionRow"]

#: A verdict that no ``init`` or ``bump`` ever acts on: listed, never judged further.
SKIPPED = ("private", "other project", "not versioned")
_MIRROR_FILE_LIMIT = 2_000_000
_ARROW = re.compile(r"(\S+)\s*->\s*(\S+)\s*$")


@dataclass(frozen=True)
class VersionRow:
    """How one repository is versioned: what ``diagnose`` prints, and what ``init`` and ``bump`` decide on."""

    repo_id: str
    name: str
    path: str
    manifest: str
    version: str | None
    scheme: str
    bump: str
    build: str
    mirrors: int
    verdict: str
    detail: str = ""
    modified: bool = False
    is_root: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class VersionMove:
    """One repository's version, before and after (or as it would be, on a dry run)."""

    name: str
    path: str
    route: str
    old: str
    new: str
    applied: bool


class VersionOperation:
    """``version diagnose``, ``version init`` and ``version bump``, over a READY tree."""

    TASK = "bump-version"
    BUILD_TASK = "bump-build"

    @staticmethod
    def require_dev(tree: WorkingGitTree, command: str) -> None:
        """Refuse *command* in a USER tree, before any repository is read: a USR or PUB tree holds no private script to run."""
        if tree.profile is not TreeProfile.DEV:
            raise GitSyncError(
                f"{command} was refused: this tree holds no private repository, so it is a user's tree. "
                "A repository's version command is private to the developer's tree (DEV or OPX). Nothing was changed."
            )

    @staticmethod
    def _tasks(path: Path) -> dict[str, object]:
        try:
            tasks = tomllib.loads((path / VersionScheme.MANIFEST).read_text(encoding="utf-8")).get("tasks", {})
        except (OSError, tomllib.TOMLDecodeError):
            return {}
        return tasks if isinstance(tasks, dict) else {}

    @staticmethod
    def _command(task: object) -> str:
        cmd = task.get("cmd", "") if isinstance(task, dict) else task
        return " ".join(cmd) if isinstance(cmd, list) else str(cmd or "")

    @staticmethod
    def _missing_script(path: Path, command: str) -> str | None:
        """The first ``.py`` file the task runs that is not there, or ``None``."""
        try:
            words = shlex.split(command)
        except ValueError:
            return None
        return next((w for w in words if w.endswith(".py") and not w.startswith("-") and not (path / w).is_file()), None)

    @staticmethod
    def _mirrors(path: Path, version: str, git_runner: GitRunner) -> int:
        """How many tracked files other than the manifest hold *version*; binary and very large files are not read."""
        needle, found = version.encode(), 0
        for relative in git_runner.tracked_files(path):
            if relative.name == VersionScheme.MANIFEST and relative.parent == Path("."):
                continue
            try:
                target = path / relative
                if target.stat().st_size > _MIRROR_FILE_LIMIT:
                    continue
                data = target.read_bytes()
            except OSError:
                continue
            found += needle in data and b"\0" not in data[:8000]
        return found

    @staticmethod
    def _row(repo: WorkingRepo, git_runner: GitRunner, project_name: str | None, pixi: bool) -> VersionRow:
        path = repo.absolute_path
        label = repo.relative_path.as_posix() if repo.relative_path else "."
        tasks = VersionOperation._tasks(path) if (path / VersionScheme.MANIFEST).is_file() else {}
        version = VersionScheme.read(path)
        scheme = VersionScheme.classify(version)
        kept = {"repo_id": repo.repo_id, "name": repo.name, "path": label, "version": version, "scheme": scheme, "is_root": repo.repo_id == ROOT_REPO_ID}
        manifest = VersionScheme.MANIFEST if (path / VersionScheme.MANIFEST).is_file() else "none"
        build = "task" if VersionOperation.BUILD_TASK in tasks else "-"
        if repo.effective_private:
            return VersionRow(manifest=manifest, bump="-", build=build, mirrors=0, verdict="private", **kept)
        if foreign_tool(repo, project_name=project_name):
            return VersionRow(manifest=manifest, bump="-", build=build, mirrors=0, verdict="other project", **kept)
        if manifest == "none":
            return VersionRow(manifest=manifest, bump="-", build="-", mirrors=0, verdict="not versioned", **kept)
        command = VersionOperation._command(tasks.get(VersionOperation.TASK))
        mirrors = VersionOperation._mirrors(path, version, git_runner) if version else 0
        missing = VersionOperation._missing_script(path, command) if command else None
        bump = "task" if command else ("pixi" if scheme in ("semver", "semver-pre") and not mirrors else "none")
        verdict, detail = "ready", ""
        if not pixi:
            verdict, detail = "no pixi", "pixi is not on PATH"
        elif version is None:
            verdict = "init"
        elif missing:
            verdict, detail = "task needed", f"the {VersionOperation.TASK} task runs {missing}, which is not in this repository"
        elif bump == "none":
            verdict = "task needed"
            detail = f"{mirrors} other file(s) hold {version}" if mirrors else f"{version} is not SemVer"
            detail += f": declare a {VersionOperation.TASK} task that updates them"
        return VersionRow(manifest=manifest, bump=bump, build=build, mirrors=mirrors, verdict=verdict, detail=detail, **kept)

    @staticmethod
    def _modified(repo: WorkingRepo, git_runner: GitRunner) -> bool:
        """What the next commit and push will publish: uncommitted changes, or commits the upstream does not have."""
        return bool(git_runner.status_porcelain(repo.absolute_path)) or (Preflight.commits_ahead_of_upstream(git_runner, repo) or 0) > 0

    @staticmethod
    def diagnose(tree: WorkingGitTree, git_runner: GitRunner, project_name: str | None) -> tuple[VersionRow, ...]:
        """One row per repository of the tree, root first. Reads only."""
        VersionOperation.require_dev(tree, "version diagnose")
        pixi = Toolchain.tool_version("pixi", git_runner) != ABSENT
        rows = []
        for repo in iter_tree(tree):
            row = VersionOperation._row(repo, git_runner, project_name, pixi)
            rows.append(row if row.verdict in SKIPPED else VersionRow(**{**row.to_dict(), "modified": VersionOperation._modified(repo, git_runner)}))
        return tuple(rows)

    @staticmethod
    def _pick(rows: Sequence[VersionRow], named: Sequence[str]) -> list[VersionRow]:
        """The rows *named* (by name, path or id), every row when none is; an unknown name is refused."""
        if not named:
            return list(rows)
        chosen = [row for row in rows if {row.name, row.path, row.repo_id} & set(named)]
        unknown = [n for n in named if not any(n in (row.name, row.path, row.repo_id) for row in rows)]
        if unknown:
            raise GitSyncError(f"version: no such repository in this tree: {', '.join(unknown)}. Nothing was changed.")
        return chosen

    @staticmethod
    def init(tree: WorkingGitTree, git_runner: GitRunner, project_name: str | None, named: Sequence[str], version: str) -> tuple[VersionMove, ...]:
        """``pixi workspace version set <version>`` in each *named* repository whose verdict is ``init`` (all of them when none is named)."""
        VersionOperation.require_dev(tree, "version init")
        if VersionScheme.classify(version) not in ("semver", "semver-pre"):
            raise GitSyncError(f"version init: {version!r} is not SemVer (MAJOR.MINOR.PATCH). Nothing was changed.")
        rows = VersionOperation.diagnose(tree, git_runner, project_name)
        targets = VersionOperation._pick(rows, named)
        refused = [row for row in targets if named and row.verdict != "init"]
        if refused:
            raise GitSyncError("version init: " + "; ".join(f"{row.name} is {row.verdict}" + (f" ({row.detail})" if row.detail else "") for row in refused) + ". Nothing was changed.")
        moves = []
        for row in (row for row in targets if row.verdict == "init"):
            manifest = str(VersionOperation._repo_dir(tree, row) / VersionScheme.MANIFEST)
            VersionOperation._pixi(git_runner, row, "workspace", "version", "set", version, "--manifest-path", manifest)
            moves.append(VersionMove(row.name, row.path, "pixi", "-", version, True))
        return tuple(moves)

    @staticmethod
    def _repo_dir(tree: WorkingGitTree, row: VersionRow) -> Path:
        """Where *row*'s repository is on disk."""
        return next(repo.absolute_path for repo in iter_tree_leaf_first(tree) if repo.repo_id == row.repo_id)

    @staticmethod
    def _pixi(git_runner: GitRunner, row: VersionRow, *args: str) -> str:
        run = git_runner.run_tool("pixi", *args)
        if not run.ran or run.returncode != 0:
            said = (run.stderr or run.stdout).strip().splitlines()
            raise GitSyncError(f"{row.name} ({row.path}): pixi {' '.join(args[:3])} failed: {said[-1] if said else 'pixi did not run'}")
        return run.stdout

    @staticmethod
    def _route(tree: WorkingGitTree, git_runner: GitRunner, row: VersionRow, level: str, *, apply: bool) -> tuple[str, str]:
        """``(old, new)`` for *row*: its own task (dry, or real), or Pixi's own bump (computed, or real)."""
        old = row.version or ""
        manifest = str(VersionOperation._repo_dir(tree, row) / VersionScheme.MANIFEST)
        if row.bump == "task":
            extra = () if apply else ("--dry-run",)
            out = VersionOperation._pixi(git_runner, row, "run", "--manifest-path", manifest, VersionOperation.TASK, level, *extra)
            said = _ARROW.search(out.strip().splitlines()[-1]) if out.strip() else None
            if not said:
                raise GitSyncError(f"{row.name} ({row.path}): its {VersionOperation.TASK} task did not end on 'old -> new'.")
            return said.group(1), said.group(2)
        new = VersionScheme.next(old, level) or ""
        if apply:
            VersionOperation._pixi(git_runner, row, "workspace", "version", level, "--manifest-path", manifest)
        return old, new

    @staticmethod
    def bump(tree: WorkingGitTree, git_runner: GitRunner, project_name: str | None, level: str, *, dry_run: bool = False) -> tuple[VersionMove, ...]:
        """Bump each modified repository, leaf first, then the root, and check the root moved by *level*; commits nothing."""
        if level not in VersionScheme.LEVELS:
            raise GitSyncError(f"version bump: the level is one of {', '.join(VersionScheme.LEVELS)}, not {level!r}.")
        rows = VersionOperation.diagnose(tree, git_runner, project_name)
        root = next((row for row in rows if row.is_root), None)
        chosen = [row for row in reversed(rows) if row.is_root or (row.modified and row.verdict not in SKIPPED)]
        unfit = [row for row in chosen if row.verdict != "ready" or (row.is_root and row.scheme not in ("semver", "semver-pre"))]
        if root is None or unfit:
            fixes = "; ".join(f"{row.name} ({row.path}): {row.verdict if row.verdict != 'ready' else 'root version ' + str(row.version) + ' is not SemVer'}" + (f", {row.detail}" if row.detail else "") for row in unfit)
            raise GitSyncError(f"version bump refused, and nothing was changed. {fixes or 'the tree has no root'}. 'cgitsync version diagnose' says what each needs; 'cgitsync version init' installs a missing version.")
        plans = [(row, *VersionOperation._route(tree, git_runner, row, level, apply=False)) for row in chosen]
        wrong = VersionScheme.moved_by(plans[-1][1], plans[-1][2]) != level
        if wrong:
            raise GitSyncError(f"version bump refused, and nothing was changed: the root would go {plans[-1][1]} -> {plans[-1][2]}, which is not a {level} bump.")
        if dry_run:
            return tuple(VersionMove(row.name, row.path, row.bump, old, new, False) for row, old, new in plans)
        moves: list[VersionMove] = []
        for index, (row, old, _) in enumerate(plans):
            try:
                before, after = VersionOperation._route(tree, git_runner, row, level, apply=True)
            except GitSyncError as exc:
                waiting = ", ".join(r.name for r, *_ in plans[index + 1:]) or "none"
                done = ", ".join(f"{m.name} ({m.old} -> {m.new})" for m in moves) or "none"
                raise GitSyncError(
                    f"version bump stopped part-way: {exc}\ncompleted: {done}\nfailed: {row.name} ({row.path})\nnot attempted: {waiting}\n"
                    "Nothing was rolled back: the changes are uncommitted files; 'git -C <path> restore .' discards them."
                ) from exc
            moves.append(VersionMove(row.name, row.path, row.bump, before or old, after, True))
        landed = VersionScheme.read(VersionOperation._repo_dir(tree, root))
        if VersionScheme.moved_by(moves[-1].old, landed) != level:
            raise GitSyncError(f"version bump: the root's pixi.toml reads {landed} after {moves[-1].old}, which is not a {level} bump. Nothing was committed; 'git -C <path> restore .' discards the changes.")
        return tuple(moves)

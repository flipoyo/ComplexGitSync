from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import GitSyncError
from .models import RefKind, WorktreeState


@dataclass(frozen=True)
class GitCommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class GitStatus:
    current_ref_kind: RefKind
    current_ref_name: str
    commit_sha: str
    worktree_state: WorktreeState


class GitRunner:
    def run(self, args: list[str], cwd: str | Path) -> GitCommandResult:
        completed = subprocess.run(
            ["git", *args],
            cwd=Path(cwd),
            check=False,
            text=True,
            capture_output=True,
        )
        result = GitCommandResult(
            args=tuple(args),
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
        if result.returncode != 0:
            raise GitSyncError(result.stderr or result.stdout or f"git {' '.join(args)} failed")
        return result

    def is_git_repo(self, cwd: str | Path) -> bool:
        try:
            self.run(["rev-parse", "--is-inside-work-tree"], cwd)
        except GitSyncError:
            return False
        return True

    def get_status(self, cwd: str | Path) -> GitStatus:
        branch = self._optional_output(["symbolic-ref", "--short", "-q", "HEAD"], cwd)
        if branch:
            kind = RefKind.BRANCH
            name = branch
        else:
            tag = self._optional_output(["describe", "--tags", "--exact-match"], cwd)
            if tag:
                kind = RefKind.TAG
                name = tag
            else:
                kind = RefKind.DETACHED
                name = "HEAD"
        sha = self.run(["rev-parse", "HEAD"], cwd).stdout
        dirty = self.run(["status", "--porcelain"], cwd).stdout
        return GitStatus(
            current_ref_kind=kind,
            current_ref_name=name,
            commit_sha=sha,
            worktree_state=WorktreeState.CLEAN if not dirty else WorktreeState.DIRTY,
        )

    def checkout(self, cwd: str | Path, ref_name: str) -> None:
        self.run(["checkout", ref_name], cwd)

    def commit(self, cwd: str | Path, message: str, *, stage_all: bool = True) -> None:
        if stage_all:
            self.run(["add", "--all"], cwd)
        self.run(["commit", "-m", message], cwd)

    def push(self, cwd: str | Path, remote_name: str = "origin") -> None:
        self.run(["push", remote_name], cwd)

    def tag(self, cwd: str | Path, tag_name: str, *, annotated: bool = True) -> None:
        args = ["tag"]
        if annotated:
            args.extend(["-a", tag_name, "-m", tag_name])
        else:
            args.append(tag_name)
        self.run(args, cwd)

    def _optional_output(self, args: list[str], cwd: str | Path) -> str | None:
        try:
            return self.run(args, cwd).stdout
        except GitSyncError:
            return None

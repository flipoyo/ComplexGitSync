"""git_runner — Git subprocess execution boundary.

Ring: 2 (the sole `import subprocess` module in the codebase)
Contract: given a repository path and a well-formed set of arguments, run
    exactly the corresponding `git` subprocess and either return its parsed
    stdout or raise GitSyncError with the command and captured stderr/stdout
    — never mutates state beyond the git repository being operated on, and
    performs no validation of Git semantics beyond what the git binary itself
    enforces.
Imports: errors, git_repo
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from .errors import GitSyncError
from .git_repo import SyncState


def _non_interactive_git_env() -> dict[str, str]:
    """Environment for a git subprocess that must never block on a prompt.

    ComplexGitSync stores no credentials and has no private-repository
    authentication story (see ``import-submodules``/``discover``'s own
    docs) — every git operation is meant to succeed on ambient
    credentials already cached by the environment, or fail. Without this,
    a missing/expired credential makes ``git`` silently wait on a
    terminal or GUI prompt that never arrives, hanging the whole CLI with
    no visible error until the user notices and interrupts it.
    ``GIT_TERMINAL_PROMPT=0`` disables the terminal prompt; ``GIT_ASKPASS``
    pointed at ``echo`` makes any GUI/helper askpass return an empty
    credential immediately instead of popping up a window. Either way,
    git fails fast with a normal, catchable error instead of hanging.
    """
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}

# ============================================================
#  GitRunnerProtocol — the boundary other rings type against
# ============================================================


@runtime_checkable
class GitRunnerProtocol(Protocol):
    """Structural contract for anything that can stand in for :class:`GitRunner`.

    Lists every public method `GitRunner` exposes, with its exact signature,
    so callers elsewhere in the codebase (``orchestre.py``'s `Orchestre` /
    `ComplexGitSyncClient`, `operations.py`, `git_tree.py`, `master.py`) can
    eventually type against this Protocol instead of the concrete class, and
    so tests can hand a hand-written fake instead of a `GitRunner` instance
    or a `unittest.mock.Mock`.

    Marked ``@runtime_checkable`` so ``isinstance(obj, GitRunnerProtocol)``
    works as a cheap sanity check (e.g. in tests asserting a fake satisfies
    the contract). `runtime_checkable` only verifies that the named methods
    *exist* on the object — it does not check signatures or return types, so
    this is a smoke check, not a substitute for the static type checker
    actually verifying call sites against the Protocol.
    """

    def remote_branch_exists(self, remote_url: str, branch: str) -> bool: ...

    def remote_tag_exists(self, remote_url: str, tag: str) -> bool: ...

    def remote_get_url(self, repo_path: Path | str, remote_name: str = "origin") -> str | None: ...

    def configure_remote(self, repo_path: Path | str, remote_name: str, remote_url: str) -> None: ...

    def clone(self, remote_url: str, destination: Path | str, *, branch: str) -> None: ...

    def rev_parse_head(self, repo_path: Path | str) -> str: ...

    def current_branch(self, repo_path: Path | str) -> str | None: ...

    def local_branch_exists(self, repo_path: Path | str, branch: str) -> bool: ...

    def create_branch(self, repo_path: Path | str, branch: str) -> None: ...

    def checkout(self, repo_path: Path | str, branch: str) -> None: ...

    def has_uncommitted_changes(self, repo_path: Path | str) -> bool: ...

    def status_porcelain(self, repo_path: Path | str) -> list[str]: ...

    def tracked_gitlink_paths(self, repo_path: Path | str) -> set[Path]: ...

    def has_staged_changes(self, repo_path: Path | str) -> bool: ...

    def stage_all(self, repo_path: Path | str) -> None: ...

    def stage_path(self, repo_path: Path | str, relative_path: str) -> None: ...

    def commit(
        self,
        repo_path: Path | str,
        message: str,
        *,
        user_name: str | None = None,
        user_email: str | None = None,
    ) -> None: ...

    def push(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
        set_upstream: bool = False,
    ) -> None: ...

    def pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None: ...

    def force_pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None: ...

    def reset_hard(self, repo_path: Path | str, ref_name: str = "HEAD") -> None: ...

    def clean_untracked(self, repo_path: Path | str) -> None: ...

    def rm_cached(self, repo_path: Path | str, path: str) -> None: ...

    def remove(self, repo_path: Path | str, path: str) -> None: ...

    def create_tag(self, repo_path: Path | str, tag_name: str) -> None: ...

    def remote_exists(self, repo_path: Path | str, remote: str = "origin") -> bool: ...

    def tag_exists(self, repo_path: Path | str, tag_name: str) -> bool: ...

    def has_unresolved_merge(self, repo_path: Path | str) -> bool: ...

    def branch_tracking_state(self, repo_path: Path | str) -> SyncState | None: ...

    def upstream_ref(self, repo_path: Path | str) -> str | None: ...

    def branch_tracking_counts(self, repo_path: Path | str) -> tuple[int, int] | None: ...

    def has_upstream(self, repo_path: Path | str) -> bool: ...


# ============================================================
#  GitRunner — the concrete Ring-2 implementation
# ============================================================


@dataclass(slots=True)
class GitRunner:
    """Git subprocess wrapper — executes git commands for clone/checkout/push actions."""

    executable: str = "git"

    def remote_branch_exists(self, remote_url: str, branch: str) -> bool:
        return self._remote_ref_exists(remote_url, "--heads", branch)

    def remote_tag_exists(self, remote_url: str, tag: str) -> bool:
        return self._remote_ref_exists(remote_url, "--tags", tag)

    def _remote_ref_exists(self, remote_url: str, ref_selector: str, ref_name: str) -> bool:
        completed = self._run("ls-remote", ref_selector, remote_url, ref_name)
        return bool(completed.stdout.strip())

    def remote_get_url(self, repo_path: Path | str, remote_name: str = "origin") -> str | None:
        """Return the URL configured for *remote_name*, or ``None`` when unset.

        Used by :meth:`ComplexGitSyncClient.discover_repos` to recover a
        checked-out repository's upstream address. A repository with no such
        remote is a normal, reportable condition — not an error — so the
        missing case is returned as ``None`` rather than raised.
        """
        try:
            url = self._run("remote", "get-url", remote_name, cwd=repo_path).stdout.strip()
        except GitSyncError:
            return None
        return url or None

    def configure_remote(self, repo_path: Path | str, remote_name: str, remote_url: str) -> None:
        """Add or update *remote_name* in *repo_path*."""
        try:
            existing = self._run("remote", "get-url", remote_name, cwd=repo_path).stdout.strip()
        except GitSyncError:
            self._run("remote", "add", remote_name, remote_url, cwd=repo_path)
            return
        if existing != remote_url:
            self._run("remote", "set-url", remote_name, remote_url, cwd=repo_path)

    def clone(self, remote_url: str, destination: Path | str, *, branch: str) -> None:
        destination_path = Path(destination)
        if destination_path.exists():
            if not destination_path.is_dir() or any(destination_path.iterdir()):
                raise GitSyncError(
                    f"Clone destination already exists and is not empty: {destination_path}"
                )
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        args: list[str] = []
        if self._uses_file_transport(remote_url):
            args.extend(["-c", "protocol.file.allow=always"])
        args.extend(
            ["clone", "--branch", branch, "--single-branch", remote_url, str(destination_path)]
        )
        self._run(*args)

    def rev_parse_head(self, repo_path: Path | str) -> str:
        return self._run("rev-parse", "HEAD", cwd=repo_path).stdout.strip()

    def current_branch(self, repo_path: Path | str) -> str | None:
        branch = self._run("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_path).stdout.strip()
        return None if branch == "HEAD" else branch

    def local_branch_exists(self, repo_path: Path | str, branch: str) -> bool:
        """Return ``True`` if *branch* exists as a local branch in *repo_path*."""
        try:
            self._run("rev-parse", "--verify", f"refs/heads/{branch}", cwd=repo_path)
            return True
        except GitSyncError:
            return False

    def create_branch(self, repo_path: Path | str, branch: str) -> None:
        """Create *branch* in *repo_path* without switching to it (``git branch``)."""
        self._run("branch", branch, cwd=repo_path)

    def checkout(self, repo_path: Path | str, branch: str) -> None:
        """Switch *repo_path* to *branch* (``git checkout``)."""
        self._run("checkout", branch, cwd=repo_path)

    def has_uncommitted_changes(self, repo_path: Path | str) -> bool:
        """Return ``True`` if *repo_path* has any tracked or staged modifications."""
        result = self._run("status", "--porcelain", cwd=repo_path)
        return bool(result.stdout.strip())

    def status_porcelain(self, repo_path: Path | str) -> list[str]:
        """Return ``git status --porcelain`` lines for *repo_path*."""
        result = self._run("status", "--porcelain", cwd=repo_path)
        return [line for line in result.stdout.splitlines() if line.strip()]

    def tracked_gitlink_paths(self, repo_path: Path | str) -> set[Path]:
        """Return paths tracked as gitlinks (mode ``160000``) in *repo_path*."""
        result = self._run("ls-files", "--stage", cwd=repo_path)
        gitlinks: set[Path] = set()
        for line in result.stdout.splitlines():
            if not line.startswith("160000 "):
                continue
            try:
                path = line.split("\t", 1)[1]
            except IndexError:
                continue
            gitlinks.add(Path(path))
        return gitlinks

    def has_staged_changes(self, repo_path: Path | str) -> bool:
        """Return ``True`` if *repo_path* has changes staged for the next commit."""
        result = self._run("diff", "--cached", "--name-only", cwd=repo_path)
        return bool(result.stdout.strip())

    def stage_all(self, repo_path: Path | str) -> None:
        """Stage all changes in *repo_path* (``git add --all``)."""
        self._run("add", "--all", cwd=repo_path)

    def stage_path(self, repo_path: Path | str, relative_path: str) -> None:
        """Stage a single path in *repo_path* (``git add -- <relative_path>``)."""
        self._run("add", "--", relative_path, cwd=repo_path)

    def commit(
        self,
        repo_path: Path | str,
        message: str,
        *,
        user_name: str | None = None,
        user_email: str | None = None,
    ) -> None:
        """Commit staged changes in *repo_path* with *message* (``git commit``)."""
        args: list[str] = []
        if user_name is not None:
            args.extend(["-c", f"user.name={user_name}"])
        if user_email is not None:
            args.extend(["-c", f"user.email={user_email}"])
        args.extend(["commit", "-m", message])
        self._run(*args, cwd=repo_path)

    def push(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
        set_upstream: bool = False,
    ) -> None:
        """Push *remote* (and optionally *ref_name*) in *repo_path* (``git push``)."""
        args = ["push"]
        if set_upstream:
            args.append("-u")
        args.append(remote)
        if ref_name:
            args.append(ref_name)
        self._run(*args, cwd=repo_path)

    def pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None:
        """Pull *remote* (and optionally *ref_name*) in *repo_path* (``git pull --ff-only``)."""
        args = ["pull", "--ff-only", remote]
        if ref_name:
            args.append(ref_name)
        self._run(*args, cwd=repo_path)

    def force_pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None:
        """Force the local branch to match *remote/ref_name* and clean untracked files."""
        selected_ref = ref_name or self.current_branch(repo_path) or "main"
        self._run("fetch", remote, selected_ref, cwd=repo_path)
        self._run("checkout", "-B", selected_ref, "FETCH_HEAD", cwd=repo_path)
        self.clean_untracked(repo_path)

    def reset_hard(self, repo_path: Path | str, ref_name: str = "HEAD") -> None:
        """Discard local tracked changes in *repo_path*."""
        self._run("reset", "--hard", ref_name, cwd=repo_path)

    def clean_untracked(self, repo_path: Path | str) -> None:
        """Remove untracked files and directories in *repo_path*."""
        self._run("clean", "-fd", cwd=repo_path)

    def rm_cached(self, repo_path: Path | str, path: str) -> None:
        """Remove *path* from the index (``git rm --cached``), keeping the working tree.

        Drops a tracked gitlink without deleting the child's working tree or
        its ``.git`` directory, preserving any local history inside the child.
        """
        self._run("rm", "--cached", path, cwd=repo_path)

    def remove(self, repo_path: Path | str, path: str) -> None:
        """Remove *path* from the working tree and stage the removal (``git rm -- <path>``).

        A plain tracked-file deletion, distinct from :meth:`rm_cached`
        (index-only, built for the submodule-to-plain-clone conversion).
        """
        self._run("rm", "--", path, cwd=repo_path)

    def create_tag(self, repo_path: Path | str, tag_name: str) -> None:
        """Create *tag_name* in *repo_path*."""
        self._run("tag", tag_name, cwd=repo_path)

    def remote_exists(self, repo_path: Path | str, remote: str = "origin") -> bool:
        """Return ``True`` when *remote* exists in *repo_path*."""
        try:
            self._run("remote", "get-url", remote, cwd=repo_path)
            return True
        except GitSyncError:
            return False

    def tag_exists(self, repo_path: Path | str, tag_name: str) -> bool:
        """Return ``True`` when *tag_name* already exists in *repo_path*."""
        completed = subprocess.run(
            [self.executable, "show-ref", "--verify", "--quiet", f"refs/tags/{tag_name}"],
            cwd=str(repo_path),
            capture_output=True,
            check=False,
            text=True,
            env=_non_interactive_git_env(),
        )
        if completed.returncode == 0:
            return True
        if completed.returncode == 1:
            return False
        command = f"{self.executable} show-ref --verify refs/tags/{tag_name}"
        details = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise GitSyncError(f"Git command failed ({command}): {details}")

    def has_unresolved_merge(self, repo_path: Path | str) -> bool:
        """Return ``True`` when *repo_path* has an in-progress merge conflict."""
        completed = subprocess.run(
            [self.executable, "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            check=False,
            text=True,
            env=_non_interactive_git_env(),
        )
        if completed.returncode == 0:
            return True
        if completed.returncode == 1:
            return False
        command = f"{self.executable} rev-parse --verify --quiet MERGE_HEAD"
        details = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise GitSyncError(f"Git command failed ({command}): {details}")

    def branch_tracking_state(self, repo_path: Path | str) -> SyncState | None:
        """Return upstream tracking state for the current branch in *repo_path*."""
        counts = self.branch_tracking_counts(repo_path)
        if counts is None:
            return None
        ahead, behind = counts
        if ahead and behind:
            return SyncState.DIVERGED
        if ahead:
            return SyncState.AHEAD
        if behind:
            return SyncState.BEHIND
        return SyncState.ALIGNED

    def upstream_ref(self, repo_path: Path | str) -> str | None:
        """Return the upstream ref for the current branch, e.g. ``origin/main``."""
        upstream = subprocess.run(
            [self.executable, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            cwd=str(repo_path),
            capture_output=True,
            check=False,
            text=True,
            env=_non_interactive_git_env(),
        )
        if upstream.returncode != 0:
            return None
        return upstream.stdout.strip() or None

    def branch_tracking_counts(self, repo_path: Path | str) -> tuple[int, int] | None:
        """Return ``(ahead, behind)`` counts against upstream for the current branch."""
        if self.upstream_ref(repo_path) is None:
            return None
        counts = self._run("rev-list", "--left-right", "--count", "HEAD...@{upstream}", cwd=repo_path)
        ahead_raw, behind_raw = counts.stdout.strip().split()
        return (int(ahead_raw), int(behind_raw))

    def has_upstream(self, repo_path: Path | str) -> bool:
        """Return ``True`` when the current branch has an upstream configured."""
        upstream = subprocess.run(
            [self.executable, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            cwd=str(repo_path),
            capture_output=True,
            check=False,
            text=True,
            env=_non_interactive_git_env(),
        )
        return upstream.returncode == 0

    def _run(
        self,
        *args: str,
        cwd: Path | str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [self.executable, *args],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            check=False,
            text=True,
            env=_non_interactive_git_env(),
        )
        if completed.returncode != 0:
            command = " ".join([self.executable, *args])
            details = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
            raise GitSyncError(f"Git command failed ({command}): {details}")
        return completed

    @staticmethod
    def _uses_file_transport(remote_url: str) -> bool:
        parsed = urlsplit(remote_url)
        if parsed.scheme == "file":
            return True
        if (
            len(parsed.scheme) == 1
            and len(remote_url) >= 2
            and remote_url[1] == ":"
            and parsed.scheme.isalpha()
        ):
            return True
        if parsed.scheme:
            return False
        return bool(remote_url) and not remote_url.startswith("git@")

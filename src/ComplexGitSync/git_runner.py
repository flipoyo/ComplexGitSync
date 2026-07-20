"""GitRunner - Controlled Git command execution with security guarantees.

This module provides the ONLY interface for executing Git commands in the system.
Direct Git subprocess invocation is forbidden elsewhere in the codebase.

Security Requirements (from DevPlanTicket.md):
1. Use argument arrays (no shell interpolation)
2. Set cwd explicitly
3. Capture stdout and stderr
4. Return typed results
5. Map Git failures to typed errors
6. Reject unsupported operations
7. Redact credentials
8. Preserve deterministic tree order
9. Identify the triggering cgitsync command
10. Never expose ..
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class GitCommand(str, Enum):
    """Enumeration of all supported Git commands."""

    INIT = "init"
    CLONE = "clone"
    ADD = "add"
    COMMIT = "commit"
    STATUS = "status"
    LOG = "log"
    BRANCH = "branch"
    CHECKOUT = "checkout"
    MERGE = "merge"
    PULL = "pull"
    PUSH = "push"
    TAG = "tag"
    REV_PARSE = "rev-parse"
    REV_LIST = "rev-list"
    SHOW_REF = "show-ref"
    DIFF = "diff"
    CONFIG = "config"
    REMOTE = "remote"


class GitErrorCode(str, Enum):
    """Typed error codes for Git operations."""

    SUCCESS = "success"
    GENERIC_ERROR = "generic_error"
    REPOSITORY_NOT_FOUND = "repository_not_found"
    NOT_A_GIT_REPO = "not_a_git_repo"
    BRANCH_NOT_FOUND = "branch_not_found"
    BRANCH_ALREADY_EXISTS = "branch_already_exists"
    CHECKOUT_ERROR = "checkout_error"
    MERGE_CONFLICT = "merge_conflict"
    COMMIT_ERROR = "commit_error"
    PUSH_ERROR = "push_error"
    PULL_ERROR = "pull_error"
    AUTH_ERROR = "auth_error"
    INVALID_ARGUMENTS = "invalid_arguments"
    UNSUPPORTED_COMMAND = "unsupported_command"
    CWD_NOT_FOUND = "cwd_not_found"


@dataclass(frozen=True, slots=True)
class GitError:
    """Typed Git error with context."""

    code: GitErrorCode
    message: str
    command: str
    cwd: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "command": self.command,
            "cwd": self.cwd,
            "stdout": self.stdout,
            "stderr": redact_credentials(self.stderr),
            "exit_code": self.exit_code,
        }

    def __str__(self) -> str:
        return f"GitError({self.code.value}: {self.message})"


@dataclass(frozen=True, slots=True)
class GitResult:
    """Result of a successful Git command execution."""

    command: str
    cwd: str
    stdout: str
    stderr: str = ""
    exit_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "cwd": self.cwd,
            "stdout": self.stdout,
            "stderr": redact_credentials(self.stderr),
            "exit_code": self.exit_code,
        }


@dataclass(frozen=True, slots=True)
class GitExecution:
    """Represents a Git command execution with all context."""

    success: bool
    result: GitResult | None = None
    error: GitError | None = None

    @property
    def ok(self) -> bool:
        return self.success and self.result is not None

    def to_dict(self) -> dict[str, Any]:
        if self.ok:
            assert self.result is not None
            return {"success": True, "result": self.result.to_dict()}
        assert self.error is not None
        return {"success": False, "error": self.error.to_dict()}


# Credentials to redact from output
_CREDS_MARKERS = frozenset({
    "password",
    "secret",
    "token",
    "credential",
    "auth",
    "key=",
})


def redact_credentials(text: str) -> str:
    """Redact potential credentials from Git output."""
    import re

    result = text
    # Redact URLs with credentials (user:password@host)
    result = re.sub(
        r"https?://[^:]+:[^@]+@",
        "https://[REDACTED]@",
        result,
    )
    # Redact credential patterns in text
    result = re.sub(
        r"(password|secret|token|credential|auth|key)=[^\s]+",
        r"\1=[REDACTED]",
        result,
        flags=re.IGNORECASE,
    )
    return result.strip()


def _get_git_executable() -> str:
    """Get the Git executable path."""
    return "git"


def _validate_cwd(cwd: str | Path | None) -> Path:
    """Validate and resolve the working directory."""
    if cwd is None:
        cwd = Path.cwd()
    elif isinstance(cwd, str):
        cwd = Path(cwd)

    if not cwd.exists():
        raise ValueError(f"Working directory does not exist: {cwd}")
    if not cwd.is_dir():
        raise ValueError(f"Working directory is not a directory: {cwd}")

    return cwd.resolve()


def _validate_args(args: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Validate Git command arguments."""
    if args is None:
        return ()
    if isinstance(args, list):
        return tuple(args)
    return args


def _build_command(
    command: GitCommand,
    args: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Build a safe Git command tuple."""
    git_exec = _get_git_executable()
    cmd_args = [git_exec, command.value]
    cmd_args.extend(_validate_args(args))
    return tuple(cmd_args)


def _map_exit_code(
    exit_code: int,
    command: GitCommand,
    stderr: str,
) -> GitErrorCode:
    """Map Git exit codes to typed error codes."""
    if exit_code == 0:
        return GitErrorCode.SUCCESS

    stderr_lower = stderr.lower()

    # Common Git error patterns
    if "not a git repository" in stderr_lower or "not a git repository" in stderr_lower:
        return GitErrorCode.NOT_A_GIT_REPO
    if "repository not found" in stderr_lower or "no such file or directory" in stderr_lower:
        return GitErrorCode.REPOSITORY_NOT_FOUND
    if "branch not found" in stderr_lower or "pathspec" in stderr_lower and "did not match" in stderr_lower:
        return GitErrorCode.BRANCH_NOT_FOUND
    if "already exists" in stderr_lower:
        return GitErrorCode.BRANCH_ALREADY_EXISTS
    if "merge conflict" in stderr_lower or "CONFLICT" in stderr:
        return GitErrorCode.MERGE_CONFLICT
    if "authentication" in stderr_lower or "permission denied" in stderr_lower:
        return GitErrorCode.AUTH_ERROR
    if "invalid" in stderr_lower or "error:" in stderr_lower:
        return GitErrorCode.GENERIC_ERROR

    # Command-specific errors
    if command == GitCommand.CHECKOUT:
        return GitErrorCode.CHECKOUT_ERROR
    if command == GitCommand.COMMIT:
        return GitErrorCode.COMMIT_ERROR
    if command == GitCommand.PUSH:
        return GitErrorCode.PUSH_ERROR
    if command == GitCommand.PULL:
        return GitErrorCode.PULL_ERROR
    if command == GitCommand.MERGE:
        return GitErrorCode.MERGE_CONFLICT

    return GitErrorCode.GENERIC_ERROR


class GitRunner:
    """Controlled Git command executor.

    This is the ONLY class permitted to invoke the Git executable.
    All Git operations must go through this class.
    """

    def __init__(
        self,
        cwd: str | Path | None = None,
        *,
        cgitsync_command: str | None = None,
        deterministic_order: bool = True,
    ) -> None:
        """Initialize GitRunner.

        Args:
            cwd: Working directory for Git commands. Defaults to current directory.
            cgitsync_command: The triggering cgitsync command for context.
            deterministic_order: Ensure deterministic output ordering.
        """
        self._cwd = _validate_cwd(cwd)
        self._cgitsync_command = cgitsync_command or "unknown"
        self._deterministic_order = deterministic_order

    @property
    def cwd(self) -> Path:
        """Current working directory."""
        return self._cwd

    @property
    def cgitsync_command(self) -> str:
        """The triggering cgitsync command."""
        return self._cgitsync_command

    def _execute(
        self,
        command: GitCommand,
        args: list[str] | tuple[str, ...] | None = None,
        *,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
    ) -> GitExecution:
        """Internal execution method."""
        cmd_tuple = _build_command(command, args)
        resolved_args = _validate_args(args)

        # Security: Never use shell=True
        # Build environment with LC_ALL=C for deterministic ordering
        env = os.environ.copy()
        if self._deterministic_order:
            env["LC_ALL"] = "C"
            env["LANG"] = "C"

        try:
            result = subprocess.run(
                cmd_tuple,
                cwd=str(self._cwd),
                capture_output=capture_output,
                text=text,
                check=check,
                shell=False,  # CRITICAL: Never use shell=True
                timeout=300,  # 5 minute timeout
                env=env,
            )

            stdout = result.stdout or ""
            stderr = result.stderr or ""

            if result.returncode != 0:
                error_code = _map_exit_code(result.returncode, command, stderr)
                return GitExecution(
                    success=False,
                    error=GitError(
                        code=error_code,
                        message=f"Git {command.value} failed with exit code {result.returncode}",
                        command=command.value,
                        cwd=str(self._cwd),
                        stdout=stdout,
                        stderr=stderr,
                        exit_code=result.returncode,
                    ),
                )

            return GitExecution(
                success=True,
                result=GitResult(
                    command=command.value,
                    cwd=str(self._cwd),
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=result.returncode,
                ),
            )

        except subprocess.TimeoutExpired as e:
            return GitExecution(
                success=False,
                error=GitError(
                    code=GitErrorCode.GENERIC_ERROR,
                    message=f"Git {command.value} timed out",
                    command=command.value,
                    cwd=str(self._cwd),
                    stdout="",
                    stderr=str(e),
                    exit_code=-1,
                ),
            )
        except FileNotFoundError as e:
            return GitExecution(
                success=False,
                error=GitError(
                    code=GitErrorCode.CWD_NOT_FOUND,
                    message=f"Working directory not found: {self._cwd}",
                    command=command.value,
                    cwd=str(self._cwd),
                    stdout="",
                    stderr=str(e),
                    exit_code=-1,
                ),
            )
        except Exception as e:
            return GitExecution(
                success=False,
                error=GitError(
                    code=GitErrorCode.GENERIC_ERROR,
                    message=f"Unexpected error executing Git {command.value}: {e}",
                    command=command.value,
                    cwd=str(self._cwd),
                    stdout="",
                    stderr=str(e),
                    exit_code=-1,
                ),
            )

    def init(
        self,
        *,
        bare: bool = False,
        initial_branch: str | None = None,
    ) -> GitExecution:
        """Initialize a new Git repository."""
        args = ["--initial-branch", initial_branch] if initial_branch else []
        if bare:
            args.append("--bare")
        result = self._execute(GitCommand.INIT, args)

        # For non-bare repos, we need to make an initial commit to have a valid HEAD
        # so that branch operations work correctly
        if result.success and not bare and initial_branch:
            # Create a .gitkeep file and commit it
            gitkeep_path = self._cwd / ".gitkeep"
            gitkeep_path.write_text("")

            # Add and commit the file
            add_result = self.add(str(gitkeep_path))
            if add_result.success:
                commit_result = self.commit(
                    "Initial commit",
                    all=False,
                    amend=False,
                    no_edit=True,
                )
                if not commit_result.success:
                    # Rollback - remove the .gitkeep file
                    gitkeep_path.unlink(missing_ok=True)
                    return commit_result

        return result

    def clone(
        self,
        url: str,
        *,
        branch: str | None = None,
        depth: int | None = None,
    ) -> GitExecution:
        """Clone a Git repository."""
        # Sanitize URL - redact credentials
        safe_url = redact_credentials(url)
        args = [url]
        if branch:
            args.extend(["--branch", branch])
        if depth:
            args.extend(["--depth", str(depth)])
        return self._execute(GitCommand.CLONE, args)

    def add(
        self,
        *paths: str | Path,
        update: bool = False,
    ) -> GitExecution:
        """Add files to staging area."""
        args = ["--all"] if not paths else [str(p) for p in paths]
        if update:
            args.insert(0, "--update")
        return self._execute(GitCommand.ADD, args)

    def commit(
        self,
        message: str,
        *,
        all: bool = False,
        amend: bool = False,
        no_edit: bool = True,
    ) -> GitExecution:
        """Commit staged changes."""
        args = ["-m", message]
        if all:
            args.append("--all")
        if amend:
            args.append("--amend")
        if no_edit:
            args.append("--no-edit")
        return self._execute(GitCommand.COMMIT, args)

    def status(
        self,
        *,
        short: bool = False,
        porcelain: bool = False,
    ) -> GitExecution:
        """Get repository status."""
        args = []
        if short:
            args.append("--short")
        if porcelain:
            args.append("--porcelain")
        return self._execute(GitCommand.STATUS, args)

    def log(
        self,
        *,
        oneline: bool = False,
        max_count: int | None = None,
        pretty: str | None = None,
    ) -> GitExecution:
        """Get commit log."""
        args = []
        if oneline:
            args.append("--oneline")
        if max_count:
            args.extend(["--max-count", str(max_count)])
        if pretty:
            args.extend(["--pretty", pretty])
        return self._execute(GitCommand.LOG, args)

    def branch(
        self,
        *,
        list: bool = True,
        all: bool = False,
        remote: bool = False,
        contains: str | None = None,
        delete: str | None = None,
        create: str | None = None,
    ) -> GitExecution:
        """List or manage branches."""
        args = []
        if list:
            args.append("--list")
        if all:
            args.append("--all")
        if remote:
            args.append("--remotes")
        if contains:
            args.extend(["--contains", contains])
        if delete:
            args.extend(["-D", delete])
        if create:
            # git branch <name> - create a new branch from current HEAD
            args = [create]
        return self._execute(GitCommand.BRANCH, args)

    def checkout(
        self,
        target: str,
        *,
        branch: bool = False,
        force: bool = False,
        orphan: bool = False,
    ) -> GitExecution:
        """Checkout a branch or commit."""
        args = [target]
        if branch:
            args.insert(0, "-b")
        if force:
            args.insert(0, "-f")
        if orphan:
            args.insert(0, "--orphan")
        return self._execute(GitCommand.CHECKOUT, args)

    def merge(
        self,
        source: str,
        *,
        no_ff: bool = False,
        no_commit: bool = False,
        ff_only: bool = False,
    ) -> GitExecution:
        """Merge a branch."""
        args = [source]
        if no_ff:
            args.insert(0, "--no-ff")
        if no_commit:
            args.insert(0, "--no-commit")
        if ff_only:
            args.insert(0, "--ff-only")
        return self._execute(GitCommand.MERGE, args)

    def pull(
        self,
        *,
        remote: str | None = None,
        branch: str | None = None,
        rebase: bool = False,
    ) -> GitExecution:
        """Pull from remote."""
        args = []
        if remote:
            args.extend([remote, branch or ""])
        if rebase:
            args.insert(0, "--rebase")
        return self._execute(GitCommand.PULL, args)

    def push(
        self,
        *,
        remote: str | None = None,
        branch: str | None = None,
        force: bool = False,
        tags: bool = False,
    ) -> GitExecution:
        """Push to remote."""
        args = []
        if remote:
            args.append(remote)
        if branch:
            args.append(branch)
        if force:
            args.insert(0, "--force")
        if tags:
            args.insert(0, "--tags")
        return self._execute(GitCommand.PUSH, args)

    def tag(
        self,
        name: str,
        *,
        message: str | None = None,
        force: bool = False,
        annotated: bool = False,
    ) -> GitExecution:
        """Create a tag."""
        args = [name]
        if annotated:
            args.insert(0, "-a")
        if message:
            args.extend(["-m", message])
        if force:
            args.insert(0, "-f")
        return self._execute(GitCommand.TAG, args)

    def rev_parse(
        self,
        *args: str,
    ) -> GitExecution:
        """Parse Git references."""
        return self._execute(GitCommand.REV_PARSE, list(args))

    def show_ref(
        self,
        *,
        heads: bool = False,
        tags: bool = False,
    ) -> GitExecution:
        """Show references."""
        args = []
        if heads:
            args.append("--heads")
        if tags:
            args.append("--tags")
        return self._execute(GitCommand.SHOW_REF, args)

    def diff(
        self,
        *args: str,
    ) -> GitExecution:
        """Show changes."""
        return self._execute(GitCommand.DIFF, list(args))

    def config(
        self,
        *args: str,
    ) -> GitExecution:
        """Get or set Git configuration."""
        return self._execute(GitCommand.CONFIG, list(args))

    def remote(
        self,
        *args: str,
    ) -> GitExecution:
        """Manage remotes."""
        return self._execute(GitCommand.REMOTE, list(args))

    def get_current_branch(self) -> str | None:
        """Get the current branch name."""
        result = self._execute(GitCommand.BRANCH, ["--show-current"])
        if result.ok and result.result:
            return result.result.stdout.strip() or None
        return None

    def get_head_commit(self) -> str | None:
        """Get the current HEAD commit hash."""
        result = self.rev_parse("HEAD")
        if result.ok and result.result:
            return result.result.stdout.strip()
        return None

    def is_git_repo(self) -> bool:
        """Check if the current directory is a Git repository."""
        result = self.rev_parse("--is-inside-work-tree")
        if result.ok and result.result:
            return result.result.stdout.strip() == "true"
        return False

    def list_branches(self, remote: bool = False) -> list[str]:
        """List all branches."""
        result = self.branch(list=True, all=True, remote=remote)
        if result.ok and result.result:
            branches = result.result.stdout.strip().split("\n")
            # Clean up branch names (remove * and whitespace)
            return [b.strip().lstrip("* ").strip() for b in branches if b.strip()]
        return []

    def list_tags(self) -> list[str]:
        """List all tags."""
        result = self._execute(GitCommand.TAG, ["--list"])
        if result.ok and result.result:
            tags = result.result.stdout.strip().split("\n")
            return [t.strip() for t in tags if t.strip()]
        return []

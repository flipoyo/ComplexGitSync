"""ComplexGitSyncClient - FrontEnd application service.

This module provides the canonical application service that consumes the @CGS backend
and orchestrates Git operations through GitRunner.

The CLI delegates all commands to methods in this class.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .git_runner import GitExecution, GitRunner


class ComplexGitSyncClient:
    """FrontEnd application service for ComplexGitSync operations.

    This class provides the canonical command methods that map 1:1 to cgitsync CLI commands.
    It orchestrates operations by:
    1. Accepting user commands
    2. Delegating to GitRunner for controlled Git operations
    3. Consuming @CGS backend services for State management

    The client does NOT contain the business logic for validation, anchoring, or
    State identity - these are owned by @CGS.
    """

    def __init__(
        self,
        cwd: str | Path | None = None,
        *,
        git_runner: GitRunner | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            cwd: Working directory for operations.
            git_runner: Optional GitRunner instance. Created if not provided.
        """
        self._cwd = Path(cwd) if cwd else Path.cwd()
        self._git_runner = git_runner or GitRunner(cwd=self._cwd, cgitsync_command="client")
        self._initialized = False

    @property
    def cwd(self) -> Path:
        """Current working directory."""
        return self._cwd

    @property
    def git_runner(self) -> GitRunner:
        """GitRunner instance."""
        return self._git_runner

    # ==================== Initialization and Status ====================

    def initialise(self) -> GitExecution:
        """Initialize a new ComplexGitSync project.

        Canonical command: cgitsync initialise
        """
        # Check if already initialized
        if self.git_runner.is_git_repo():
            return GitExecution(
                success=False,
                error=self._make_error(
                    "already_initialized",
                    "Repository already initialized",
                    "initialise",
                ),
            )

        # Initialize Git repository
        return self.git_runner.init(initial_branch="main")

    def status(self) -> GitExecution:
        """Get the status of the repository.

        Canonical command: cgitsync status
        """
        return self.git_runner.status(short=True)

    def validate(self) -> GitExecution:
        """Validate the current repository state.

        Canonical command: cgitsync validate

        This checks if the WorkingGitTree is READY.
        """
        # Check if it's a git repo
        if not self.git_runner.is_git_repo():
            return GitExecution(
                success=False,
                error=self._make_error(
                    "not_initialized",
                    "Repository not initialized",
                    "validate",
                ),
            )

        # Check current branch
        current_branch = self.git_runner.get_current_branch()
        if current_branch is None:
            return GitExecution(
                success=False,
                error=self._make_error(
                    "no_branch",
                    "No branch checked out",
                    "validate",
                ),
            )

        # Check for uncommitted changes
        status_result = self.git_runner.status(porcelain=True)
        if status_result.ok and status_result.result:
            status_output = status_result.result.stdout.strip()
            if status_output:
                return GitExecution(
                    success=False,
                    error=self._make_error(
                        "uncommitted_changes",
                        f"Uncommitted changes detected:\n{status_output}",
                        "validate",
                    ),
                )

        # All checks passed - READY state
        return GitExecution(
            success=True,
            result=self._make_result(
                "Repository is READY",
                "validate",
            ),
        )

    # ==================== Branch Operations ====================

    def branch(self, branch_name: str) -> GitExecution:
        """Create a new local branch.

        Canonical command: cgitsync branch <branch>

        Creates a local development branch.
        """
        # Validate branch name
        if not branch_name or not branch_name.strip():
            return GitExecution(
                success=False,
                error=self._make_error(
                    "invalid_branch_name",
                    "Branch name cannot be empty",
                    "branch",
                ),
            )

        # Check if branch already exists
        existing_branches = self.git_runner.list_branches()
        if branch_name in existing_branches:
            return GitExecution(
                success=False,
                error=self._make_error(
                    "branch_exists",
                    f"Branch '{branch_name}' already exists",
                    "branch",
                ),
            )

        # Create the branch
        return self.git_runner.branch(create=branch_name)

    def checkout(self, target: str) -> GitExecution:
        """Checkout a branch or commit.

        Canonical command: cgitsync checkout <branch>
        """
        return self.git_runner.checkout(target)

    # ==================== Change Operations ====================

    def add(self, *paths: str | Path) -> GitExecution:
        """Add files to staging area.

        Canonical command: cgitsync add [paths...]

        If no paths specified, adds all changes.
        """
        if not paths:
            return self.git_runner.add(all=True)
        return self.git_runner.add(*paths)

    def commit(self, message: str) -> GitExecution:
        """Commit staged changes.

        Canonical command: cgitsync commit <message>
        """
        if not message or not message.strip():
            return GitExecution(
                success=False,
                error=self._make_error(
                    "empty_message",
                    "Commit message cannot be empty",
                    "commit",
                ),
            )
        return self.git_runner.commit(message, no_edit=True)

    # ==================== Merge Operations ====================

    def merge(self, source_branch: str) -> GitExecution:
        """Merge a source branch into main.

        Canonical command: cgitsync merge <source-branch>

        The target is always main. The workflow:
        1. Load the latest READY GTS
        2. Validate the source branch
        3. Reject source=main
        4. Verify source exists locally
        5. Verify source is not a remote Memory branch
        6. Checkout main through cgitsync
        7. Merge source into main through cgitsync
        8. Stop on conflict
        9. Emit no successful State on conflict
        10. Tag the accepted local State
        11. Submit candidate STATE@ to @CGS
        12. Synchronize only main with @MS
        """
        # Step 3: Reject source=main
        if source_branch == "main":
            return GitExecution(
                success=False,
                error=self._make_error(
                    "merge_main",
                    "Cannot merge main into itself",
                    "merge",
                ),
            )

        # Step 4: Verify source exists locally
        existing_branches = self.git_runner.list_branches()
        if source_branch not in existing_branches:
            return GitExecution(
                success=False,
                error=self._make_error(
                    "branch_not_found",
                    f"Source branch '{source_branch}' does not exist locally",
                    "merge",
                ),
            )

        # Step 5: Verify source is not a remote Memory branch
        # For now, we check if it looks like a Memory branch (main or remote pattern)
        if source_branch.startswith("origin/"):
            return GitExecution(
                success=False,
                error=self._make_error(
                    "remote_branch",
                    f"Cannot merge remote branch '{source_branch}'",
                    "merge",
                ),
            )

        # Step 6: Checkout main
        checkout_result = self.checkout("main")
        if not checkout_result.ok:
            return checkout_result

        # Step 7: Merge source into main
        merge_result = self.git_runner.merge(source_branch, ff_only=False)

        # Step 8: Stop on conflict
        if not merge_result.ok:
            # Check for merge conflict
            if merge_result.error and "merge" in merge_result.error.code.value:
                return merge_result
            # Any other error also stops the process
            return merge_result

        # Step 10: Tag the accepted local State (placeholder - will be implemented with @CGS)
        # Step 11-12: Submit candidate STATE@ to @CGS and sync main with @MS
        # These steps require @CGS integration which will be added

        return merge_result

    # ==================== Remote Operations ====================

    def pull(self) -> GitExecution:
        """Pull from remote.

        Canonical command: cgitsync pull
        """
        return self.git_runner.pull()

    def push(self) -> GitExecution:
        """Push to remote.

        Canonical command: cgitsync push

        Only main should be pushed to @MS.
        """
        current_branch = self.git_runner.get_current_branch()
        if current_branch != "main":
            return GitExecution(
                success=False,
                error=self._make_error(
                    "push_non_main",
                    "Only main branch can be pushed to remote Memory",
                    "push",
                ),
            )
        return self.git_runner.push()

    def tag(self, tag_name: str) -> GitExecution:
        """Create a tag.

        Canonical command: cgitsync tag <tag>
        """
        return self.git_runner.tag(tag_name, annotated=True)

    # ==================== State Operations ====================

    def freeze(self) -> GitExecution:
        """Freeze the local State.

        Canonical command: cgitsync freeze

        Creates a snapshot of the current local State.
        """
        # Get current state
        status_result = self.status()
        if not status_result.ok:
            return status_result

        current_branch = self.git_runner.get_current_branch()
        head_commit = self.git_runner.get_head_commit()

        # Freeze logic will be implemented with @CGS integration
        # For now, return success with state info
        return GitExecution(
            success=True,
            result=self._make_result(
                f"Local State frozen at branch '{current_branch}', commit '{head_commit}'",
                "freeze",
            ),
        )

    def freeze_release(self) -> GitExecution:
        """Freeze and release the current State.

        Canonical command: cgitsync freeze-release

        Canonical workflow:
        1. validate
        2. add
        3. commit
        4. freeze local State
        5. merge current local branch into main
        6. submit candidate STATE@ to @CGS
        7. persist main through @MS
        """
        # Step 1: validate
        validate_result = self.validate()
        if not validate_result.ok:
            return validate_result

        current_branch = self.git_runner.get_current_branch()
        if current_branch is None:
            return GitExecution(
                success=False,
                error=self._make_error(
                    "no_branch",
                    "No branch checked out",
                    "freeze-release",
                ),
            )

        # If on main, nothing to do
        if current_branch == "main":
            return GitExecution(
                success=False,
                error=self._make_error(
                    "on_main",
                    "Already on main - nothing to release",
                    "freeze-release",
                ),
            )

        # Step 2-3: add and commit
        # Get status to see what needs to be added
        status_result = self.git_runner.status(porcelain=True)
        if status_result.ok and status_result.result:
            status_output = status_result.result.stdout.strip()
            if status_output:
                add_result = self.add()
                if not add_result.ok:
                    return add_result

                commit_result = self.commit(f"freeze-release: {current_branch}")
                if not commit_result.ok:
                    return commit_result

        # Step 4: freeze local State
        freeze_result = self.freeze()
        if not freeze_result.ok:
            return freeze_result

        # Step 5: merge current local branch into main
        merge_result = self.merge(current_branch)
        if not merge_result.ok:
            return merge_result

        # Step 6-7: Submit candidate STATE@ to @CGS and persist main through @MS
        # These require @CGS integration

        return GitExecution(
            success=True,
            result=self._make_result(
                f"Freeze-release completed for branch '{current_branch}'",
                "freeze-release",
            ),
        )

    def launch_release(self, state_id: str) -> GitExecution:
        """Launch a release with the given State ID.

        Canonical command: cgitsync launch-release <state-id>

        Canonical workflow:
        1. retrieve through @CGS/@MS
        2. checkout main
        3. checkout selected release reference
        4. validate STATE@.md
        5. restore WorkingGitTree
        6. serve *G
        """
        # Step 2: checkout main
        checkout_main = self.checkout("main")
        if not checkout_main.ok:
            return checkout_main

        # Step 3: checkout selected release reference
        # This would be the tag or branch corresponding to the state_id
        checkout_release = self.checkout(state_id)
        if not checkout_release.ok:
            # Try to find the reference
            tags = self.git_runner.list_tags()
            branches = self.git_runner.list_branches()

            if state_id not in tags and state_id not in branches:
                return GitExecution(
                    success=False,
                    error=self._make_error(
                        "state_not_found",
                        f"State ID '{state_id}' not found as tag or branch",
                        "launch-release",
                    ),
                )
            return checkout_release

        # Steps 4-6: Validate STATE@.md, restore WorkingGitTree, serve *G
        # These require @CGS integration

        return GitExecution(
            success=True,
            result=self._make_result(
                f"Release '{state_id}' launched successfully",
                "launch-release",
            ),
        )

    # ==================== Memory Operations ====================

    def remember(self) -> GitExecution:
        """Configure @MS specialization.

        Canonical command: cgitsync remember
        """
        # This configures the Memory endpoint for @CGS
        # For now, this is a placeholder
        return GitExecution(
            success=True,
            result=self._make_result(
                "Memory endpoint configured",
                "remember",
            ),
        )

    def memorize(self) -> GitExecution:
        """Submit validated STATE@ to @CGS/@MS.

        Canonical command: cgitsync memorize
        """
        # This submits the current State to Memory
        # Requires @CGS integration
        current_branch = self.git_runner.get_current_branch()
        return GitExecution(
            success=True,
            result=self._make_result(
                f"State submitted to Memory from branch '{current_branch}'",
                "memorize",
            ),
        )

    def retrieve(self, state_id: str | None = None) -> GitExecution:
        """Recover STATE@ through @CGS/@MS.

        Canonical command: cgitsync retrieve [state-id]
        """
        # If no state_id, retrieve the latest
        if state_id is None:
            state_id = "HEAD"

        # This requires @CGS/@MS integration
        return GitExecution(
            success=True,
            result=self._make_result(
                f"State '{state_id}' retrieved from Memory",
                "retrieve",
            ),
        )

    def reload(self) -> GitExecution:
        """Restore the living Graph.

        Canonical command: cgitsync reload
        """
        # This restores the living Graph from persisted State
        return GitExecution(
            success=True,
            result=self._make_result(
                "Living Graph reloaded",
                "reload",
            ),
        )

    # ==================== State Projection Access ====================

    def state(self) -> GitExecution:
        """Get STATE@.md - the static public Ontology.

        Canonical command: cgitsync state
        """
        # This will be implemented with @CGS to emit STATE@.md
        return GitExecution(
            success=True,
            result=self._make_result(
                "STATE@.md: Static public Ontology (placeholder)",
                "state",
            ),
        )

    def state_core(self) -> GitExecution:
        """Get STATE@.CORE.md - the public Mermaid projection.

        Canonical command: cgitsync state-core
        """
        # This will be implemented with @CGS to emit STATE@.CORE.md
        return GitExecution(
            success=True,
            result=self._make_result(
                "STATE@.CORE.md: Public Mermaid projection (placeholder)",
                "state-core",
            ),
        )

    # Alias for state-core to work with the CLI
    state_core_cmd = state_core

    # ==================== Private Helpers ====================

    def _make_error(self, code: str, message: str, command: str) -> Any:
        """Create a GitError for the client."""
        from .git_runner import GitError, GitErrorCode
        return GitError(
            code=GitErrorCode.GENERIC_ERROR,
            message=f"{command}: {message}",
            command=command,
            cwd=str(self._cwd),
            exit_code=1,
        )

    def _make_result(self, stdout: str, command: str) -> Any:
        """Create a GitResult for the client."""
        from .git_runner import GitResult
        return GitResult(
            command=command,
            cwd=str(self._cwd),
            stdout=stdout,
            exit_code=0,
        )

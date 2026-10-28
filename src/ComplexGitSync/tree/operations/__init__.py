"""Tier 2 synchronization operations for ComplexGitSync, one class per operation family.

Ring: 2
Contract: re-export the package surface unchanged.
Imports: ancestors, branch, commit, fetch, memory_merge, merge, outcome, preflight, push, removal, restart
"""

from __future__ import annotations

from .ancestors import (
    AncestorOperation,
    RepoAncestry,
)
from .branch import (
    BranchOperation,
    BranchTopologyConflict,
    BranchTopologyReport,
    RepoBranches,
)
from .commit import (
    CommitOperation,
)
from .fetch import (
    FetchOperation,
)
from .memory_merge import (
    MemoryMergeOperation,
    MemoryMergePlan,
)
from .merge import (
    MERGE_INTO_ACTS,
    MERGE_RESOLVE_HINT,
    MergeIntoPlan,
    MergeOperation,
    ResolveOutcome,
)
from .outcome import (
    RepoOutcome,
)
from .preflight import (
    Preflight,
    PreflightDiagnostic,
    PreflightSeverity,
)
from .push import (
    PushOperation,
)
from .removal import (
    RemovalOperation,
)
from .restart import (
    RestartOperation,
)

_collect_branch_alignment_diagnostics = Preflight._collect_branch_alignment_diagnostics
_describe_merge_conflict = MergeOperation.describe_merge_conflict
_path_is_relative_to = Preflight._path_is_relative_to
_refresh_repo_after_checkout = BranchOperation.refresh_repo_after_checkout
_restart_tree = RestartOperation._restart_tree
_status_line_path = Preflight._status_line_path
_status_line_targets_any = Preflight._status_line_targets_any
_unmanaged_gitlink_paths = Preflight._unmanaged_gitlink_paths
add_tree = CommitOperation.add_tree
branch_tree = BranchOperation.branch_tree
checkout_tree = BranchOperation.checkout_tree
close_branch = BranchOperation.close_branch
commit_tree = CommitOperation.commit_tree
create_global_branch = BranchOperation.create_global_branch
list_branches = BranchOperation.list_branches
fetch_tree = FetchOperation.fetch_tree
freeze_release_tree = PushOperation.freeze_release_tree
merge_into_status = MergeOperation.merge_into_status
merge_into_tree = MergeOperation.merge_into_tree
merge_source_ref = MergeOperation.merge_source_ref
merge_status = MergeOperation.merge_status
merge_tree = MergeOperation.merge_tree
merge_tree_one_at_a_time = MergeOperation.merge_tree_one_at_a_time
paths_outside_scope = RemovalOperation.paths_outside_scope
propagate_global_branch = BranchOperation.propagate_global_branch
push_tree = PushOperation.push_tree
refresh_private_tree = RestartOperation.refresh_private_tree
remove_paths = RemovalOperation.remove_paths
restart_tree = RestartOperation.restart_tree
restart_tree_force = RestartOperation.restart_tree_force
tag_tree = PushOperation.tag_tree
validate_branch_topology = BranchOperation.validate_branch_topology

__all__ = [
    "AncestorOperation",
    "RepoAncestry",
    "BranchOperation",
    "BranchTopologyConflict",
    "BranchTopologyReport",
    "CommitOperation",
    "MERGE_INTO_ACTS",
    "MERGE_RESOLVE_HINT",
    "MemoryMergeOperation",
    "MemoryMergePlan",
    "MergeIntoPlan",
    "MergeOperation",
    "Preflight",
    "PreflightDiagnostic",
    "PreflightSeverity",
    "PushOperation",
    "RemovalOperation",
    "RepoBranches",
    "RepoOutcome",
    "ResolveOutcome",
    "RestartOperation",
    "add_tree",
    "branch_tree",
    "checkout_tree",
    "close_branch",
    "commit_tree",
    "create_global_branch",
    "freeze_release_tree",
    "list_branches",
    "fetch_tree",
    "FetchOperation",
    "merge_into_status",
    "merge_into_tree",
    "merge_source_ref",
    "merge_status",
    "merge_tree",
    "merge_tree_one_at_a_time",
    "paths_outside_scope",
    "propagate_global_branch",
    "push_tree",
    "refresh_private_tree",
    "remove_paths",
    "restart_tree",
    "restart_tree_force",
    "tag_tree",
    "validate_branch_topology",
]

"""ComplexGitSync package: deterministic distributed workspace synchronization over Git trees."""

__version__ = "0001.50"

# --- Tier 1 — Public Errors (errors.py) ---
from .errors import (
    ArchitectureNotLoadedError,
    ComplexGitSyncError,
    ConfigValidationError,
    FallbackRejectedError,
    GitSyncError,
    NestedConfigDiscoveryError,
    TreeNotReadyError,
)

# --- Tier 1 — Core State (git_repo.py) ---
from .git_repo import (
    AccessProtocol,
    DiscoveryState,
    GitProvider,
    GitRepo,
    NodeType,
    RefKind,
    RepoAddress,
    RepoLifecycleState,
    RepoNode,
    RepoRegistryEntry,
    SyncState,
)

# --- Tier 1 — Core State + Tree Utilities (git_tree.py) ---
from .git_tree import (
    DependencyTreeRegistry,
    GitTree,
    ProjectTreeState,
    TreeLifecycleState,
    find_strongly_connected_components,
    fix_circularities,
    iter_tree,
    iter_tree_leaf_first,
    topological_sort,
)

# --- Tier 2 — Actions (operations.py) ---
from .operations import (
    BranchTopologyConflict,
    BranchTopologyReport,
    add_tree,
    checkout_tree,
    commit_tree,
    create_global_branch,
    freeze_release_tree,
    propagate_global_branch,
    push_tree,
    tag_tree,
    validate_branch_topology,
)

# --- Tier 2/3 — Actions + Client (orchestre.py) ---
from .orchestre import (
    CgsDocument,
    ComplexGitSyncClient,
    ConfigDocument,
    GitRunner,
    GocDocument,
    GtsDocument,
    LocalGitRegister,
    Orchestre,
    SyncLedger,
)

__all__ = [
    "__version__",
    # errors.py
    "ArchitectureNotLoadedError",
    "ComplexGitSyncError",
    "ConfigValidationError",
    "FallbackRejectedError",
    "GitSyncError",
    "NestedConfigDiscoveryError",
    "TreeNotReadyError",
    # git_repo.py
    "AccessProtocol",
    "DiscoveryState",
    "GitProvider",
    "GitRepo",
    "NodeType",
    "RefKind",
    "RepoAddress",
    "RepoLifecycleState",
    "RepoNode",
    "RepoRegistryEntry",
    "SyncState",
    # git_tree.py
    "DependencyTreeRegistry",
    "GitTree",
    "ProjectTreeState",
    "TreeLifecycleState",
    "find_strongly_connected_components",
    "fix_circularities",
    "iter_tree",
    "iter_tree_leaf_first",
    "topological_sort",
    # operations.py
    "checkout_tree",
    "add_tree",
    "BranchTopologyConflict",
    "BranchTopologyReport",
    "commit_tree",
    "create_global_branch",
    "freeze_release_tree",
    "propagate_global_branch",
    "push_tree",
    "tag_tree",
    "validate_branch_topology",
    # orchestre.py
    "CgsDocument",
    "ComplexGitSyncClient",
    "ConfigDocument",
    "GocDocument",
    "GtsDocument",
    "GitRunner",
    "LocalGitRegister",
    "Orchestre",
    "SyncLedger",
]

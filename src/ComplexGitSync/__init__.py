"""ComplexGitSync package: deterministic distributed workspace synchronization over Git trees."""

__version__ = "0002.06"

# --- Tier 1 — Public Errors (errors.py) ---
# --- Cross-cutting document definitions ---
from .cgs_format import (
    CgsDocument,
    normalize_cgs,
    parse_cgs,
    parse_repo_id,
    parse_repository_identifier,
)
from .config_document import ConfigDocument
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
    CANONICAL_GIT_PROVIDERS,
    KNOWN_PROVIDER_HOSTS,
    AccessProtocol,
    DiscoveryState,
    GitProvider,
    GitRepo,
    NodeType,
    RefKind,
    RepoAddress,
    RepoLifecycleState,
    RepoNode,
    SyncState,
    WorkingRepo,
    validate_git_provider,
)

# --- Tier 1 — Core State + Tree Utilities (git_tree.py) ---
from .git_tree import (
    ROOT_REPO_ID,
    GitTree,
    ProjectTreeState,
    TreeLifecycleState,
    WorkingGitTree,
    find_strongly_connected_components,
    fix_circularities,
    iter_tree,
    iter_tree_leaf_first,
    topological_sort,
)
from .master import MasterConfig

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
    restart_tree_force,
    tag_tree,
    validate_branch_topology,
)

# --- Tier 2/3 — Actions + Client (orchestre.py) ---
from .orchestre import (
    ComplexGitSyncClient,
    GitRunner,
    GocDocument,
    GtsDocument,
    LocalGitRegister,
    MemoryBinding,
    MemoryMemorizeResult,
    MemoryReloadResult,
    MemoryRememberResult,
    MemoryRetrieveResult,
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
    "MasterConfig",
    "NestedConfigDiscoveryError",
    "TreeNotReadyError",
    # git_repo.py
    "AccessProtocol",
    "CANONICAL_GIT_PROVIDERS",
    "DiscoveryState",
    "GitProvider",
    "GitRepo",
    "KNOWN_PROVIDER_HOSTS",
    "NodeType",
    "RefKind",
    "RepoAddress",
    "RepoLifecycleState",
    "RepoNode",
    "SyncState",
    "WorkingRepo",
    "validate_git_provider",
    # git_tree.py
    "GitTree",
    "ProjectTreeState",
    "ROOT_REPO_ID",
    "TreeLifecycleState",
    "WorkingGitTree",
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
    "restart_tree_force",
    "tag_tree",
    "validate_branch_topology",
    # cgs_format.py / config_document.py
    "CgsDocument",
    "ConfigDocument",
    "normalize_cgs",
    "parse_cgs",
    "parse_repo_id",
    "parse_repository_identifier",
    # orchestre.py
    "ComplexGitSyncClient",
    "GocDocument",
    "GtsDocument",
    "GitRunner",
    "LocalGitRegister",
    "MemoryBinding",
    "MemoryMemorizeResult",
    "MemoryRememberResult",
    "MemoryReloadResult",
    "MemoryRetrieveResult",
    "Orchestre",
    "SyncLedger",
]

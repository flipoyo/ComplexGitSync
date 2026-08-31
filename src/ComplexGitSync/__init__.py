"""ComplexGitSync package: deterministic distributed workspace synchronization over Git trees."""

__version__ = "0002.17"

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
    ComplexGitSyncError,
    ConfigValidationError,
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
    SyncState,
    WorkingRepo,
    validate_git_provider,
)
from .git_runner import GitRunner

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
from .gts_document import GtsDocument
from .integrity import Finding, VerificationReport
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
    LocalGitRegister,
    Orchestre,
    SyncLedger,
)

__all__ = [
    "__version__",
    # errors.py
    "ComplexGitSyncError",
    "ConfigValidationError",
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
    # integrity.py
    "Finding",
    "VerificationReport",
    # orchestre.py
    "ComplexGitSyncClient",
    "GtsDocument",
    "GitRunner",
    "LocalGitRegister",
    "Orchestre",
    "SyncLedger",
]

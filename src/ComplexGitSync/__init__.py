"""ComplexGitSync package."""

__version__ = "0000.03"

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
)

# --- Tier 2/3 — Actions + Client (orchestre.py) ---
from .orchestre import (
    CgsDocument,
    ComplexGitSyncClient,
    ConfigDocument,
    GitRunner,
    GocDocument,
    GtsDocument,
    Orchestre,
)

__all__ = [
    "__version__",
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
    # orchestre.py
    "CgsDocument",
    "ComplexGitSyncClient",
    "ConfigDocument",
    "GocDocument",
    "GtsDocument",
    "GitRunner",
    "Orchestre",
]

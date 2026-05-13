"""ComplexGitSync package."""

__version__ = "0000.02"

from .access_protocol import AccessProtocol
from .client import ComplexGitSyncClient
from .documents import CgsDocument, ConfigDocument, GocDocument, GtsDocument
from .git_provider import GitProvider
from .git_repo import GitRepo
from .git_tree import GitTree
from .orchestre import Orchestre
from .repo_address import RepoAddress
from .registry import (
    DependencyTreeRegistry,
    DiscoveryState,
    NodeType,
    ProjectTreeState,
    RepoLifecycleState,
    RepoNode,
    RepoRegistryEntry,
    SyncState,
    TreeLifecycleState,
)

__all__ = [
    "__version__",
    "AccessProtocol",
    "CgsDocument",
    "ComplexGitSyncClient",
    "ConfigDocument",
    "DependencyTreeRegistry",
    "DiscoveryState",
    "GitProvider",
    "GitRepo",
    "GitTree",
    "GocDocument",
    "GtsDocument",
    "NodeType",
    "Orchestre",
    "ProjectTreeState",
    "RepoAddress",
    "RepoLifecycleState",
    "RepoNode",
    "RepoRegistryEntry",
    "SyncState",
    "TreeLifecycleState",
]

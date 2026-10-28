"""git_repo — a repository and the graph it belongs to: the dependency gx4g will share.

Ring: 0 (the package itself; its modules say their own rings)
Contract: the identity of a repository (``GitRepo``, ``RepoAddress``, the
    lifecycle and sync states), the branch fallback chain and the privacy
    rule, the one ``subprocess`` runner and the clone guard. **Nothing in this
    folder imports anything else from ``ComplexGitSync``**, and a test holds it
    to that; it is the dependency, not a part.
Imports: none

Re-exports what the module ``git_repo`` held before ``SrcLayout``, so
``from ComplexGitSync.git_repo import GitRepo`` keeps working.
"""

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
    RepoScope,
    SyncState,
    WorkingRepo,
    convert_remote_url_protocol,
    repo_remote_url,
    validate_git_provider,
)

__all__ = [
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
    "RepoScope",
    "SyncState",
    "WorkingRepo",
    "convert_remote_url_protocol",
    "repo_remote_url",
    "validate_git_provider",
]

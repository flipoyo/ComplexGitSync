"""The ComplexGitSyncClient public facade and the collaborators it delegates to.

Ring: 3
Contract: re-export every name ``orchestre`` has always exported -- the client,
    its collaborators, and the helper functions callers import by their old
    names -- so no import elsewhere changed when the module became a package.
Imports: auth_hints, client, command_run_logger, git_probes, git_runner, git_tree, gts_document, memory, memory_facts, orchestre, registry, reports, runtime_state_store, universal_clock
"""

from __future__ import annotations

# Names other modules and callers have always imported from ``ComplexGitSync.orchestre``.
from ..git_runner import GitRunner
from ..git_tree import ROOT_REPO_ID
from ..gts_document import GtsDocument
from ..memory import SyncLedger
from ..registry import RegistryTranslator
from ..universal_clock import SystemClock
from .auth_hints import (
    AuthFailureHints,
)
from .client import (
    ComplexGitSyncClient,
)
from .command_run_logger import (
    CommandRunLogger,
)
from .git_probes import (
    GitProbes,
)
from .memory_facts import (
    MemoryFacts,
)
from .orchestre import (
    Orchestre,
)
from .reports import (
    DiscoveredRepo,
    DiscoverReport,
    GitignoreSyncEntry,
    InitFromSubmodulesReport,
)
from .runtime_state_store import (
    RuntimeStateStore,
)

_blocking_worktree_dirt = GitProbes.blocking_worktree_dirt
_is_dot_named_mount = GitProbes.is_dot_named_mount
_looks_like_https_auth_failure = AuthFailureHints._looks_like_https_auth_failure
_looks_like_ssh_auth_failure = AuthFailureHints.looks_like_ssh_auth_failure
_protocol_switch_hint = AuthFailureHints.protocol_switch_hint
_remote_url_for_identifier = MemoryFacts.remote_url_for_identifier
_scope_for = GitProbes.scope_for
_unmanaged_gitlink_paths = GitProbes.unmanaged_gitlink_paths
_url_to_repo_identifier = GitProbes.url_to_repo_identifier
_walk_git_repositories = GitProbes.walk_git_repositories
create_run_logger = CommandRunLogger.create_run_logger
resolve_command_scope = GitProbes.resolve_command_scope

__all__ = [
    "ROOT_REPO_ID",
    "GitRunner",
    "GtsDocument",
    "RegistryTranslator",
    "SyncLedger",
    "SystemClock",
    "AuthFailureHints",
    "CommandRunLogger",
    "ComplexGitSyncClient",
    "DiscoverReport",
    "DiscoveredRepo",
    "GitProbes",
    "GitignoreSyncEntry",
    "InitFromSubmodulesReport",
    "MemoryFacts",
    "Orchestre",
    "RuntimeStateStore",
    "create_run_logger",
    "resolve_command_scope",
]

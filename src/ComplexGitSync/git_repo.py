"""git_repo — core per-repository identity model for ComplexGitSync.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: define per-repository identity types, state enumerations, and
    remote-URL construction; parse nothing (textual repository-ID authoring
    syntax is parsed only by cgs_format.parse_repo_id).
Imports: none

This module is the **GitRepo anchor** — the authoritative source for all
per-repository identity types, state enumerations, and structural definitions.
It receives already-separated identity fields; textual repository-ID authoring
syntax is parsed only by :func:`ComplexGitSync.cgs_format.parse_repo_id`.

Classes / enums defined here (Tier 1 — Core State):
    AccessProtocol      SSH vs HTTPS transport selection
    GitProvider         Supported Git hosting providers
    NodeType            Position of a repo in the dependency tree (root/parent/leaf)
    RefKind             Kind of a Git reference (branch/tag/detached/…)
    RepoLifecycleState  Per-repo lifecycle progression
    SyncState           Synchronization status relative to the remote
    DiscoveryState      Nested .cgs discovery status
    GitRepo             Canonical reference identity of a single repository
    WorkingRepo         Mutable runtime repository used by WorkingGitTree
    RepoAddress         Derives the remote URL from a GitRepo
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AccessProtocol(StrEnum):
    """SSH vs HTTPS transport selection."""

    SSH = "ssh"
    HTTPS = "https"


class GitProvider(StrEnum):
    """Supported Git hosting providers."""

    GITHUB = "github"
    GITLAB = "gitlab"
    CODEBERG = "codeberg"
    CUSTOM = "custom"


KNOWN_PROVIDER_HOSTS: dict[GitProvider, str] = {
    GitProvider.GITHUB: "github.com",
    GitProvider.GITLAB: "gitlab.com",
    GitProvider.CODEBERG: "codeberg.org",
}
"""Canonical hosts for first-class providers with deterministic remotes."""

CANONICAL_GIT_PROVIDERS = frozenset(provider.value for provider in GitProvider)
"""Canonical provider names shared by format validation and Git behavior."""


def validate_git_provider(
    provider: Any,
    *,
    gitprovider_url: Any = None,
) -> GitProvider:
    """Validate an already-separated provider value and its host requirement.

    This function does not parse repository authoring syntax. It validates the
    canonical provider field shared by document and CLI workflows and enforces
    the rule that ``custom`` has no inferred host.
    """
    try:
        parsed = provider if isinstance(provider, GitProvider) else GitProvider(provider)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"gitprovider invalid: {provider!r} "
            f"(choose from: {sorted(CANONICAL_GIT_PROVIDERS)})"
        ) from exc

    if parsed == GitProvider.CUSTOM and (
        not isinstance(gitprovider_url, str) or not gitprovider_url.strip()
    ):
        raise ValueError("gitprovider_url is required for custom provider")
    return parsed


class NodeType(StrEnum):
    """Role of a repository node within the dependency tree."""

    ROOT = "root"
    PARENT = "parent"
    LEAF = "leaf"


class RefKind(StrEnum):
    """Kind of a Git reference (branch, tag, detached HEAD, etc.)."""

    AUTO = "auto"
    BRANCH = "branch"
    TAG = "tag"
    DETACHED = "detached"
    UNKNOWN = "unknown"


class RepoLifecycleState(StrEnum):
    """Lifecycle state of a single repository entry."""

    DECLARED = "DECLARED"
    PENDING = "PENDING"
    READY = "READY"
    FALLBACK_READY = "FALLBACK_READY"
    MISSING = "MISSING"
    ERROR = "ERROR"


class SyncState(StrEnum):
    """Synchronization state of a single repository relative to its remote."""

    ALIGNED = "ALIGNED"
    FALLBACK_APPLIED = "FALLBACK_APPLIED"
    DETACHED_EXACT = "DETACHED_EXACT"
    DIRTY = "DIRTY"
    AHEAD = "AHEAD"
    BEHIND = "BEHIND"
    DIVERGED = "DIVERGED"
    ERROR = "ERROR"
    PENDING = "PENDING"


class DiscoveryState(StrEnum):
    """State of nested ``.cgs`` discovery for a repository entry."""

    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    DISABLED = "DISABLED"
    MISSING = "MISSING"


# ---------------------------------------------------------------------------
# GitRepo — core per-repo identity
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GitRepo:
    """Canonical reference identity of a single repository.

    Captures the provider, namespace, project name, access protocol, and
    optionally a resolved commit SHA. Runtime tree and synchronization state
    lives in :class:`WorkingRepo`. Construction is side-effect free: remote
    inspection is performed explicitly by the runtime Git layer, never by this
    dataclass.
    """

    project_owner_name: str
    project_name: str
    repo_name: str | None = None
    gitprovider: GitProvider = GitProvider.GITHUB
    group_name: str | None = None
    gitprovider_url: str | None = None
    access_protocol: AccessProtocol = AccessProtocol.SSH
    commit_sha: str | None = None

    @property
    def resolved_group_name(self) -> str:
        return self.group_name or self.project_name

# ---------------------------------------------------------------------------
# RepoAddress — URL derivation from GitRepo
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RepoAddress:
    """Encapsulates the identity fields required to compose a Git remote URL.

    Build an instance directly or use :meth:`from_repo` to derive one from a
    :class:`GitRepo`.

    Building methods
    ~~~~~~~~~~~~~~~~
    * :meth:`to_ssh`  – return ``git@host:namespace/repo_name.git``
    * :meth:`to_https` – return ``https://host/namespace/repo_name.git``
    * :meth:`to_url`  – return the URL for an explicit :class:`AccessProtocol`
    * :meth:`from_repo` – class-method factory from a :class:`GitRepo`
    """

    gitprovider: GitProvider
    project_name: str
    repo_name: str | None = None
    project_owner_name: str | None = None
    group_name: str | None = None
    gitprovider_url: str | None = None

    @classmethod
    def from_repo(cls, repo: GitRepo) -> RepoAddress:
        """Derive a :class:`RepoAddress` from an existing :class:`GitRepo`."""
        return cls(
            gitprovider=repo.gitprovider,
            project_name=repo.project_name,
            repo_name=repo.repo_name,
            project_owner_name=repo.project_owner_name,
            group_name=repo.group_name,
            gitprovider_url=repo.gitprovider_url,
        )

    def _resolve_host(self) -> str:
        if self.gitprovider_url:
            base = str(self.gitprovider_url).strip()
            parsed = urlsplit(base if "://" in base else f"https://{base}")
            host = parsed.netloc or parsed.path.strip("/").split("/", 1)[0]
            if host:
                return host
            if self.gitprovider == GitProvider.CUSTOM:
                raise ValueError(
                    f"Could not extract a host from gitprovider_url: {self.gitprovider_url!r}"
                )
        if self.gitprovider == GitProvider.CUSTOM:
            raise ValueError(
                "gitprovider_url is required for custom provider addresses."
            )
        try:
            return KNOWN_PROVIDER_HOSTS[self.gitprovider]
        except KeyError as exc:
            raise ValueError(
                f"No canonical host is registered for provider {self.gitprovider!s}."
            ) from exc

    def _resolve_namespace(self) -> str:
        if self.gitprovider == GitProvider.GITLAB:
            namespace = self.group_name or self.project_owner_name
            if not namespace:
                raise ValueError(
                    "group_name or project_owner_name is required for GitLab addresses."
                )
            return namespace
        if self.gitprovider in {GitProvider.GITHUB, GitProvider.CODEBERG}:
            if not self.project_owner_name:
                raise ValueError(
                    f"project_owner_name is required for {self.gitprovider.value} addresses."
                )
            return self.project_owner_name
        ns = self.group_name or self.project_owner_name
        if not ns:
            raise ValueError(
                "group_name or project_owner_name is required for custom provider addresses."
            )
        return ns

    def _resolve_repo_name(self) -> str:
        name = self.repo_name or self.project_name
        if not name:
            raise ValueError("repo_name or project_name is required for repository addresses.")
        return name

    def validate(self) -> None:
        """Validate canonical provider, host, namespace, and repository fields."""
        self._resolve_host()
        self._resolve_namespace()
        self._resolve_repo_name()

    def to_ssh(self) -> str:
        """Return the SSH remote URL (``git@host:namespace/repo_name.git``)."""
        host = self._resolve_host()
        namespace = self._resolve_namespace()
        repo_name = self._resolve_repo_name()
        return f"git@{host}:{namespace}/{repo_name}.git"

    def to_https(self) -> str:
        """Return the HTTPS remote URL (``https://host/namespace/repo_name.git``)."""
        host = self._resolve_host()
        namespace = self._resolve_namespace()
        repo_name = self._resolve_repo_name()
        return f"https://{host}/{namespace}/{repo_name}.git"

    def to_url(self, protocol: AccessProtocol = AccessProtocol.SSH) -> str:
        """Return the remote URL for the given *protocol*.

        Delegates to :meth:`to_ssh` or :meth:`to_https` based on *protocol*.
        """
        if protocol == AccessProtocol.SSH:
            return self.to_ssh()
        return self.to_https()


_SSH_REMOTE_RE = re.compile(r"^(?P<user>[^@\s]+)@(?P<host>[^:\s]+):(?P<path>.+)$")


def convert_remote_url_protocol(url: str, protocol: AccessProtocol) -> str:
    """Rewrite *url* to use *protocol*, preserving its host and path exactly.

    Unlike :func:`repo_remote_url` below, which derives a URL from a
    repository's stored identity fields, this reads an existing remote URL
    and only swaps its scheme — ``ssh`` (``user@host:path``) to/from
    ``https``/``http`` (``scheme://host/path``). Nothing about host or
    path is inferred, so this cannot rebuild the wrong address the way
    deriving one from identity can when that identity is stale, missing,
    or was never fully known — see
    ``AgentSpec/archive/20260904_GtsProviderLoss_DevPlanTicket.md``, where
    a ``.gts`` snapshot with no recorded provider caused exactly that.

    A URL already in the requested form is returned unchanged (idempotent
    no-op) rather than reassembled, so an unusual but valid existing URL
    (a non-``git`` SSH user, an unconventional path) survives untouched.
    Raises :exc:`ValueError` for a URL in neither recognised form — this
    function parses transport shape only, not provider identity; it is not
    the repository-ID authoring grammar this module's contract reserves
    for :func:`~ComplexGitSync.cgs_format.parse_repo_id`.
    """
    stripped = url.strip()
    has_git_suffix = stripped.endswith(".git")
    body = stripped[:-4] if has_git_suffix else stripped

    ssh_match = _SSH_REMOTE_RE.match(body)
    if ssh_match:
        if protocol == AccessProtocol.SSH:
            return stripped
        return f"https://{ssh_match.group('host')}/{ssh_match.group('path')}.git"

    parsed = urlsplit(body)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        if protocol == AccessProtocol.HTTPS:
            return stripped
        path = parsed.path.strip("/")
        return f"git@{parsed.netloc}:{path}.git"

    raise ValueError(f"Unrecognised remote URL, cannot convert protocol: {url!r}")


# ---------------------------------------------------------------------------
# WorkingRepo — mutable runtime record per repo
# ---------------------------------------------------------------------------


@dataclass
class WorkingRepo(GitRepo):
    """Mutable runtime repository within a :class:`WorkingGitTree`.

    Identity fields are inherited from :class:`GitRepo`; the fields below track
    tree position and live synchronisation state observed during operations.
    """

    project_owner_name: str | None = None
    project_name: str | None = None
    repo_id: str = ""
    name: str = ""
    node_type: NodeType = NodeType.LEAF
    parent_id: str | None = None
    absolute_path: Path = Path(".")
    relative_path: Path | None = None
    source_cgs_path: Path | None = None
    current_ref_kind: RefKind | None = None
    current_ref_name: str | None = None
    target_ref_kind: RefKind | None = None
    target_ref_name: str | None = None
    resolved_ref_kind: RefKind | None = None
    resolved_ref_name: str | None = None
    commit_sha: str | None = None
    repo_lifecycle_state: RepoLifecycleState = RepoLifecycleState.DECLARED
    sync_state: SyncState = SyncState.PENDING
    discovery_state: DiscoveryState = DiscoveryState.PENDING
    fallback_branch: str | None = None
    fallback_applied: bool = False
    fallback_reason: str | None = None
    worktree_state: str | None = None
    is_reachable: bool = True
    gitprovider: GitProvider = GitProvider.GITHUB
    # False only for an entry read from a .gts written before the provider
    # was recorded in the snapshot -- gitprovider above is then a filled-in
    # default (GITHUB), not a known fact. See repo_remote_url's docstring.
    gitprovider_declared: bool = True
    repo_name: str | None = None
    group_name: str | None = None
    gitprovider_url: str | None = None
    access_protocol: AccessProtocol = AccessProtocol.SSH
    default_branch: str | None = None
    nested_config: str | None = None
    pinned: bool = False
    remote_name: str | None = None
    is_external_reference: bool = False


def repo_remote_url(repo: WorkingRepo, protocol: AccessProtocol) -> str:
    """Build *repo*'s remote URL for *protocol*.

    The same :class:`RepoAddress` construction
    :meth:`ComplexGitSyncClient._build_remote_url` uses for cloning,
    exposed as a free function so ``operations.py`` (which has no client
    to call a method on) can compute a repo's URL under a *different*
    protocol than whatever its `origin` remote is currently configured
    to — used by ``push``/``pull``/``pull-force``'s ``--force-protocol``
    to rewrite an already-cloned repo's remote in place.
    """
    address = RepoAddress(
        gitprovider=repo.gitprovider,
        project_name=repo.project_name or repo.name,
        repo_name=repo.repo_name or repo.project_name or repo.name,
        project_owner_name=repo.project_owner_name,
        group_name=repo.group_name,
        gitprovider_url=repo.gitprovider_url,
    )
    return address.to_url(protocol)

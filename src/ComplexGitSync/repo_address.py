from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from .access_protocol import AccessProtocol
from .git_provider import GitProvider
from .git_repo import GitRepo


@dataclass(slots=True)
class RepoAddress:
    """Encapsulates the identity fields required to compose a Git remote URL.

    Build an instance directly or use :meth:`from_repo` to derive one from a
    :class:`GitRepo`.

    Building methods
    ~~~~~~~~~~~~~~~~
    * :meth:`to_ssh`  – return ``git@host:namespace/project_name.git``
    * :meth:`to_https` – return ``https://host/namespace/project_name.git``
    * :meth:`to_url`  – return the URL for an explicit :class:`AccessProtocol`
    * :meth:`from_repo` – class-method factory from a :class:`GitRepo`
    """

    gitprovider: GitProvider
    project_name: str
    project_owner_name: str | None = None
    group_name: str | None = None
    gitprovider_url: str | None = None

    # ------------------------------------------------------------------
    # Class-method factories
    # ------------------------------------------------------------------

    @classmethod
    def from_repo(cls, repo: GitRepo) -> RepoAddress:
        """Derive a :class:`RepoAddress` from an existing :class:`GitRepo`."""
        return cls(
            gitprovider=repo.gitprovider,
            project_name=repo.project_name,
            project_owner_name=repo.project_owner_name,
            group_name=repo.group_name,
            gitprovider_url=repo.gitprovider_url,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
        if self.gitprovider == GitProvider.GITLAB:
            return "gitlab.com"
        return "github.com"

    def _resolve_namespace(self) -> str:
        if self.gitprovider == GitProvider.GITLAB:
            return self.group_name or self.project_name
        if self.gitprovider == GitProvider.GITHUB:
            if not self.project_owner_name:
                raise ValueError(
                    "project_owner_name is required for GitHub addresses."
                )
            return self.project_owner_name
        # GitProvider.CUSTOM: prefer group_name, fall back to project_owner_name
        ns = self.group_name or self.project_owner_name
        if not ns:
            raise ValueError(
                "group_name or project_owner_name is required for custom provider addresses."
            )
        return ns

    # ------------------------------------------------------------------
    # Building methods
    # ------------------------------------------------------------------

    def to_ssh(self) -> str:
        """Return the SSH remote URL (``git@host:namespace/project_name.git``)."""
        host = self._resolve_host()
        namespace = self._resolve_namespace()
        return f"git@{host}:{namespace}/{self.project_name}.git"

    def to_https(self) -> str:
        """Return the HTTPS remote URL (``https://host/namespace/project_name.git``)."""
        host = self._resolve_host()
        namespace = self._resolve_namespace()
        return f"https://{host}/{namespace}/{self.project_name}.git"

    def to_url(self, protocol: AccessProtocol = AccessProtocol.SSH) -> str:
        """Return the remote URL for the given *protocol*.

        Delegates to :meth:`to_ssh` or :meth:`to_https` based on *protocol*.
        """
        if protocol == AccessProtocol.SSH:
            return self.to_ssh()
        return self.to_https()

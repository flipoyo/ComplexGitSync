from __future__ import annotations

from dataclasses import dataclass

from .access_protocol import AccessProtocol
from .git_provider import GitProvider


@dataclass(slots=True)
class GitRepo:
    project_owner_name: str
    project_name: str
    gitprovider: GitProvider = GitProvider.GITHUB
    group_name: str | None = None
    gitprovider_url: str | None = None
    access_protocol: AccessProtocol = AccessProtocol.SSH
    commit_sha: str | None = None

    @property
    def resolved_group_name(self) -> str:
        return self.group_name or self.project_name

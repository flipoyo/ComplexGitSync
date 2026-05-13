from __future__ import annotations

from enum import StrEnum


class GitProvider(StrEnum):
    GITHUB = "github"
    GITLAB = "gitlab"
    CUSTOM = "custom"

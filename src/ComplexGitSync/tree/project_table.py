"""project_table — what a ``.cgs`` project table may say.

Ring: 0 (pure — checks a dict, reads nothing)
Contract: name the problems of a ``project`` table's keys and of its ``anonymous`` option (AnonymousProject §2, §4.1),
    as the validation errors ``cgs_format`` collects. Imports: git_repo
"""

from __future__ import annotations

from typing import Any

from ..git_repo.project_alias import ProjectAlias

#: The keys a ``.cgs`` project table may carry. An older cgitsync ignores a key it does not know, which for
#: ``anonymous`` would write the real name into every shared repository (AnonymousProject §4.1), so an unknown key
#: is refused rather than ignored.
PROJECT_TABLE_KEYS = frozenset({"name", "default_branch", "fallback_branch", "default_remote_name", "anonymous"})


def project_table_problems(project: dict[str, Any]) -> list[str]:
    """What is wrong with a project table's keys and with its ``anonymous`` option, as validation errors."""
    problems = [
        f"unknown key in 'project': {key!r} (known: {', '.join(sorted(PROJECT_TABLE_KEYS))})"
        for key in sorted(set(project) - PROJECT_TABLE_KEYS)
    ]
    mode = project.get("anonymous")
    if "anonymous" in project and mode not in ProjectAlias.MODES:
        problems.append(f"project.anonymous must be one of {', '.join(map(repr, ProjectAlias.MODES))}, not {mode!r}")
    name = project.get("name")
    if "anonymous" in project and isinstance(name, str) and ProjectAlias.looks_like_alias(name):
        problems.append(
            f"project name {name!r} has the shape of an alias (anonymous-<i>, or six hex digits with an optional -<i>): "
            "an anonymous project cannot be called by something that looks like another project's alias"
        )
    return problems


__all__ = ["PROJECT_TABLE_KEYS", "project_table_problems"]

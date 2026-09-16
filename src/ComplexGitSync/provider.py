"""provider — creating a repository on a host, without holding a credential.

Ring: 0 (pure — decides what to run, runs nothing)
Contract: given a repository identifier, say which command-line tool creates
    it and with which arguments. Running that tool is `git_runner.run_tool`'s
    job, as running Git is `git_runner`'s job for everything else.
Imports: git_repo

The rule this module amends
---------------------------
ComplexGitSync used to say it never creates a repository on a host, because
doing so "would mean a network call and a stored credential where there is
neither". The second half was the real reason, and it still holds:

    **This project stores no credential and implements no provider's API.**
    When a repository must be created, it runs the provider's own
    command-line tool, which the user has already signed in to.

`gh`, `glab` and `tea` each hold their own token, in their own configuration,
under their own `auth login`. Nothing here reads one, and nothing here sends
one. The tool that already owns the credential makes the call.

When the tool is not installed, or is installed and not signed in, the
answer is the same one the tool gave before this module existed: print the
command, and let the user run it. That was never wrong — only incomplete.
"""

from __future__ import annotations

from dataclasses import dataclass

from .git_repo import GitProvider

#: Which command-line tool speaks for each provider. A provider with no
#: entry here has no creation command, and the caller says so by name rather
#: than guessing at one.
PROVIDER_TOOLS: dict[str, str] = {
    GitProvider.GITHUB.value: "gh",
    GitProvider.GITLAB.value: "glab",
    GitProvider.CODEBERG.value: "tea",
}

#: How a user signs the tool in. Printed when it is installed but has no
#: credentials, because "not logged in" and "not installed" need different
#: fixes and a single message covering both helps nobody.
SIGN_IN_COMMANDS: dict[str, str] = {
    "gh": "gh auth login",
    "glab": "glab auth login",
    "tea": "tea login add",
}


@dataclass(frozen=True, slots=True)
class CreationPlan:
    """The one command that creates a repository, ready to run or to print."""

    tool: str
    argv: tuple[str, ...]
    sign_in: str

    @property
    def command(self) -> str:
        """The plan as a line a user can paste into a shell."""
        return " ".join([self.tool, *self.argv])


def creation_plan(
    identity: dict[str, str],
    *,
    private: bool = True,
    description: str | None = None,
) -> CreationPlan | None:
    """How to create the repository *identity* names, or ``None``.

    *identity* is what :func:`~ComplexGitSync.cgs_format.parse_repo_id`
    returns — the only parser of a repository identifier in this project, so
    the owner or group comes from the same place here as everywhere else.
    ``github:flipoyo/.memory`` is owner ``flipoyo``; ``gitlab:some/group/x``
    is group ``some/group``. There is no second spelling to learn.

    ``None`` means this provider has no tool, which the caller reports by
    name — inventing a command for a host nobody can name is worse than
    saying so.
    """
    provider = identity.get("gitprovider", "")
    owner = identity.get("project_owner_name", "")
    name = identity.get("repo_name") or identity.get("project_name") or ""
    tool = PROVIDER_TOOLS.get(provider)
    if not tool or not owner or not name:
        return None

    visibility = "--private" if private else "--public"
    if tool == "tea":
        # Gitea's CLI takes the owner as an option rather than a path, and
        # spells visibility the other way round: it asks whether the
        # repository is private, not which of the two it is.
        argv: list[str] = ["repo", "create", "--name", name, "--owner", owner]
        if private:
            argv.append("--private")
        if description:
            argv += ["--description", description]
    else:
        argv = ["repo", "create", f"{owner}/{name}", visibility]
        if description:
            argv += ["--description", description]

    return CreationPlan(tool=tool, argv=tuple(argv), sign_in=SIGN_IN_COMMANDS.get(tool, ""))


def looks_like_already_exists(message: str) -> bool:
    """Whether a tool refused because the repository is already there.

    Creating what already exists is the normal state of the first step for
    anybody who did it by hand, so it is reported as "already there" and not
    as a failure. The three tools word it differently and none of them gives
    a distinct exit code, so the wording is what there is to read.
    """
    lowered = message.lower()
    return any(
        phrase in lowered
        for phrase in (
            "already exists",
            "already taken",
            "name already",
            "repository creation failed. some common reasons",
        )
    )


def looks_like_not_signed_in(message: str) -> bool:
    """Whether a tool refused because nobody has signed it in."""
    lowered = message.lower()
    return any(
        phrase in lowered
        for phrase in (
            "not logged in",
            "not logged into",
            "authentication required",
            "auth login",
            "no token",
            "unauthorized",
            "requires authentication",
        )
    )


__all__ = [
    "PROVIDER_TOOLS",
    "SIGN_IN_COMMANDS",
    "CreationPlan",
    "creation_plan",
    "looks_like_already_exists",
    "looks_like_not_signed_in",
]

"""auth_hints — Recognise an authentication failure in Git's prose and suggest the other protocol.

Ring: 3
Contract: Recognise an authentication failure in Git's prose and suggest the other protocol.
Imports: none
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# Each marker records who writes it, because that decides whether it survives
# a non-English machine. OpenSSH ships no translations, so anything ssh prints
# is English everywhere; git translates its own prose, so a git-worded marker
# matches only because git_runner.py pins the message locale
# (.agent/.local/.localSpec/DevTickets/archive/20260911_GitLocaleIndependence_DevPlanTicket.md).
_SSH_AUTH_FAILURE_MARKERS = (
    # OpenSSH's own wording — locale-proof.
    "Permission denied (publickey)",
    "Host key verification failed",
    # Git's own wording. Verified 2026-09-11 against an unreachable ssh remote:
    # French renders it "Impossible de lire le depot distant." and matched
    # nothing until the locale pin landed.
    "Could not read from remote repository",
)

_HTTPS_AUTH_FAILURE_MARKERS = (
    # GitLab, verified firsthand against the live cawaqsviz remote
    # (ProtocolSwitchOnPush_DevPlanTicket §0.4) — a real captured response,
    # not guessed wording.
    "HTTP Basic: Access denied",
    # cgitsync's own signature for "a credential was needed and the ambient
    # environment had none to offer" (GIT_TERMINAL_PROMPT=0 / GIT_ASKPASS —
    # see git_runner.py's _non_interactive_git_env) — provider-agnostic,
    # fires for any HTTPS remote regardless of host.
    "could not read Username",
    "terminal prompts disabled",
    # GitHub, verified firsthand 2026-09-11 against a real HTTPS fetch of a
    # repository the ambient credentials cannot read. Server-sent, so GitHub
    # writes it in English whatever the machine's locale is — the sturdiest
    # kind of marker there is, and the reason to prefer these where a
    # provider offers one.
    "Invalid username or token",
    # Git's own wording for the same failure, measured in the same run:
    # French renders it "Echec d'authentification pour '...'". Neither this
    # line nor the one above matched before, so a GitHub HTTPS auth failure
    # produced no hint at all, in any language. This one now matches because
    # the message locale is pinned; the one above would match regardless.
    "Authentication failed for",
    # Codeberg's own wording is still unverified — ProtocolSwitchOnPush
    # §1.3: ship what's confirmed, never guess. A missed match here just
    # degrades to the plain GitSyncError, same as an unmatched SSH failure.
)


class AuthFailureHints:
    """Recognise an authentication failure in Git's prose and suggest the other protocol."""

    @staticmethod
    def looks_like_ssh_auth_failure(git_error_message: str) -> bool:
        """Heuristic match on stderr for a likely SSH auth failure.

        No wording here is a stable API, so a missed match just degrades to the
        plain :class:`~.errors.GitSyncError` from before this hint existed.
        """
        return any(marker in git_error_message for marker in _SSH_AUTH_FAILURE_MARKERS)

    @staticmethod
    def _looks_like_https_auth_failure(git_error_message: str) -> bool:
        """The same, for HTTPS; see :func:`_looks_like_ssh_auth_failure`."""
        return any(marker in git_error_message for marker in _HTTPS_AUTH_FAILURE_MARKERS)

    @staticmethod
    def protocol_switch_hint(git_error_message: str, *, command: str) -> str | None:
        """Return an actionable ``--force-protocol`` hint for *git_error_message*,
        or ``None`` when it matches neither known failure shape.

        Reads which failure shape matched rather than trusting any repo's
        recorded ``access_protocol`` — that value can be stale or simply
        unknown (an *adopted*, not cloned, root's remote is whatever the user
        set it to outside cgitsync entirely; see
        ProtocolSwitchOnPush_DevPlanTicket §0.2). Suggests the opposite of
        whichever marker set matched, never both.
        """
        if AuthFailureHints.looks_like_ssh_auth_failure(git_error_message):
            return (
                f"hint: this looks like an SSH authentication failure — pass "
                f"--force-protocol https to '{command}' if the repository is "
                f"public, or configure an SSH key/agent for this runner "
                f"otherwise."
            )
        if AuthFailureHints._looks_like_https_auth_failure(git_error_message):
            return (
                f"hint: this looks like an HTTPS authentication failure — pass "
                f"--force-protocol ssh to '{command}' if you have an SSH key "
                f"registered with the provider, or configure an HTTPS "
                f"credential helper otherwise."
            )
        return None


__all__ = [
    "AuthFailureHints",
]

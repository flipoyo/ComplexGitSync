"""git_branch — the one owner of "which branch does this repository target?".

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: given already-read declared fields (a ``.cgs``/``.gts`` repository
    mapping, or a live ``WorkingRepo``), return the branch or tag the
    repository targets together with the reason that branch was chosen.
    Decides nothing about the filesystem, the remote, or the tree's shape.
Imports: git_repo

Why this module exists
----------------------
Four ``.cgs`` fields decide a branch and three of them fall back to each
other:

    repos[].fallback_branch -> repos[].default_branch
                            -> project.default_branch
                            -> DEFAULT_BRANCH ("main")

Before this module, that chain was written out by hand in six places across
five modules, and not one of them read :data:`DEFAULT_BRANCH` — each spelled
the literal ``"main"`` again and each stopped at a different link, so
changing the constant moved only one of them. ``CLAUDE.md`` already forbids
a second repository-identifier parser (``parse_repo_id`` is the only one);
this module is that same rule applied to branches.

It is a **resolver, not a registry**. It holds no tree, no root and no
pinning state — ``git_tree.py`` owns those. Callers hand it the declared
fields and it hands back a :class:`BranchResolution`.

The public surface
------------------
    DEFAULT_BRANCH            The branch a ``.cgs`` that names none resolves to
    BranchSource              Which link of the chain supplied the answer
    BranchResolution          The answer: name, kind, source, and why
    apply_declared_defaults   Fill one entry's declared branch fields in place
    resolve_declared_ref      Target ref of one repository entry in a document
    resolve_entry_ref         Target ref of a live WorkingRepo
    resolve_propagated_ref    Target ref under a tree-wide branch move (pinning)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from .git_repo import RefKind

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

    from .git_repo import WorkingRepo

#: The branch a ``.cgs`` resolves to when neither the repository entry nor
#: the ``[project]`` table names one. This is the *language* default, for
#: other people's files; every ``.cgs`` this repository owns states its own
#: ``project.default_branch`` explicitly instead (see the test in
#: ``tests/unit/test_install_cgs.py``).
DEFAULT_BRANCH = "main"


class BranchSource(StrEnum):
    """Which link of the fallback chain supplied a resolved branch."""

    TAG = "tag"
    """The entry declares a tag, so no branch was consulted at all."""

    REPO_BRANCH = "repo_branch"
    """``repos[].branch`` — an explicit per-repository branch."""

    REPO_DEFAULT = "repo_default"
    """``repos[].default_branch``."""

    PROJECT_DEFAULT = "project_default"
    """``project.default_branch`` — inherited by the repository."""

    BUILTIN_DEFAULT = "builtin_default"
    """:data:`DEFAULT_BRANCH` — nothing in the document named a branch."""

    PINNED = "pinned"
    """The repository is shared with other projects, so a tree-wide branch
    move left it on its own branch."""

    OBSERVED = "observed"
    """Read from the repository as it currently sits on disk."""

    ENTRY_RESOLVED = "entry_resolved"
    """The ref the entry last landed on (``resolved_ref_name``)."""

    ENTRY_TARGET = "entry_target"
    """The ref the entry aims at (``target_ref_name``)."""

    FALLBACK = "fallback"
    """The declared ``fallback_branch`` was used because the target branch
    was not available."""


_SOURCE_REASONS: dict[BranchSource, str] = {
    BranchSource.TAG: "entry declares a tag",
    BranchSource.REPO_BRANCH: "repository entry declares 'branch'",
    BranchSource.REPO_DEFAULT: "repository entry declares 'default_branch'",
    BranchSource.PROJECT_DEFAULT: "inherited from 'project.default_branch'",
    BranchSource.BUILTIN_DEFAULT: (
        f"no branch declared anywhere; fell back to the built-in default "
        f"'{DEFAULT_BRANCH}'"
    ),
    BranchSource.PINNED: "repository is pinned, so the tree-wide branch move skipped it",
    BranchSource.OBSERVED: "read from the repository on disk",
    BranchSource.ENTRY_RESOLVED: "the ref this repository last landed on",
    BranchSource.ENTRY_TARGET: "the ref this repository targets",
    BranchSource.FALLBACK: "target branch unavailable; used the declared fallback",
}


@dataclass(frozen=True, slots=True)
class BranchResolution:
    """One resolved ref, and the reason it was chosen.

    *kind* is ``None`` only when the caller asked to keep whatever kind the
    entry already carried — :func:`resolve_propagated_ref` does that for a
    pinned repository, which must not have its tag/branch kind rewritten by
    a tree-wide branch move.
    """

    name: str | None
    kind: RefKind | None
    source: BranchSource
    fallback_applied: bool = False

    @property
    def reason(self) -> str:
        """Why this ref was chosen, in one sentence fit for a log line."""
        return _SOURCE_REASONS[self.source]

    @property
    def is_default(self) -> bool:
        """True when nothing in the document named this branch."""
        return self.source is BranchSource.BUILTIN_DEFAULT

    def apply_to(self, entry: WorkingRepo) -> None:
        """Record this resolution as *entry*'s target ref.

        Writes ``target_ref_name``, ``target_ref_kind`` (only when this
        resolution carries one) and the ``fallback_applied``/
        ``fallback_reason`` pair, so the reason a branch was chosen is
        recorded once here rather than recomputed at each reader.
        """
        entry.target_ref_name = self.name
        if self.kind is not None:
            entry.target_ref_kind = self.kind
        entry.fallback_applied = self.fallback_applied
        entry.fallback_reason = self.reason if self.fallback_applied else None

    @classmethod
    def from_landed_ref(
        cls,
        entry: WorkingRepo,
        landed_name: str,
        landed_kind: RefKind,
        *,
        requested_name: str | None = None,
    ) -> BranchResolution:
        """Describe the ref a repository actually landed on after a clone.

        *landed_name* is what the working tree ended up on; *requested_name*
        is the ref the clone asked for, used only when the entry declares no
        target of its own. A mismatch between the two means the declared
        ``fallback_branch`` (or the remote's own default) took over, which is
        what ``fallback_applied`` records.

        The comparison itself is pure — the caller does the Git work and
        reports the outcome here rather than this module reaching for a
        remote.
        """
        expected = entry.target_ref_name or requested_name or landed_name
        applied = landed_name != expected
        return cls(
            name=landed_name,
            kind=landed_kind,
            source=BranchSource.FALLBACK if applied else BranchSource.ENTRY_TARGET,
            fallback_applied=applied,
        )

    def fallback_detail(self, entry: WorkingRepo) -> str | None:
        """The long-form fallback message, naming both refs, or ``None``.

        Kept separate from :attr:`reason` because a log line wants the two
        branch names in it and a status table does not.
        """
        if not self.fallback_applied:
            return None
        return (
            f"branch '{entry.target_ref_name}' not found on remote; "
            f"cloned '{self.name}' instead"
        )


def apply_declared_defaults(repo: MutableMapping[str, Any], project_default: str) -> None:
    """Fill *repo*'s ``default_branch`` and ``fallback_branch`` in place.

    This is the *authoring-time* half of the chain, applied by
    :func:`ComplexGitSync.cgs_format.normalize_cgs` so that a normalized
    document carries no implicit branch anywhere: after this runs, every
    repository entry states in full both the branch it targets and the
    branch it falls back to.

    ``repos[].default_branch`` defaults to *project_default* (itself
    defaulted to :data:`DEFAULT_BRANCH` by the caller), and
    ``repos[].fallback_branch`` defaults to whatever ``default_branch``
    just resolved to — the second and third links of the chain.
    """
    default_branch = str(repo.get("default_branch") or project_default or DEFAULT_BRANCH)
    repo["default_branch"] = default_branch
    repo["fallback_branch"] = str(repo.get("fallback_branch") or default_branch)


def resolve_declared_ref(
    repo: Mapping[str, Any],
    *,
    document_default_branch: str | None,
) -> BranchResolution:
    """Resolve the ref one repository entry of a document targets.

    *repo* is a repository mapping straight out of a ``.cgs`` or ``.gts``
    document — not a :class:`~ComplexGitSync.git_repo.WorkingRepo`. A tag
    wins outright; otherwise the branch chain runs
    ``branch`` → ``default_branch`` → *document_default_branch* →
    :data:`DEFAULT_BRANCH`.
    """
    tag = _as_optional_str(repo.get("tag"))
    if tag:
        return BranchResolution(name=tag, kind=RefKind.TAG, source=BranchSource.TAG)

    candidates = (
        (_as_optional_str(repo.get("branch")), BranchSource.REPO_BRANCH),
        (_as_optional_str(repo.get("default_branch")), BranchSource.REPO_DEFAULT),
        (_as_optional_str(document_default_branch), BranchSource.PROJECT_DEFAULT),
        (DEFAULT_BRANCH, BranchSource.BUILTIN_DEFAULT),
    )
    return _first_named(candidates, kind=RefKind.BRANCH)


def resolve_entry_ref(
    entry: WorkingRepo,
    *,
    observed_branch: str | None = None,
) -> BranchResolution:
    """Resolve the branch a live tree entry is on, or should be on.

    *observed_branch* is what Git reports for the working tree right now,
    when the caller has already read it; it wins, because a repository that
    is sitting on a branch is on that branch whatever the document says.
    Below it the chain runs ``resolved_ref_name`` → ``target_ref_name`` →
    ``default_branch`` → :data:`DEFAULT_BRANCH`.
    """
    candidates = (
        (_as_optional_str(observed_branch), BranchSource.OBSERVED),
        (_as_optional_str(entry.resolved_ref_name), BranchSource.ENTRY_RESOLVED),
        (_as_optional_str(entry.target_ref_name), BranchSource.ENTRY_TARGET),
        (_as_optional_str(entry.default_branch), BranchSource.REPO_DEFAULT),
        (DEFAULT_BRANCH, BranchSource.BUILTIN_DEFAULT),
    )
    return _first_named(candidates, kind=RefKind.BRANCH)


def resolve_propagated_ref(
    entry: WorkingRepo,
    ref_name: str,
    *,
    ref_kind: RefKind = RefKind.BRANCH,
) -> BranchResolution:
    """Resolve what *entry* targets when the whole tree moves to *ref_name*.

    A repository declared ``pinned`` in its ``.cgs`` is **shared with other
    projects and must stay on its own branch**, so a tree-wide *branch* move
    leaves it on its declared ``default_branch``. A **tag** reaches every
    repository, pinned or not, so a frozen release stays reproducible. That
    asymmetry — branches stop at a pin, tags do not — is the whole meaning of
    the field.

    For a pinned entry the returned ``kind`` is ``None``: the move must not
    rewrite the kind of ref that entry already carries.
    """
    if entry.effective_pinned and ref_kind is RefKind.BRANCH:
        pinned_name = (
            _as_optional_str(entry.default_branch)
            or _as_optional_str(entry.target_ref_name)
            or DEFAULT_BRANCH
        )
        return BranchResolution(name=pinned_name, kind=None, source=BranchSource.PINNED)
    source = BranchSource.TAG if ref_kind is RefKind.TAG else BranchSource.REPO_BRANCH
    return BranchResolution(name=ref_name, kind=ref_kind, source=source)


def _first_named(
    candidates: tuple[tuple[str | None, BranchSource], ...],
    *,
    kind: RefKind,
) -> BranchResolution:
    """Return the first non-empty candidate, tagged with where it came from."""
    for name, source in candidates:
        if name:
            return BranchResolution(name=name, kind=kind, source=source)
    # Unreachable: the caller always ends its chain with DEFAULT_BRANCH.
    return BranchResolution(
        name=DEFAULT_BRANCH, kind=kind, source=BranchSource.BUILTIN_DEFAULT
    )


def _as_optional_str(value: Any) -> str | None:
    """Normalize a document value to a non-empty string, or ``None``.

    A local copy rather than an import: ``git_tree.py``'s identical helper
    lives in Ring 1, and importing it here would be an upward import.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "DEFAULT_BRANCH",
    "BranchResolution",
    "BranchSource",
    "apply_declared_defaults",
    "resolve_declared_ref",
    "resolve_entry_ref",
    "resolve_propagated_ref",
]

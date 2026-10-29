"""op_boundary — OP, the one door through which an operation on G enters ComplexGitSync.

Ring: 3 (wraps the client; calls no provider, writes nothing to the register)
Contract: an operation on G is one client method, asked by G (``internal``)
    or by GX4G (``external``), on a tree installed at one Proof-of-Existence
    level. ``Op.run`` runs it on the GitTree, which may refuse (IntegrityMount);
    ``Op.is_poe`` returns ``OP*``, what of it can be proved to exist, bounded
    by the level's vector. ``*NAME{NODE, EDGE*} = G{NAME, NODE, EDGE, OP*}``.
Imports: errors, merkle, operating_space, git_tree, op_levels, workspace_locking

The model (owner, ExplDistantLocal and OpBoundary, 2026-10-29)
--------------------------------------------------------------
G has four parts, ``{NAME, NODE, EDGE, OP}``. A level exposes a fixed part of it,
four bits, ``.PUBLIC = {1,1,1,1}`` being the invariant: ``.USER {1,1,0,0}``,
``.DEV {1,0,0,0}``, ``.OPX {0,0,0,0}``. ``isPoE(OP)`` returns ``OP*``: the
operation's name, the PoE graph's name ``*NAME`` (a name of its own, ``*`` then
NAME: the ``*`` is the signal, the PoE itself is never written) and
``SHA256(G.MerkleRoot)``; never a branch, a commit message or a path. The level
bounds it: ``NAME`` exposes ``*NAME`` and the hash, ``NODE`` the repositories,
``EDGE`` the mounts between them, ``OP`` the operation carried on those edges
(the edges are then starred). ``*G.poe = OP*.poe``.

**An external request is never exposed more than the same internal one**: the
origin is recorded in the run log and never changes ``OP*``.

Which operations go through the door (owner, Q2): the ones that write
(``WRITING_METHODS``, ``verify`` with ``repair``) and ``fetch``, which reaches
outside the tree. Reads stay direct. The CLI enters as ``internal``
(:class:`RoutedClient`); gx4g builds an ``Op`` with ``external`` through the
Python API (owner, Q5). Until MemoryMount records the level at install, the
level is read from the tree's profile (owner): USER is ``.USER``, DEV is ``.DEV``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..errors import GitSyncError
from ..merkle import MerkleRoot
from ..operating_space.operating_space import OperatingSpace
from ..tree.git_tree import ROOT_REPO_ID
from .op_levels import DEV, LEVELS, OPX, PUBLIC, USER, Leaves, LevelCommand, PoE
from .workspace_locking import WRITING_METHODS, WRITING_WHEN

if TYPE_CHECKING:
    from .client import ComplexGitSyncClient

#: The operations that go through ``OP`` (owner, Q2): every write, and what reaches outside the tree.
ROUTED = frozenset((*WRITING_METHODS, *WRITING_WHEN, "fetch"))


class Origin(StrEnum):
    """Who asked: G itself, or GX4G."""

    INTERNAL = "internal"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class OpStar:
    """``OP*``: what of one operation can be proved to exist, at one level. Never the operation itself."""

    operation: str
    name: str
    merkle_root_hash: str
    nodes: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    level: PoE
    leaves: tuple[tuple[str, str], ...] = ()

    @property
    def poe(self) -> dict[str, Any]:
        """The proof ``OP*`` gives, bounded by the level: empty at ``.OPX``. It is also ``*G.poe``."""
        proof: dict[str, Any] = {}
        if self.level.name:
            proof["name"] = self.name
            proof["merkle_root_sha256"] = self.merkle_root_hash
        if self.level.node:
            proof["nodes"] = list(self.nodes)
            if self.leaves:
                proof["leaves"] = dict(self.leaves)
        if self.level.edge:
            star = {"op": self.operation} if self.level.op else {}
            proof["edges"] = [{"from": a, "to": b, **star} for a, b in self.edges]
        elif self.level.op:
            proof["op"] = self.operation
        return proof

    @property
    def sigma(self) -> int:
        return self.level.sigma


@dataclass
class Op:
    """One operation on G, through the boundary."""

    client: ComplexGitSyncClient
    operation: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    origin: Origin = Origin.INTERNAL
    level: PoE | None = None

    def run(self) -> Any:
        """The result on the GitTree; a refusal stays a refusal. Only a routed operation may go through."""
        if not self.routes(self.operation, self.kwargs):
            raise GitSyncError(f"{self.operation!r} does not go through OP: only writes and fetch do.")
        log = getattr(self.client, "_log_event", None)  # the run log's one door; a stand-in client may have none
        if log is not None:
            log("op", operation=self.operation, origin=self.origin.value)
        return getattr(self.client, self.operation)(*self.args, **self.kwargs)

    @staticmethod
    def routes(operation: str, kwargs: dict[str, Any]) -> bool:
        """Whether a call goes through OP: a routed name, and ``verify`` only with ``repair`` (owner, Q2)."""
        return operation in ROUTED and (operation not in WRITING_WHEN or bool(kwargs.get(WRITING_WHEN[operation])))

    def is_poe(self, beta: Mapping[str, str] | None = None) -> OpStar:
        """``isPoE(OP)``: ``OP*``, read from the tree as it now is, bounded by the level. The origin never enters.

        *beta* is the other side's leaves (``Leaves.of_state`` of a registered State): each leaf of the tree is then
        ``equal``, ``different`` or absent on one side, a field of ``OP*`` (FD3). Without it every leaf is ``unverified``.
        """
        registry = self.client.get_dependency_registry()
        level = self.level or PoE.of_profile(registry.profile)
        root = registry.repos.get(ROOT_REPO_ID)
        project = registry.project_name or (root.name if root else "")
        merkle = OperatingSpace(registry).gittree_root if root is not None else ""
        names = {e.repo_id: e.name for e in registry.values()}
        return OpStar(
            operation=self.operation,
            name=f"*{project}",
            merkle_root_hash=MerkleRoot.content_digest(merkle.encode("utf-8")),
            nodes=tuple(sorted(e.name for e in registry.values())),
            edges=tuple(sorted((names[e.parent_id], e.name) for e in registry.values() if e.parent_id in names)),
            level=level,
            leaves=tuple(Leaves.compare(Leaves.of_tree(self.client), beta).items()),
        )


class RoutedClient:
    """The client as the CLI sees it: every routed method goes through ``OP`` as ``internal``; the rest is the client."""

    __slots__ = ("_client",)

    def __init__(self, client: ComplexGitSyncClient) -> None:
        object.__setattr__(self, "_client", client)

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._client, name)
        if name not in ROUTED or not callable(attribute):
            return attribute
        return lambda *args, **kwargs: (
            Op(self._client, name, args, kwargs).run() if Op.routes(name, kwargs) else attribute(*args, **kwargs)
        )

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._client, name, value)


__all__ = ["DEV", "LEVELS", "OPX", "PUBLIC", "ROUTED", "USER", "LevelCommand", "Leaves", "Op", "OpStar", "Origin", "PoE", "RoutedClient"]

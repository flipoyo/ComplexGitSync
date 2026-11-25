"""data_spec — what a repository declares about the data it holds.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: ``DataSpec`` is the normalised form of a repository's
    ``data = { backend = "dvc" }`` declaration, the same in a ``.cgs`` entry
    and a ``.gts`` ``[[repo_state]]``. It validates it offline and writes it back; it runs nothing.
Imports: none

Data is a capability of a repository, not a kind of one (DataArchitecture):
``data`` changes nothing about ``private``, ``writable`` or who may write
where, and the Git commit stays authoritative for revisions.

Only ``"dvc"`` is accepted. ``"git-lfs"`` is reserved and refused as not
implemented, never treated as Git or as DVC. ``service`` and ``paths`` are
reserved too: ``service`` waits for DataArchitecture §7 C2, ``paths`` for
DataAuthoring. The flat spelling ``data_backend``/``data_paths`` on the
repository itself is refused by name, so the old proposal never parses
silently as an unknown key (DataSchema, owner, 2026-11-23).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class DataSpec:
    """A repository's data declaration: which tool manages its data bytes."""

    backend: str

    #: The key a repository entry declares it under, in ``.cgs`` and ``.gts`` alike.
    KEY = "data"
    #: The backends this build implements.
    ACCEPTED_BACKENDS = ("dvc",)
    #: Backends the type contract names but this build does not implement.
    RESERVED_BACKENDS = ("git-lfs",)
    #: Keys inside ``data`` that are reserved for a later ticket, and which one.
    RESERVED_KEYS = MappingProxyType({
        "service": "the data service and its remote wait for DataArchitecture §7 C2",
        "paths": "data paths come with DataAuthoring", "data_paths": "data paths come with DataAuthoring",
    })
    #: The flat spelling of an earlier proposal, refused on the repository entry itself.
    FLAT_KEYS = ("data_backend", "data_paths")

    def __post_init__(self) -> None:
        if refused := self._backend_problems(self.backend):  # a DataSpec is never built around a refused backend
            raise ValueError(refused[0])

    @classmethod
    def problems(cls, entry: Mapping[str, Any]) -> list[str]:
        """Every reason *entry*'s data declaration is refused, empty when it is valid or absent.

        Each reason starts with the key it is about, so the caller only has to
        name the repository in front of it.
        """
        found = [
            f"{key} is not a key: declare data as {cls.KEY} = {{ backend = \"dvc\" }}"
            for key in cls.FLAT_KEYS
            if key in entry
        ]
        if cls.KEY not in entry:
            return found
        declared = entry[cls.KEY]
        expected = f'{cls.KEY} = {{ backend = "dvc" }}'
        if not isinstance(declared, Mapping):
            return [*found, f"{cls.KEY} must be a table, as in {expected}; got {declared!r}"]
        for key in declared:
            if key in cls.RESERVED_KEYS:
                found.append(f"{cls.KEY}.{key} is not implemented yet: {cls.RESERVED_KEYS[key]}")
            elif key != "backend":
                found.append(f"{cls.KEY}.{key} is not a known key (accepted: backend)")
        found.extend(cls._backend_problems(declared.get("backend")))
        return found

    @classmethod
    def _backend_problems(cls, backend: Any) -> list[str]:
        if backend is None:
            return [f"{cls.KEY}.backend is missing: a data repository must name its backend"]
        if backend in cls.RESERVED_BACKENDS:
            return [f"{cls.KEY}.backend = {backend!r} is not implemented (accepted: {', '.join(cls.ACCEPTED_BACKENDS)})"]
        if backend not in cls.ACCEPTED_BACKENDS:
            return [f"{cls.KEY}.backend = {backend!r} is not a known backend (accepted: {', '.join(cls.ACCEPTED_BACKENDS)})"]
        return []

    @classmethod
    def from_entry(cls, entry: Mapping[str, Any]) -> DataSpec | None:
        """The declaration of a repository entry, or ``None`` for a Git-only one.

        Raises ``ValueError`` naming every problem when the declaration is
        refused; a document's ``validate`` reports them first, with the
        repository's name.
        """
        refused = cls.problems(entry)
        if refused:
            raise ValueError("; ".join(refused))
        declared = entry.get(cls.KEY)
        return None if declared is None else cls(backend=str(declared["backend"]))

    def to_dict(self) -> dict[str, str]:
        """The table written back to a ``.cgs`` entry or a ``[[repo_state]]``."""
        return {"backend": self.backend}


__all__ = ["DataSpec"]

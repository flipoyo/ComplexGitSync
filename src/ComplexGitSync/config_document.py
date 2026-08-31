"""config_document — pure, format-neutral configuration-document base.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: wrap a dict, expose dot-path read access and a validation hook.
Imports: none

The shared :class:`ConfigDocument` base lives outside both ``cgs_format.py`` and
``orchestre.py`` because it is also used by the runtime ``.gts`` format.

File-based loading/saving (``from_toml``/``to_toml``/``from_json``/``to_json``/
``from_yaml``/``to_yaml``) used to live on this class, but all six call
``open()`` directly and therefore do real filesystem I/O — that disqualifies
them from Ring 0 (see ``AgentSpec/IsolationPlan.md`` and the WP-CFG entry in
``AgentSpec/20260828_Isolation_DevPlanTicket.md`` §0). They now live in the
sibling Ring-1 module ``config_document_io.py`` as ``ConfigDocumentIOMixin``,
which subclasses combine with this class via multiple inheritance to regain
the exact same method names and call syntax.

``print()`` moved out alongside them even though it writes to *stdout* rather
than a file: Ring 0's contract above is "no I/O" without a stdout carve-out,
and ``print()`` is not a pure function of its inputs (it has an observable
side effect and returns nothing useful) — so on the same reasoning that
disqualifies the file writers, it is not Ring-0 either. It now lives in
``config_document_io.py`` next to the file-I/O methods it shares
``tomli_w.dumps`` formatting logic with.

``from_dict`` stays here: it validates and wraps a plain ``dict`` that the
*caller* already has in memory — no ``open()``, no stream, nothing that
touches the outside world. It is also the shared landing point every
``ConfigDocumentIOMixin`` loader classmethod calls after parsing bytes off
disk, so it has to remain reachable through ``cls.from_dict`` regardless of
where in the MRO the I/O mixin sits.
"""

from __future__ import annotations

import copy
from typing import Any

_MISSING = object()


def _dot_get(data: dict[str, Any], key: str, default: Any = None) -> Any:
    """Return the value at the dot-separated *key* path inside *data*."""
    parts = key.split(".")
    node: Any = data
    for part in parts:
        if not isinstance(node, dict):
            return default
        node = node.get(part, _MISSING)
        if node is _MISSING:
            return default
    return node


class ConfigDocument:
    """Base class for all ComplexGitSync configuration document types.

    Every subclass wraps a raw dictionary and exposes:

    Instance methods
    ~~~~~~~~~~~~~~~~
    * ``read(key, default=None)`` – dot-path traversal into the underlying dict
    * ``get(key, default=None)``  – alias for ``read``
    * ``to_dict()``               – return a deep copy of the underlying dict
    * ``validate()``              – raise ``ConfigValidationError`` on schema violations

    Class-method factories
    ~~~~~~~~~~~~~~~~~~~~~~
    * ``from_dict(data)``    – create and validate from a plain dict

    File-based loading (``from_toml``/``from_json``/``from_yaml``), saving
    (``to_toml``/``to_json``/``to_yaml``), and ``print()`` are **not** on this
    class — they do real I/O and live on ``ConfigDocumentIOMixin`` in the
    sibling ``config_document_io.py`` module instead. A concrete document
    type combines both, e.g. ``class CgsDocument(ConfigDocument,
    ConfigDocumentIOMixin): ...``, so callers see the exact same method
    names on the exact same object; only the module that defines them moved.
    """

    FORMAT_VERSION: str = "1.0"
    DOCUMENT_KIND: str = "base"

    def __init__(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise TypeError(f"ConfigDocument data must be a dict, got {type(data).__name__!r}.")
        self._data: dict[str, Any] = data

    def read(self, key: str, default: Any = None) -> Any:
        """Return the value at the dot-separated *key* path, or *default*."""
        return _dot_get(self._data, key, default)

    def get(self, key: str, default: Any = None) -> Any:
        """Alias for :meth:`read`."""
        return self.read(key, default)

    def to_dict(self) -> dict[str, Any]:
        """Return a deep copy of the underlying dictionary."""
        return copy.deepcopy(self._data)

    def validate(self) -> None:
        """Validate required fields.  Raises :exc:`ConfigValidationError` on failure."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfigDocument:
        """Create a document from a plain dictionary and validate it."""
        doc = cls(data)
        doc.validate()
        return doc

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}(kind={self.DOCUMENT_KIND!r})"


__all__ = ["ConfigDocument"]

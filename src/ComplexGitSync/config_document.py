"""Format-neutral configuration document I/O for ComplexGitSync.

The shared :class:`ConfigDocument` base lives outside both ``cgs.py`` and
``orchestre.py`` because it is also used by runtime formats such as ``.gts``
and ``.goc``.
"""

from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

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
    * ``print()``                 – write a human-readable representation to stdout
    * ``to_dict()``               – return a deep copy of the underlying dict
    * ``validate()``              – raise ``ConfigValidationError`` on schema violations

    Class-method factories
    ~~~~~~~~~~~~~~~~~~~~~~
    * ``from_dict(data)``    – create and validate from a plain dict
    * ``from_toml(path)``    – load from a ``.toml`` / ``.cgs`` / ``.gts`` / ``.goc`` file
    * ``from_json(path)``    – load from a ``.json`` file
    * ``from_yaml(path)``    – load from a ``.yml`` / ``.yaml`` file (requires PyYAML)

    Serializers
    ~~~~~~~~~~~
    * ``to_toml(path)``       – write as TOML
    * ``to_json(path)``       – write as JSON
    * ``to_yaml(path)``       – write as YAML (requires PyYAML)
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

    def print(self) -> None:
        """Print the document as TOML to *stdout*."""
        print(tomli_w.dumps(self._data))

    def to_dict(self) -> dict[str, Any]:
        """Return a deep copy of the underlying dictionary."""
        return copy.deepcopy(self._data)

    def validate(self) -> None:
        """Validate required fields.  Raises :exc:`ConfigValidationError` on failure."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfigDocument":
        """Create a document from a plain dictionary and validate it."""
        doc = cls(data)
        doc.validate()
        return doc

    @classmethod
    def from_toml(cls, path: Path | str) -> "ConfigDocument":
        """Load a document from a TOML file (includes ``.cgs``, ``.gts``, ``.goc``)."""
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        return cls.from_dict(data)

    @classmethod
    def from_json(cls, path: Path | str) -> "ConfigDocument":
        """Load a document from a JSON file."""
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_dict(data)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "ConfigDocument":
        """Load a document from a YAML file.

        Requires ``PyYAML``. Install the package with the ``yaml`` extra.
        """
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for YAML support.  "
                "Install ComplexGitSync with the yaml extra using pixi."
            ) from exc
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return cls.from_dict(data)

    def to_toml(self, path: Path | str) -> None:
        """Write the document to a TOML file."""
        with open(path, "wb") as fh:
            tomli_w.dump(self._data, fh)

    def to_json(self, path: Path | str, *, indent: int = 2) -> None:
        """Write the document to a JSON file."""
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=indent)

    def to_yaml(self, path: Path | str) -> None:
        """Write the document to a YAML file.

        Requires ``PyYAML``. Install the package with the ``yaml`` extra.
        """
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for YAML support.  "
                "Install ComplexGitSync with the yaml extra using pixi."
            ) from exc
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(self._data, fh, default_flow_style=False, allow_unicode=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}(kind={self.DOCUMENT_KIND!r})"


__all__ = ["ConfigDocument"]

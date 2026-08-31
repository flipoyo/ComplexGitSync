"""config_document_io — file-based load/save for ConfigDocument subclasses.

Ring: 1 (filesystem only, no subprocess)
Contract: read/write a ConfigDocument-shaped object to TOML/JSON/YAML.
Imports: config_document

Home for the six ``open()`` call sites (plus ``print()``) that used to live
on ``config_document.ConfigDocument`` itself before WP-CFG (see
``AgentSpec/20260828_Isolation_DevPlanTicket.md`` §0 and
``AgentSpec/IsolationPlan.md``) reclassified them out of Ring 0.

``ConfigDocumentIOMixin`` is designed to be combined with
:class:`~ComplexGitSync.config_document.ConfigDocument` (or any subclass of
it) via ordinary Python multiple inheritance, e.g.::

    class CgsDocument(ConfigDocument, ConfigDocumentIOMixin):
        ...

Because Python resolves attribute lookup through the whole MRO, every method
defined here — ``from_toml``, ``from_json``, ``from_yaml``, ``to_toml``,
``to_json``, ``to_yaml``, ``print`` — becomes available on the combined
subclass under the *exact* same name it had when it lived directly on
``ConfigDocument``. Existing call sites such as ``CgsDocument.from_toml(path)``
or ``document.to_toml(output)`` do not need to change at all once a subclass
picks up this mixin; only the module the method's *implementation* lives in
has moved.

This mixin does not stand on its own: ``from_toml``/``from_json``/
``from_yaml`` call ``cls.from_dict(data)``, and the six instance methods read
``self._data``. Both are supplied by ``ConfigDocument`` (or a compatible
class) elsewhere in the MRO — this module only imports
``ConfigDocument`` for typing/documentation purposes and never constructs it
standalone.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomli_w

if TYPE_CHECKING:
    from .config_document import ConfigDocument


class ConfigDocumentIOMixin:
    """File-based load/save/print for :class:`ConfigDocument` subclasses.

    Mix this in *alongside* ``ConfigDocument`` — it assumes the combined
    class provides ``from_dict`` (classmethod) and an instance attribute
    ``_data: dict[str, Any]``, both of which ``ConfigDocument`` supplies.
    """

    if TYPE_CHECKING:
        _data: dict[str, Any]

        @classmethod
        def from_dict(cls, data: dict[str, Any]) -> "ConfigDocument": ...

    @classmethod
    def from_toml(cls, path: Path | str) -> Any:
        """Load a document from a TOML file (includes ``.cgs``, ``.gts``)."""
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        return cls.from_dict(data)

    @classmethod
    def from_json(cls, path: Path | str) -> Any:
        """Load a document from a JSON file."""
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_dict(data)

    @classmethod
    def from_yaml(cls, path: Path | str) -> Any:
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

    def print(self) -> None:
        """Print the document as TOML to *stdout*."""
        print(tomli_w.dumps(self._data))


__all__ = ["ConfigDocumentIOMixin"]

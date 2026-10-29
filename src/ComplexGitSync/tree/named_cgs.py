"""named_cgs — a `yyyy-xxx.cgs` file's name is a contract (BeforeCGS-BETA §7).

Ring: 0 (pure — reads a path's name and a parsed table; no I/O, no clock, no environment)
Contract: a ``.cgs`` named ``<yyyy>-<xxx>.cgs``, where ``xxx`` is exactly three
    characters and ``yyyy`` (of any length) is the project's own name, opens the
    project ``yyyy`` on branch ``xxx`` and falls back to ``CGS-BETA``. A file that
    says otherwise is refused by name, before anything is cloned.
Imports: errors

``yyyy`` is the project's name and ``xxx`` the operation's code (``opx``, ``dev``,
``usr``, ``pub``): the file is ``$G.NAME-$G.OP.NAME.cgs``, the edge's name. Only a file
whose ``yyyy`` equals its ``project.name`` is concerned (owner, AD2), so
``molonari-light.cgs`` (five characters after the dash) and every file with no such
shape are read as they always were, and so is a ``memory reboot`` export, ``<project>-v<N>.cgs``
(``v10`` to ``v99`` have three characters but name a version, not an operation). The check reads the parsed table *before*
normalisation, so a ``default_branch`` the file leaves out is not mistaken for ``main``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..errors import ConfigValidationError

#: The branch every repository of such a tree falls back to; ``main`` still follows it (AD3).
FALLBACK = "CGS-BETA"

_NAMED = re.compile(r"(?P<project>.+)-(?P<operation>(?!v\d+$)[^-.]{3})")  # `-v10`..`-v99` is a memory reboot export, not an operation


class NamedCgs:
    """The contract a ``yyyy-xxx.cgs`` file's name makes."""

    @staticmethod
    def parse(path: Path | str) -> tuple[str, str] | None:
        """``(yyyy, xxx)`` from the file's name, cut at the last ``-``; ``None`` for a name without the shape."""
        found = _NAMED.fullmatch(Path(path).stem)
        return (found["project"], found["operation"]) if found else None

    @staticmethod
    def expected(project: str, operation: str) -> str:
        """The project line the name asks for."""
        return f'project = {{ name = "{project}", default_branch = "{operation}", fallback_branch = "{FALLBACK}" }}'

    @staticmethod
    def check(path: Path | str, raw: Mapping[str, Any]) -> None:
        """Refuse, by name, a concerned file whose project line disagrees with it; any other file passes."""
        named = NamedCgs.parse(path)
        project = raw.get("project")
        table = {"name": project} if isinstance(project, str) else project
        if named is None or not isinstance(table, Mapping) or table.get("name") != named[0]:
            return
        if (table.get("default_branch"), table.get("fallback_branch")) != (named[1], FALLBACK):
            raise ConfigValidationError(
                f"{Path(path).name} is named for project {named[0]!r} on branch {named[1]!r}, so its project line must be "
                f"{NamedCgs.expected(*named)}; it says default_branch={table.get('default_branch')!r}, "
                f"fallback_branch={table.get('fallback_branch')!r}."
            )


__all__ = ["FALLBACK", "NamedCgs"]

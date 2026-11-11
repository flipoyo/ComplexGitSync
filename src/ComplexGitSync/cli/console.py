"""cli.console — a command never dies of a character its terminal cannot show.

Ring: 4 (CLI adapter)
Contract: make standard output and error replace a character their encoding cannot encode, instead of raising; the encoding
    itself is left as the terminal has it. Touches no file and no repository.
Imports: none

**Why.** A Windows console, or a pipe on Windows, often still encodes as cp1252, which has no box-drawing character: ``initialise``
printed its tree with ``└──`` and ended in ``UnicodeEncodeError`` after cloning, so CI on Windows failed at its first command
(LinkAtCGS §8). Printing ``?`` for what cannot be shown costs a glyph; the exception cost the whole command.
"""

from __future__ import annotations

import sys

__all__ = ["ConsoleEncoding"]


class ConsoleEncoding:
    """What the process's standard streams do with a character they cannot encode."""

    @staticmethod
    def replace_unencodable() -> None:
        """Set ``errors="replace"`` on stdout and stderr when they can be reconfigured; the encoding is not changed."""
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure) and getattr(stream, "errors", "") != "replace":
                reconfigure(errors="replace")

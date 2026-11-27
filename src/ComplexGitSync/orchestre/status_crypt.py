"""status_crypt — the ``CRYPT`` column of ``cgitsync status``, as text.

Ring: 3 (pure in fact — no I/O, no clock, no environment — but it sits with the one caller that renders it)
Contract: given the table :func:`..status_render._render_status_table` already rendered and one label per row
    (``locked``, ``unlocked`` or ``None``), append the ``CRYPT`` column, and only when some repository is encrypted,
    so the table of a tree with no git-crypt path reads exactly as it always has (EncryptedRepos E2). Never runs
    ``git`` and never mutates its input.
Imports: none
"""

from __future__ import annotations

from collections.abc import Sequence

CRYPT_LEGEND = (
    "legend: CRYPT — a repository whose .gitattributes asks for git-crypt: unlocked = this clone "
    "has the filter, so files are encrypted when committed; locked = it has not, and a new file "
    "under an encrypted path is refused at 'commit' and 'push' (cgitsync never unlocks)"
)


def with_crypt_column(table: str, crypt: Sequence[str | None]) -> str:
    """*table* with a last ``CRYPT`` column: ``-`` for a repository without git-crypt paths.

    *crypt* has one entry per data row, in order. Without any label the table is returned unchanged.
    """
    if not any(crypt):
        return table
    lines = table.split("\n")
    labels = [label or "-" for label in crypt]
    width = max(len("CRYPT"), *(len(label) for label in labels))
    header, separator, rows = lines[0], lines[1], lines[2:]
    return "\n".join(
        [
            f"{header}  CRYPT",
            f"{separator}  {'-' * width}",
            *(f"{row}  {label}" for row, label in zip(rows, labels, strict=True)),
        ]
    )


__all__ = ["CRYPT_LEGEND", "with_crypt_column"]

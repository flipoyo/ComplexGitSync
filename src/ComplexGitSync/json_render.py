"""json_render — the machine-readable shape of what `cgitsync` reports.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: given values a command has already computed, build the JSON
    payload it prints and serialise it. One module for every command's
    shape, so the field names are decided once rather than re-invented per
    command, and `cli/` carries none of them.
Imports: none

Why this is not in ``status_render.py``
---------------------------------------
That module renders one table for humans. This one answers a different
question — what a script reads — for more than one command, and the two
will keep diverging: a column can be renamed when the wording improves, a
JSON field cannot, because something is parsing it.

The promise attached to these payloads
--------------------------------------
**Additive only.** New fields may appear; existing fields do not change
meaning and do not vanish. :data:`SCHEMA_VERSION` says which generation a
reader is looking at, so a consumer that cares can check rather than guess.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

#: Bumped only when a field changes meaning or goes — never for an addition,
#: which is what "additive only" promises a reader.
SCHEMA_VERSION = 1

#: The per-repository field names, in the order the table prints them. One
#: name per column, plus the two booleans a machine should not have to parse
#: out of a string (a trailing ``*``, an empty ``-``).
_REPOSITORY_FIELDS = (
    "name",
    "path",
    "scope",
    "local_branch",
    "upstream_branch",
    "local",
    "sync",
    "head",
    "recorded",
)


def status_payload(
    *,
    cgshome: str,
    use_case: str,
    cgitsync_branch: str,
    lifecycle_state: str,
    is_ready: bool,
    registry_complete: bool,
    rows: Sequence[tuple[str, str, str, str, str, str, str, str, str]],
    counts: Any,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    """What ``cgitsync status --json`` prints.

    The same values the table is built from, so the two cannot disagree —
    including ``cgitsync_branch``, which answers "which branch am I on?"
    with the word the table already shows.

    Two conveniences a machine should not have to parse out of text: the
    ``*`` that marks a HEAD differing from the recorded commit becomes
    ``recorded_matches``, and the ``-`` standing for "no upstream" becomes
    ``null``.
    """
    repositories = []
    for row in rows:
        entry = dict(zip(_REPOSITORY_FIELDS, row, strict=True))
        head = entry["head"]
        entry["recorded_matches"] = not head.endswith("*")
        entry["head"] = head.rstrip("*")
        if entry["upstream_branch"] == "-":
            entry["upstream_branch"] = None
        repositories.append(entry)

    return {
        "schema_version": SCHEMA_VERSION,
        "command": "status",
        "cgshome": cgshome,
        "use_case": use_case,
        "cgitsync_branch": cgitsync_branch,
        "tree_state": {
            "lifecycle_state": lifecycle_state,
            "is_ready": is_ready,
            "registry_complete": registry_complete,
        },
        "summary": {
            "repos": len(repositories),
            "dirty": counts.dirty,
            "staged": counts.staged,
            "ahead": counts.ahead,
            "behind": counts.behind,
            "unmeasured": counts.unmeasured,
            "recorded_mismatch": counts.recorded_mismatch,
            "errors": counts.errors,
        },
        "repositories": repositories,
        "warnings": list(warnings),
    }


def empty_status_payload(
    *,
    cgshome: str,
    use_case: str,
    cgitsync_branch: str,
    lifecycle_state: str,
) -> dict[str, Any]:
    """``status --json`` for a workspace that holds no repositories.

    The same object shape as :func:`status_payload` with everything at zero,
    rather than a different one: a caller should be able to read
    ``repositories`` and ``summary.repos`` without first asking whether this
    workspace is the empty kind. ``is_ready`` is false, because an empty
    tree is a project that has not started, not one that is good to go.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "status",
        "cgshome": cgshome,
        "use_case": use_case,
        "cgitsync_branch": cgitsync_branch,
        "tree_state": {
            "lifecycle_state": lifecycle_state,
            "is_ready": False,
            "registry_complete": False,
        },
        "summary": {
            "repos": 0,
            "dirty": 0,
            "staged": 0,
            "ahead": 0,
            "behind": 0,
            "unmeasured": 0,
            "recorded_mismatch": 0,
            "errors": 0,
        },
        "repositories": [],
        "warnings": [],
    }


def verify_payload(
    *,
    cgshome: str,
    state: str,
    entries: int,
    findings: Sequence[tuple[Any, Any, str]],
    repair: bool,
) -> dict[str, Any]:
    """What ``cgitsync verify --json`` prints.

    ``status`` is one of the five answers — ``verified``, ``no-history``,
    ``legacy``, ``corrupt``, ``time-inconsistent`` — never a blur of two. An
    empty register reads as ``no-history`` rather than as a clean chain,
    because a check that cannot fail is not a check; and a chain whose links
    all held but whose timestamps move backwards reads as
    ``time-inconsistent`` rather than ``corrupt``, because the history is
    intact and only the clock that stamped it was not.

    ``findings`` carries each finding's name as a string rather than an enum
    member, so the object survives serialisation without a custom encoder
    and reads the same to a consumer in any language.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "verify",
        "cgshome": cgshome,
        "status": state,
        "entries": entries,
        "repair_attempted": repair,
        "findings": [
            {
                "seq": seq,
                "finding": getattr(finding, "name", str(finding)),
                "detail": detail,
            }
            for seq, finding, detail in findings
        ],
    }


def error_payload(
    *,
    command: str | None,
    exit_code: int,
    message: str,
    error_type: str,
) -> dict[str, Any]:
    """What a JSON-capable command prints when it fails.

    Still one JSON object on stdout, because a caller that pipes this
    command must not have to tell success from failure by whether the pipe
    parsed. ``exit_code`` repeats what the process returns, so either signal
    may be used.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "status": "error",
        "exit_code": exit_code,
        "error_type": error_type,
        "message": message,
    }


def dumps(payload: dict[str, Any]) -> str:
    """Serialise *payload* deterministically: one line, stable key order."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


__all__ = [
    "SCHEMA_VERSION",
    "dumps",
    "empty_status_payload",
    "error_payload",
    "status_payload",
    "verify_payload",
]

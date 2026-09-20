"""store — the State area's writer, and the register format that predates it.

Ring: 1 (filesystem and clock, via universal_clock; no subprocess, no Git)
Contract: write one State to disk atomically, answer whether a workspace
    holds history in the old single-file format, and read that format for
    the callers that still expose it. Builds no document and decides no
    tree: what to write is handed in.
Imports: errors, gts_document, paths, states, universal_clock

Two things live here, and it is worth saying why they are together.

**The State writer.** A State is one file, named by its own content hash,
published by a single rename so a reader never sees half of one. That is
memory's business, and it used to sit in the middle of ``orchestre.py``.

**The single-file register.** ``LocalGitRegister`` and ``SyncLedger`` are
the format ComplexGitSync wrote before the hash-chained ledger: one TOML
file rewritten whole on every operation, append-only by convention, with no
chain and no way to detect an edit. **Nothing writes it any more.** It is
kept, read-only, because every workspace created before the chain has one
and ``get_ledger_history``/``replay_ledger`` still answer from it.
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from ..errors import ConfigValidationError
from ..gts_document import GtsDocument
from ..paths import _path_to_environment_marker
from ..universal_clock import ClockProtocol, SystemClock
from .states import (
    _STATE_DIR_RE,
    _format_state_id,
    _next_state_directory_order,
    _parse_state_hash,
    state_path,
)


def write_state(cgitsync_dir: Path, state_hash: str, write: Any, *, suffix: str = ".gts") -> Path:
    """Write one State, atomically, at the path its content hash names.

    *write* is called with a temporary path in the same directory and must
    write the file; the rename that follows is what makes the State appear
    all at once. A crash leaves either the previous State or none, never a
    truncated one.

    The old layout got the same guarantee by building a whole directory and
    renaming it into place. A State is one file now, so it costs one rename.
    """
    destination = state_path(cgitsync_dir, state_hash, suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        write(temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def legacy_register_exists(workspace: Path) -> bool:
    """Whether *workspace* holds history in the single-file ``.lgr`` format.

    That format is what :class:`LocalGitRegister` writes: one TOML file,
    rewritten whole on every operation, with a sequential id and no chain.
    It is readable and it is not verifiable — an edit to it leaves no trace
    — so a workspace that has one has history that ``verify`` must report as
    *legacy* rather than as nothing at all.

    Every workspace created before the hash-chained register is written is
    in exactly this state, which is why the answer matters more than it
    looks.
    """
    cgitsync_dir = workspace / ".cgitsync"
    # Three places one has ever lived: inside a state directory (copied
    # forward before every write), at the workspace root (older still), and
    # at `.cgitsync/<project>.lgr`, where the flat state layout put it.
    # Miss one and a workspace with history is told it has none, which is
    # the lie this whole answer exists to remove.
    for candidate in (
        cgitsync_dir.glob("state(*)_*/*.lgr"),
        cgitsync_dir.glob("*.lgr"),
        workspace.glob("*.lgr"),
    ):
        if any(candidate):
            return True
    return False


class LocalGitRegister:
    """Project-local ``.lgr`` register for generated ``.gts`` snapshots.

    The TOML structure keeps:
    - a ``[register]`` section for the current snapshot pointer, and
    - a ``[[snapshots]]`` list for public ``state(HASH(.@))`` identifiers.

    The private TIME-L0 anchor never leaves the local execution context.
    ``snapshot_hash`` remains the canonical hash of the ``.gts`` payload, but it
    does not participate in State identity.
    """

    _HASH_CHUNK_SIZE = 65536

    def __init__(self, register_path: Path | str) -> None:
        self.register_path = Path(register_path)

    def record_snapshot(
        self,
        snapshot_path: Path | str,
        *,
        state_hash: str | None = None,
        state_order: int | None = None,
        recorded_snapshot_path: Path | str | None = None,
        clock: ClockProtocol | None = None,
    ) -> str:
        resolved_snapshot_path = Path(snapshot_path).resolve()
        public_snapshot_path = (
            Path(recorded_snapshot_path).resolve()
            if recorded_snapshot_path is not None
            else resolved_snapshot_path
        )
        snapshot_path_marker = _path_to_environment_marker(public_snapshot_path)

        data = self._load()
        snapshots = data.setdefault("snapshots", [])
        # A caller that does not name the State is asking this register to
        # invent a name, which is what the timestamp anchor used to do for
        # every write. The content hash of the snapshot being recorded is
        # the State's name; falling back to the file's own digest keeps a
        # hand-rolled call working without minting a clock reading.
        public_state_hash = (
            state_hash
            if state_hash is not None
            else self._hash_snapshot_file(resolved_snapshot_path)
        )
        snapshot_id = _format_state_id(public_state_hash)
        if state_order is None:
            state_order = self._next_state_order(snapshots, public_state_hash)
        recorded_at = (
            (clock or SystemClock())
            .now()
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        # One hash, one meaning. ``state_hash`` and ``snapshot_hash`` were
        # two fields that never agreed — one a clock reading that named the
        # directory, the other the content digest that named nothing. Now a
        # State *is* its content, so there is one value and it is called
        # what it is. Old registers keep both; nothing rewrites them.
        snapshots.append(
            {
                "id": snapshot_id,
                "state_hash": public_state_hash,
                "state_order": state_order,
                "snapshot_path": snapshot_path_marker,
                "recorded_at": recorded_at,
            }
        )

        register = data.setdefault("register", {})
        register["current_snapshot_id"] = snapshot_id
        register["current_state_hash"] = public_state_hash
        register["current_snapshot_path"] = snapshot_path_marker

        self.register_path.parent.mkdir(parents=True, exist_ok=True)
        self.register_path.write_text(tomli_w.dumps(data), encoding="utf-8")
        return snapshot_id

    def _load(self) -> dict[str, Any]:
        if not self.register_path.is_file():
            return {"register": {}, "snapshots": []}
        return tomllib.loads(self.register_path.read_text(encoding="utf-8"))

    def _next_state_order(self, snapshots: list[dict[str, Any]], state_hash: str) -> int:
        """Return the next local ordering suffix for State directories."""
        max_order = -1
        for entry in snapshots:
            if not isinstance(entry, dict):
                continue
            entry_state_hash = entry.get("state_hash")
            if not isinstance(entry_state_hash, str):
                entry_state_hash = _parse_state_hash(str(entry.get("id", "")))
            if entry_state_hash != state_hash:
                continue
            raw_order = entry.get("state_order")
            if isinstance(raw_order, int):
                max_order = max(max_order, raw_order)
                continue
            raw_id = str(entry.get("id", ""))
            if raw_id.startswith("gts-"):
                try:
                    max_order = max(max_order, int(raw_id.removeprefix("gts-")) - 1)
                except ValueError:
                    continue
        register_parent = self.register_path.parent
        cgitsync_dir = (
            register_parent.parent
            if _STATE_DIR_RE.fullmatch(register_parent.name) is not None
            else register_parent / ".cgitsync"
        )
        return max(max_order + 1, _next_state_directory_order(cgitsync_dir, state_hash))

    def _hash_snapshot_file(self, snapshot_path: Path) -> str:
        """Compute a canonical snapshot hash for ``snapshot_path``."""
        try:
            document = GtsDocument.from_toml(snapshot_path)
        except (OSError, tomllib.TOMLDecodeError, ConfigValidationError):
            digest = hashlib.sha256()
            with snapshot_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(self._HASH_CHUNK_SIZE), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        if document.snapshot_hash:
            return document.snapshot_hash
        return document.compute_snapshot_hash()


def _get_actor() -> str:
    """Return the current system user name, or ``'unknown'`` on failure."""
    try:
        import getpass

        return getpass.getuser()
    except Exception:  # pragma: no cover
        return "unknown"


def _topological_sort_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return *events* in topological order (parents before children).

    Uses Kahn's BFS algorithm on the ``parent_sync_ids`` graph.
    Events without a valid ``sync_id`` are appended last, preserving their
    original relative order.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        if isinstance(event, dict):
            sid = str(event.get("sync_id", ""))
            if sid:
                by_id[sid] = event

    in_degree: dict[str, int] = {sid: 0 for sid in by_id}
    children: dict[str, list[str]] = {sid: [] for sid in by_id}

    for sid, event in by_id.items():
        for parent_id in event.get("parent_sync_ids", []):
            parent_str = str(parent_id)
            if parent_str in by_id:
                in_degree[sid] += 1
                children[parent_str].append(sid)

    queue: list[str] = sorted(sid for sid, deg in in_degree.items() if deg == 0)
    result: list[dict[str, Any]] = []
    while queue:
        current = queue.pop(0)
        result.append(by_id[current])
        for child in sorted(children.get(current, [])):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    # Append any events not reachable via the DAG (malformed entries)
    seen: set[str] = {str(e.get("sync_id", "")) for e in result}
    for event in events:
        if not isinstance(event, dict) or str(event.get("sync_id", "")) not in seen:
            result.append(event)

    return result


class SyncLedger:
    """Append-only DAG ledger for synchronisation operations in the ``.lgr`` file.

    Extends the :class:`LocalGitRegister` format with a ``[[ledger]]``
    section that records each synchronisation operation as an immutable
    DAG event.  Events are linked via ``parent_sync_ids`` to form a
    directed acyclic graph that reconstructs workspace evolution history.

    Schema for each ledger event:

    .. code-block:: toml

        [[ledger]]
        sync_id         = "lgr-000001"
        parent_sync_ids = []              # empty list for the first event
        operation       = "clone"
        timestamp       = "2026-05-20T19:48:50.159Z"
        actor           = "user"
        workspace_hash  = "<sha256>"      # document.snapshot_hash from .gts
        gts_snapshot_id = "state(<hash>)" # links to [[snapshots]] entry
        affected_repos  = ["demo", "dep"]

    ``workspace_hash`` is the canonical SHA-256 digest of the ``.gts``
    snapshot (``GtsDocument.snapshot_hash``), linking each event directly
    to the immutable workspace state it records.
    """

    def __init__(self, register_path: Path | str) -> None:
        self.register_path = Path(register_path)

    def record_event(
        self,
        *,
        operation: str,
        workspace_hash: str,
        gts_snapshot_id: str,
        affected_repos: list[str],
        actor: str | None = None,
        clock: ClockProtocol | None = None,
    ) -> str:
        """Append an immutable event to the ledger and return the new ``sync_id``.

        Parameters
        ----------
        operation:
            The synchronisation operation that produced this event (e.g.
            ``"clone"``, ``"freeze_release"``, ``"checkout"``).
        workspace_hash:
            The canonical SHA-256 snapshot hash (``GtsDocument.snapshot_hash``)
            that identifies the workspace state after the operation.
        gts_snapshot_id:
            The public State id (``state(HASH(.@))``) assigned by the
            :class:`LocalGitRegister` for the same ``.gts`` file.
        affected_repos:
            Ordered list of repository names involved in the operation.
        actor:
            The system user or process that triggered the operation.  When
            ``None``, the current OS user name is detected automatically.
        clock:
            Names ``timestamp`` — real by default
            (:class:`~..universal_clock.SystemClock`).
        """
        data = self._load()
        events: list[dict[str, Any]] = data.setdefault("ledger", [])

        sync_id = self._next_event_id(events)
        parent_ids: list[str] = (
            [str(events[-1]["sync_id"])] if events and isinstance(events[-1], dict) and events[-1].get("sync_id") else []
        )

        timestamp = (
            (clock or SystemClock())
            .now()
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        resolved_actor = actor if actor is not None else _get_actor()

        events.append(
            {
                "sync_id": sync_id,
                "parent_sync_ids": parent_ids,
                "operation": operation,
                "timestamp": timestamp,
                "actor": resolved_actor,
                "workspace_hash": workspace_hash,
                "gts_snapshot_id": gts_snapshot_id,
                "affected_repos": affected_repos,
            }
        )

        self.register_path.parent.mkdir(parents=True, exist_ok=True)
        self.register_path.write_text(tomli_w.dumps(data), encoding="utf-8")
        return sync_id

    def history(self) -> list[dict[str, Any]]:
        """Return all ledger events in topological DAG order (parents first)."""
        data = self._load()
        return _topological_sort_events(list(data.get("ledger", [])))

    def replay(self) -> list[dict[str, Any]]:
        """Return events in topological order for deterministic replay.

        Alias for :meth:`history`.  Iterating the result in sequence
        reconstructs the workspace evolution from first operation to last.
        """
        return self.history()

    def _load(self) -> dict[str, Any]:
        if not self.register_path.is_file():
            return {"register": {}, "snapshots": [], "ledger": []}
        return tomllib.loads(self.register_path.read_text(encoding="utf-8"))

    def _next_event_id(self, events: list[dict[str, Any]]) -> str:
        """Return the next sequential event id in ``lgr-XXXXXX`` format."""
        max_id = 0
        for entry in events:
            raw_id = str(entry.get("sync_id", ""))
            if raw_id.startswith("lgr-"):
                try:
                    max_id = max(max_id, int(raw_id.removeprefix("lgr-")))
                except ValueError:
                    continue
        return f"lgr-{max_id + 1:06d}"

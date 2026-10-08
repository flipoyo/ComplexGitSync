"""memory_facts — Read-only facts the memory commands compute: what a State's ledger says, and who a memory belongs to.

Ring: 3
Contract: Read-only facts the memory commands compute: what a State's ledger says, and who a memory belongs to.
Imports: cgs_format, errors, git_repo, gts_document, memory, orchestre, registry, status_render
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..cgs_format import CgsDocument, parse_repo_id
from ..errors import (
    ConfigValidationError,
)

if TYPE_CHECKING:
    pass
from ..git_repo import (
    AccessProtocol,
    GitProvider,
    WorkingRepo,
    repo_remote_url,
)
from ..gts_document import GtsDocument
from ..memory import (
    Finding,
)
from ..memory.commit_log import (
    COMMIT_LOG_DIR_NAME,
    CommitLog,
)
from ..memory.environment import EnvironmentStore
from ..memory.pending import (
    PendingMemory,
)
from ..memory.repository import (
    SELF_HISTORY_SUBDIR_NAME,
    MemoryRepository,
)
from ..memory.states import (
    MemoryStates,
)
from ..registry import (
    RegistryTranslator,
)
from ..status_render import (
    _status_scope_label,
)

_FREEZE_COMMAND_ORIGINS = frozenset({"freeze", "freeze_release", "freeze_state"})


class MemoryFacts:
    """Read-only facts the memory commands compute: what a State's ledger says, and who a memory belongs to."""

    @staticmethod
    def release_snapshot_slug(release_name: str) -> str:
        """Return a filesystem-friendly release suffix for immutable .gts files."""
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", release_name.strip()).strip(".-_")
        return slug or "release"

    @staticmethod
    def verify_states_on_disk(
        workspace: Path,
        entries: Sequence[Any],
    ) -> list[tuple[int, Finding, str]]:
        """Cross-reference the chain against the States actually on disk.

        Three questions that became answerable only once a State was named by
        its content: an entry naming a State nobody can find
        (``MISSING_STATE``), a State nobody recorded (``ORPHAN_STATE``), and a
        stored snapshot whose content no longer hashes to the name it is filed
        under (``STATE_DIGEST_MISMATCH``).

        The third is the one the naming change bought outright: before, a
        State's name was a timestamp, so its contents could be edited freely and
        nothing about the name would disagree.
        """
        cgitsync_dir = workspace / ".cgitsync"
        findings: list[tuple[int, Finding, str]] = []
        recorded: dict[str, int] = {}

        for entry in entries:
            state_hash = MemoryStates.parse_hash(entry.state_id)
            if state_hash is None:
                continue
            recorded.setdefault(state_hash, entry.seq)
            snapshot = PendingMemory(cgitsync_dir).state_path(state_hash)
            if snapshot is None:
                findings.append((
                    entry.seq,
                    Finding.MISSING_STATE,
                    f"entry names {entry.state_id}, which is not on disk",
                ))
                continue
            try:
                document = GtsDocument.from_toml(snapshot)
                digest = document.compute_snapshot_hash()
            except (OSError, tomllib.TOMLDecodeError, ConfigValidationError) as exc:
                findings.append((
                    entry.seq,
                    Finding.STATE_DIGEST_MISMATCH,
                    f"{snapshot.name} could not be read: {exc}",
                ))
                continue
            if digest != state_hash:
                findings.append((
                    entry.seq,
                    Finding.STATE_DIGEST_MISMATCH,
                    f"{snapshot.name} now hashes to {digest}",
                ))

        for snapshot in PendingMemory(cgitsync_dir).state_files():
            if snapshot.stem not in recorded:
                findings.append((
                    0,
                    Finding.ORPHAN_STATE,
                    f"{snapshot.name} is on disk and no entry records it",
                ))
        return findings

    @staticmethod
    def normalise_state_argument(state: str) -> str:
        """Strip a decoration a State's name is commonly printed or cited with,
        so pasting it back verbatim works the same as typing the bare prefix.

        Handles, in order: the full ``state(<hash>)`` id (`_parse_state_hash`,
        the same form a ledger entry and `self-history add --state-before`
        both use) and the ``state=`` label `memory explore --timeline` prints
        each row under — the label reads exactly like the value to give back,
        which is precisely the mistake a real user made with it. Anything else
        passes through unchanged: an ordinary bare prefix, typed by hand.
        """
        parsed = MemoryStates.parse_hash(state)
        if parsed is not None:
            return parsed
        if state.startswith("state="):
            return state[len("state=") :]
        return state

    @staticmethod
    def normalise_environment_argument(env_ref: str) -> str:
        """`_normalise_state_argument`'s sibling for an Environment reference:
        strips the full ``env(<hash>)`` id (the form `memory show`'s own
        ``environment=`` line prints it in) and a leading ``env=`` label,
        leaving a bare prefix typed by hand unchanged."""
        parsed = EnvironmentStore.parse_hash(env_ref)
        if parsed is not None:
            return parsed
        if env_ref.startswith("env="):
            return env_ref[len("env=") :]
        return env_ref

    @staticmethod
    def resolve_ledger_state(cgitsync_dir: Path, state_id: str) -> str | None:
        """The state hash *state_id* names, verified against the ledger — or
        ``None`` when it does not hold up.

        The same three questions `verify`'s own `MISSING_STATE`/
        `STATE_DIGEST_MISMATCH` findings ask (`_verify_states_on_disk`, above),
        applied to one citation rather than a whole chain: does some ledger
        entry actually name this state_id, is the State it names on disk, and
        does that file's content still hash to the name it is filed under. A
        `.gts` is a static snapshot — once a tree is READY it does not
        re-discover anything — so "is this a real State" is never a question
        of re-parsing `.cgs` or re-deriving something from a transformed `.gts`;
        it is a question of what the ledger itself recorded, exactly the way
        `verify` already answers it. AgentReport WP3: a self-history record
        citing a state that fails any of the three is not naming "a State the
        work moved between" — a fake or malformed reference is refused here
        (`self_history_add`) rather than recorded as though it were fact.
        """
        state_hash = MemoryStates.parse_hash(state_id)
        if state_hash is None:
            return None
        entries = PendingMemory(cgitsync_dir).read_ledger_entries()
        if not any(entry.state_id == state_id for entry in entries):
            return None
        snapshot = PendingMemory(cgitsync_dir).state_path(state_hash)
        if snapshot is None:
            return None
        try:
            document = GtsDocument.from_toml(snapshot)
            digest = document.compute_snapshot_hash()
        except (OSError, tomllib.TOMLDecodeError, ConfigValidationError):
            return None
        return state_hash if digest == state_hash else None

    @staticmethod
    def repos_written_between(
        cgitsync_dir: Path, workspace: Path, before_hash: str, after_hash: str
    ) -> list[tuple[str, str]] | None:
        """Which repositories changed between two verified States, and their
        scope — AgentReport WP4/D5's "how" for ``repos_written``: **observed**
        by diffing two `.gts` snapshots' own ``commit_sha`` per repository,
        rather than typed by the orchestrator.

        A `.gts` already names, per repository, exactly the fact this needs
        (`gts_document.py`'s canonical fields include `commit_sha`) — the same
        "trust what was already verified" principle `_resolve_ledger_state`
        applies to a single citation applies here to a pair of them. Returns
        ``None`` when either snapshot fails to load, so the caller can fall
        back to whatever the orchestrator declared rather than record a false
        empty diff.
        """
        try:
            before_doc = GtsDocument.from_toml(PendingMemory(cgitsync_dir).state_path(before_hash))
            after_doc = GtsDocument.from_toml(PendingMemory(cgitsync_dir).state_path(after_hash))
            before_tree = RegistryTranslator.from_gts_document(before_doc, tree_root=workspace)
            after_tree = RegistryTranslator.from_gts_document(after_doc, tree_root=workspace)
        except (OSError, tomllib.TOMLDecodeError, ConfigValidationError, TypeError):
            return None
        before_shas = {repo.name: repo.commit_sha for repo in before_tree.values()}
        written: list[tuple[str, str]] = []
        for repo in after_tree.values():
            if before_shas.get(repo.name) != repo.commit_sha:
                written.append((repo.name, _status_scope_label(repo)))
        return written

    @staticmethod
    def identifier_of(remote_url: str) -> str:
        """The `.cgs` spelling of a remote URL, for a message that names a command.

        Best effort and used only in prose: a URL this cannot read back is
        printed as itself, which is still the thing the reader has to act on.
        """
        trimmed = remote_url.removesuffix(".git")
        if ":" in trimmed and "@" in trimmed:
            host, _, path = trimmed.partition(":")
            host = host.rpartition("@")[2]
        else:
            parts = trimmed.split("/")
            host, path = (parts[2], "/".join(parts[3:])) if len(parts) > 3 else ("", trimmed)
        provider = {"github.com": "github", "gitlab.com": "gitlab", "codeberg.org": "codeberg"}.get(
            host, ""
        )
        return f"{provider}:{path}" if provider and path else remote_url

    @staticmethod
    def remote_url(identifier: str) -> str:
        """:meth:`remote_url_for_identifier`, looked up through the package.

        ``orchestre._remote_url_for_identifier`` is the name callers patch to
        point a memory at a local remote, so every caller in this package goes
        through the attribute at call time rather than binding the function.
        """
        from .. import orchestre

        return orchestre._remote_url_for_identifier(identifier)

    @staticmethod
    def remote_url_for_identifier(identifier: str) -> str:
        """The SSH remote URL a `.cgs` identifier points at.

        `repo_remote_url` builds a URL from a repository *object*; this is the
        same answer starting from the written form, which is what a command
        given ``github:flipoyo/.memory`` on a command line has. The identifier
        is parsed by `parse_repo_id` and by nothing else, as everywhere.
        """
        identity = parse_repo_id(identifier)
        return repo_remote_url(
            WorkingRepo(
                project_owner_name=identity["project_owner_name"],
                project_name=identity["project_name"],
                repo_name=identity["repo_name"],
                gitprovider=GitProvider(identity["gitprovider"]),
            ),
            AccessProtocol.SSH,
        )

    @staticmethod
    def self_history_identity_from_config(config_path: Path) -> tuple[str, str] | None:
        """``(owner, remote_url)`` for self-history, read from `.memory`'s own
        already-committed ``config-memory.cgs`` — or ``None`` when that file
        does not exist, which means this project has not adopted self-history
        at all.

        This is the one place self-history's identity is decided from —
        `self_history_adopt` writes the file once, on the machine that first
        bootstraps it; every reader here (an automatic adopt on a second
        machine, a clone) only ever repeats it back from that committed
        content, never re-derives it from a registry, a `.cgs` flag, or a
        runtime probe. ``self_history_adopt``'s own bootstrap write is the one
        exception, and it is exactly that: the one place this fact is *decided*
        rather than *read*.
        """
        if not config_path.is_file():
            return None
        document = CgsDocument.from_toml(config_path)
        self_history_repo = next(
            (repo for repo in document.repos if repo.get("relative_path") == SELF_HISTORY_SUBDIR_NAME),
            None,
        )
        if self_history_repo is None:
            return None
        owner = str(self_history_repo["project_owner_name"])
        return owner, MemoryFacts.remote_url(MemoryRepository.self_history_repository_id(owner))

    @staticmethod
    def verify_commit_logs(
        workspace: Path,
        entries: Sequence[Any],
    ) -> list[tuple[int, Finding, str]]:
        """Check the commit messages against the chain that vouched for them.

        Two questions. **Is the log still what the entry signed?** — an entry
        records the digest of the rows it wrote, so a row edited, added or
        removed afterwards no longer matches and is reported
        (``COMMIT_LOG_MISMATCH``). **Is there a log for a State nobody holds?**
        — messages kept under a State that is not on disk
        (``ORPHAN_COMMIT_LOG``).

        The second is reported and never repaired. Deleting a record because the
        thing beside it went missing is how a record stops being one — the same
        rule the ledger itself follows.
        """
        cgitsync_dir = workspace / ".cgitsync"
        folded_dir, pending_dir = PendingMemory(cgitsync_dir).dirs()
        if not (folded_dir / COMMIT_LOG_DIR_NAME).is_dir() and not (pending_dir / COMMIT_LOG_DIR_NAME).is_dir():
            return []
        findings: list[tuple[int, Finding, str]] = []

        known = {entry.seq: entry for entry in entries}
        grouped = PendingMemory(cgitsync_dir).commit_log_rows()
        for entry in entries:
            committed, published = grouped.get(entry.seq, ([], []))
            if not committed and not published:
                if entry.commit_log:
                    findings.append((
                        entry.seq,
                        Finding.COMMIT_LOG_MISMATCH,
                        "entry records a commit log whose rows are gone",
                    ))
                continue
            if not entry.commit_log:
                findings.append((
                    entry.seq,
                    Finding.COMMIT_LOG_MISMATCH,
                    f"{len(committed) + len(published)} row(s) name an entry "
                    "that recorded no commit log",
                ))
                continue
            try:
                digest = CommitLog.digest_of_rows(committed, published)
            except TypeError as exc:
                findings.append((
                    entry.seq,
                    Finding.COMMIT_LOG_MISMATCH,
                    f"a commit row could not be read back: {exc}",
                ))
                continue
            if digest != entry.commit_log:
                findings.append((
                    entry.seq,
                    Finding.COMMIT_LOG_MISMATCH,
                    f"rows now digest to {digest}, entry recorded {entry.commit_log}",
                ))

        for seq in sorted(set(grouped) - set(known)):
            committed, published = grouped[seq]
            findings.append((
                seq,
                Finding.COMMIT_LOG_MISMATCH,
                f"{len(committed) + len(published)} row(s) name entry {seq}, "
                "which the chain does not have",
            ))

        for state_hash in PendingMemory(cgitsync_dir).state_hashes_with_logs():
            if PendingMemory(cgitsync_dir).state_path(state_hash) is None:
                findings.append((
                    0,
                    Finding.ORPHAN_COMMIT_LOG,
                    f"{state_hash[:12]} has commit messages and no State",
                ))
        return findings

    @staticmethod
    def workspace_of_snapshot(snapshot_path: Path) -> Path | None:
        """The workspace a snapshot belongs to: the directory holding its `.cgitsync`.

        A State lives at ``<workspace>/.cgitsync/state/<hash>.gts``, so the
        workspace is found by walking up — the same rule discovery uses, and the
        one answer that does not depend on an environment variable or a working
        directory. **Cloned onto another machine, this is what makes the memory
        resolve to the new tree rather than the old one's paths.**

        ``None`` for a snapshot that is not inside a workspace — a loose file
        somebody passed to ``--gts``. Guessing its own directory would be worse
        than saying nothing: the document then answers from its own recorded
        root, which is right, where a guess would silently rebuild the tree in
        the wrong place.
        """
        for candidate in (snapshot_path.parent, *snapshot_path.parents):
            if (candidate / ".cgitsync").is_dir():
                return candidate
        return None

    @staticmethod
    def next_reboot_cgs_version(cgs_dir: Path, project_name: str) -> int:
        """The `-v<N>` a `memory reboot` export should carry next.

        The topology before any reboot is implicitly `v1` and is never written
        under that name (`memory-dev_1-4_MemoryReboot_DevPlanTicket.md` §2), so
        an empty directory answers `2` — one more than the unwritten `v1` —
        and every later reboot answers one more than the highest version
        already sitting beside it. Never reused, never chosen: found by
        scanning, the same discipline a State's own name already follows.
        """
        pattern = re.compile(rf"^{re.escape(project_name)}-v(\d+)\.cgs$")
        highest = 1
        if cgs_dir.is_dir():
            for path in cgs_dir.iterdir():
                match = pattern.match(path.name)
                if match:
                    highest = max(highest, int(match.group(1)))
        return highest + 1


__all__ = [
    "MemoryFacts",
]

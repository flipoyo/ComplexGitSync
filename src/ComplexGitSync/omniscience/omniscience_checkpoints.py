"""omniscience_checkpoints — the global index on the ``_global`` branch: read it, check it, extend it.

Ring: 3
Contract: read the chain of checkpoints the remote holds, decide which
    records are *accepted* (every record an earlier checkpoint covered, plus
    each proposed one that validates), and append one checkpoint covering them
    when the set grew. Reports, and never repairs, a chain that was rewritten,
    forked or broken, and lists every file it did not accept with the reason.
Imports: errors, global_index, memory_facts, merkle, omniscience_appender, omniscience_reader, omniscience_record, omniscience_repository, operating_space

A record somebody added with plain ``git`` is only *proposed*. It is accepted
when it parses, is named by its own hash, sits on the branch its project and
branch name, carries the ``os_id`` of its project, is not for a reserved name,
and its memory commit can still be fetched from the memory repository it
names; otherwise it stays where it is, visible, and is listed as unaccepted,
and never reaches ``H_GLOBAL``. A record that cannot be checked now, because
its memory repository cannot be read from here, is unaccepted too: it is
tried again at the next checkpoint, and somebody who can read it accepts it
meanwhile. Acceptance is sticky: a record an earlier checkpoint covered is
never re-judged, only required to still be there.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomli_w

from ..errors import ConfigValidationError, GitSyncError
from ..merkle import MerkleRoot
from ..operating_space.operating_space import OperatingSpace
from ..orchestre.memory_facts import MemoryFacts
from .global_index import GlobalIndex
from .omniscience_appender import RegisterAppender
from .omniscience_reader import RegisterReader
from .omniscience_record import OmniscienceRecord
from .omniscience_repository import GLOBAL_BRANCH, OmniscienceRepository

if TYPE_CHECKING:
    from ..orchestre.client import ComplexGitSyncClient

CHECKPOINT_DIR = "checkpoints/"

#: Inside the mount's own ``.git``: the last ``_global`` tip this tree found sound. A fetch by anything
#: else moves ``refs/remotes/origin/_global``, so that ref alone cannot say what this tree last saw.
SEEN_FILE = "omniscience-seen-global"


class GlobalCheckpoints:
    """The checkpoint chain of one mounted register."""

    def __init__(self, client: ComplexGitSyncClient, mount: Path) -> None:
        self.client = client
        self.mount = mount
        self.reader = RegisterReader(client.git_runner, mount)
        self._memory: dict[tuple[str, str], str | None] = {}

    # -- what this tree last saw --------------------------------------------

    def _seen(self) -> str | None:
        """The ``_global`` tip this tree last found sound, else the remote-tracking ref as cloned (a first run)."""
        try:
            seen = (self.mount / ".git" / SEEN_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            seen = ""
        return seen or self.client.git_runner.ref_sha(self.mount, f"refs/remotes/origin/{GLOBAL_BRANCH}")

    def last_seen(self) -> str | None:
        """What this tree last found sound, for a reader that only compares and never remembers."""
        return self._seen()

    def _remember(self, sha: str | None) -> None:
        if sha:
            try:
                (self.mount / ".git" / SEEN_FILE).write_text(sha + "\n", encoding="utf-8")
            except OSError:
                pass  # only a convenience for noticing a rewrite; the chain itself is checked every time

    # -- the chain ----------------------------------------------------------

    def chain(self, tip: str | None) -> tuple[dict[str, dict[str, Any]], str | None]:
        """Every checkpoint at *tip*, name to body, and the name of the head (``None`` when there is none).

        Raises ``ConfigValidationError`` naming the first thing wrong: a file not named by its own hash,
        a parent that is not there, or more than one head (the chain forked).
        """
        git = self.client.git_runner
        found: dict[str, dict[str, Any]] = {}
        for path in sorted(git.tree_blobs(self.mount, tip, CHECKPOINT_DIR)) if tip else ():
            text = git.show_file(self.mount, tip, path) or ""
            try:
                body = tomllib.loads(text)
            except tomllib.TOMLDecodeError as exc:
                raise ConfigValidationError(f"checkpoint {path} is not TOML: {exc}") from exc
            name = MerkleRoot.checkpoint_hash(body)
            if path != f"{CHECKPOINT_DIR}{name}.toml":
                raise ConfigValidationError(f"checkpoint {path} is not named by its own hash ({name[:12]})")
            found[name] = body
        for name, body in found.items():
            if body.get("parent") and body["parent"] not in found:
                raise ConfigValidationError(f"checkpoint {name[:12]} names a parent, {str(body['parent'])[:12]}, that is not in the chain")
        parents = {body.get("parent") for body in found.values()}
        heads = sorted(name for name in found if name not in parents)
        if len(heads) > 1:
            raise ConfigValidationError(f"the chain has {len(heads)} heads ({', '.join(h[:12] for h in heads)}): it forked")
        return found, (heads[0] if heads else None)

    # -- acceptance ---------------------------------------------------------

    def _problem_with(self, record: OmniscienceRecord, branch: str, verified: frozenset[str]) -> str | None:
        """Why *record*, filed on *branch*, is not acceptable; ``None`` when it is."""
        if record.project == GLOBAL_BRANCH or OmniscienceRepository.branch(record.project, record.project_branch) == GLOBAL_BRANCH:
            return f"{GLOBAL_BRANCH!r} is reserved for the index, so no project can be registered under it"
        if record.os_id != OperatingSpace.id_of(record.project):
            return "its os_id is not the id of its project's name"
        expected = OmniscienceRepository.branch(record.project, record.project_branch)
        if branch != expected:
            return f"it is filed on {branch!r}, but its project and branch name {expected!r}"
        if record.record_hash in verified:
            return None
        return self._memory_problem(record)

    def _memory_problem(self, record: OmniscienceRecord) -> str | None:
        key = (record.memory_repository, record.memory_commit)
        if key not in self._memory:
            self._memory[key] = self._ask_memory(*key)
        return self._memory[key]

    def _ask_memory(self, repository: str, commit: str) -> str | None:
        git = self.client.git_runner
        try:
            url = MemoryFacts.remote_url(repository)
        except (GitSyncError, ValueError, KeyError) as exc:
            return f"its memory repository {repository!r} is not an address this build can read ({exc})"
        refs, said = git.remote_refs(url)
        if refs is None:
            return f"its memory repository {repository} cannot be read from here, so its commit cannot be checked yet ({said.splitlines()[0] if said else 'no answer'})"
        if not git.remote_holds_commit(url, commit):
            return f"its memory repository {repository} does not hold commit {commit[:12]}"
        return None

    # -- extending ----------------------------------------------------------

    def extend(self, verified: frozenset[str] = frozenset()) -> dict[str, Any]:
        """Append a checkpoint covering every accepted record, when the accepted set grew.

        *verified* names records the caller has just checked in full (its own,
        against its own memory), so they are not asked of a remote again.
        Returns ``status`` (``extended``, ``unchanged``, ``rewritten``,
        ``refused``), the head's ``checkpoint`` name and ``global_root`` when
        there is one, ``records`` (how many it covers), ``unaccepted``, ``problem`` and ``retries``.
        """
        git = self.client.git_runner
        remembered = self._seen()
        outcome: dict[str, Any] = {"status": "unchanged", "checkpoint": None, "global_root": None, "records": 0, "unaccepted": [], "problem": None}

        def build(tip: str | None) -> dict[str, str] | None:
            outcome.update(status="unchanged", unaccepted=[], problem=None)
            if remembered and tip and remembered != tip and not git.is_ancestor(self.mount, remembered, tip):
                outcome.update(status="rewritten", problem=f"the remote's {GLOBAL_BRANCH!r} no longer contains the checkpoint this tree last saw ({remembered[:12]}); history was rewritten there. Nothing was extended and nothing was repaired.")
                return None
            try:
                chain, head = self.chain(tip)
            except ConfigValidationError as exc:
                outcome.update(status="refused", problem=f"the checkpoint chain is not sound: {exc}. Nothing was extended.")
                return None
            files = self._candidates()
            valid = {f.value.record_hash: f for f in files if isinstance(f.value, OmniscienceRecord)}
            covered = list(chain[head]["records"]) if head else []
            lost = [name for name in covered if name not in valid]
            if lost:
                outcome.update(status="refused", problem=f"{len(lost)} record(s) an earlier checkpoint covers are no longer in the register ({', '.join(n[:12] for n in lost[:3])}). Nothing was extended and nothing was repaired.")
                return None
            accepted = {name: valid[name].value for name in covered}
            for item in files:
                reason = item.problem
                if item.value is not None and item.value.record_hash not in accepted:  # type: ignore[union-attr]
                    reason = self._problem_with(item.value, item.branch, verified)  # type: ignore[arg-type]
                    if reason is None:
                        accepted[item.value.record_hash] = item.value  # type: ignore[union-attr]
                if reason is not None:
                    outcome["unaccepted"].append({"branch": item.branch, "path": item.path, "reason": reason})
            index = GlobalIndex.of(accepted.values())
            outcome.update(global_root=index.root, records=len(accepted), checkpoint=head)
            if (head and sorted(accepted) == sorted(chain[head]["records"])) or (not head and not accepted):
                return None
            body = index.checkpoint_body(parent=head or "")
            name = index.checkpoint_hash(parent=head or "")
            outcome.update(status="extended", checkpoint=name)
            return {f"{CHECKPOINT_DIR}{name}.toml": tomli_w.dumps(body)}

        appended = RegisterAppender(self.client, self.mount).append(GLOBAL_BRANCH, build, "checkpoint")
        outcome["retries"] = appended["retries"]
        if outcome["status"] in ("extended", "unchanged"):
            self._remember(appended["commit"])
        if outcome["status"] == "extended" and not appended["appended"]:
            outcome["status"] = "unchanged"  # somebody else's checkpoint already covered the same records
        return outcome

    def _candidates(self):
        """Every file under ``register/`` of every project branch of the remote."""
        files = []
        for branch, sha in sorted(self.reader.remote_branches().items()):
            if branch != GLOBAL_BRANCH:
                files.extend(self.reader.records(branch, sha))
        return files


__all__ = ["CHECKPOINT_DIR", "GlobalCheckpoints"]

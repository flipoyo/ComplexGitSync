"""autofix.repair_divergent_user — repairs a hash-chained private
repository whose branch diverged because two users or machines each wrote
to it independently between pulls.

Ring: 2 (orchestrates git_runner.py and memory/; imports no subprocess itself)
Contract: DivergentUserRepair.matches()/repair(), per the Repair protocol in
    base.py. Diagnoses and repairs a repository registered in
    CHAIN_SHAPED_REPOS (today: .memory's lgr/) whose git history carries a
    seq/prev chain a plain merge cannot see (memory/ledger_entry.py).
Imports: base, errors, memory, git_repo, git_runner

Design reference: .agent/.local/.localSpec/DevTickets/archive/20260923_Autofix_DevPlanTicket.md, sections 5-7. The
algorithm below is the hand-run rescue
(scripts/rescue_20260922_memory_ledger_splice.py) generalised: no
hardcoded refs, no hardcoded commit hashes.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import GitSyncError
from ..memory import integrity, ledger_store
from ..memory.ledger_entry import LedgerEntry, compute_entry_hash
from .base import CHAIN_SHAPED_REPOS, RepairOutcome, Situation, is_chain_shaped

if TYPE_CHECKING:
    from ..git_repo import WorkingRepo
    from ..git_runner import GitRunnerProtocol

#: Substrings this project has already seen twice for a divergence, on the
#: push side and the pull side respectively — the archived Autofix ticket (.agent/.local/.localSpec/DevTickets/archive/20260923_Autofix_DevPlanTicket.md) §1/§3.
_DIVERGENCE_MARKERS = (
    "[rejected]",
    "fetch first",
    "diverging branches",
    "not possible to fast-forward",
)

#: AdditionalSpecs.md's register schema: "the genesis entry carries prev =
#: 'sha256:' + '0' * 64" — duplicated here (ledger_entry._GENESIS_PREV is
#: private to that module) only for the empty-chain-at-the-merge-base edge
#: case; every real incident so far has had entries to chain from instead.
_GENESIS_PREV = "sha256:" + "0" * 64


class DivergentUserRepair:
    """See the module docstring."""

    name = "divergent_user"

    def matches(self, situation: Situation) -> bool:
        """A recognised divergence message, naming a repository this
        project already knows has a sequencing invariant (D1's registry).

        Declines a plain-text divergence (the error text matches, the
        repository is not registered) on purpose — that case merges fine
        with `git` alone and is not this repair's job.
        """
        error_lower = situation.source_error.lower()
        if not any(marker in error_lower for marker in _DIVERGENCE_MARKERS):
            return False
        return is_chain_shaped(situation.repo.name)

    def repair(self, situation: Situation, runner: "GitRunnerProtocol") -> RepairOutcome:
        """§5's algorithm, parametric over ``situation.repo`` and its
        current tracking branch. See the module docstring for what each
        step does and why; the eight steps map onto the ticket's §7
        exactly, in order."""
        repo = situation.repo
        repo_path = Path(repo.absolute_path)
        chain_dir = CHAIN_SHAPED_REPOS[repo.name]
        lgr_dir = repo_path / chain_dir

        upstream = runner.upstream_ref(repo_path)
        if upstream is None:
            raise GitSyncError(
                f"{repo.name}: no upstream configured; cannot diagnose a divergence."
            )
        runner.fetch(repo_path)

        local_ref = runner.rev_parse_head(repo_path)
        merge_base = runner.merge_base(repo_path, local_ref, upstream)
        if merge_base is None:
            raise GitSyncError(
                f"{repo.name}: {local_ref} and {upstream} share no history; "
                "refusing to guess at a repair."
            )

        try:
            runner.merge(repo_path, upstream, no_ff=True)
        except GitSyncError:
            pass  # conflicted, as expected — the merge is now in progress
        else:
            return RepairOutcome(
                repaired=False,
                detail=f"{repo.name}: {upstream} merged cleanly; nothing to splice.",
            )

        originals = self._read_new_entries(
            runner, repo, repo_path, chain_dir, merge_base, local_ref, upstream
        )
        first_new_seq = self._write_spliced_chain(
            runner, repo_path, chain_dir, lgr_dir, merge_base, originals
        )
        report = self._verify_or_abort(runner, repo, repo_path, lgr_dir)
        last_new_seq = first_new_seq + len(originals) - 1

        # §7 step 8 — stage and complete the merge commit. Machine-
        # generated, so this is not the commit-message rule's "written by
        # hand" case (AgentConduct.md §2's own carve-out).
        runner.stage_all(repo_path)
        entry_word = "entry" if len(originals) == 1 else "entries"
        message = (
            f"autofix(divergent_user): repaired {repo.name} — spliced "
            f"{len(originals)} colliding/orphaned {chain_dir}/ {entry_word} "
            f"into seq {first_new_seq}..{last_new_seq}, "
            f"verify_chain: {report.state.name}"
        )
        runner.commit(repo_path, message)

        return RepairOutcome(
            repaired=True,
            detail=(
                f"{repo.name}: merged and spliced {len(originals)} entries "
                f"(seq {first_new_seq}..{last_new_seq}); "
                f"verify_chain reported {report.state.name}."
            ),
        )

    def _read_new_entries(
        self,
        runner: "GitRunnerProtocol",
        repo: "WorkingRepo",
        repo_path: Path,
        chain_dir: str,
        merge_base: str,
        local_ref: str,
        upstream: str,
    ) -> list[dict]:
        """§7 steps 2-4: the union of both sides' entries new since
        *merge_base*, disjointness-checked, sorted by ``recorded_at``."""
        local_new = runner.added_paths(repo_path, merge_base, local_ref, subdir=chain_dir)
        remote_new = runner.added_paths(repo_path, merge_base, upstream, subdir=chain_dir)
        colliding = sorted(set(local_new) & set(remote_new))
        if not colliding:
            runner.merge_abort(repo_path)
            raise GitSyncError(
                f"{repo.name}: conflicted, but no colliding path under {chain_dir}/ "
                "was found — not this repair's job."
            )

        originals: list[dict] = []
        for ref, paths in ((local_ref, local_new), (upstream, remote_new)):
            for path in paths:
                text = runner.show_file(repo_path, ref, path)
                if text is None:
                    runner.merge_abort(repo_path)
                    raise GitSyncError(f"{repo.name}: could not read {path} at {ref}.")
                originals.append(tomllib.loads(text)["entry"])

        # §7 step 3 — refuse, do not guess, if the two sides are not
        # disjoint: two entries claiming the same recorded_at with
        # different content are not independent appends.
        seen_at: dict[str, tuple] = {}
        for entry in originals:
            key = (
                entry.get("command"),
                tuple(entry.get("argv", ())),
                entry.get("state_id"),
                entry.get("state_dir"),
                entry.get("outcome"),
            )
            recorded_at = entry["recorded_at"]
            if recorded_at in seen_at and seen_at[recorded_at] != key:
                runner.merge_abort(repo_path)
                raise GitSyncError(
                    f"{repo.name}: two entries share recorded_at={recorded_at} "
                    "with different content — not disjoint, refusing to splice."
                )
            seen_at[recorded_at] = key

        # §7 step 4 — chronological order, not "one side then the other".
        originals.sort(key=lambda e: e["recorded_at"])
        return originals

    def _write_spliced_chain(
        self,
        runner: "GitRunnerProtocol",
        repo_path: Path,
        chain_dir: str,
        lgr_dir: Path,
        merge_base: str,
        originals: list[dict],
    ) -> int:
        """§7 steps 5-6: recompute seq/prev/entry_hash for each of
        *originals*, chained from *merge_base*'s tip, and write them.
        Returns the first new seq number, for the commit message."""
        head_text = runner.show_file(repo_path, merge_base, f"{chain_dir}/HEAD")
        if head_text is not None:
            head_toml = tomllib.loads(head_text)["head"]
            prev_hash = head_toml["entry_hash"]
            next_seq = head_toml["seq"] + 1
        else:
            prev_hash = _GENESIS_PREV
            next_seq = 1
        first_new_seq = next_seq

        for seq in range(first_new_seq, first_new_seq + len(originals)):
            stale = ledger_store.entry_path(lgr_dir, seq)
            if stale.exists():
                stale.unlink()

        for original in originals:
            toolchain = tuple(sorted(original.get("toolchain", {}).items()))
            release = tuple(sorted(original.get("release", {}).items()))
            environment = original.get("environment", "")
            commit_log = original.get("commit_log", "")
            entry_hash = compute_entry_hash(
                seq=next_seq,
                prev=prev_hash,
                recorded_at=original["recorded_at"],
                command=original["command"],
                argv=original["argv"],
                state_id=original["state_id"],
                state_dir=original["state_dir"],
                outcome=original["outcome"],
                toolchain=toolchain,
                commit_log=commit_log,
                environment=environment,
                release=release,
            )
            entry = LedgerEntry(
                seq=next_seq,
                prev=prev_hash,
                recorded_at=original["recorded_at"],
                command=original["command"],
                argv=tuple(original["argv"]),
                state_id=original["state_id"],
                state_dir=original["state_dir"],
                outcome=original["outcome"],
                toolchain=toolchain,
                commit_log=commit_log,
                entry_hash=entry_hash,
                environment=environment,
                release=release,
            )
            ledger_store.write_entry(lgr_dir, entry)
            prev_hash = entry_hash
            next_seq += 1
        return first_new_seq

    def _verify_or_abort(
        self, runner: "GitRunnerProtocol", repo: "WorkingRepo", repo_path: Path, lgr_dir: Path
    ) -> integrity.VerificationReport:
        """§7 step 7: verify before ever committing. ``VERIFIED`` is the
        only passing result — anything else aborts outright, exactly as
        if this repair had never run."""
        entries = ledger_store.read_all_entries(lgr_dir)
        report = integrity.verify_chain(entries)
        if not report.is_verified:
            runner.merge_abort(repo_path)
            raise GitSyncError(
                f"{repo.name}: splice produced {report.state.name}, not VERIFIED "
                f"— aborted, nothing written. Findings: {report.findings}"
            )
        ledger_store.verify_and_repair_head(lgr_dir)
        return report

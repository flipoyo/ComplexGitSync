# First public release: ticket backlog

*Reorganized: 2026-09-11 — completed planning update; no implementation, commit, or push.*

## 1. Final active backlog and old-to-new mapping

[UpstreamBranchDisplay](../archive/20260911_UpstreamBranchDisplay_DevPlanTicket.md)
was already closed before this reorganization. Its archive was left unchanged.
The existing Priority 1 renumbering was retained; tickets were identified by
title to avoid confusing old and reused numbers.

| Final ID | Active ticket | Actual previous ID | Disposition |
|---|---|---|---|
| 1-1 | [GitLocaleIndependence](../archive/20260911_GitLocaleIndependence_DevPlanTicket.md) | 1-1 (originally 1-3) | Retained; plan corrected |
| 1-2 | [UnifiedProjectPrivateScope](../archive/20260911_UnifiedProjectPrivateScope_DevPlanTicket.md) | 1-2 | Retained; plan corrected |
| 1-3 | [StateMemory](../openTickets/1-1_StateMemory_DevPlanTicket.md) | 2-1 | Promoted to Priority 1 |
| 1-4 | [CliContract](../openTickets/1-2_CliContract_DevPlanTicket.md) | 2-5 | Promoted to Priority 1 |
| 1-5 | [UserInstallPath](../openTickets/1-3_UserInstallPath_DevPlanTicket.md) | 2-6 | Promoted to Priority 1 |
| 2-1 | [CliTypoSuggestion](../openTickets/2-1_CliTypoSuggestion_DevPlanTicket.md) | 2-2 | Renumbered; optional improvement |

During discussion, CliContract and UserInstallPath were provisionally called
2-2 and 2-3. Those intermediate filenames were not present when this update
ran; the table records the actual moves. The original closed 1-1 was
UpstreamBranchDisplay, whereas current 1-1 is GitLocaleIndependence.

Priority 1 implementation order is the table order. CliTypoSuggestion can
ship independently. Shared files and dependencies require coordination, not
merging tickets. No additional active tickets were created for deferred work.

## 2. Incorporated review corrections and release scope


The following decisions have been incorporated into the active ticket plans and acceptance criteria. Relevant code was rechecked after UpstreamBranchDisplay closed. These are implementation requirements for future work; this reorganization implemented none of them.

### 1-1 GitLocaleIndependence

- The proposed language settings must handle an inherited `LC_ALL`, which overrides `LC_MESSAGES`. The implementation plan and regression tests must cover this case.
- The original subprocess cleanup list omitted `local_only_commit_count()`. Recheck all direct Git subprocess calls after the completed UpstreamBranchDisplay changes; do not rely on the old four-method list.

### 1-2 UnifiedProjectPrivateScope

- Specify how `--all` interacts with merge previews and the existing interactive conflict-resolution mode. Ordinary merges must check both project and writable-private repositories before merging anything. Interactive resolution deliberately permits partial progress; document that distinction.
- Use one shared commit message for `commit --all`.
- The ignored `rm --private` and `freeze --private` flags are real bugs, but fixing them need not block the additive `--all` feature. Record their disposition explicitly instead of silently making them prerequisites.

### 1-3 StateMemory

- First-release scope covers reliable local state/history, meaningful verification, portable content identity, and compatibility with existing workspaces.
- Missing or legacy history must be distinguishable from successfully verified history. Address the misleading clean verification result as the first implementation step.
- The current content hash includes workspace paths. Specify portable hashing and compatibility with older snapshots; merely using the existing hash to name directories does not ensure identical identity across machines.
- The three store-level integrity finding names already exist; their checks remain to be implemented. Correct the old claim that the enum lacks them after confirming the current code.
- Publishing the register as its own repository remains an explicitly deferred milestone, not a first-release requirement.

### 1-4 CliContract

- First-release scope covers consistent exit codes, clean handling of expected errors, status/verification JSON, and documented compatibility promises.
- Cover exceptions that currently escape as tracebacks, not just explicit handler return values. JSON output guarantees must also cover failure paths.
- Defer validation JSON and JSON for all dry-run plans to explicitly recorded follow-up work. Align work packages and acceptance criteria with this reduced scope.
- Coordinate verification output with StateMemory, and compatibility/version promises with UserInstallPath.

### 1-5 UserInstallPath

- Require a tested user installation outside the source checkout, consistent version reporting, a repeatable release process, and clear user instructions. Declare prerequisites such as Git.
- Support only operating systems actually validated. Expanding platform coverage must not block the first release.
- The reported broken CI specification filename has already been corrected; verify the current workflow and remove that obsolete prerequisite.
- Check package-name availability before publication, but do not make it block unrelated preparation. Publication and external account actions are not authorized by this planning task.

### 2-1 CliTypoSuggestion

- Retain as an optional Priority 2 improvement.
- Suggestions go to stderr, preserve the invalid-command exit code, and never execute a suggested command automatically. Include cases ensuring options and their values are not mistaken for command names.

## 3. Archived without implementation

| Previous ID | Archived proposal | Closure disposition |
|---|---|---|
| 2-3 | [GitOrchestratorCommand](../archive/20260911_GitOrchestratorCommand_DevPlanTicket.md) | Not planned for the first release |
| 2-4 | [AnonymousAgent](../archive/20260911_AnonymousAgent_DevPlanTicket.md) | Not planned |

**GitOrchestratorCommand:** the proposal combines workspace selection,
configuration defaults, and project creation into an unsettled additional
format. Preserve the requirement that users understand which workspace a
command affects; concrete remaining failures can be handled separately. Its
old requirement that all private repositories remain on declared branches
conflicts with the current behavior of writable private repositories and must
be reassessed before reuse.

**AnonymousAgent:** the development-environment migration adds complexity
without improving the released tool's core behavior. Revisit only if a concrete
need emerges. Its installation-layout assumptions are outdated: developer
mounts belong in `examples/complexgitsync4dev.cgs`, while `install.cgs` is the
user installation.

Each archive carries a dated closure note and preserves the original proposal
verbatim below that note. Neither closure claims implementation. No proposed
migration, remote operation, freeze, fork, branch change, or project-creation
workflow was performed. Existing historical archives were not edited.

## 4. Validation and remaining decisions

- Active filenames are consecutive: Priority 1 has five tickets (1-1 through
  1-5); Priority 2 has one (2-1). Every retained title appears once.
- Active cross-ticket links and all links in this summary resolve. The old
  source-code links in StateMemory were corrected to be relative to its directory.
- Two pre-existing guidance targets remain unavailable in this checkout:
  `CLAUDE.md` is a symlink into the absent `.claude` mount, and
  `.agentSpec/TICKETLIFECYCLE.md` is absent. Their active-ticket references were
  preserved rather than inventing replacement guidance. Naming follows the
  available [TicketPriorityIds convention](../archive/20260910_TicketPriorityIds_DevPlanTicket.md):
  `<priority>-<rank>_` for active work and `YYYYMMDD_` for archived tickets.
- Changes are confined to six active plans, the two new archive files, and this
  summary. Existing archive contents and tracked non-planning files are unchanged.
- No runtime tests were run: this task changed planning documents only.

Implementation decisions remain explicitly recorded in the tickets: how to
handle inherited locale categories, the portable hash version and legacy
migration details, CLI error/schema semantics, and the release version scheme,
package ownership, and validated platform set. Package availability and remote
release configuration were not checked during this local reorganization.

Deferred work remains documented: ignored `rm`/`freeze` private flags and their
parser audit (1-2), register publication and its future gates (1-3), validation
and dry-run JSON (1-4), and additional platforms/standalone binaries (1-5).
These deferred items do not gate closure of their respective release tickets.

## 2. What has happened since — 2026-09-11

Two of the tickets above have shipped, so the table's *Final ID* column is
the record of what this review decided, not the current ranks. The links
have been repointed to where each file is now.

| Ticket | Then | Now |
|---|---|---|
| ReleaseDocsDebt | 2-2 | [archived](../archive/20260911_ReleaseDocsDebt_DevPlanTicket.md) |
| GitLocaleIndependence | 1-1 | [archived](../archive/20260911_GitLocaleIndependence_DevPlanTicket.md) |
| UnifiedProjectPrivateScope | 1-2 | [archived](../archive/20260911_UnifiedProjectPrivateScope_DevPlanTicket.md) |
| StateMemory | 1-3 | **1-1** |
| CliContract | 1-4 | **1-2** |
| UserInstallPath | 1-5 | **1-3** |
| CliTypoSuggestion | 2-1 | 2-1, unchanged |
| DeadScopeFlags | — | **2-2**, filed by UnifiedProjectPrivateScope |

Priority 1 was compacted each time a ticket shipped and now runs 1..3;
priority 2 runs 1..2 since DeadScopeFlags was appended to it. The order within each pile is unchanged — this was
closing a gap, not a re-ranking, which is what a Ticket review does.

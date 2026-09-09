# The `.gts` silently drops `writable`

*Created: 2026-09-09*

> **Answered and fixed on 2026-09-09.** A `.gts` written by code predating
> the field still loses it — that half is unavoidable, since the older build
> cannot write a key it does not know. What is fixed is the damage: the loss
> is no longer permanent. `registry.reconcile_declared_fields` re-reads
> `private`, `writable` and `default_branch` from the `.cgs` every time a
> snapshot is loaded, so a lossy snapshot heals on the next command instead
> of poisoning every one after it. It reads the *declaring parent's*
> document, because a mount's privacy is declared by whatever mounts it.
>
> The principle it settled: a `.cgs` is hand-written and says what a
> repository **is**; a `.gts` is generated and says what the tree's **state**
> is. A declared fact comes from the `.cgs`. The account is in
> `AgentSpec/archive/20260909_MergeAndPrivateBranch_DevPlanTicket.md` §8.

## The original note

Also worth its own ticket: the .gts silently drops writable when written by code that predates the field. It bit twice today and I had to rebuild the state both times.

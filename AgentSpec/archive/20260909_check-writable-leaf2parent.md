# Does private/writable propagate from a parent to its leaves?

*Created: 2026-09-08*

> **Answered and fixed on 2026-09-09.** The rule asked for here is
> implemented as `git_tree.propagate_privacy`, which walks the tree
> root-first and pushes each parent's `private`/`writable` onto everything
> nested inside it. A leaf under a private parent is private; a leaf that
> declares nothing takes its parent's writability; a leaf that declares its
> own flags keeps them, capped by the parent — it may restrict itself
> further, never open itself wider than the repository holding it. The
> answers land in `WorkingRepo.propagated_private`/`propagated_writable` and
> are read through `effective_private`/`effective_writable`; the declared
> flags are left untouched so serializing a `.cgs` still writes what its
> author wrote. Tests: `tests/unit/test_repo_scope.py`. The full account is
> in `AgentSpec/archive/20260909_MultiBranchResume_DevPlanTicket.md` §7.
>
> The vocabulary moved too: the field asked about as `pinned` is now
> spelled `private`.

## The original note

does the pinned read-only or pinned writable of a parent properly propagates in GitTree. It is very important for the leaf->parent->root sequence. The rule is the parent defines the leaves even though they do contain other parents. It is a bottom up approach. It is a property of GitRepo

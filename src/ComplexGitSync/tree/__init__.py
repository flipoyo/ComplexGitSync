"""tree — the GitTree ``cgitsync`` mounts and operates.

Ring: 0 (the package itself; its modules say their own rings)
Contract: the documents (``.cgs``, ``.gts``), the tree structures, the memory,
    the Git actions and the repairs. It may import ``git_repo/`` and
    ``merkle/``; it is imported by ``operating_space/``, ``orchestre/`` and
    ``cli/``. Imports nothing at load time, so any module here can be
    imported alone.
Imports: none
"""

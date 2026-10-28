"""operating_space — the Operating Space and the API that serves it.

Ring: 2 (the interface between the file tree and the Git actions)
Contract: ``OperatingSpace``, the one module of this folder. It may import
    everything below it (``tree/``, ``git_repo/``, ``merkle/``).
Imports: operating_space

Re-exports what the module ``operating_space`` held before ``SrcLayout``.
"""

from .operating_space import OperatingSpace

__all__ = ["OperatingSpace"]

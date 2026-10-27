"""merkle — every hash this project computes, behind one door that never shows a tree.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: re-export ``MerkleRoot`` and its two proof types. Nothing here imports
    anything else of ``ComplexGitSync``: the folder can become a repository of its own.
Imports: none
"""

from .merkle_root import GlobalInclusionProof, InclusionProof, MerkleError, MerkleRoot

__all__ = ["GlobalInclusionProof", "InclusionProof", "MerkleError", "MerkleRoot"]

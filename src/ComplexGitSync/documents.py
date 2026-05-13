"""Parsing, validation, and serialization for .cgs, .gts, and .goc documents.

Hierarchy
---------
ConfigDocument          – base class: read / get / print / from_* / to_*
  CgsDocument           – local authoring spec (.cgs, .toml)
  GtsDocument           – generated Git Tree State snapshot (.gts, .toml)
  GocDocument           – Git Orchestration Command script (.goc, .toml)

Each document class lives in its own module (DevSpecs § Object-Oriented
Design).  This module re-exports all four classes for backward compatibility.
"""

from __future__ import annotations

from .cgs_document import CgsDocument
from .config_document import ConfigDocument
from .goc_document import GocDocument
from .gts_document import GtsDocument

__all__ = [
    "CgsDocument",
    "ConfigDocument",
    "GocDocument",
    "GtsDocument",
]

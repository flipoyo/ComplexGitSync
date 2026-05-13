"""Dependency-tree node type enumeration."""

from __future__ import annotations

from enum import StrEnum


class NodeType(StrEnum):
    """Role of a repository node within the dependency tree."""

    ROOT = "root"
    PARENT = "parent"
    LEAF = "leaf"

from __future__ import annotations

import pytest

from CGS import CandidateState, Graph, MemorySystem, ServerGateway


@pytest.fixture
def graph() -> Graph:
    return Graph("Demo", {"kind": "node"}, [{"from": "a", "to": "b"}], "sync")


@pytest.fixture
def candidate() -> CandidateState:
    return CandidateState(
        "Demo",
        {"tree": ["a", "b"]},
        {"tree": ["a", "b"]},
        {"runtime": "kept private"},
    )


@pytest.fixture
def memory_system() -> MemorySystem:
    return MemorySystem()


@pytest.fixture
def server_gateway() -> ServerGateway:
    return ServerGateway()

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .drivers import EchoValueDriver, ValueDriver, _driver_accepts
from .errors import CGSContractError, CGSError, ErrorCode
from .gateway import Gateway, GatewayResult
from .graph import Graph
from .living_graph import LivingGraph
from .serialization import canonical_json, freeze_json
from .state import CandidateState, State
from .state_id import _validate_digest
from .state_core_graph import StateCoreGraph
from .state_ontology import StateOntology


@dataclass(frozen=True, slots=True)
class ServerPublication:
    graph_binding: str
    gateway_binding: str
    state_id: str
    state_digest: str
    state_ontology: StateOntology
    state_core_graph: StateCoreGraph
    operations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_binding": self.graph_binding,
            "gateway_binding": self.gateway_binding,
            "state_id": self.state_id,
            "state_digest": self.state_digest,
            "state_ontology": self.state_ontology.to_dict(),
            "state_core_graph": self.state_core_graph.to_dict(),
            "operations": list(self.operations),
        }


def _canonical_publication(
    living_graph: LivingGraph,
    state_ontology: StateOntology,
    state_core_graph: StateCoreGraph,
) -> ServerPublication | CGSError:
    state = living_graph.state
    if (
        type(state) is not State
        or state.graph_name != state_ontology.name
        or state_ontology != StateOntology.from_graph(living_graph.graph)
        or state_core_graph != StateCoreGraph.for_graph(living_graph.graph.name)
        or living_graph.graph.digest != state.graph_binding
        or living_graph.gateway != Gateway(living_graph.graph)
        or living_graph.gateway.binding != state.gateway_binding
        or state_core_graph.graph_name != state.graph_name
    ):
        return CGSError(
            ErrorCode.SERVER_REJECTED,
            "Server accepts only matching validated living projections",
            "server.prepare",
        )
    return ServerPublication(
        graph_binding=state.graph_binding,
        gateway_binding=state.gateway_binding,
        state_id=state.state_id.value,
        state_digest=state.state_digest,
        state_ontology=state_ontology,
        state_core_graph=state_core_graph,
        operations=("state", "state-core", "validated-operation"),
    )


def _publication_from_blob(blob: str) -> ServerPublication:
    import json

    value = json.loads(blob)
    ontology_data = value["state_ontology"]
    ontology = StateOntology(
        name=ontology_data["name"],
        node=freeze_json(ontology_data["node"]),
        edge=freeze_json(ontology_data["edge"]),
        op=freeze_json(ontology_data["op"]),
    )
    core_data = value["state_core_graph"]
    core = StateCoreGraph(core_data["graph_name"], core_data["mermaid"])
    publication = ServerPublication(
        graph_binding=value["graph_binding"],
        gateway_binding=value["gateway_binding"],
        state_id=value["state_id"],
        state_digest=value["state_digest"],
        state_ontology=ontology,
        state_core_graph=core,
        operations=tuple(value["operations"]),
    )
    if _publication_blob(publication) != blob:
        raise ValueError("Server publication blob is not canonical")
    return publication


def _publication_blob(publication: ServerPublication) -> str:
    if type(publication) is not ServerPublication:
        raise TypeError("Server accepts ServerPublication values only")
    for label, value in (
        ("Graph binding", publication.graph_binding),
        ("Gateway binding", publication.gateway_binding),
        ("StateId", publication.state_id),
        ("State digest", publication.state_digest),
    ):
        _validate_digest(value, label)
    graph = Graph(
        publication.state_ontology.name,
        publication.state_ontology.node,
        publication.state_ontology.edge,
        publication.state_ontology.op,
    )
    if (
        publication.graph_binding != graph.digest
        or publication.gateway_binding != Gateway(graph).binding
        or publication.state_core_graph != StateCoreGraph.for_graph(graph.name)
        or publication.operations != ("state", "state-core", "validated-operation")
    ):
        raise ValueError("Server publication binding is invalid")
    return canonical_json(publication.to_dict())


class ServerGateway:
    """Sealed physical wrapper around a detached-value driver."""

    __slots__ = ("name", "_driver", "_publication_blobs")

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("ServerGateway is sealed; compose a ValueDriver")

    def __init__(
        self,
        name: str = "@LOCALHOST@G",
        *,
        driver: ValueDriver | None = None,
    ) -> None:
        if not name:
            raise ValueError("ServerGateway.name must be non-empty")
        self.name = name
        self._driver = driver or EchoValueDriver()
        self._publication_blobs: tuple[str, ...] = ()

    @property
    def publications(self) -> tuple[ServerPublication, ...]:
        return tuple(_publication_from_blob(blob) for blob in self._publication_blobs)

    def _accept(self, operation: str, value: Any) -> bool:
        return _driver_accepts(self._driver, operation, canonical_json(value))

    def listen(self, gateway: Gateway, candidate: CandidateState) -> GatewayResult:
        canonical = Gateway.listen(gateway, candidate)
        message = {
            "gateway_binding": gateway.binding,
            "candidate": candidate.to_dict(),
        }
        return canonical if self._accept("listen", message) else self._gateway_rejection("listen")

    def interpret(self, gateway: Gateway, listened: GatewayResult) -> GatewayResult:
        canonical = Gateway.interpret(gateway, listened)
        message = {
            "gateway_binding": gateway.binding,
            "candidate": listened.value.to_dict()
            if type(listened.value) is CandidateState
            else None,
        }
        return (
            canonical
            if self._accept("interpret", message)
            else self._gateway_rejection("interpret")
        )

    def validate(self, gateway: Gateway, interpreted: GatewayResult) -> GatewayResult:
        canonical = Gateway.validate(gateway, interpreted)
        value = interpreted.value
        message = {
            "gateway_binding": gateway.binding,
            "candidate_digest": getattr(value, "candidate_digest", None),
            "left": getattr(value, "left_json", None),
            "right": getattr(value, "right_json", None),
        }
        return (
            canonical if self._accept("validate", message) else self._gateway_rejection("validate")
        )

    def transfer(
        self,
        gateway: Gateway,
        validated: GatewayResult,
        state: State,
    ) -> GatewayResult:
        canonical = Gateway.transfer(gateway, validated, state)
        message = {
            "gateway_binding": gateway.binding,
            "state": {**state.to_memory_data(), "state_digest": state.state_digest},
        }
        return (
            canonical if self._accept("transfer", message) else self._gateway_rejection("transfer")
        )

    def emit_state_ontology(self, gateway: Gateway, transferred: GatewayResult) -> StateOntology:
        canonical = Gateway.emit_state_ontology(gateway, transferred)
        if not self._accept("emit_state_ontology", canonical.to_dict()):
            raise CGSContractError(ErrorCode.EMISSION_REJECTED, "physical emission rejected")
        return canonical

    def emit_state_core_graph(self, gateway: Gateway, transferred: GatewayResult) -> StateCoreGraph:
        canonical = Gateway.emit_state_core_graph(gateway, transferred)
        if not self._accept("emit_state_core_graph", canonical.to_dict()):
            raise CGSContractError(ErrorCode.EMISSION_REJECTED, "physical emission rejected")
        return canonical

    def prepare_publication(
        self,
        living_graph: LivingGraph,
        state_ontology: StateOntology,
        state_core_graph: StateCoreGraph,
    ) -> ServerPublication | CGSError:
        canonical = _canonical_publication(living_graph, state_ontology, state_core_graph)
        if type(canonical) is not ServerPublication:
            return canonical
        if not self._accept("prepare_publication", canonical.to_dict()):
            return CGSError(
                ErrorCode.SERVER_REJECTED,
                "physical publication preparation rejected",
                "server.prepare",
            )
        return canonical

    def publish(self, publication: ServerPublication) -> CGSError | None:
        try:
            blob = _publication_blob(publication)
        except (CGSContractError, TypeError, ValueError):
            return CGSError(
                ErrorCode.SERVER_REJECTED,
                "Server publication binding is invalid",
                "server.publish",
            )
        if not self._accept("publish", publication.to_dict()):
            return CGSError(
                ErrorCode.SERVER_REJECTED,
                "physical publication rejected",
                "server.publish",
            )
        self._publication_blobs = self._publication_blobs + (blob,)
        return None

    def _journal_snapshot(self) -> tuple[str, ...]:
        return tuple(self._publication_blobs)

    def _restore_journal(self, snapshot: tuple[str, ...]) -> None:
        self._publication_blobs = tuple(snapshot)

    @staticmethod
    def _gateway_rejection(stage: str) -> GatewayResult:
        return GatewayResult(
            stage=None,
            error=CGSError(ErrorCode.SERVER_REJECTED, "physical Gateway rejected", stage),
        )

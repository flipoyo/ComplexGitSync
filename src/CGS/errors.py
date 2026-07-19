from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorCode(str, Enum):
    INVALID_GRAPH = "invalid_graph"
    INVALID_CANDIDATE = "invalid_candidate"
    PARTIAL_STATE = "partial_state"
    GRAPH_NAME_MISMATCH = "graph_name_mismatch"
    VALIDATION_FAILED = "validation_failed"
    INVALID_PIPELINE_STAGE = "invalid_pipeline_stage"
    OWNERSHIP_VIOLATION = "ownership_violation"
    INVALID_AUTHORITATIVE_STATE = "invalid_authoritative_state"
    MEMORY_REJECTED = "memory_rejected"
    SERVER_REJECTED = "server_rejected"
    OPERATOR_FAILED = "operator_failed"


@dataclass(frozen=True, slots=True)
class CGSError:
    code: ErrorCode
    message: str
    stage: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message, "stage": self.stage}


class CGSContractError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.error = CGSError(code=code, message=message, stage="contract")
        super().__init__(message)


class OwnershipError(CGSContractError):
    pass

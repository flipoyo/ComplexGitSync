from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Protocol

from .errors import ErrorCode


@dataclass(frozen=True, slots=True)
class DriverReceipt:
    operation: str
    request_digest: str
    accepted: bool
    error_code: ErrorCode | None = None


class ValueDriver(Protocol):
    def handle(self, operation: str, message: str) -> DriverReceipt: ...


class EchoValueDriver:
    """Default detached-value physical driver."""

    __slots__ = ()

    def handle(self, operation: str, message: str) -> DriverReceipt:
        return _accepted_receipt(operation, message)


def _message_digest(message: str) -> str:
    return hashlib.sha256(b"CGS-DRIVER-v1\x00" + message.encode("utf-8")).hexdigest()


def _accepted_receipt(operation: str, message: str) -> DriverReceipt:
    return DriverReceipt(operation, _message_digest(message), True)


def _driver_accepts(driver: ValueDriver, operation: str, message: str) -> bool:
    detached = message.encode("utf-8").decode("utf-8")
    try:
        receipt = driver.handle(operation, detached)
    except Exception:
        return False
    return type(receipt) is DriverReceipt and receipt == _accepted_receipt(operation, detached)

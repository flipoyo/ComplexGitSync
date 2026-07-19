import hashlib
import json

from CGS import DriverReceipt, ErrorCode


class RecordingDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.behavior: dict[str, str] = {}

    def handle(self, operation: str, message: str) -> DriverReceipt:
        assert type(operation) is str
        assert type(message) is str
        self.calls.append((operation, message))
        mode = self.behavior.get(operation, "accept")
        if mode == "raise":
            raise RuntimeError("credential=hidden .@ RIGHT=private /env/PATH")
        digest = hashlib.sha256(b"CGS-DRIVER-v1\x00" + message.encode("utf-8")).hexdigest()
        if mode == "reject":
            return DriverReceipt(operation, digest, False, ErrorCode.SERVER_REJECTED)
        if mode == "wrong_digest":
            return DriverReceipt(operation, "0" * 64, True)
        if mode == "wrong_operation":
            return DriverReceipt("forged", digest, True)
        if mode == "mutate_detached":
            value = json.loads(message)
            if isinstance(value, dict):
                value["credential"] = "mutated"
        return DriverReceipt(operation, digest, True)

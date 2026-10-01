"""command_run_logger — The run log every command writes.

Ring: 3
Contract: The run log every command writes.
Imports: universal_clock
"""

from __future__ import annotations

import json
import logging
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass
from ..universal_clock import ClockProtocol, SystemClock


class CommandRunLogger:
    """Structured JSON logger for a single ComplexGitSync command run.

    ``command_name``/``run_stamp`` name a :meth:`ensure_log_file` fallback.
    They are carried rather than recomputed because the stamp must be this
    run's own, and `universal_clock.py` is the sole reader of the clock —
    `create_run_logger` has already read it once for this logger's name.
    """

    #: How many run logs `.cgitsync/logs/` keeps (LocalRunLogs D2). A run log is a local
    #: record of one run, never pushed; `autofix` only ever needs the most recent.
    MAX_RUN_LOGS = 200

    def __init__(
        self,
        logger: logging.Logger,
        *,
        log_path: Path | None = None,
        command_name: str = "command",
        run_stamp: str = "",
    ) -> None:
        self._logger = logger
        self.log_path = log_path
        self._buffered_lines: list[str] = []
        self._command_name = command_name
        self._run_stamp = run_stamp

    def log_event(self, event: str, *, level: int = logging.INFO, **fields: object) -> None:
        """Log *event* together with arbitrary keyword *fields* as a JSON record."""
        record: dict[str, Any] = {
            "operation": self._operation_for_event(event, fields),
            "event": event,
        }
        for key, value in fields.items():
            if isinstance(value, (str, int, float, bool, type(None))):
                record[key] = value
            else:
                record[key] = str(value)
        line = json.dumps(record, default=str)
        self._buffered_lines.append(line)
        self._logger.log(level, line)
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")

    def bind_log_file(self, log_path: Path | str) -> None:
        """Write buffered records to *log_path* and append future records there."""
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(
            "".join(f"{line}\n" for line in self._buffered_lines),
            encoding="utf-8",
        )
        self.prune_old_logs(self.log_path.parent, keep_path=self.log_path)

    @staticmethod
    def prune_old_logs(logs_dir: Path | str, *, keep_path: Path | str, keep: int = MAX_RUN_LOGS) -> int:
        """Delete the oldest ``*.log`` files in *logs_dir* beyond the *keep* most recent.

        The one place the bound lives: both writers of a run log — this class's
        :meth:`bind_log_file` and `write_gts_snapshot`'s own file, whose name starts with
        the command, not the time — call it. Oldest by modification time, never *keep_path*
        (the log just written). Failing to delete only warns, like every other recording
        failure. Returns how many files were deleted.
        """
        protected = Path(keep_path)
        try:
            logs = sorted(
                (path for path in Path(logs_dir).glob("*.log") if path != protected),
                key=lambda path: (path.stat().st_mtime_ns, path.name),
            )
        except OSError as exc:
            warnings.warn(f"could not list run logs in {logs_dir}: {exc}", stacklevel=2)
            return 0
        deleted = 0
        for path in logs[: max(0, len(logs) + 1 - keep)]:
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                warnings.warn(f"could not delete old run log {path}: {exc}", stacklevel=2)
        return deleted

    def ensure_log_file(self, logs_dir: Path | str) -> Path | None:
        """Bind a log file under *logs_dir* if this run has not bound one.

        `write_gts_snapshot` used to be the only caller of
        :meth:`bind_log_file`, so records reached disk only when the run also
        wrote a State — never on a refusal, which by definition writes none.
        The failing run `autofix` exists to diagnose was therefore the one
        run that left no trace. Returns the path bound, the one already
        bound, or ``None``: a logger that cannot write must not mask the
        error it was called to record, so ``OSError`` is swallowed.
        """
        if self.log_path is not None:
            return self.log_path
        stamped = f"{self._command_name}-{self._run_stamp}" if self._run_stamp else self._command_name
        try:
            self.bind_log_file(Path(logs_dir) / f"{stamped}.log")
        except OSError:
            self.log_path = None
            return None
        return self.log_path

    @staticmethod
    def _operation_for_event(event: str, fields: dict[str, object]) -> str:
        if event.startswith("memory_"):
            return "CGS-MEM"
        if event == "nested_cgs_discovery":
            return "GT-DISCOVER"
        if event in {"repo_state_transition", "tree_state_transition"}:
            return "GT-CLONE"
        if event in {"circularity_fixed", "validate_branch_topology_start", "validate_branch_topology_end"}:
            return "GT-VALIDATE"
        if event.startswith("fs_purge_"):
            return "FS-PURGE"
        if event == "command_start" or event == "command_end":
            command = str(fields.get("command", "command")).replace("_", "-").upper()
            if command in {"VALIDATE", "VALIDATE-TOPOLOGY"}:
                return "GT-VALIDATE"
            if command == "PURGE":
                return "FS-PURGE"
            if command in {"INITIALISE", "CLEAN-INIT", "CLONE", "PULL"}:
                return "GT-CLONE"
            return f"CGS-{command}"
        return "CGS-RUN"

    @staticmethod
    def create_run_logger(
        command_name: str,
        *,
        profile: str = "quiet",
        clock: ClockProtocol | None = None,
    ) -> CommandRunLogger:
        """Create a :class:`CommandRunLogger` for a specific command invocation.

        ``clock`` names the run — real by default (:class:`SystemClock`), so a
        caller that cares about the exact timestamp in the logger name can
        inject a fixed one instead of two runs in the same second racing
        ``logging``'s global logger cache below.

        A run log lives in the tree's own ``.cgitsync/logs/`` and nowhere else.
        This used to take a ``project_root``/``project_log_dir``/``source_path``
        trio and use none of them, beside a `_resolve_log_dir` no caller called
        that still answered with the ``XDG_STATE_HOME`` location the tree had
        moved away from. Both are gone, and ``project.log_dir`` is not read.
        """
        timestamp = (clock or SystemClock()).now().strftime("%Y%m%dT%H%M%SZ")

        logger_name = f"ComplexGitSync.run.{command_name}.{timestamp}"
        logger = logging.getLogger(logger_name)
        # Two runs of one command inside the same second share this name, and
        # `logging` caches loggers globally — so without this the second run
        # keeps the first run's handler, writes to a stream that may already be
        # closed, and `logging` prints its own traceback to stderr. Handlers
        # would also accumulate, one per invocation, in any process that runs
        # more than one command.
        for stale in list(logger.handlers):
            logger.removeHandler(stale)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        console_level = logging.INFO if profile == "verbose" else logging.WARNING
        ch = logging.StreamHandler()
        ch.setLevel(console_level)
        ch.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(ch)

        return CommandRunLogger(logger, command_name=command_name, run_stamp=timestamp)


__all__ = [
    "CommandRunLogger",
]

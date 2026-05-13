"""Logging subsystem for ComplexGitSync command runs."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CommandRunLogger:
    """Structured JSON logger for a single ComplexGitSync command run.

    Each logged event is serialised as a JSON object on its own line so that
    log files can be parsed programmatically.
    """

    def __init__(self, logger: logging.Logger, *, log_path: Path | None = None) -> None:
        self._logger = logger
        self.log_path = log_path

    def log_event(self, event: str, *, level: int = logging.INFO, **fields: object) -> None:
        """Log *event* together with arbitrary keyword *fields* as a JSON record."""
        record: dict[str, Any] = {"event": event}
        for key, value in fields.items():
            if isinstance(value, (str, int, float, bool, type(None))):
                record[key] = value
            else:
                record[key] = str(value)
        self._logger.log(level, json.dumps(record, default=str))


def create_run_logger(
    command_name: str,
    *,
    profile: str = "verbose",
    source_path: Path | None = None,
    project_root: Path | None = None,
    project_log_dir: Any = None,
) -> CommandRunLogger:
    """Create a :class:`CommandRunLogger` for a specific command invocation.

    The log file is written to, in priority order:

    1. ``<project_root>/<project_log_dir>`` when both are set in the ``.cgs``.
    2. ``$XDG_STATE_HOME/ComplexGitSync/logs/`` when ``XDG_STATE_HOME`` is set.
    3. ``~/.local/state/ComplexGitSync/logs/`` as the final fallback.

    The file name is ``<YYYYMMDDTHHMMSSZ>-<command_name>.log``.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_filename = f"{timestamp}-{command_name}.log"

    log_dir = _resolve_log_dir(project_root, project_log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename

    logger_name = f"ComplexGitSync.run.{command_name}.{timestamp}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(fh)

    if profile == "verbose":
        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)
        ch.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(ch)

    return CommandRunLogger(logger, log_path=log_path)


def _resolve_log_dir(project_root: Path | None, project_log_dir: Any) -> Path:
    if project_root is not None and project_log_dir:
        return (project_root / str(project_log_dir)).resolve()

    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "ComplexGitSync" / "logs"

    return Path.home() / ".local" / "state" / "ComplexGitSync" / "logs"

"""
Structured logging configuration.

When LOG_FORMAT=json (env), emit single-line JSON records suitable for k3s/Loki.
Otherwise keep the existing human-readable format.
"""

from __future__ import annotations

import json
import logging
import os


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """Replace the root handler formatter when JSON logging is requested."""
    if os.getenv("LOG_FORMAT", "").lower() != "json":
        return

    root = logging.getLogger()
    formatter = JsonFormatter()
    for handler in root.handlers:
        handler.setFormatter(formatter)

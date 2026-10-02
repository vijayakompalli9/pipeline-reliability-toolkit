"""Structured key=value logging on top of stdlib ``logging``."""

from __future__ import annotations

import logging
import sys
from typing import Any


class KeyValueFormatter(logging.Formatter):
    """Render records as ``ts=... level=... logger=... event=... k=v`` lines."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [
            f"ts={self.formatTime(record, '%Y-%m-%dT%H:%M:%S')}",
            f"level={record.levelname.lower()}",
            f"logger={record.name}",
            f"event={record.getMessage()}",
        ]
        fields: dict[str, Any] = getattr(record, "fields", {}) or {}
        for key, value in fields.items():
            text = str(value)
            parts.append(f'{key}="{text}"' if " " in text else f"{key}={text}")
        if record.exc_info:
            parts.append(f'exc="{self.formatException(record.exc_info)!r}"')
        return " ".join(parts)


def configure_logging(level: str = "INFO") -> None:
    """Install the key=value formatter on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not any(isinstance(h.formatter, KeyValueFormatter) for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(KeyValueFormatter())
        root.addHandler(handler)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Log a named event with structured fields."""
    logger.log(level, event, extra={"fields": fields})

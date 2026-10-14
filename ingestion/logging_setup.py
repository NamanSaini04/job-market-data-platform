"""
Structured logging: every log line is one JSON object in data/logs/pipeline.jsonl,
plus a short readable line on screen.

Why JSON: a computer can filter and count it later
("show every failed board in the last week") without fragile text parsing.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

_FIELDS = ("run_id", "source", "board", "status", "records", "duration_ms", "error")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for key in _FIELDS:
            if hasattr(record, key):
                out[key] = getattr(record, key)
        return json.dumps(out, default=str)


class ScreenFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(f"{k}={getattr(record, k)}" for k in _FIELDS
                          if hasattr(record, k) and k != "run_id")
        return f"{record.levelname:<7} {record.getMessage()} {extras}".rstrip()


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pipeline")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    file_handler = logging.FileHandler(log_dir / "pipeline.jsonl")
    file_handler.setFormatter(JsonFormatter())
    screen = logging.StreamHandler()
    screen.setFormatter(ScreenFormatter())
    logger.addHandler(file_handler)
    logger.addHandler(screen)
    logger.propagate = False
    return logger

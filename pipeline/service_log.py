"""Structured origin logs for the private review API. Not a publish.

Worker and review processes append JSON lines under APTPLANS_LOGS. Compose
json-file logs stay on the Docker host. This file is the programmatic view:
no docker.sock, no journal mount, no secrets in the payload.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import logging
import os
import re

MAX_LOG_BYTES = 5 * 1024 * 1024
KEEP_LINES = 2000
DEFAULT_TAIL = 100
MAX_TAIL = 500

_SOCKS = re.compile(r"socks5h?://[^\s]+", re.I)
_BEARER = re.compile(r"(Bearer\s+)\S+", re.I)
_API_KEY = re.compile(r"(X-Api-Key:\s*)\S+", re.I)


def logs_dir_from_env(override: Path | None = None) -> Path:
    if override is not None:
        return override
    raw = os.environ.get("APTPLANS_LOGS", "").strip()
    if raw:
        return Path(raw)
    overlay = os.environ.get("APTPLANS_CATALOG_OVERLAY", "").strip()
    if overlay:
        return Path(overlay).parent / "logs"
    return Path(__file__).resolve().parents[1] / "data" / "logs"


def worker_log_path(logs_dir: Path | None = None) -> Path:
    return logs_dir_from_env(logs_dir) / "worker.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact(text: str) -> str:
    """Drop proxy URLs, bearer tokens, and long hex keys from a log line."""
    cleaned = _SOCKS.sub("socks5h://redacted", text)
    cleaned = _BEARER.sub(r"\1redacted", cleaned)
    cleaned = _API_KEY.sub(r"\1redacted", cleaned)
    for name in (
        "APTPLANS_REVIEW_TOKEN",
        "APTPLANS_FETCH_PROXY",
        "INTAKE_GITHUB_TOKEN",
        "APTPLANS_SEARCH_KEY",
        "APTPLANS_GEMINI_KEY",
        "MEILI_MASTER_KEY",
        "CLOUDFLARE_ORIGIN_KEY",
        "PIA_OPENVPN_PASSWORD",
        "PIA_SOCKS_PASSWORD",
    ):
        value = os.environ.get(name, "").strip()
        if len(value) >= 8:
            cleaned = cleaned.replace(value, "redacted")
    return cleaned


def append_log(event: dict, *, logs_dir: Path | None = None, name: str = "worker") -> None:
    path = logs_dir_from_env(logs_dir) / f"{name}.jsonl"
    payload = {"at": utc_now(), **event}
    message = payload.get("message")
    if isinstance(message, str):
        payload["message"] = redact(message)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str, ensure_ascii=True) + "\n")
        if path.stat().st_size > MAX_LOG_BYTES:
            lines = path.read_text(encoding="utf-8").splitlines()[-KEEP_LINES:]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return


def tail_jsonl(path: Path, n: int = DEFAULT_TAIL) -> list[dict]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict] = []
    for line in lines[-max(1, n) :]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"message": redact(line)})
    return rows


class JsonlLogHandler(logging.Handler):
    """Mirror aptplans loggers into a private JSONL file."""

    def __init__(self, logs_dir: Path | None = None, name: str = "worker") -> None:
        super().__init__()
        self.logs_dir = logs_dir
        self.file_stem = name

    def emit(self, record: logging.LogRecord) -> None:
        try:
            append_log(
                {
                    "logger": record.name,
                    "level": record.levelname,
                    "message": record.getMessage(),
                },
                logs_dir=self.logs_dir,
                name=self.file_stem,
            )
        except Exception:
            self.handleError(record)


def attach_jsonl_handler(logger: logging.Logger, *, name: str = "worker") -> None:
    if any(isinstance(handler, JsonlLogHandler) for handler in logger.handlers):
        return
    logger.addHandler(JsonlLogHandler(name=name))

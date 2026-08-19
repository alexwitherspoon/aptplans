"""Outer gates. The local model does not override a failed check."""

from __future__ import annotations

from enum import Enum
import re
from urllib.parse import urlparse, unquote

MAX_BYTES = 500 * 1024 * 1024

SSI_RE = re.compile(
    r"(?:^|[^a-z0-9])ssi(?:[^a-z0-9]|$)|sensitive.?security|security.?identification",
    re.I,
)
NEWS_RE = re.compile(
    r"newsletter|port_news|(?:^|[_./-])news(?:[_./-]|$)",
    re.I,
)


class GateResult(Enum):
    OK = "ok"
    SSI = "ssi"
    NOT_PLAN = "not_plan"
    NOT_FILE = "not_file"
    TOO_LARGE = "too_large"


def filename_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    return path.rsplit("/", 1)[-1]


def intake_status(gate: GateResult) -> str | None:
    if gate is GateResult.OK:
        return None
    if gate is GateResult.NOT_FILE:
        return "not_plan"
    if gate is GateResult.TOO_LARGE:
        return "needs_human"
    return gate.value


def evaluate_file(url: str, filename: str, data: bytes) -> GateResult:
    name = filename or filename_from_url(url)
    haystack = f"{name} {url}"
    if SSI_RE.search(haystack):
        return GateResult.SSI
    if NEWS_RE.search(name) or NEWS_RE.search(urlparse(url).path):
        return GateResult.NOT_PLAN
    if len(data) > MAX_BYTES:
        return GateResult.TOO_LARGE
    if not data.startswith(b"%PDF"):
        return GateResult.NOT_FILE
    return GateResult.OK

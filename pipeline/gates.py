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
_ENCYCLOPEDIA_HOSTS = ("wikipedia.org",)


def sniff_media(data: bytes) -> str:
    """What we hashed: a PDF, an HTML page, or something else."""
    if data.startswith(b"%PDF"):
        return "pdf"
    head = data.lstrip()[:512].lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<html" in head:
        return "html"
    if b"<head" in head and (b"<body" in head or b"<title" in head):
        return "html"
    return "other"


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


class GateResult(Enum):
    OK = "ok"
    SSI = "ssi"
    NOT_PLAN = "not_plan"
    NOT_FILE = "not_file"
    TOO_LARGE = "too_large"


def filename_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    name = path.rsplit("/", 1)[-1]
    return name or "download"


def looks_like_pdf(url: str) -> bool:
    return unquote(urlparse(url).path).lower().endswith(".pdf")


def intake_status(gate: GateResult) -> str | None:
    if gate is GateResult.OK:
        return None
    if gate is GateResult.NOT_FILE:
        return "not_plan"
    if gate is GateResult.TOO_LARGE:
        return "needs_human"
    return gate.value


def evaluate_file(url: str, filename: str, data: bytes) -> GateResult:
    """PDF fetch gate. HTML hubs use evaluate_payload(..., allow_html=True)."""
    return evaluate_payload(url, filename, data, allow_html=False)


def evaluate_payload(
    url: str,
    filename: str,
    data: bytes,
    *,
    allow_html: bool = False,
) -> GateResult:
    name = filename or filename_from_url(url)
    haystack = f"{name} {url}"
    if SSI_RE.search(haystack):
        return GateResult.SSI
    if NEWS_RE.search(name) or NEWS_RE.search(urlparse(url).path):
        return GateResult.NOT_PLAN
    if any(host in _host(url) for host in _ENCYCLOPEDIA_HOSTS):
        return GateResult.NOT_PLAN
    if len(data) > MAX_BYTES:
        return GateResult.TOO_LARGE
    media = sniff_media(data)
    if media == "pdf":
        return GateResult.OK
    if allow_html and media == "html":
        return GateResult.OK
    return GateResult.NOT_FILE

"""Parse URL hosts for filters. Not redirect validation."""

from __future__ import annotations

from urllib.parse import urlparse


def url_hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def host_matches(url: str, host: str) -> bool:
    """True when the URL host is host or a subdomain of it."""
    name = url_hostname(url)
    label = host.lower().strip()
    if not name or not label:
        return False
    return name == label or name.endswith(f".{label}")


def is_example_url(url: str) -> bool:
    if not url.startswith("http"):
        return False
    if host_matches(url, "example.com"):
        return True
    path = urlparse(url).path or ""
    return "/example/" in path


def is_wikipedia_url(url: str) -> bool:
    return host_matches(url, "wikipedia.org")

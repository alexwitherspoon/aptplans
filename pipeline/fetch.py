"""HTTP fetch for the serial worker. Fail closed when egress is required."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse, unquote
from urllib.request import OpenerDirector, ProxyHandler, Request, build_opener, urlopen
from urllib.robotparser import RobotFileParser

from pipeline.gates import MAX_BYTES

DEFAULT_UA = "aptplans.org"
_robots: dict[str, RobotFileParser | None] = {}


def _user_agent() -> str:
    return os.environ.get("APTPLANS_USER_AGENT") or DEFAULT_UA


def egress_required() -> bool:
    flag = os.environ.get("APTPLANS_EGRESS_REQUIRED", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    if flag in {"0", "false", "no"}:
        return False
    return os.environ.get("APP_ENV", "").strip().lower() == "production"


def _resolve_proxy(proxy_url: str | None) -> str:
    proxy = proxy_url if proxy_url is not None else os.environ.get("APTPLANS_FETCH_PROXY", "")
    proxy = proxy.strip()
    if egress_required() and not proxy:
        raise RuntimeError("APTPLANS_FETCH_PROXY is required when egress is enforced")
    return proxy


def _socks_opener(proxy_url: str) -> OpenerDirector:
    parsed = urlparse(proxy_url)
    if parsed.scheme not in {"socks5", "socks5h"}:
        raise RuntimeError(f"unsupported SOCKS proxy scheme {parsed.scheme}")
    try:
        import socks
        from sockshandler import SocksiPyHandler
    except ImportError as exc:
        raise RuntimeError("SOCKS proxy is set but PySocks is not installed") from exc
    handler = SocksiPyHandler(
        socks.SOCKS5,
        parsed.hostname,
        parsed.port or 1080,
        parsed.scheme == "socks5h",
        unquote(parsed.username) if parsed.username else None,
        unquote(parsed.password) if parsed.password else None,
    )
    return build_opener(handler)


def _http_opener(proxy_url: str) -> OpenerDirector:
    parsed = urlparse(proxy_url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError(f"unsupported HTTP proxy scheme {parsed.scheme}")
    return build_opener(
        ProxyHandler({"http": proxy_url, "https": proxy_url}),
    )


def _proxy_opener(proxy_url: str) -> OpenerDirector:
    parsed = urlparse(proxy_url)
    if parsed.scheme in {"socks5", "socks5h"}:
        return _socks_opener(proxy_url)
    if parsed.scheme in {"http", "https"}:
        return _http_opener(proxy_url)
    raise RuntimeError(f"unsupported fetch proxy scheme {parsed.scheme}")


def _robots_ok(url: str, timeout: int, proxy_url: str | None) -> bool:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin in _robots:
        parser = _robots[origin]
        return True if parser is None else parser.can_fetch(_user_agent(), url)
    robots_url = origin + "/robots.txt"
    try:
        data, status = fetch_bytes(
            robots_url, timeout=min(timeout, 15), proxy_url=proxy_url, honor_robots=False
        )
    except Exception:
        _robots[origin] = None
        return True
    if int(status) >= 400:
        _robots[origin] = None
        return True
    parser = RobotFileParser()
    parser.parse(data.decode("utf-8", errors="replace").splitlines())
    _robots[origin] = parser
    return parser.can_fetch(_user_agent(), url)


def fetch_bytes(
    url: str,
    user_agent: str | None = None,
    timeout: int = 60,
    proxy_url: str | None = None,
    honor_robots: bool = True,
) -> tuple[bytes, int]:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
        return path.read_bytes(), 200

    proxy = _resolve_proxy(proxy_url)
    if honor_robots and parsed.scheme in {"http", "https"} and not _robots_ok(url, timeout, proxy or None):
        raise PermissionError(f"robots.txt disallows {url}")
    headers = {"User-Agent": user_agent or _user_agent()}
    request = Request(url, headers=headers)
    if proxy:
        opener = _proxy_opener(proxy)
        response_cm = opener.open(request, timeout=timeout)
    else:
        response_cm = urlopen(request, timeout=timeout)
    with response_cm as response:
        status = getattr(response, "status", 200) or 200
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"payload exceeds {MAX_BYTES} bytes")
    return data, int(status)


def fetch_meta(
    url: str,
    method: str = "HEAD",
    timeout: int = 30,
    proxy_url: str | None = None,
    honor_robots: bool = True,
) -> tuple[int, str]:
    """Status and final URL only. Does not store a body. Used by the link checker."""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
        return (200 if path.is_file() else 404), url

    proxy = _resolve_proxy(proxy_url)
    if honor_robots and parsed.scheme in {"http", "https"} and not _robots_ok(url, timeout, proxy or None):
        raise PermissionError(f"robots.txt disallows {url}")
    headers = {"User-Agent": _user_agent()}
    if method == "GET":
        headers["Range"] = "bytes=0-0"
    request = Request(url, headers=headers, method=method)
    try:
        if proxy:
            opener = _proxy_opener(proxy)
            response_cm = opener.open(request, timeout=timeout)
        else:
            response_cm = urlopen(request, timeout=timeout)
        with response_cm as response:
            status = getattr(response, "status", 200) or 200
            final = response.geturl() or url
            if method != "HEAD":
                response.read(64)
            return int(status), final
    except HTTPError as exc:
        final = getattr(exc, "url", None) or url
        return int(exc.code), final


def post_json(
    url: str,
    payload: dict,
    user_agent: str | None = None,
    timeout: int = 60,
    proxy_url: str | None = None,
) -> dict:
    body = json.dumps(payload).encode("utf-8")
    proxy = _resolve_proxy(proxy_url)
    headers = {
        "User-Agent": user_agent or _user_agent(),
        "Content-Type": "application/json",
    }
    request = Request(url, data=body, headers=headers, method="POST")
    if proxy:
        opener = _proxy_opener(proxy)
        response_cm = opener.open(request, timeout=timeout)
    else:
        response_cm = urlopen(request, timeout=timeout)
    with response_cm as response:
        status = getattr(response, "status", 200) or 200
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"payload exceeds {MAX_BYTES} bytes")
    if int(status) >= 400:
        raise ValueError(f"POST {url} returned {status}")
    return json.loads(data.decode("utf-8"))

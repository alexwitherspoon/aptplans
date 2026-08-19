"""HTTP fetch for the serial worker. Fail closed if a configured SOCKS proxy is down."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse, unquote
from urllib.request import OpenerDirector, Request, build_opener, urlopen
from urllib.robotparser import RobotFileParser
import os

from pipeline.gates import MAX_BYTES

DEFAULT_UA = "aptplans.org"
_robots: dict[str, RobotFileParser | None] = {}


def _user_agent() -> str:
    return os.environ.get("APTPLANS_USER_AGENT") or DEFAULT_UA


def _socks_opener(proxy_url: str) -> OpenerDirector:
    parsed = urlparse(proxy_url)
    if parsed.scheme not in {"socks5", "socks5h"}:
        raise RuntimeError(f"unsupported fetch proxy scheme {parsed.scheme}")
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

    proxy = proxy_url if proxy_url is not None else os.environ.get("APTPLANS_FETCH_PROXY", "")
    if honor_robots and parsed.scheme in {"http", "https"} and not _robots_ok(url, timeout, proxy or None):
        raise PermissionError(f"robots.txt disallows {url}")
    headers = {"User-Agent": user_agent or _user_agent()}
    request = Request(url, headers=headers)
    if proxy:
        opener = _socks_opener(proxy)
        response_cm = opener.open(request, timeout=timeout)
    else:
        response_cm = urlopen(request, timeout=timeout)
    with response_cm as response:
        status = getattr(response, "status", 200) or 200
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"payload exceeds {MAX_BYTES} bytes")
    return data, int(status)


def post_json(
    url: str,
    payload: dict,
    user_agent: str | None = None,
    timeout: int = 60,
    proxy_url: str | None = None,
) -> dict:
    body = json.dumps(payload).encode("utf-8")
    proxy = proxy_url if proxy_url is not None else os.environ.get("APTPLANS_FETCH_PROXY", "")
    headers = {
        "User-Agent": user_agent or _user_agent(),
        "Content-Type": "application/json",
    }
    request = Request(url, data=body, headers=headers, method="POST")
    if proxy:
        opener = _socks_opener(proxy)
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

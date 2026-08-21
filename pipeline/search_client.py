"""Search APIs that return packets. Do not scrape result HTML.

Brave Search is the default on production (`APP_ENV=production`). CI and local
dev replay fixtures unless `APTPLANS_LIVE_SEARCH=1`. Gemini escalate stays
optional and still needs `APTPLANS_GEMINI_KEY`.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen
import json
import logging
import os
import time

from pipeline.local_env import load_local_env
from pipeline.meter import (
    brave_query_cap,
    budget_available,
    budget_wait_max_seconds,
    budget_wait_seconds,
    charge_local,
    charge_search,
    commit_brave_search,
    gemini_query_cap,
    load_search_meter,
)
from pipeline.queries import packet_urls
from pipeline.refresh import overlay_dir_from_env
from pipeline.search_plan import SKIP_HOSTS, SearchHit, SearchIdentity, SearchSession

log = logging.getLogger("aptplans.search")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "catalog" / "references" / "search_sessions.json"
GEMINI_MODEL = "gemini-3.6-flash"
REDIRECT_HOSTS = (
    "vertexaisearch.cloud.google.com",
    "google.com",
    "googleusercontent.com",
    "grounding-api-redirect",
)
GEMINI_PROMPT = """Search the web for this exact query and list destination URLs.

Query: {query}

Return JSON only:
{{"hits":[{{"title":"","url":"https://...","snippet":""}}]}}

Rules:
- Destination URLs only (the official page or PDF).
- Do not classify files as master plan, ALP, notice, or not-plan.
- Do not decide whether a URL should be fetched.
- Prefer .gov and airport-sponsor hosts.
"""


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def live_search_enabled() -> bool:
    """Live Brave/Google search runs on production. CI and local dev use fixtures unless opted in."""
    flag = os.environ.get("APTPLANS_LIVE_SEARCH", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    if flag in {"0", "false", "no"}:
        return False
    if os.environ.get("APP_ENV", "").strip().lower() == "production":
        return True
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true":
        return False
    if _truthy("CI"):
        return False
    return False


def search_provider() -> str:
    load_local_env()
    if live_search_enabled():
        return (os.environ.get("APTPLANS_SEARCH_PROVIDER") or "brave").strip().lower()
    return "fixture"


def gemini_configured() -> bool:
    load_local_env()
    return bool(os.environ.get("APTPLANS_GEMINI_KEY", "").strip())


def _overlay_dir() -> Path:
    return overlay_dir_from_env()


def _meter_kinds_for_call(*, include_gemini: bool = False) -> list[str]:
    kinds: list[str] = []
    if live_search_enabled():
        provider = search_provider()
        if provider == "brave":
            kinds.append("brave")
        elif provider == "google":
            kinds.append("google")
    if include_gemini and gemini_configured():
        kinds.append("gemini")
    return kinds


def wait_for_meter_budget(*, include_gemini: bool = False, sleep=time.sleep) -> None:
    """Block until local budget allows another metered search call."""
    kinds = _meter_kinds_for_call(include_gemini=include_gemini)
    if not kinds:
        return
    delay = max(budget_wait_seconds(kind) for kind in kinds)
    while delay > 0:
        pause = min(delay, budget_wait_max_seconds())
        log.info("search budget paused; sleep %.0fs kinds=%s", pause, ",".join(kinds))
        sleep(pause)
        delay = max(budget_wait_seconds(kind) for kind in kinds)


def load_fixture(path: Path | None = None) -> dict[str, list[SearchHit]]:
    target = path or DEFAULT_FIXTURE
    payload = json.loads(target.read_text(encoding="utf-8"))
    rows: dict[str, list[SearchHit]] = {}
    for query, hits in (payload.get("queries") or {}).items():
        rows[query] = [
            SearchHit(
                title=item.get("title") or "",
                url=item["url"],
                snippet=item.get("snippet") or "",
                query=query,
            )
            for item in hits
            if item.get("url")
        ]
    return rows


def fixture_search(query: str, *, fixture: dict[str, list[SearchHit]] | None = None) -> list[SearchHit]:
    rows = fixture if fixture is not None else load_fixture()
    return list(rows.get(query) or [])


def _usable_url(url: str) -> bool:
    if not url.startswith("http"):
        return False
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if any(part in url for part in REDIRECT_HOSTS) or host in SKIP_HOSTS:
        return False
    if host.endswith(".google.com") or host.endswith(".googleusercontent.com"):
        return False
    return True


def _brave_search(query: str, *, key: str, count: int) -> list[SearchHit]:
    wait_for_meter_budget()
    if not budget_available("brave"):
        return []
    params = urlencode({"q": query, "count": str(count)})
    req = Request(
        f"https://api.search.brave.com/res/v1/web/search?{params}",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": key,
            "User-Agent": os.environ.get("APTPLANS_USER_AGENT") or "aptplans.org",
        },
    )
    with urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
        if not commit_brave_search(resp.headers):
            log.warning("brave search succeeded but local ledger did not charge")
    hits = []
    for item in (payload.get("web") or {}).get("results") or []:
        url = item.get("url") or ""
        if not _usable_url(url):
            continue
        hits.append(
            SearchHit(
                title=item.get("title") or "",
                url=url,
                snippet=item.get("description") or "",
                query=query,
            )
        )
    return hits


def _google_search(query: str, *, key: str, cx: str, count: int) -> list[SearchHit]:
    wait_for_meter_budget()
    if not budget_available("google"):
        return []
    params = urlencode({"key": key, "cx": cx, "q": query, "num": str(min(count, 10))})
    req = Request(
        f"https://www.googleapis.com/customsearch/v1?{params}",
        headers={"User-Agent": os.environ.get("APTPLANS_USER_AGENT") or "aptplans.org"},
    )
    with urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    charge_local("google")
    hits = []
    for item in payload.get("items") or []:
        url = item.get("link") or ""
        if not _usable_url(url):
            continue
        hits.append(
            SearchHit(
                title=item.get("title") or "",
                url=url,
                snippet=item.get("snippet") or "",
                query=query,
            )
        )
    return hits


def _gemini_text(payload: dict) -> str:
    parts = []
    for cand in payload.get("candidates") or []:
        content = cand.get("content") or {}
        for part in content.get("parts") or []:
            text = part.get("text") or ""
            if text:
                parts.append(text)
    return "\n".join(parts)


def _gemini_json_hits(text: str, *, query: str) -> list[SearchHit]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    rows = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    hits = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not _usable_url(url):
            continue
        hits.append(
            SearchHit(
                title=str(item.get("title") or ""),
                url=url,
                snippet=str(item.get("snippet") or ""),
                query=query,
            )
        )
    return hits


def _label_from_url(url: str) -> str:
    """Filename or host. Do not keep model-written titles; those look like classifications."""
    path = unquote(urlparse(url).path).rstrip("/")
    name = path.rsplit("/", 1)[-1]
    return name or urlparse(url).netloc


def hits_from_gemini_payload(payload: dict, *, query: str) -> list[SearchHit]:
    """Take destination URLs only. Ignore model prose about kind or fetch."""
    found: list[SearchHit] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        if not _usable_url(url) or url in seen:
            return
        seen.add(url)
        found.append(
            SearchHit(title=_label_from_url(url), url=url, snippet="", query=query)
        )

    text = _gemini_text(payload)
    for hit in _gemini_json_hits(text, query=query):
        add(hit.url)
    for cand in payload.get("candidates") or []:
        meta = cand.get("groundingMetadata") or cand.get("grounding_metadata") or {}
        for chunk in meta.get("groundingChunks") or meta.get("grounding_chunks") or []:
            web = (chunk or {}).get("web") or {}
            add(str(web.get("uri") or web.get("url") or ""))
    for url in packet_urls(prose=text):
        add(url)
    return found


def _gemini_search(query: str, *, count: int = 8) -> list[SearchHit]:
    load_local_env()
    key = os.environ.get("APTPLANS_GEMINI_KEY", "").strip()
    if not key:
        raise RuntimeError("APTPLANS_GEMINI_KEY is unset")
    wait_for_meter_budget(include_gemini=True)
    if not budget_available("gemini"):
        return []
    model = os.environ.get("APTPLANS_GEMINI_MODEL") or GEMINI_MODEL
    body = json.dumps(
        {
            "contents": [{"role": "user", "parts": [{"text": GEMINI_PROMPT.format(query=query)}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 1024},
        }
    ).encode("utf-8")
    req = Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": os.environ.get("APTPLANS_USER_AGENT") or "aptplans.org",
        },
        method="POST",
    )
    with urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    charge_local("gemini")
    return hits_from_gemini_payload(payload, query=query)[:count]


def live_search(query: str, *, count: int = 8) -> list[SearchHit]:
    load_local_env()
    if not live_search_enabled():
        raise RuntimeError("live search is disabled in this environment")
    key = os.environ.get("APTPLANS_SEARCH_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "APTPLANS_SEARCH_KEY is unset; production expects Brave Search in .env.secrets"
        )
    provider = search_provider()
    if provider == "brave":
        return _brave_search(query, key=key, count=count)
    if provider == "google":
        cx = os.environ.get("APTPLANS_SEARCH_CX", "").strip()
        if not cx:
            raise RuntimeError("APTPLANS_SEARCH_CX is required for Google CSE")
        return _google_search(query, key=key, cx=cx, count=count)
    raise RuntimeError(f"unknown search provider {provider}")


def search_hits(query: str, *, provider: str | None = None, count: int = 8) -> list[SearchHit]:
    kind = (provider or search_provider()).strip().lower()
    if kind == "fixture":
        return fixture_search(query)
    if kind in {"brave", "google"}:
        return live_search(query, count=count)
    if kind == "gemini":
        return _gemini_search(query, count=count)
    raise RuntimeError(f"unknown search provider {kind}")


def gemini_escalate(identity: SearchIdentity, session: SearchSession) -> list[SearchHit]:
    """One grounded Gemini search after Brave stalls. Packets only."""
    if not gemini_configured():
        return []
    query = session.queries[0] if session.queries else f'"{identity.name}" {identity.lid} "master plan"'
    return _gemini_search(query)

"""Search APIs that return packets. Do not scrape result HTML.

Brave Search is the default on production (`APP_ENV=production`). CI and local
dev replay fixtures unless `APTPLANS_LIVE_SEARCH=1`. Gemini escalate stays
optional and still needs `APTPLANS_GEMINI_KEY`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen
import json
import os
import sys

from pipeline.local_env import load_local_env
from pipeline.queries import packet_urls
from pipeline.refresh import overlay_dir_from_env
from pipeline.search_plan import SKIP_HOSTS, SearchHit, SearchIdentity, SearchSession

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "catalog" / "references" / "search_sessions.json"
METER_NAME = "search_meter.json"
# Brave Search: $5 / 1k requests, $5 monthly credit. Budget is billed spend.
BRAVE_USD_PER_1K = 5.0
BRAVE_MONTHLY_CREDIT_USD = 5.0
BRAVE_MONTHLY_BUDGET_USD = 25.0
# Gemini 3.6 Flash Google Search grounding: 5,000 free prompts/month, then
# $14 / 1k search queries (one prompt can fire more than one query).
GEMINI_MODEL = "gemini-3.6-flash"
GEMINI_USD_PER_1K = 14.0
GEMINI_MONTHLY_BUDGET_USD = 25.0
GEMINI_FREE_PROMPTS = 5000
# Conservative billed-overage fuse: treat each escalate as several queries.
GEMINI_QUERIES_PER_PROMPT = 4.0
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


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _meter_path() -> Path:
    return _overlay_dir() / METER_NAME


def load_search_meter() -> dict:
    path = _meter_path()
    if not path.is_file():
        return {"month": _month_key(), "brave": 0, "google": 0, "gemini": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    month = str(payload.get("month") or "")
    if month != _month_key():
        return {"month": _month_key(), "brave": 0, "google": 0, "gemini": 0}
    return {
        "month": month,
        "brave": int(payload.get("brave") or 0),
        "google": int(payload.get("google") or 0),
        "gemini": int(payload.get("gemini") or 0),
    }


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


def brave_query_cap() -> int:
    """Queries allowed this month for $25 billed plus the $5 Brave credit.

    At $5 / 1k: credit covers 1,000 queries, $25 billed covers 5,000 more.
    APTPLANS_SEARCH_MONTHLY_CAP, if set, is a tighter query fuse.
    """
    budget = max(_env_float("APTPLANS_BRAVE_MONTHLY_BUDGET_USD", BRAVE_MONTHLY_BUDGET_USD), 0.0)
    credit = max(_env_float("APTPLANS_BRAVE_MONTHLY_CREDIT_USD", BRAVE_MONTHLY_CREDIT_USD), 0.0)
    rate = _env_float("APTPLANS_BRAVE_USD_PER_1K", BRAVE_USD_PER_1K)
    if rate <= 0:
        return 0
    queries = int((budget + credit) / rate * 1000)
    explicit = os.environ.get("APTPLANS_SEARCH_MONTHLY_CAP", "").strip()
    if explicit:
        return min(queries, int(explicit))
    return queries


def gemini_query_cap() -> int:
    """Escalate prompts allowed this month for $25 billed on Gemini 3.6 Flash.

    Grounding includes 5,000 free prompts/month across Gemini 3, then $14 / 1k
    search queries. One prompt can fire more than one query, so the paid slice
    divides by APTPLANS_GEMINI_QUERIES_PER_PROMPT (default 4). Packets still
    go through explore/confirm/gates; bad URLs are dropped. APTPLANS_GEMINI_MONTHLY_CAP
    is an optional tighter prompt fuse.
    """
    budget = max(_env_float("APTPLANS_GEMINI_MONTHLY_BUDGET_USD", GEMINI_MONTHLY_BUDGET_USD), 0.0)
    rate = _env_float("APTPLANS_GEMINI_USD_PER_1K", GEMINI_USD_PER_1K)
    free = int(os.environ.get("APTPLANS_GEMINI_FREE_PROMPTS") or GEMINI_FREE_PROMPTS)
    per_prompt = max(_env_float("APTPLANS_GEMINI_QUERIES_PER_PROMPT", GEMINI_QUERIES_PER_PROMPT), 1.0)
    if rate <= 0:
        return max(free, 0)
    paid = int(budget / rate * 1000 / per_prompt)
    queries = max(free, 0) + max(paid, 0)
    explicit = os.environ.get("APTPLANS_GEMINI_MONTHLY_CAP", "").strip()
    if explicit:
        return min(queries, int(explicit))
    return queries


def monthly_cap(kind: str) -> int:
    if kind == "gemini":
        return gemini_query_cap()
    if kind == "brave":
        return brave_query_cap()
    return int(os.environ.get("APTPLANS_SEARCH_MONTHLY_CAP") or brave_query_cap())


def charge_search(kind: str) -> bool:
    """Count one live request. False if this month's cap is already spent."""
    meter = load_search_meter()
    used = int(meter.get(kind) or 0)
    cap = monthly_cap(kind)
    if used >= cap:
        print(
            f"search cap reached kind={kind} month={meter['month']} used={used} cap={cap}",
            file=sys.stderr,
        )
        return False
    meter[kind] = used + 1
    dest = _meter_path()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(meter) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


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
    params = urlencode({"key": key, "cx": cx, "q": query, "num": str(min(count, 10))})
    req = Request(
        f"https://www.googleapis.com/customsearch/v1?{params}",
        headers={"User-Agent": os.environ.get("APTPLANS_USER_AGENT") or "aptplans.org"},
    )
    with urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
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
    if not charge_search("gemini"):
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
    meter_kind = "brave" if provider == "brave" else provider
    if meter_kind in {"brave", "google"} and not charge_search(meter_kind):
        return []
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

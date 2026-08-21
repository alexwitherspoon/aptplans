"""Local ledger for metered search APIs. Cloud quota is recorded when exposed.

Brave returns X-RateLimit-* headers on successful responses (no billing API).
Gemini has no API-key usage endpoint; local counts only until GCP is wired.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
import fcntl
import json
import os
import re
import sys

from pipeline.refresh import overlay_dir_from_env

LEDGER_NAME = "search_meter.json"
LEDGER_LOCK_NAME = "search_meter.lock"
LEDGER_VERSION = 2
PROVIDERS = ("brave", "gemini", "google")
DEFAULT_BUDGET_WAIT_MAX_SEC = 3600.0

# Brave Search: $5 / 1k requests, $5 monthly credit. Budget is billed spend.
BRAVE_USD_PER_1K = 5.0
BRAVE_MONTHLY_CREDIT_USD = 5.0
BRAVE_MONTHLY_BUDGET_USD = 25.0
# Gemini 3.6 Flash Google Search grounding: 5,000 free prompts/month, then
# $14 / 1k search queries (one prompt can fire more than one query).
GEMINI_USD_PER_1K = 14.0
GEMINI_MONTHLY_BUDGET_USD = 25.0
GEMINI_FREE_PROMPTS = 5000
GEMINI_QUERIES_PER_PROMPT = 4.0

_POLICY_RE = re.compile(r"(\d+);w=(\d+)")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


def ledger_path(overlay_dir: Path | None = None) -> Path:
    root = overlay_dir or overlay_dir_from_env()
    return root / LEDGER_NAME


def _ledger_lock_path(overlay_dir: Path | None = None) -> Path:
    return ledger_path(overlay_dir).with_name(LEDGER_LOCK_NAME)


@contextmanager
def _ledger_lock(overlay_dir: Path | None = None):
    path = _ledger_lock_path(overlay_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _empty_provider() -> dict:
    return {"local": {"charged": 0, "last_at": None}, "cloud": None}


def _empty_ledger() -> dict:
    return {
        "version": LEDGER_VERSION,
        "month": _month_key(),
        "providers": {kind: _empty_provider() for kind in PROVIDERS},
    }


def _migrate_legacy(payload: dict) -> dict:
    if int(payload.get("version") or 0) >= LEDGER_VERSION and isinstance(
        payload.get("providers"), dict
    ):
        return payload
    ledger = _empty_ledger()
    month = str(payload.get("month") or "")
    if month:
        ledger["month"] = month
    for kind in PROVIDERS:
        charged = int(payload.get(kind) or 0)
        if charged:
            ledger["providers"][kind]["local"] = {
                "charged": charged,
                "last_at": payload.get(f"{kind}_last_at"),
            }
    return ledger


def _read_ledger_file(path: Path) -> dict:
    if not path.is_file():
        return _empty_ledger()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    ledger = _migrate_legacy(payload)
    if ledger.get("month") != _month_key():
        return _empty_ledger()
    for kind in PROVIDERS:
        ledger["providers"].setdefault(kind, _empty_provider())
    return ledger


def _write_ledger_file(ledger: dict, path: Path) -> bool:
    ledger = dict(ledger)
    ledger["version"] = LEDGER_VERSION
    ledger.setdefault("month", _month_key())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def load_ledger(overlay_dir: Path | None = None) -> dict:
    return _read_ledger_file(ledger_path(overlay_dir))


def save_ledger(ledger: dict, overlay_dir: Path | None = None) -> bool:
    return _write_ledger_file(ledger, ledger_path(overlay_dir))


def brave_query_cap() -> int:
    """Queries allowed this month for $25 billed plus the $5 Brave credit."""
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
    """Escalate prompts allowed this month for $25 billed on Gemini grounding."""
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


def google_query_cap() -> int:
    explicit = os.environ.get("APTPLANS_GOOGLE_MONTHLY_CAP", "").strip()
    if explicit:
        return int(explicit)
    fallback = os.environ.get("APTPLANS_SEARCH_MONTHLY_CAP", "").strip()
    if fallback:
        return int(fallback)
    return brave_query_cap()


def monthly_cap(kind: str) -> int:
    if kind == "gemini":
        return gemini_query_cap()
    if kind == "brave":
        return brave_query_cap()
    if kind == "google":
        return google_query_cap()
    return brave_query_cap()


def local_charged(kind: str, overlay_dir: Path | None = None) -> int:
    ledger = load_ledger(overlay_dir)
    provider = ledger["providers"].get(kind) or _empty_provider()
    return int((provider.get("local") or {}).get("charged") or 0)


def budget_remaining(kind: str, overlay_dir: Path | None = None) -> int:
    return max(0, monthly_cap(kind) - local_charged(kind, overlay_dir))


def budget_available(kind: str, overlay_dir: Path | None = None) -> bool:
    reserve = max(0, int(os.environ.get("APTPLANS_SEARCH_BUDGET_RESERVE", "0") or 0))
    return budget_remaining(kind, overlay_dir) > reserve


def _seconds_until_utc_month_end() -> float:
    now = datetime.now(timezone.utc)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return max(1.0, (end - now).total_seconds())


def budget_wait_seconds(kind: str, overlay_dir: Path | None = None) -> float:
    """Seconds to sleep before retrying when the local budget fuse is spent."""
    reserve = max(0, int(os.environ.get("APTPLANS_SEARCH_BUDGET_RESERVE", "0") or 0))
    if budget_remaining(kind, overlay_dir) > reserve:
        return 0.0
    ledger = load_ledger(overlay_dir)
    cloud = (ledger["providers"].get(kind) or {}).get("cloud")
    if isinstance(cloud, dict):
        reset = cloud.get("monthly_reset_seconds")
        if isinstance(reset, int) and reset > 0:
            return min(float(reset), _seconds_until_utc_month_end())
    return _seconds_until_utc_month_end()


def budget_wait_max_seconds() -> float:
    raw = os.environ.get("APTPLANS_SEARCH_BUDGET_WAIT_MAX_SEC", "").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_BUDGET_WAIT_MAX_SEC


def charge_local(kind: str, overlay_dir: Path | None = None) -> bool:
    """Record one successful billed request. False if the local budget cap is spent."""
    if kind not in PROVIDERS:
        return False
    path = ledger_path(overlay_dir)
    with _ledger_lock(overlay_dir):
        ledger = _read_ledger_file(path)
        provider = ledger["providers"][kind]
        charged = int((provider.get("local") or {}).get("charged") or 0)
        cap = monthly_cap(kind)
        if charged >= cap:
            print(
                f"search cap reached kind={kind} month={ledger['month']} used={charged} cap={cap}",
                file=sys.stderr,
            )
            return False
        provider["local"] = {"charged": charged + 1, "last_at": _utc_now()}
        return _write_ledger_file(ledger, path)


def charge_search(kind: str, overlay_dir: Path | None = None) -> bool:
    """Backward-compatible alias for charge_local."""
    return charge_local(kind, overlay_dir)


def _split_header_values(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        try:
            out.append(int(piece))
        except ValueError:
            continue
    return out


def parse_brave_ratelimit(headers: Message | dict) -> dict | None:
    """Parse Brave X-RateLimit-* headers. Returns the monthly window when aligned."""
    if isinstance(headers, dict):
        get = headers.get
    else:
        get = headers.get  # type: ignore[assignment]

    policy_raw = get("X-RateLimit-Policy") or get("x-ratelimit-policy")
    limit_raw = get("X-RateLimit-Limit") or get("x-ratelimit-limit")
    remaining_raw = get("X-RateLimit-Remaining") or get("x-ratelimit-remaining")
    reset_raw = get("X-RateLimit-Reset") or get("x-ratelimit-reset")
    if not policy_raw or not limit_raw or not remaining_raw:
        return None

    windows: list[dict] = []
    for match in _POLICY_RE.finditer(str(policy_raw)):
        limit = int(match.group(1))
        window_seconds = int(match.group(2))
        windows.append({"limit": limit, "window_seconds": window_seconds})

    limits = _split_header_values(str(limit_raw))
    remainings = _split_header_values(str(remaining_raw))
    resets = _split_header_values(str(reset_raw)) if reset_raw else []

    if len(limits) != len(windows) or len(remainings) != len(limits):
        return None

    rows: list[dict] = []
    for index, window in enumerate(windows):
        row = {
            **window,
            "remaining": remainings[index],
            "reset_seconds": resets[index] if index < len(resets) else None,
        }
        row["used"] = max(0, row["limit"] - row["remaining"])
        rows.append(row)

    monthly = max(rows, key=lambda row: row["window_seconds"])
    return {
        "source": "brave_x_ratelimit",
        "observed_at": _utc_now(),
        "windows": rows,
        "monthly_limit": monthly["limit"],
        "monthly_remaining": monthly["remaining"],
        "monthly_used": monthly["used"],
        "monthly_reset_seconds": monthly.get("reset_seconds"),
    }


def record_cloud(kind: str, cloud: dict | None, overlay_dir: Path | None = None) -> None:
    if kind not in PROVIDERS or not cloud:
        return
    path = ledger_path(overlay_dir)
    with _ledger_lock(overlay_dir):
        ledger = _read_ledger_file(path)
        ledger["providers"][kind]["cloud"] = cloud
        _write_ledger_file(ledger, path)


def commit_brave_search(headers: Message | dict, overlay_dir: Path | None = None) -> bool:
    """Record Brave cloud quota and one local charge atomically."""
    cloud = parse_brave_ratelimit(headers)
    path = ledger_path(overlay_dir)
    with _ledger_lock(overlay_dir):
        ledger = _read_ledger_file(path)
        provider = ledger["providers"]["brave"]
        if cloud:
            provider["cloud"] = cloud
        charged = int((provider.get("local") or {}).get("charged") or 0)
        cap = monthly_cap("brave")
        if charged >= cap:
            print(
                f"search cap reached kind=brave month={ledger['month']} used={charged} cap={cap}",
                file=sys.stderr,
            )
            _write_ledger_file(ledger, path)
            return False
        provider["local"] = {"charged": charged + 1, "last_at": _utc_now()}
        _write_ledger_file(ledger, path)
        return True


def record_brave_cloud(headers: Message | dict, overlay_dir: Path | None = None) -> None:
    cloud = parse_brave_ratelimit(headers)
    if cloud:
        record_cloud("brave", cloud, overlay_dir)


def reconcile(kind: str, overlay_dir: Path | None = None) -> dict:
    """Compare local charged count with the last cloud observation when present."""
    ledger = load_ledger(overlay_dir)
    provider = ledger["providers"].get(kind) or _empty_provider()
    local = int((provider.get("local") or {}).get("charged") or 0)
    cap = monthly_cap(kind)
    out: dict = {
        "kind": kind,
        "month": ledger.get("month"),
        "local_charged": local,
        "local_cap": cap,
        "local_remaining": max(0, cap - local),
        "cloud": provider.get("cloud"),
    }
    cloud = provider.get("cloud")
    if isinstance(cloud, dict) and cloud.get("monthly_limit") is not None:
        cloud_used = int(cloud.get("monthly_used") or 0)
        out["cloud_used"] = cloud_used
        out["cloud_remaining"] = cloud.get("monthly_remaining")
        out["cloud_limit"] = cloud.get("monthly_limit")
        out["drift"] = local - cloud_used
    return out


def ledger_summary(overlay_dir: Path | None = None) -> dict:
    """Status payload for review API and operators."""
    ledger = load_ledger(overlay_dir)
    providers: dict[str, dict] = {}
    for kind in PROVIDERS:
        providers[kind] = reconcile(kind, overlay_dir)
    return {
        "month": ledger.get("month"),
        "version": ledger.get("version", LEDGER_VERSION),
        "providers": providers,
    }


def load_search_meter(overlay_dir: Path | None = None) -> dict:
    """Flat month/count view for older callers."""
    ledger = load_ledger(overlay_dir)
    return {
        "month": ledger.get("month"),
        "brave": local_charged("brave", overlay_dir),
        "google": local_charged("google", overlay_dir),
        "gemini": local_charged("gemini", overlay_dir),
    }

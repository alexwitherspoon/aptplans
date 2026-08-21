"""Local client for the private review API. Reads a gitignored API key.

The key lives in `.env` or `.env.review` (or `APTPLANS_REVIEW_TOKEN` in the
environment). This process must not print the token. Pulls are not a publish.
"""

from __future__ import annotations

from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json
import os

from pipeline.local_env import load_local_env

DEFAULT_URL = "https://aptplans.org/review"


def load_review_env(repo: Path | None = None) -> None:
    """Load APTPLANS_REVIEW_* from local env files without overriding the process."""
    load_local_env(repo)


def review_credentials(repo: Path | None = None) -> tuple[str, str]:
    load_review_env(repo)
    token = os.environ.get("APTPLANS_REVIEW_TOKEN", "").strip()
    url = os.environ.get("APTPLANS_REVIEW_URL", DEFAULT_URL).strip().rstrip("/")
    return token, url or DEFAULT_URL


def review_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Api-Key": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def review_request(
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    token: str | None = None,
    base: str | None = None,
    repo: Path | None = None,
    timeout: float = 30,
) -> dict:
    """Call a review API path. Raises SystemExit on missing key or HTTP errors."""
    loaded_token, loaded_base = review_credentials(repo)
    token = (token if token is not None else loaded_token).strip()
    base = (base if base is not None else loaded_base).rstrip("/")
    if not token:
        raise SystemExit(
            "missing APTPLANS_REVIEW_TOKEN; copy .env.example to .env "
            "(or .env.review.example to .env.review)"
        )
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{base}{path}",
        data=body,
        headers=review_headers(token),
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"review API {exc.code} for {path}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(
            f"review API unreachable at {base} ({exc.reason}). "
            "Set APTPLANS_REVIEW_URL (https://aptplans.org/review) and APTPLANS_REVIEW_TOKEN."
        ) from exc


def review_get_bytes(
    path: str,
    *,
    token: str | None = None,
    base: str | None = None,
    repo: Path | None = None,
    timeout: float = 120,
) -> bytes | None:
    """Fetch a private reject body. None if expired or missing. Do not print it."""
    loaded_token, loaded_base = review_credentials(repo)
    token = (token if token is not None else loaded_token).strip()
    base = (base if base is not None else loaded_base).rstrip("/")
    if not token:
        raise SystemExit(
            "missing APTPLANS_REVIEW_TOKEN; copy .env.example to .env "
            "(or .env.review.example to .env.review)"
        )
    request = Request(
        f"{base}{path}",
        headers=review_headers(token),
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return None
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"review API {exc.code} for {path}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(
            f"review API unreachable at {base} ({exc.reason}). "
            "Set APTPLANS_REVIEW_URL (https://aptplans.org/review) and APTPLANS_REVIEW_TOKEN."
        ) from exc

"""Load gitignored APTPLANS_* keys from the repo root. Do not print them."""

from __future__ import annotations

from pathlib import Path
import os

REPO = Path(__file__).resolve().parents[1]
# .env.review is review-only. Brave/Gemini keys go in .env (not origin .env.search).
ENV_FILES = (".env.review", ".env.local", ".env")


def _apply_env_file(path: Path, *, prefixes: tuple[str, ...]) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not any(key.startswith(prefix) for prefix in prefixes):
            continue
        if key in os.environ:
            continue
        os.environ[key] = value.strip().strip("'").strip('"')


def load_local_env(repo: Path | None = None) -> None:
    """Fill missing APTPLANS_* keys. Do not replace a key already in the process environment."""
    root = repo or REPO
    for name in ENV_FILES:
        path = root / name
        if not path.is_file():
            continue
        prefixes = ("APTPLANS_REVIEW_",) if name == ".env.review" else ("APTPLANS_",)
        _apply_env_file(path, prefixes=prefixes)

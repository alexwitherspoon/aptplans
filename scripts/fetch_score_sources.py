"""Download official gold URLs into data/score for local training. Not CI. Not a publish.

Git keeps labels and official URLs. Full originals for training are catalog/references/files
(committed PDFs and hub HTML) or gitignored data/score after this script. Do not store
excerpts in JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.evidence import SCORE_CACHE, gold_source_path, load_score_gold
from pipeline.gates import sniff_media
from pipeline.sanitize import redact_html_secrets

UA = "aptplans.org eval (https://aptplans.org)"


def fetch(url: str) -> tuple[int, bytes, str]:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=120) as resp:
        return resp.status, resp.read(), resp.headers.get("Content-Type") or ""


def suffix_for(url: str, data: bytes, content_type: str) -> str:
    media = sniff_media(data)
    if media == "pdf" or url.lower().endswith(".pdf"):
        return ".pdf"
    if media == "html" or "html" in content_type.lower():
        return ".html"
    if url.lower().endswith((".aspx", ".shtml", ".htm")):
        return ".html"
    return ".bin"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch official gold sources into data/score")
    parser.add_argument("--id", action="append", help="Only these case ids")
    parser.add_argument("--sleep", type=float, default=0.8)
    args = parser.parse_args()
    SCORE_CACHE.mkdir(parents=True, exist_ok=True)
    wanted = set(args.id or [])
    rows = []
    for case in load_score_gold().get("cases") or []:
        if wanted and case.get("id") not in wanted:
            continue
        url = case.get("url") or ""
        if not url.startswith("http") or "example.com" in url or "/example/" in url:
            rows.append({"id": case.get("id"), "skip": "synthetic or missing url"})
            continue
        existing = gold_source_path(case)
        if existing is not None and SCORE_CACHE not in existing.parents:
            rows.append({"id": case.get("id"), "skip": "committed original already on disk"})
            continue
        if "wikipedia.org" in url:
            rows.append({"id": case.get("id"), "skip": "encyclopedia host"})
            continue
        try:
            status, data, ctype = fetch(url)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            rows.append({"id": case.get("id"), "url": url, "error": str(exc)})
            time.sleep(args.sleep)
            continue
        suffix = suffix_for(url, data, ctype)
        if suffix == ".html" and (
            len(data) < 800
            or b"request rejected" in data[:1000].lower()
            or b"access denied" in data[:1000].lower()
        ):
            rows.append(
                {
                    "id": case.get("id"),
                    "url": url,
                    "error": f"not original page ({len(data)} bytes)",
                }
            )
            time.sleep(args.sleep)
            continue
        if suffix == ".pdf" and not data.startswith(b"%PDF"):
            rows.append({"id": case.get("id"), "url": url, "error": "not a PDF"})
            time.sleep(args.sleep)
            continue
        dest = SCORE_CACHE / f"{case['id']}{suffix}"
        if suffix == ".html":
            data = redact_html_secrets(data.decode("utf-8", "replace")).encode("utf-8")
        dest.write_bytes(data)
        rows.append(
            {
                "id": case.get("id"),
                "url": url,
                "status": status,
                "bytes": len(data),
                "path": str(dest.relative_to(ROOT)),
            }
        )
        time.sleep(args.sleep)
    print(json.dumps({"n": len(rows), "dir": str(SCORE_CACHE), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

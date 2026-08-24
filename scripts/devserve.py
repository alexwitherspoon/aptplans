"""Build dist/, serve it, and rebuild when site or catalog files change."""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIR = {"__pycache__", ".git", ".pytest_cache"}
POLL_SEC = 0.4
DEBOUNCE_SEC = 0.2
PREVIEW_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,80}$")

log = logging.getLogger("aptplans.dev")


def files_dir(root: Path | None = None) -> Path:
    base = root or ROOT
    raw = (
        os.environ.get("PUBLIC_FILES_PATH")
        or os.environ.get("APTPLANS_PUBLIC_FILES")
        or ""
    )
    if raw.strip():
        return Path(raw)
    return base / "data" / "public-files"


def resolve_file_request(url_path: str, files_root: Path) -> Path | None:
    """Map /files/{name} onto the public projection. Reject path traversal."""
    prefix = "/files/"
    if not url_path.startswith(prefix):
        return None
    rel = url_path[len(prefix) :]
    if not rel or rel.endswith("/") or ".." in Path(rel).parts:
        return None
    dest = (files_root / rel).resolve()
    try:
        dest.relative_to(files_root.resolve())
    except ValueError:
        return None
    return dest


def preview_doc_id(url_path: str) -> str | None:
    prefix = "/files/preview/"
    if not url_path.startswith(prefix) or not url_path.endswith(".pdf"):
        return None
    name = url_path[len(prefix) : -4]
    if not PREVIEW_ID.fullmatch(name):
        return None
    return name


def load_preview_catalog():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from catalog.seed import seed_catalog

    overlay = os.environ.get("APTPLANS_CATALOG_OVERLAY", "").strip()
    return seed_catalog(
        ROOT / "catalog",
        overlay_dir=Path(overlay) if overlay else None,
    )


def official_pdf_url(doc_id: str, catalog=None) -> str | None:
    from catalog.models import visible_on_site

    catalog = catalog if catalog is not None else load_preview_catalog()
    doc = catalog.documents_by_id.get(doc_id)
    if doc is None or doc.kind == "notice" or not visible_on_site(doc):
        return None
    if doc.inferred_media() != "pdf":
        return None
    if (doc.source_status or "") == "dead":
        return None
    url = (doc.source_url or "").strip()
    if not url.startswith(("https://", "http://")):
        return None
    return url


def fetch_preview_pdf(url: str, dest: Path, opener=None) -> None:
    from pipeline.gates import MAX_BYTES

    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": os.environ.get("APTPLANS_USER_AGENT") or "aptplans.org"},
    )
    open_fn = opener or urllib.request.urlopen
    with open_fn(req, timeout=60) as resp:
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("payload too large")
    if not data.startswith(b"%PDF"):
        raise ValueError("not a pdf")
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)


def ensure_preview_file(url_path: str, files_root: Path, catalog=None, opener=None) -> Path | None:
    mapped = resolve_file_request(url_path, files_root)
    if mapped is None:
        return None
    doc_id = preview_doc_id(url_path)
    if not doc_id:
        return mapped
    url = official_pdf_url(doc_id, catalog=catalog)
    if not url:
        if mapped.is_file():
            mapped.unlink()
        return mapped
    if mapped.is_file():
        return mapped
    log.info("fetching official pdf for preview id=%s", doc_id)
    try:
        fetch_preview_pdf(url, mapped, opener=opener)
    except Exception as exc:
        log.warning("preview fetch failed id=%s: %s", doc_id, exc)
    return mapped


def watch_roots(root: Path | None = None) -> list[Path]:
    base = root or ROOT
    roots = [base / "site", base / "catalog", base / "pipeline"]
    overlay = os.environ.get("APTPLANS_CATALOG_OVERLAY", "").strip()
    extra = Path(overlay) if overlay else base / "data" / "catalog"
    if extra not in roots:
        roots.append(extra)
    return roots


def tree_stamp(roots: list[Path]) -> tuple[tuple[str, int, int], ...]:
    rows: list[tuple[str, int, int]] = []
    for folder in roots:
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR for part in path.parts):
                continue
            if path.suffix == ".pyc":
                continue
            stat = path.stat()
            rows.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(rows))


def rebuild(out: Path) -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "site" / "build.py"), "--out", str(out)],
        check=True,
        cwd=str(ROOT),
    )


class _Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:
        log.info("%s", format % args)


def serve(host: str, port: int, out: Path, files_root: Path | None = None) -> ThreadingHTTPServer:
    directory = str(out)
    extra = files_root if files_root is not None else files_dir()

    class DistHandler(_Handler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def translate_path(self, path: str) -> str:
            mapped = ensure_preview_file(path.split("?", 1)[0], extra)
            if mapped is not None:
                return str(mapped)
            return super().translate_path(path)

    return ThreadingHTTPServer((host, port), DistHandler)


def watch_loop(
    roots: list[Path],
    out: Path,
    sleep=time.sleep,
    stamp=tree_stamp,
    build=rebuild,
    running=lambda: True,
) -> None:
    current = stamp(roots)
    while running():
        sleep(POLL_SEC)
        nxt = stamp(roots)
        if nxt == current:
            continue
        sleep(DEBOUNCE_SEC)
        nxt = stamp(roots)
        current = nxt
        log.info("change detected; rebuilding %s", out)
        build(out)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Build, serve, and rebuild AptPlans on file changes")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args(argv)
    out = args.out if args.out.is_absolute() else ROOT / args.out
    roots = watch_roots()
    log.info("building %s", out)
    rebuild(out)
    stored = files_dir()
    stored.mkdir(parents=True, exist_ok=True)
    httpd = serve(args.host, args.port, out, stored)
    log.info(
        "serving http://%s:%s  (rebuilds on changes under site/, catalog/, pipeline/, overlay; /files from %s)",
        args.host,
        args.port,
        stored,
    )
    import threading

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        watch_loop(roots, out)
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

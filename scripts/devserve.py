"""Build dist/, serve it, and rebuild when site or catalog files change."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIR = {"__pycache__", ".git", ".pytest_cache"}
POLL_SEC = 0.4
DEBOUNCE_SEC = 0.2

log = logging.getLogger("aptplans.dev")


def watch_roots(root: Path | None = None) -> list[Path]:
    base = root or ROOT
    roots = [base / "site", base / "catalog"]
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


def serve(host: str, port: int, out: Path) -> ThreadingHTTPServer:
    directory = str(out)

    class DistHandler(_Handler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

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
    httpd = serve(args.host, args.port, out)
    log.info(
        "serving http://%s:%s  (rebuilds on changes under site/, catalog/, overlay)",
        args.host,
        args.port,
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

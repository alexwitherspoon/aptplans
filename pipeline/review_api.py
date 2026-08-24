"""Private review API. HTTPS at https://aptplans.org/review (Caddy :443).

Not a public page. Auth is APTPLANS_REVIEW_TOKEN (Authorization: Bearer or
X-Api-Key). GET /v1/health is the only unauthenticated path. Failed artifacts
stay on origin for 90 days and can be pulled over this API for local training.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
import hmac
import json
import os
import re
import sys

from catalog.store import load_overlay
from pipeline.classifications import classification_stats, load_classifications
from pipeline.grant_classify import apply_grant_spend_label, grant_spend_spot_check_queue
from pipeline.evaluations import EVALUATIONS
from pipeline.outcomes import (
    BUCKETS,
    export_gold_candidates,
    load_outcomes,
    outcome_stats,
    overlay_dir_from_env,
    record_outcome,
    training_signals,
)
from pipeline.reject import (
    compact_reject,
    get_reject,
    live_rejects,
    purge_expired,
    read_reject_bytes,
    reject_dir as reject_dir_from_env,
    training_case,
)
from pipeline.review_client import load_review_env
from pipeline.lock import worker_lock
from pipeline.queue import JobQueue, QueueJob
from pipeline.service_log import logs_dir_from_env
from pipeline.status import queue_dir_from_env, service_logs, system_status

REJECT_SHA = re.compile(r"^/v1/rejects/([a-f0-9]{64})(/bytes)?$")
DOCUMENT_PATH = re.compile(r"^/v1/documents/([^/]+)$")
DOCUMENT_BYTES_PATH = re.compile(r"^/v1/documents/([^/]+)/bytes$")
REVIEW_STATUSES = frozenset(
    {"pending", "curated", "auto_pass", "needs_human", "published"}
)


def _json(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    body = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _bytes(handler: BaseHTTPRequestHandler, code: int, body: bytes, content_type: str) -> None:
    handler.send_response(code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _file(handler: BaseHTTPRequestHandler, path: Path, content_type: str) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(path.stat().st_size))
    handler.end_headers()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            handler.wfile.write(chunk)


def _content_type(suffix: str) -> str:
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".html":
        return "text/html; charset=utf-8"
    return "application/octet-stream"


class ReviewHandler(BaseHTTPRequestHandler):
    overlay_dir: Path
    reject_dir: Path
    files_dir: Path
    queue_dir: Path
    logs_dir: Path
    token: str

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def _tokens_from_request(self) -> list[str]:
        tokens = []
        header = self.headers.get("Authorization") or ""
        if header.startswith("Bearer "):
            tokens.append(header[7:].strip())
        elif header.lower().startswith("apikey "):
            tokens.append(header[7:].strip())
        key = (self.headers.get("X-Api-Key") or "").strip()
        if key:
            tokens.append(key)
        return [token for token in tokens if token]

    def _authorized(self) -> bool:
        if not self.token:
            return False
        return any(hmac.compare_digest(got, self.token) for got in self._tokens_from_request())

    def _reject_list(self) -> list[dict]:
        try:
            purge_expired(dest=self.reject_dir)
        except OSError:
            pass
        return [compact_reject(row) for row in live_rejects(dest=self.reject_dir)]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/v1/health":
            _json(self, 200, {"ok": True, "service": "aptplans-review"})
            return
        if not self._authorized():
            _json(self, 401, {"error": "unauthorized"})
            return
        if parsed.path == "/v1/stats":
            _json(self, 200, outcome_stats(overlay_dir=self.overlay_dir))
            return
        if parsed.path == "/v1/evaluations":
            stats = classification_stats(self.overlay_dir)
            _json(
                self,
                200,
                {
                    "tasks": {name: spec.description for name, spec in EVALUATIONS.items()},
                    "stats": stats,
                    "recent": load_classifications(self.overlay_dir)[-50:],
                },
            )
            return
        if parsed.path == "/v1/classification_queue":
            query = parse_qs(parsed.query)
            evaluation = (query.get("evaluation") or ["grant_spend"])[0]
            if evaluation == "grant_spend":
                queue = grant_spend_spot_check_queue(self.overlay_dir)
            else:
                queue = []
            _json(self, 200, {"evaluation": evaluation, "n": len(queue), "items": queue})
            return
        if parsed.path == "/v1/status":
            _json(
                self,
                200,
                system_status(
                    self.overlay_dir,
                    queue_dir=self.queue_dir,
                    reject_dir=self.reject_dir,
                    logs_dir=self.logs_dir,
                ),
            )
            return
        if parsed.path == "/v1/logs":
            query = parse_qs(parsed.query)
            raw_n = (query.get("n") or ["100"])[0]
            try:
                n = int(raw_n)
            except ValueError:
                n = 100
            _json(
                self,
                200,
                service_logs(self.overlay_dir, logs_dir=self.logs_dir, n=n),
            )
            return
        if parsed.path == "/v1/outcomes":
            query = parse_qs(parsed.query)
            bucket = (query.get("bucket") or [""])[0]
            rows = load_outcomes(self.overlay_dir)
            if bucket:
                if bucket not in BUCKETS:
                    _json(self, 400, {"error": "unknown bucket", "buckets": list(BUCKETS)})
                    return
                rows = [row for row in rows if row.get("bucket") == bucket]
            _json(self, 200, {"n": len(rows), "outcomes": rows[-200:]})
            return
        if parsed.path == "/v1/gold":
            rows = export_gold_candidates(self.overlay_dir)
            _json(self, 200, {"n": len(rows), "cases": rows})
            return
        if parsed.path == "/v1/signals":
            _json(
                self,
                200,
                training_signals(self.overlay_dir, reject_dir=self.reject_dir),
            )
            return
        if parsed.path == "/v1/rejects":
            rows = self._reject_list()
            cases = [case for case in (training_case(row) for row in rows) if case]
            _json(self, 200, {"n": len(rows), "rejects": rows, "cases": cases})
            return
        match = REJECT_SHA.match(parsed.path)
        if match:
            sha = match.group(1)
            if match.group(2):
                found = read_reject_bytes(sha, dest=self.reject_dir)
                if found is None:
                    _json(self, 404, {"error": "unknown reject"})
                    return
                row, body = found
                _bytes(self, 200, body, _content_type(row.get("suffix") or ".bin"))
                return
            row = get_reject(sha, dest=self.reject_dir)
            if row is None:
                _json(self, 404, {"error": "unknown reject"})
                return
            _json(self, 200, compact_reject(row))
            return
        if parsed.path == "/v1/documents":
            overlay = load_overlay(self.overlay_dir)
            docs = list(overlay.values())
            query = parse_qs(parsed.query)
            review = (query.get("review_status") or [""])[0]
            if review:
                docs = [row for row in docs if row.get("review_status") == review]
            raw_limit = (query.get("limit") or ["200"])[0]
            try:
                limit = max(1, min(int(raw_limit), 2000))
            except ValueError:
                limit = 200
            _json(self, 200, {"n": len(docs), "documents": docs[:limit]})
            return
        document_bytes = DOCUMENT_BYTES_PATH.match(parsed.path)
        if document_bytes:
            document_id = unquote(document_bytes.group(1))
            document = load_overlay(self.overlay_dir).get(document_id)
            if document is None:
                _json(self, 404, {"error": "unknown document"})
                return
            sha = str(document.get("content_sha256") or "")
            if not re.fullmatch(r"[a-f0-9]{64}", sha):
                _json(self, 404, {"error": "document has no preserved bytes"})
                return
            for suffix in (".pdf", ".html"):
                path = self.files_dir / f"{sha}{suffix}"
                if path.is_file():
                    _file(self, path, _content_type(suffix))
                    return
            _json(self, 404, {"error": "preserved bytes unavailable"})
            return
        _json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            _json(self, 401, {"error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > 32_000:
            _json(self, 413, {"error": "too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            _json(self, 400, {"error": "invalid json"})
            return
        if parsed.path != "/v1/label" or not isinstance(payload, dict):
            _json(self, 404, {"error": "not found"})
            return
        evaluation = str(payload.get("evaluation") or "").strip()
        if evaluation == "grant_spend":
            gold = payload.get("gold") if isinstance(payload.get("gold"), dict) else {}
            category = str(gold.get("spend_category") or payload.get("spend_category") or "").strip()
            grant_number = str(payload.get("grant_number") or "").strip()
            if not grant_number or category not in {"maintenance", "growth", "planning", "other"}:
                _json(self, 400, {"error": "grant_number and gold.spend_category are required"})
                return
            ok = apply_grant_spend_label(
                self.overlay_dir,
                grant_number=grant_number,
                spend_category=category,
                reason=str(payload.get("reason") or gold.get("reason") or ""),
            )
            if not ok:
                _json(self, 404, {"error": "grant not found"})
                return
            _json(self, 201, {"ok": True, "grant_number": grant_number, "spend_category": category})
            return
        url = str(payload.get("url") or "")
        gold = payload.get("gold")
        if not url.startswith("http") or not isinstance(gold, dict):
            _json(self, 400, {"error": "url and gold are required"})
            return
        row = record_outcome(
            self.overlay_dir,
            {
                "id": payload.get("id"),
                "document_id": payload.get("document_id"),
                "lid": payload.get("lid") or "",
                "name": payload.get("name") or "",
                "state": payload.get("state") or "",
                "url": url,
                "label": payload.get("label") or "",
                "shape": payload.get("shape"),
                "gold": gold,
                "job_status": "labeled",
                "review_status": payload.get("review_status"),
                "bucket": payload.get("bucket")
                if payload.get("bucket") in BUCKETS
                else "accepted",
                "source": "human",
            },
        )
        _json(self, 201, {"ok": True, "outcome": row})

    def do_PATCH(self) -> None:
        if not self._authorized():
            _json(self, 401, {"error": "unauthorized"})
            return
        match = DOCUMENT_PATH.match(urlparse(self.path).path)
        if not match:
            _json(self, 404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 32_000:
            _json(self, 413, {"error": "too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            _json(self, 400, {"error": "invalid json"})
            return
        if not isinstance(payload, dict):
            _json(self, 400, {"error": "object required"})
            return
        requested = str(payload.get("review_status") or "").strip()
        if requested not in REVIEW_STATUSES:
            _json(self, 400, {"error": "invalid review_status"})
            return
        document_id = unquote(match.group(1))
        document = load_overlay(self.overlay_dir).get(document_id)
        if document is None:
            _json(self, 404, {"error": "unknown document"})
            return
        current_sha = str(document.get("content_sha256") or "").strip() or None
        submitted_sha = (
            str(payload.get("expected_content_sha256") or "").strip() or None
        )
        if requested == "curated" and document.get("completeness") != "link_only":
            _json(self, 409, {"error": "curated status is only for link-only records"})
            return
        if requested in {"auto_pass", "published"}:
            if document.get("completeness") != "complete":
                _json(
                    self,
                    409,
                    {"error": "preserved publication requires a complete document"},
                )
                return
            if not submitted_sha or not re.fullmatch(r"[a-f0-9]{64}", submitted_sha):
                _json(
                    self,
                    400,
                    {"error": "expected_content_sha256 is required for publication"},
                )
                return
            if current_sha != submitted_sha:
                _json(self, 409, {"error": "document content changed"})
                return
        expected_sha = submitted_sha or current_sha
        job = QueueJob(
            kind="review",
            document_id=document_id,
            source_url=document.get("source_url"),
            airport_lid=document.get("airport_lid"),
            state=document.get("state"),
            requested_review_status=requested,
            expected_content_sha256=expected_sha,
            requested_by="operator",
            request_reason=str(payload.get("reason") or "").strip() or None,
        )
        with worker_lock(self.queue_dir):
            JobQueue(self.queue_dir).enqueue(job)
        _json(
            self,
            202,
            {
                "ok": True,
                "job_id": job.id,
                "document_id": document_id,
                "requested_review_status": requested,
                "expected_content_sha256": expected_sha,
            },
        )


def make_server(
    overlay_dir: Path,
    token: str,
    host: str = "127.0.0.1",
    port: int = 8787,
    reject_dir: Path | None = None,
    files_dir: Path | None = None,
    queue_dir: Path | None = None,
    logs_dir: Path | None = None,
):
    handler = type(
        "BoundReviewHandler",
        (ReviewHandler,),
        {
            "overlay_dir": overlay_dir,
            "reject_dir": reject_dir or reject_dir_from_env(),
            "files_dir": files_dir or Path(
                os.environ.get("APTPLANS_FILES", "/var/lib/aptplans/files")
            ),
            "queue_dir": queue_dir or queue_dir_from_env(),
            "logs_dir": logs_dir or logs_dir_from_env(),
            "token": token,
        },
    )
    return ThreadingHTTPServer((host, port), handler)


def main() -> int:
    load_review_env()
    raw = os.environ.get("APTPLANS_REVIEW_BIND", "127.0.0.1:8787")
    host, _, port_s = raw.rpartition(":")
    host = host or "127.0.0.1"
    port = int(port_s or "8787")
    token = os.environ.get("APTPLANS_REVIEW_TOKEN", "").strip()
    if not token and os.environ.get("APP_ENV") != "production":
        token = "dev-review-token"
    overlay = overlay_dir_from_env()
    rejects = reject_dir_from_env()
    if host not in {"127.0.0.1", "localhost", "::1"} and os.environ.get("APTPLANS_REVIEW_PUBLIC") != "1":
        print("refusing to bind a non-loopback address without APTPLANS_REVIEW_PUBLIC=1", file=sys.stderr)
        return 2
    from pipeline.service_log import attach_jsonl_handler
    import logging

    attach_jsonl_handler(logging.getLogger("aptplans"), name="review")
    server = make_server(overlay, token, host=host, port=port, reject_dir=rejects)
    print(
        f"aptplans review api on {host}:{port} overlay={overlay} reject={rejects}",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

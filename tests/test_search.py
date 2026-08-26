from __future__ import annotations

import pytest

from catalog.models import Airport, Document, Grant
import pipeline.search as search
from pipeline.search import (
    SETTINGS,
    airport_record,
    configured,
    document_record,
    funding_record,
    page_record,
)
from pipeline.textstore import read_pages, write_pages


def test_wait_task_only_ignores_explicit_error_codes(monkeypatch) -> None:
    monkeypatch.setattr(
        search,
        "_request",
        lambda *_args, **_kwargs: {
            "status": "failed",
            "error": {"code": "index_not_found"},
        },
    )
    search._wait_task(
        1,
        ignored_error_codes=frozenset({"index_not_found"}),
    )
    with pytest.raises(RuntimeError, match="index_not_found"):
        search._wait_task(1)


def test_generation_count_uses_exact_pagination(monkeypatch) -> None:
    observed = {}

    def request(method, path, payload):
        observed.update(
            {"method": method, "path": path, "payload": payload}
        )
        return {"totalHits": 554}

    monkeypatch.setattr(search, "_request", request)
    assert search._generation_document_count("staged", "generation") == 554
    assert observed == {
        "method": "POST",
        "path": "/indexes/staged/search",
        "payload": {
            "q": "",
            "filter": '_generation = "generation"',
            "page": 1,
            "hitsPerPage": 1,
        },
    }


def test_configured_requires_url_and_key(monkeypatch) -> None:
    monkeypatch.delenv("MEILI_URL", raising=False)
    monkeypatch.delenv("MEILI_MASTER_KEY", raising=False)
    assert configured() is False
    monkeypatch.setenv("MEILI_URL", "http://search:7700")
    assert configured() is False
    monkeypatch.setenv("MEILI_MASTER_KEY", "aptplanslocalkey1")
    assert configured() is True


def test_write_pages_skips_empty_and_keeps_numbers(tmp_path) -> None:
    rows = write_pages(tmp_path, "abc", ["Hello chapter", "  ", "Mulino inventory"])
    assert [row["page"] for row in rows] == [1, 3]
    assert rows[1]["text"] == "Mulino inventory"
    loaded = read_pages(tmp_path, "abc")
    assert loaded == rows
    assert read_pages(tmp_path, "missing") == []


def test_page_record_links_hashed_pdf() -> None:
    document = Document(
        id="4s9-2008-inventory",
        kind="master_plan",
        source_url="https://example.com/plan.pdf",
        completeness="complete",
        airport_lid="4S9",
        state="OR",
        title="Inventory",
        preserved_url="/files/deadbeef.pdf",
    )
    record = page_record(document, 2, "Mulino")
    assert record["type"] == "page"
    assert record["url"] == "/files/deadbeef.pdf#page=2"
    assert record["lid"] == "4S9"


def test_catalog_records_have_stable_ids() -> None:
    airport = Airport(lid="PDX", name="Portland Intl", city="Portland", state="OR")
    assert airport_record(airport)["id"] == "airport-PDX"
    assert airport_record(airport)["outlook"] is None
    declining = airport_record(
        airport,
        {"trajectory": {"band": "declining", "position": -0.5}},
    )
    assert declining["outlook"] == "declining"
    assert "planning outlook declining" in declining["text"]
    growing = airport_record(airport, outlook="growing")
    assert growing["outlook"] == "growing"
    document = Document(
        id="pdx-2045",
        kind="master_plan",
        source_url="https://example.com/plan.pdf",
        completeness="link_only",
        title="PDX 2045",
    )
    assert document_record(document)["url"] == "/documents/pdx-2045/"
    grant = Grant(airport_lid="PDX", description="Reconstruct Taxiway, more", grant_number="3-41-1")
    assert funding_record(grant)["id"].startswith("funding-")
    assert funding_record(grant)["title"].startswith("PDX")


def test_search_ranks_page_text_above_unofficial_summary() -> None:
    assert SETTINGS["searchableAttributes"] == ["lid", "title", "text", "summary"]
    assert "outlook" in SETTINGS["filterableAttributes"]
    assert "outlook" in SETTINGS["displayedAttributes"]


def test_upsert_preserved_is_noop_without_meili(monkeypatch) -> None:
    from pipeline.search import upsert_preserved

    monkeypatch.delenv("MEILI_URL", raising=False)
    monkeypatch.delenv("MEILI_MASTER_KEY", raising=False)

    def boom(*_args, **_kwargs):
        raise AssertionError("must not call Meilisearch")

    monkeypatch.setattr("pipeline.search._request", boom)
    upsert_preserved({"id": "x", "kind": "master_plan"}, [{"page": 1, "text": "hi"}])


def test_sync_catalog_removes_unlisted_documents(monkeypatch) -> None:
    from catalog.store import Catalog
    from pipeline.search import sync_catalog

    listed = Document(
        id="listed",
        kind="master_plan",
        source_url="https://example.com/listed.pdf",
        completeness="complete",
        review_status="auto_pass",
    )
    pending = Document(
        id="pending",
        kind="master_plan",
        source_url="https://example.com/pending.pdf",
        completeness="complete",
        review_status="pending",
    )
    linked = Document(
        id="linked",
        kind="master_plan",
        source_url="https://example.com/",
        completeness="link_only",
        review_status="curated",
    )
    upserted: list[dict] = []
    removed: list[str] = []
    monkeypatch.setattr("pipeline.search.upsert", lambda rows: upserted.extend(rows))
    monkeypatch.setattr("pipeline.search.remove_document", removed.append)

    sync_catalog(Catalog(documents=[listed, pending, linked]))

    ids = {row["id"] for row in upserted}
    assert "document-listed" in ids
    assert "document-linked" in ids
    assert "document-pending" not in ids
    assert removed == ["pending"]


def test_sync_pages_skips_unlisted_documents(tmp_path, monkeypatch) -> None:
    from catalog.store import Catalog
    from pipeline.search import sync_pages

    listed = Document(
        id="listed",
        kind="master_plan",
        source_url="https://example.com/listed.pdf",
        completeness="complete",
        review_status="auto_pass",
        content_sha256="listed-sha",
    )
    pending = Document(
        id="pending",
        kind="master_plan",
        source_url="https://example.com/pending.pdf",
        completeness="complete",
        review_status="pending",
        content_sha256="pending-sha",
    )
    write_pages(tmp_path, "listed-sha", ["listed text"])
    write_pages(tmp_path, "pending-sha", ["private text"])
    upserted: list[dict] = []
    monkeypatch.setattr("pipeline.search.upsert", lambda rows: upserted.extend(rows))

    sync_pages(Catalog(documents=[listed, pending]), tmp_path)

    assert [row["id"] for row in upserted] == ["page-listed-1"]
    assert "private text" not in {row["text"] for row in upserted}


def test_upsert_preserved_removes_pending_and_loads_pages_after_vet(
    tmp_path, monkeypatch
) -> None:
    from pipeline.search import upsert_preserved

    monkeypatch.setenv("MEILI_URL", "http://search:7700")
    monkeypatch.setenv("MEILI_MASTER_KEY", "key")
    monkeypatch.setattr("pipeline.search.ensure_index", lambda: None)
    removed: list[str] = []
    upserted: list[dict] = []
    deleted_pages: list[str] = []
    monkeypatch.setattr("pipeline.search.remove_document", removed.append)
    monkeypatch.setattr("pipeline.search.upsert", lambda rows: upserted.extend(rows))
    monkeypatch.setattr("pipeline.search.delete_pages", deleted_pages.append)

    pending = {
        "id": "doc",
        "kind": "master_plan",
        "source_url": "https://example.com/plan.pdf",
        "completeness": "complete",
        "review_status": "pending",
        "content_sha256": "sha",
    }
    upsert_preserved(pending, [{"page": 1, "text": "private"}], dest=tmp_path)
    assert removed == ["doc"]
    assert upserted == []

    write_pages(tmp_path, "sha", ["published text"])
    upsert_preserved({**pending, "review_status": "auto_pass"}, None, dest=tmp_path)
    assert deleted_pages == ["doc"]
    assert [row["id"] for row in upserted] == ["document-doc", "page-doc-1"]
    assert upserted[-1]["text"] == "published text"


def test_boot_sync_skips_without_meili(monkeypatch) -> None:
    from pipeline.search import boot_sync

    monkeypatch.delenv("MEILI_URL", raising=False)
    monkeypatch.delenv("MEILI_MASTER_KEY", raising=False)

    def boom(*_args, **_kwargs):
        raise AssertionError("must not call Meilisearch")

    monkeypatch.setattr("pipeline.search._request", boom)
    boot_sync()


def test_backfill_text_writes_missing_sidecar(tmp_path) -> None:
    import hashlib
    import sqlite3

    from catalog import REFERENCE_FILES
    from catalog.store import Catalog
    from pipeline.queue import JobQueue
    from pipeline.search import backfill_text

    pdf = REFERENCE_FILES / "4s9-2008-inventory.pdf"
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    files = tmp_path / "files"
    files.mkdir()
    (files / f"{digest}.pdf").write_bytes(pdf.read_bytes())
    catalog = Catalog(
        documents=[
            Document(
                id="4s9-2008-inventory",
                kind="master_plan",
                source_url="https://example.com/plan.pdf",
                completeness="complete",
                content_sha256=digest,
            )
        ]
    )
    assert backfill_text(
        catalog,
        files_dir=files,
        dest=tmp_path / "text",
        ledger_root=tmp_path / "ledger",
    ) == 1
    assert backfill_text(
        catalog,
        files_dir=files,
        dest=tmp_path / "text",
        ledger_root=tmp_path / "ledger",
    ) == 0
    assert "Mulino" in (tmp_path / "text" / f"{digest}.jsonl").read_text(encoding="utf-8")
    (tmp_path / "text" / f"{digest}.jsonl").write_text(
        "not-json\n", encoding="utf-8"
    )
    assert backfill_text(
        catalog,
        files_dir=files,
        dest=tmp_path / "text",
        ledger_root=tmp_path / "ledger",
    ) == 1
    assert "Mulino" in (tmp_path / "text" / f"{digest}.jsonl").read_text(
        encoding="utf-8"
    )

    with sqlite3.connect(JobQueue(tmp_path / "ledger").path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM extraction_manifests"
        ).fetchone()[0] == 1


def test_backfill_text_skips_notices(tmp_path) -> None:
    from catalog.store import Catalog
    from pipeline.search import backfill_text

    catalog = Catalog(
        documents=[
            Document(
                id="notice-1",
                kind="notice",
                source_url="https://example.com/n.pdf",
                completeness="complete",
                content_sha256="abc",
            )
        ]
    )
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "abc.pdf").write_bytes(b"%PDF")
    assert backfill_text(
        catalog,
        files_dir=tmp_path / "files",
        dest=tmp_path / "text",
        ledger_root=tmp_path / "ledger",
    ) == 0
    assert not (tmp_path / "text" / "abc.jsonl").exists()


def test_caddy_search_proxy_is_post_only() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("Caddyfile", "Caddyfile.prod"):
        text = (root / "docker" / name).read_text(encoding="utf-8")
        assert "path /search/query" in text
        assert "method POST" in text
        assert "max_size 8KB" in text
        assert "rewrite * /indexes/aptplans/search" in text
        assert "Bearer {$MEILI_MASTER_KEY}" in text
        assert "search:7700" in text
        assert "dial_timeout 1s" in text
        assert "handle_path /files/*" in text
        files_handler = text.split("handle_path /files/*", 1)[1].split("}", 1)[0]
        assert 'Cache-Control "no-store"' in files_handler
        assert 'Cache-Control "no-store"' in text
        assert 'Cache-Control "public, max-age=86400"' not in text
        assert "root * /srv/releases/current/public-files" in text
        assert "/dumps" not in text
        assert "/indexes/aptplans/documents" not in text


def test_caddy_review_proxy_is_https_and_token() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    local = (root / "docker" / "Caddyfile").read_text(encoding="utf-8")
    prod = (root / "docker" / "Caddyfile.prod").read_text(encoding="utf-8")
    assert "handle_path /review/*" in prod
    assert "import review" in prod
    assert "reverse_proxy review:8787" in prod
    assert "Cache-Control \"no-store, private\"" in prod
    assert "header X-Api-Key *" in prod
    assert "header Authorization Bearer*" in prod
    assert "path /v1/health" in prod
    assert ":443" in prod
    assert "handle_path /review/*" not in local
    site80 = prod.split(":80", 1)[1].split(":443", 1)[0]
    assert "import review" not in site80
    assert "import static" in site80


def test_makefile_binds_local_text_and_search() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "Makefile").read_text(encoding="utf-8")
    assert "TEXT_PATH ?= $(CURDIR)/data/text" in text
    assert "SEARCH_PATH ?= $(CURDIR)/data/search" in text
    assert "LOGS_PATH ?= $(CURDIR)/data/logs" in text
    assert "up: site" in text and "$(COMPOSE) up --build site" in text
    assert "stack:" in text and "$(COMPOSE) up --build" in text


def test_bootstrap_writes_search_key_once() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "scripts" / "host" / "bootstrap.sh").read_text(
        encoding="utf-8"
    )
    assert "ensure_search_env" in text
    assert "openssl rand -hex 24" in text
    assert 'if [ -f "${search_file}" ]' in text
    assert "MEILI_MASTER_KEY=${key}" in text
    deploy = (Path(__file__).resolve().parents[1] / "scripts" / "host" / "remote-deploy.sh").read_text(
        encoding="utf-8"
    )
    assert ".env.search" in deploy

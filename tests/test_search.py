from __future__ import annotations

from catalog.models import Airport, Document, Grant
from pipeline.search import (
    SETTINGS,
    airport_record,
    configured,
    document_record,
    funding_record,
    page_record,
)
from pipeline.textstore import read_pages, write_pages


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


def test_upsert_preserved_is_noop_without_meili(monkeypatch) -> None:
    from pipeline.search import upsert_preserved

    monkeypatch.delenv("MEILI_URL", raising=False)
    monkeypatch.delenv("MEILI_MASTER_KEY", raising=False)

    def boom(*_args, **_kwargs):
        raise AssertionError("must not call Meilisearch")

    monkeypatch.setattr("pipeline.search._request", boom)
    upsert_preserved({"id": "x", "kind": "master_plan"}, [{"page": 1, "text": "hi"}])


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

    from catalog import REFERENCE_FILES
    from catalog.store import Catalog
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
    assert backfill_text(catalog, files_dir=files, dest=tmp_path / "text") == 1
    assert backfill_text(catalog, files_dir=files, dest=tmp_path / "text") == 0
    assert "Mulino" in (tmp_path / "text" / f"{digest}.jsonl").read_text(encoding="utf-8")


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
    assert backfill_text(catalog, files_dir=tmp_path / "files", dest=tmp_path / "text") == 0
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
        assert "/dumps" not in text
        assert "/indexes/aptplans/documents" not in text


def test_makefile_binds_local_text_and_search() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "Makefile").read_text(encoding="utf-8")
    assert "TEXT_PATH ?= $(CURDIR)/data/text" in text
    assert "SEARCH_PATH ?= $(CURDIR)/data/search" in text
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

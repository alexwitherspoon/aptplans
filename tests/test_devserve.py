from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("devserve", ROOT / "scripts" / "devserve.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_tree_stamp_changes_when_file_appears(tmp_path: Path) -> None:
    dev = _load()
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "page.html").write_text("one", encoding="utf-8")
    first = dev.tree_stamp([folder])
    (folder / "page.html").write_text("two", encoding="utf-8")
    second = dev.tree_stamp([folder])
    assert second != first
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "x.pyc").write_bytes(b"x")
    assert dev.tree_stamp([folder]) == second


def test_watch_roots_include_overlay(tmp_path: Path, monkeypatch) -> None:
    dev = _load()
    monkeypatch.delenv("APTPLANS_CATALOG_OVERLAY", raising=False)
    roots = dev.watch_roots(tmp_path)
    assert tmp_path / "site" in roots
    assert tmp_path / "catalog" in roots
    assert tmp_path / "pipeline" in roots
    assert tmp_path / "data" / "catalog" in roots
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    roots = dev.watch_roots(tmp_path)
    assert tmp_path / "overlay" in roots


def test_resolve_file_request_stays_under_files_root(tmp_path: Path) -> None:
    dev = _load()
    stored = tmp_path / "files"
    stored.mkdir()
    (stored / "abc.pdf").write_bytes(b"%PDF")
    assert dev.resolve_file_request("/files/abc.pdf", stored) == (stored / "abc.pdf").resolve()
    assert dev.resolve_file_request("/files/../abc.pdf", stored) is None
    assert dev.resolve_file_request("/documents/x/", stored) is None
    assert dev.resolve_file_request("/files/", stored) is None


def test_preview_doc_id_and_official_pdf_url(tmp_path: Path) -> None:
    from catalog.models import Document
    from catalog.store import Catalog

    dev = _load()
    assert dev.preview_doc_id("/files/preview/4s9-2019-alp.pdf") == "4s9-2019-alp"
    assert dev.preview_doc_id("/files/abc.pdf") is None
    assert dev.preview_doc_id("/files/preview/../x.pdf") is None
    alp = Document(
        id="4s9-2019-alp",
        kind="alp",
        source_url="https://www.oregon.gov/aviation/airports/Documents/4S9/ODA_Doc_4S9_ALP.pdf",
        completeness="link_only",
        review_status="curated",
        media="pdf",
    )
    catalog = Catalog(airports=[], states=[], documents=[alp])
    assert dev.official_pdf_url("4s9-2019-alp", catalog=catalog).endswith("ODA_Doc_4S9_ALP.pdf")
    dest = tmp_path / "preview" / "4s9-2019-alp.pdf"

    class Resp:
        def read(self, _n: int) -> bytes:
            return b"%PDF-1.4 fake"

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

    dev.fetch_preview_pdf("https://example.com/a.pdf", dest, opener=lambda _req, timeout=0: Resp())
    assert dest.read_bytes().startswith(b"%PDF")
    hidden_catalog = Catalog(
        airports=[],
        states=[],
        documents=[alp.overlay({"review_status": "pending"})],
    )
    assert dev.official_pdf_url("4s9-2019-alp", catalog=hidden_catalog) is None
    assert (
        dev.ensure_preview_file(
            "/files/preview/4s9-2019-alp.pdf",
            tmp_path,
            catalog=hidden_catalog,
        )
        == dest
    )
    assert not dest.exists()


def test_preview_catalog_is_reloaded_for_each_visibility_check(monkeypatch) -> None:
    from catalog.models import Document
    from catalog.store import Catalog

    dev = _load()
    visible = Document(
        id="preview-plan",
        kind="alp",
        source_url="https://example.com/preview.pdf",
        completeness="link_only",
        review_status="curated",
        media="pdf",
    )
    catalogs = iter(
        [
            Catalog(documents=[visible]),
            Catalog(documents=[visible.overlay({"review_status": "pending"})]),
        ]
    )
    monkeypatch.setattr("catalog.seed.seed_catalog", lambda *_args, **_kwargs: next(catalogs))

    assert dev.official_pdf_url("preview-plan") == "https://example.com/preview.pdf"
    assert dev.official_pdf_url("preview-plan") is None


def test_watch_loop_rebuilds_once_on_stamp_change(tmp_path: Path) -> None:
    dev = _load()
    builds: list[Path] = []
    stamps = [
        (("a", 1, 1),),
        (("a", 1, 1),),
        (("b", 2, 2),),
        (("b", 2, 2),),
    ]
    n = {"i": 0}

    def stamp(_roots):
        value = stamps[min(n["i"], len(stamps) - 1)]
        n["i"] += 1
        return value

    def running():
        return n["i"] < 6

    dev.watch_loop(
        [tmp_path],
        tmp_path / "dist",
        sleep=lambda _s: None,
        stamp=stamp,
        build=builds.append,
        running=running,
    )
    assert builds == [tmp_path / "dist"]

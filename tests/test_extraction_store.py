from pathlib import Path
import sqlite3
import subprocess

import pytest
from PIL import Image

from catalog import REFERENCE_FILES
from pipeline.extraction_store import ExtractionStore
from pipeline.ocr import OcrPage, TesseractOcr, _parse_tsv
from pipeline.queue import JobQueue


BROOKINGS = REFERENCE_FILES / "brookings-fy2025-26-adopted-budget.pdf"
BROOKINGS_SHA256 = (
    "da0928d71169b2b27cc1eaec29fd861541cde8ed3d2b8a2b1217448260ce57ad"
)


class FakeOcr:
    version = "fake-ocr/1"
    language = "eng"
    dpi = 200
    page_segmentation_mode = 12
    timeout_seconds = 180

    def __init__(self) -> None:
        self.calls: list[int] = []

    def extract_page(self, _source: Path, page_number: int) -> OcrPage:
        self.calls.append(page_number)
        section = "RESOURCES" if page_number == 57 else "EXPENDITURES"
        return OcrPage(
            text=f"CITY OF BROOKINGS AIRPORT {section} OCR PAGE {page_number}",
            coordinates=[
                {
                    "x": 10,
                    "y": 20,
                    "width": 30,
                    "height": 40,
                    "text": "OCR",
                    "confidence": 97.0,
                }
            ],
            quality={"word_count": 3, "mean_confidence": 97.0},
        )


def test_image_only_pages_route_to_cached_ocr_manifest(tmp_path: Path) -> None:
    ocr = FakeOcr()
    store = ExtractionStore(tmp_path / "ledger", tmp_path / "extractions")
    manifest = store.extract_pdf(
        BROOKINGS,
        content_sha256=BROOKINGS_SHA256,
        ocr=ocr,
        minimum_image_pixels=6_000_000,
    )

    page_57 = manifest.pages[56]
    assert manifest.status == "completed"
    assert page_57["method"] == "ocr"
    assert page_57["text"].endswith("OCR PAGE 57")
    assert page_57["coordinates"][0]["confidence"] == 97.0
    assert manifest.coordinates_available is True
    assert 57 in ocr.calls

    calls = list(ocr.calls)
    cached = store.extract_pdf(
        BROOKINGS,
        content_sha256=BROOKINGS_SHA256,
        ocr=ocr,
        minimum_image_pixels=6_000_000,
    )
    assert cached.manifest_key == manifest.manifest_key
    assert ocr.calls == calls

    changed_ocr = FakeOcr()
    changed_ocr.version = "fake-ocr/2"
    changed = store.extract_pdf(
        BROOKINGS,
        content_sha256=BROOKINGS_SHA256,
        ocr=changed_ocr,
        minimum_image_pixels=6_000_000,
    )
    assert changed.manifest_key != manifest.manifest_key


def test_missing_ocr_records_supervised_pages_without_losing_native_text(
    tmp_path: Path,
) -> None:
    store = ExtractionStore(tmp_path / "ledger", tmp_path / "extractions")
    manifest = store.extract_pdf(
        BROOKINGS,
        content_sha256=BROOKINGS_SHA256,
        minimum_image_pixels=6_000_000,
    )

    assert manifest.status == "partial"
    assert manifest.pages[55]["method"] == "native"
    assert "AIRPORT BUDGET 2025-26" in manifest.pages[55]["text"]
    assert manifest.pages[56]["method"] == "supervised"
    assert manifest.pages[56]["error"]["code"] == "ocr_unavailable"

    database = JobQueue(tmp_path / "ledger").path
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM artifact_versions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM extraction_manifests"
        ).fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE extraction_manifests SET status='failed'"
            )


def test_ocr_failure_is_not_cached(tmp_path: Path) -> None:
    class FailingOcr(FakeOcr):
        def extract_page(
            self, _source: Path, page_number: int
        ) -> OcrPage:
            raise RuntimeError(f"transient OCR failure on page {page_number}")

    store = ExtractionStore(tmp_path / "ledger", tmp_path / "extractions")
    with pytest.raises(RuntimeError, match="transient OCR failure"):
        store.extract_pdf(
            BROOKINGS,
            content_sha256=BROOKINGS_SHA256,
            ocr=FailingOcr(),
            minimum_image_pixels=6_000_000,
        )

    with sqlite3.connect(JobQueue(tmp_path / "ledger").path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM extraction_manifests"
        ).fetchone()[0] == 0

    recovered = store.extract_pdf(
        BROOKINGS,
        content_sha256=BROOKINGS_SHA256,
        ocr=FakeOcr(),
        minimum_image_pixels=6_000_000,
    )
    assert recovered.status == "completed"


def test_tesseract_tsv_preserves_word_coordinates() -> None:
    page = _parse_tsv(
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t95.5\tAirport\n"
        "5\t1\t1\t1\t1\t2\t45\t20\t50\t40\t90.0\tFund\n"
    )
    assert page.text == "Airport Fund"
    assert page.coordinates[0]["x"] == 10
    assert page.quality == {
        "word_count": 2,
        "mean_confidence": 92.75,
    }


def test_tesseract_adapter_uses_bounded_local_commands(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict]] = []
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t99\tAirport\n"
    )

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == "pdftoppm":
            Image.new("RGB", (120, 80), "white").save(
                Path(argv[-1]).with_suffix(".png")
            )
        output = tsv if argv[0] == "tesseract" else ""
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")

    page = TesseractOcr(run=fake_run).extract_page(
        tmp_path / "source.pdf", 7
    )
    assert page.text == "Airport"
    assert page.quality["coordinate_space"] == "rendered_pixels"
    assert page.quality["render_width"] == 120
    assert calls[0][0][:5] == ["pdftoppm", "-f", "7", "-l", "7"]
    assert calls[1][0][0] == "tesseract"
    assert calls[1][0][calls[1][0].index("--oem") + 1] == "1"
    assert calls[1][0][calls[1][0].index("--psm") + 1] == "12"
    for _argv, kwargs in calls:
        assert kwargs["env"]["LC_ALL"] == "C"
        assert kwargs["env"]["OMP_THREAD_LIMIT"] == "1"
        assert kwargs["timeout"] == 180
        assert "shell" not in kwargs


def test_tesseract_identity_includes_renderer_and_language_data(
    tmp_path: Path, monkeypatch
) -> None:
    traineddata = tmp_path / "eng.traineddata"
    traineddata.write_bytes(b"language-data")
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))

    def fake_run(argv, **_kwargs):
        if argv[0] == "tesseract":
            return subprocess.CompletedProcess(
                argv, 0, stdout="tesseract 5.3.0\n", stderr=""
            )
        return subprocess.CompletedProcess(
            argv, 0, stdout="", stderr="pdftoppm version 22.12.0\n"
        )

    identity = TesseractOcr(run=fake_run).version
    assert "tesseract 5.3.0" in identity
    assert "pdftoppm version 22.12.0" in identity
    assert (
        "eng.traineddata/"
        "02c3d2552730b1bf8aa0463a9f8fb93f281473c205b1a445db82b34e8922c1fe"
    ) in identity

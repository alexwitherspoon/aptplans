from __future__ import annotations

import hashlib
from pathlib import Path

from catalog import REFERENCE_FILES
from pipeline.files import store_bytes
from pipeline.gates import GateResult, evaluate_file
from pipeline.fetch import fetch_bytes

INVENTORY = REFERENCE_FILES / "4s9-2008-inventory.pdf"


def test_store_bytes_names_file_by_sha256(tmp_path: Path) -> None:
    data = INVENTORY.read_bytes()
    stored = store_bytes(data, tmp_path)
    digest = hashlib.sha256(data).hexdigest()
    assert stored.sha256 == digest
    assert stored.path == tmp_path / f"{digest}.pdf"
    assert stored.path.read_bytes() == data


def test_ssi_filename_is_not_stored() -> None:
    result = evaluate_file(
        url="https://example.com/PDX_ALP_SSI_sheet.pdf",
        filename="PDX_ALP_SSI_sheet.pdf",
        data=b"%PDF-1.4 fake",
    )
    assert result == GateResult.SSI
    result = evaluate_file(
        url="https://example.com/sensitive-security-information.pdf",
        filename="sensitive-security-information.pdf",
        data=b"%PDF-1.4 fake",
    )
    assert result == GateResult.SSI


def test_newsletter_fails_kind_gate() -> None:
    result = evaluate_file(
        url="https://portofhoodriver.com/Port_News_Fall_Winter_08.pdf",
        filename="Port_News_Fall_Winter_08.pdf",
        data=b"%PDF-1.4 fake",
    )
    assert result == GateResult.NOT_PLAN


def test_reference_pdf_passes_gates() -> None:
    data = INVENTORY.read_bytes()
    result = evaluate_file(
        url="https://www.oregon.gov/aviation/Airports/Documents/4S9/Master%20Plan/2008/Chapter%202%20-%20Inventory.pdf",
        filename="Chapter 2 - Inventory.pdf",
        data=data,
    )
    assert result == GateResult.OK


def test_html_hub_is_not_a_pdf_payload() -> None:
    result = evaluate_file(
        url="https://pdx2045.org/",
        filename="",
        data=b"<!DOCTYPE html><html><title>PDX 2045</title></html>",
    )
    assert result == GateResult.NOT_FILE


def test_fetch_file_url_reads_fixture() -> None:
    url = INVENTORY.resolve().as_uri()
    data, status = fetch_bytes(url, user_agent="aptplans.org")
    assert status == 200
    assert data.startswith(b"%PDF")
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(INVENTORY.read_bytes()).hexdigest()

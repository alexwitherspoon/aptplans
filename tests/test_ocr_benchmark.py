from __future__ import annotations

import json
from pathlib import Path

from pipeline import ocr_benchmark
from pipeline.ocr import OcrPage


PAGE_TEXT = {
    57: """
        CITY OF BROOKINGS AIRPORT RESOURCES
        NET WORKING CAPITAL 33-09-4910 140,000
        GRANT REVENUE 33-03-4050 226,200
        FEES 33-04-4105 3,100
        RENTS 33-04-4115 34,500
        TOTAL RESOURCES 404,300
    """,
    58: """
        CITY OF BROOKINGS AIRPORT EXPENDITURES
        SALARIES & WAGES 33-10-5005 12,687
        PERS 33-10-5015 3,800
        CONSTRUCTION 33-10-7025 233,200
        CONTINGENCY 33-10-9200 131,003
        TOTAL EXPENDITURES 404,300
    """,
}


class GoldOcr:
    version = "gold-ocr/1"
    language = "eng"
    dpi = 200
    page_segmentation_mode = 12
    timeout_seconds = 180

    def __init__(self, replacements: dict[str, str] | None = None) -> None:
        self.replacements = replacements or {}
        self.calls: list[int] = []

    def extract_page(self, _source: Path, page_number: int) -> OcrPage:
        self.calls.append(page_number)
        text = PAGE_TEXT[page_number]
        for old, new in self.replacements.items():
            text = text.replace(old, new)
        return OcrPage(
            text=text,
            coordinates=[
                {
                    "x": 1,
                    "y": 2,
                    "width": 3,
                    "height": 4,
                    "text": "Airport",
                    "confidence": 99.0,
                }
            ],
            quality={"word_count": 30, "mean_confidence": 99.0},
        )


def test_gold_contract_matches_frozen_brookings_pages() -> None:
    gold = ocr_benchmark._load_gold()
    schema = json.loads(
        (
            ocr_benchmark.REFERENCE_FILES.parent.parent
            / "ocr_gold.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert set(schema["required"]) == set(gold)
    assert gold["source_sha256"] == ocr_benchmark.SOURCE_SHA256
    assert [row["page"] for row in gold["pages"]] == [57, 58]
    assert sum(len(row["assertions"]) for row in gold["pages"]) == 12


def test_gold_backed_ocr_benchmark_passes_task_assertions() -> None:
    ocr = GoldOcr(
        {
            "CITY OF BROOKINGS": "City   of Brookings",
            "140,000": "$140000",
        }
    )
    result = ocr_benchmark.run(
        ocr=ocr,
        minimum_page_characters=5,
    )
    assert result["status"] == "passed"
    assert result["assertions"] == {
        "passed": 12,
        "total": 12,
        "misses": [],
    }
    assert ocr.calls == [57, 58]
    assert result["ocr_options"]["dpi"] == 200


def test_incorrect_amount_fails_despite_high_confidence() -> None:
    result = ocr_benchmark.run(
        ocr=GoldOcr({"131,003": "131,093"}),
        minimum_page_characters=5,
    )
    assert result["status"] == "failed"
    assert result["assertions"]["misses"] == ["contingency"]
    assert result["pages"][1]["mean_confidence"] == 99.0


def test_numeric_assertions_reject_prefixed_digits() -> None:
    result = ocr_benchmark.run(
        ocr=GoldOcr({"3,100": "13,100"}),
        minimum_page_characters=5,
    )
    assert result["status"] == "failed"
    assert result["assertions"]["misses"] == ["airport-fees"]

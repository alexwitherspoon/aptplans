"""Benchmark the self-hosted OCR lane against Brookings airport tables."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from time import perf_counter
import unicodedata

from catalog import REFERENCE_FILES
from pipeline.ocr import TesseractOcr


SOURCE = REFERENCE_FILES / "brookings-fy2025-26-adopted-budget.pdf"
SOURCE_SHA256 = (
    "da0928d71169b2b27cc1eaec29fd861541cde8ed3d2b8a2b1217448260ce57ad"
)
GOLD_PATH = REFERENCE_FILES.parent / "ocr_gold.json"


def _load_gold(path: Path = GOLD_PATH) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "dataset_id",
        "source_path",
        "source_sha256",
        "pages",
    }
    if set(payload) != required or payload["schema_version"] != 1:
        raise ValueError("invalid OCR gold contract")
    if not isinstance(payload["dataset_id"], str) or not payload[
        "dataset_id"
    ].strip():
        raise ValueError("invalid OCR gold dataset ID")
    if payload["source_path"] != (
        "files/brookings-fy2025-26-adopted-budget.pdf"
    ):
        raise ValueError("invalid OCR gold source path")
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload["source_sha256"])):
        raise ValueError("invalid OCR gold source hash")
    page_numbers: set[int] = set()
    assertion_ids: set[str] = set()
    for page in payload["pages"]:
        if set(page) != {"page", "assertions"}:
            raise ValueError("invalid OCR gold page")
        page_number = page["page"]
        if (
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number < 1
            or page_number in page_numbers
        ):
            raise ValueError("duplicate or invalid OCR gold page")
        page_numbers.add(page_number)
        for assertion in page["assertions"]:
            assertion_id = str(assertion.get("id") or "")
            kind = assertion.get("kind")
            if kind == "sequence":
                expected_keys = {"id", "kind", "text"}
            elif kind == "all_of":
                expected_keys = {"id", "kind", "terms"}
            elif kind == "window":
                expected_keys = {
                    "id",
                    "kind",
                    "anchor",
                    "terms",
                    "max_characters",
                }
            else:
                expected_keys = set()
            if (
                not assertion_id
                or assertion_id in assertion_ids
                or set(assertion) != expected_keys
                or kind not in {"sequence", "all_of", "window"}
            ):
                raise ValueError("invalid OCR gold assertion")
            if kind == "sequence" and (
                not isinstance(assertion["text"], str)
                or not assertion["text"].strip()
            ):
                raise ValueError("empty OCR gold sequence")
            if kind == "all_of" and (
                not isinstance(assertion["terms"], list)
                or len(assertion["terms"]) < 2
                or any(
                    not isinstance(term, str) or not term.strip()
                    for term in assertion["terms"]
                )
            ):
                raise ValueError(
                    "OCR all-of assertion requires two non-empty terms"
                )
            if kind == "window" and (
                not isinstance(assertion["anchor"], str)
                or not assertion["anchor"].strip()
                or not isinstance(assertion["max_characters"], int)
                or isinstance(assertion["max_characters"], bool)
                or not 1 <= assertion["max_characters"] <= 1000
                or not isinstance(assertion["terms"], list)
                or not assertion["terms"]
                or any(
                    not isinstance(term, str) or not term.strip()
                    for term in assertion["terms"]
                )
            ):
                raise ValueError("invalid OCR window assertion")
            assertion_ids.add(assertion_id)
    if page_numbers != {57, 58} or not assertion_ids:
        raise ValueError("OCR gold contract must cover pages 57 and 58")
    return payload


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = re.sub(r"(?<=\d)[,$-](?=\d)", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _contains(normalized: str, expected: str) -> bool:
    expected = _normalize(expected)
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(expected)}(?![a-z0-9])",
            normalized,
        )
    )


def _evaluate(text: str, assertions: list[dict]) -> list[str]:
    normalized = _normalize(text)
    misses: list[str] = []
    for assertion in assertions:
        if assertion["kind"] == "sequence":
            passed = _contains(normalized, assertion["text"])
        elif assertion["kind"] == "all_of":
            passed = all(
                _contains(normalized, term)
                for term in assertion["terms"]
            )
        else:
            anchor = _normalize(assertion["anchor"])
            match = re.search(
                rf"(?<![a-z0-9]){re.escape(anchor)}(?![a-z0-9])",
                normalized,
            )
            offset = match.start() if match else -1
            window = (
                normalized[
                    offset : offset + int(assertion["max_characters"])
                ]
                if offset >= 0
                else ""
            )
            passed = bool(window) and all(
                _contains(window, term)
                for term in assertion["terms"]
            )
        if not passed:
            misses.append(str(assertion["id"]))
    return misses


def run(
    *,
    source: Path = SOURCE,
    ocr: TesseractOcr | None = None,
    gold_path: Path = GOLD_PATH,
    minimum_page_characters: int = 50,
) -> dict:
    gold = _load_gold(gold_path)
    source = Path(source)
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SOURCE_SHA256 or digest != gold["source_sha256"]:
        raise ValueError("Brookings OCR benchmark bytes changed")
    backend = ocr or TesseractOcr()
    version = backend.version
    started = perf_counter()
    rows: list[dict] = []
    misses: list[str] = []
    assertion_count = 0
    for gold_page in gold["pages"]:
        page_number = int(gold_page["page"])
        page_started = perf_counter()
        result = backend.extract_page(source, page_number)
        text = result.text.strip()
        if len(text) < int(minimum_page_characters):
            raise ValueError(
                f"Brookings OCR page {page_number} fell below text floor"
            )
        if not result.coordinates:
            raise ValueError(
                f"Brookings OCR page {page_number} has no cited coordinates"
            )
        page_misses = _evaluate(text, gold_page["assertions"])
        misses.extend(page_misses)
        assertion_count += len(gold_page["assertions"])
        rows.append(
            {
                "page": page_number,
                "characters": len(text),
                "word_count": int(result.quality.get("word_count") or 0),
                "mean_confidence": result.quality.get("mean_confidence"),
                "assertions_passed": (
                    len(gold_page["assertions"]) - len(page_misses)
                ),
                "assertions_total": len(gold_page["assertions"]),
                "misses": page_misses,
                "seconds": round(perf_counter() - page_started, 3),
            }
        )
    return {
        "status": "passed" if not misses else "failed",
        "dataset_id": gold["dataset_id"],
        "source_sha256": digest,
        "ocr_version": version,
        "ocr_options": {
            "language": getattr(backend, "language", None),
            "dpi": getattr(backend, "dpi", None),
            "page_segmentation_mode": getattr(
                backend, "page_segmentation_mode", None
            ),
            "timeout_seconds": getattr(backend, "timeout_seconds", None),
        },
        "pages": rows,
        "assertions": {
            "passed": assertion_count - len(misses),
            "total": assertion_count,
            "misses": misses,
        },
        "total_characters": sum(row["characters"] for row in rows),
        "total_seconds": round(perf_counter() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--minimum-page-characters", type=int, default=50)
    args = parser.parse_args()
    try:
        result = run(
            minimum_page_characters=max(1, args.minimum_page_characters),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

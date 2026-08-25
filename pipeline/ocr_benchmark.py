"""Benchmark the self-hosted OCR lane against Brookings airport tables."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

from catalog import REFERENCE_FILES
from pipeline.ocr import TesseractOcr


SOURCE = REFERENCE_FILES / "brookings-fy2025-26-adopted-budget.pdf"
SOURCE_SHA256 = (
    "da0928d71169b2b27cc1eaec29fd861541cde8ed3d2b8a2b1217448260ce57ad"
)
PAGES = (57, 58)
REQUIRED_PHRASES = {
    57: ("city of brookings", "airport", "resources"),
    58: ("city of brookings", "airport", "expenditures"),
}


def run(
    *,
    source: Path = SOURCE,
    ocr: TesseractOcr | None = None,
    minimum_page_characters: int = 50,
    minimum_mean_confidence: float = 85.0,
) -> dict:
    source = Path(source)
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError("Brookings OCR benchmark bytes changed")
    backend = ocr or TesseractOcr()
    version = backend.version
    started = perf_counter()
    rows: list[dict] = []
    for page_number in PAGES:
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
        confidence = result.quality.get("mean_confidence")
        if confidence is None or float(confidence) < minimum_mean_confidence:
            raise ValueError(
                f"Brookings OCR page {page_number} confidence is too low"
            )
        normalized = text.lower()
        missing = [
            phrase
            for phrase in REQUIRED_PHRASES[page_number]
            if phrase not in normalized
        ]
        if missing:
            raise ValueError(
                f"Brookings OCR page {page_number} missing golden phrases: "
                + ", ".join(missing)
            )
        rows.append(
            {
                "page": page_number,
                "characters": len(text),
                "text_sha256": hashlib.sha256(
                    text.encode("utf-8")
                ).hexdigest(),
                "word_count": int(result.quality.get("word_count") or 0),
                "mean_confidence": result.quality.get("mean_confidence"),
                "seconds": round(perf_counter() - page_started, 3),
            }
        )
    return {
        "status": "passed",
        "source_sha256": digest,
        "ocr_version": version,
        "pages": rows,
        "total_characters": sum(row["characters"] for row in rows),
        "total_seconds": round(perf_counter() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--minimum-page-characters", type=int, default=50)
    parser.add_argument("--minimum-mean-confidence", type=float, default=85.0)
    args = parser.parse_args()
    try:
        result = run(
            minimum_page_characters=max(1, args.minimum_page_characters),
            minimum_mean_confidence=max(
                0.0, args.minimum_mean_confidence
            ),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

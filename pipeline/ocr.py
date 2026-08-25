"""Self-hosted OCR boundary for image-only PDF pages."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import io
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from typing import Callable


class OcrUnavailable(RuntimeError):
    """The configured local OCR toolchain is not available."""


@dataclass(frozen=True)
class OcrPage:
    text: str
    coordinates: list[dict]
    quality: dict


RunCommand = Callable[..., subprocess.CompletedProcess]


def _command(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def ocr_enabled() -> bool:
    return os.environ.get("APTPLANS_OCR", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


class TesseractOcr:
    """Render with Poppler and OCR locally with Tesseract TSV output."""

    def __init__(
        self,
        *,
        language: str = "eng",
        dpi: int = 200,
        page_segmentation_mode: int = 12,
        timeout_seconds: int = 180,
        run: RunCommand = subprocess.run,
    ) -> None:
        self.language = language
        self.dpi = max(72, int(dpi))
        self.page_segmentation_mode = int(page_segmentation_mode)
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.run = run

    @property
    def version(self) -> str:
        command = _command("APTPLANS_TESSERACT_BIN", "tesseract")
        environment = {
            **os.environ,
            "LANG": "C",
            "LC_ALL": "C",
            "OMP_THREAD_LIMIT": "1",
        }
        try:
            result = self.run(
                [command, "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
                env=environment,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            raise OcrUnavailable("Tesseract is unavailable") from exc
        first = (result.stdout or result.stderr or "").splitlines()
        if not first:
            raise OcrUnavailable("Tesseract did not report a version")
        return first[0].strip()

    def extract_page(self, source: Path, page_number: int) -> OcrPage:
        renderer = _command("APTPLANS_PDFTOPPM_BIN", "pdftoppm")
        tesseract = _command("APTPLANS_TESSERACT_BIN", "tesseract")
        environment = {
            **os.environ,
            "LANG": "C",
            "LC_ALL": "C",
            "OMP_THREAD_LIMIT": "1",
        }
        with TemporaryDirectory(prefix="aptplans-ocr-") as raw:
            prefix = Path(raw) / "page"
            try:
                self.run(
                    [
                        renderer,
                        "-f",
                        str(page_number),
                        "-l",
                        str(page_number),
                        "-singlefile",
                        "-r",
                        str(self.dpi),
                        "-png",
                        str(source),
                        str(prefix),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    env=environment,
                )
                result = self.run(
                    [
                        tesseract,
                        str(prefix.with_suffix(".png")),
                        "stdout",
                        "-l",
                        self.language,
                        "--oem",
                        "1",
                        "--psm",
                        str(self.page_segmentation_mode),
                        "tsv",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    env=environment,
                )
            except FileNotFoundError as exc:
                raise OcrUnavailable("local OCR command is unavailable") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"OCR timed out on page {page_number}"
                ) from exc
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or "").strip()
                raise RuntimeError(
                    f"OCR failed on page {page_number}: {detail[:500]}"
                ) from exc
        return _parse_tsv(result.stdout)


def _parse_tsv(payload: str) -> OcrPage:
    words: list[dict] = []
    lines: dict[tuple[int, int, int], list[str]] = {}
    confidences: list[float] = []
    for row in csv.DictReader(io.StringIO(payload), delimiter="\t"):
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        try:
            confidence = float(row.get("conf") or -1)
            coordinate = {
                "x": int(row.get("left") or 0),
                "y": int(row.get("top") or 0),
                "width": int(row.get("width") or 0),
                "height": int(row.get("height") or 0),
                "text": text,
                "confidence": confidence,
            }
            line_key = (
                int(row.get("block_num") or 0),
                int(row.get("par_num") or 0),
                int(row.get("line_num") or 0),
            )
        except (TypeError, ValueError):
            continue
        words.append(coordinate)
        lines.setdefault(line_key, []).append(text)
        if confidence >= 0:
            confidences.append(confidence)
    text = "\n".join(" ".join(lines[key]) for key in sorted(lines))
    mean_confidence = (
        round(sum(confidences) / len(confidences), 3) if confidences else None
    )
    return OcrPage(
        text=text,
        coordinates=words,
        quality={
            "word_count": len(words),
            "mean_confidence": mean_confidence,
        },
    )

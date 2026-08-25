"""Immutable, content-addressed PDF extraction manifests."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.metadata import version as package_version
import json
import os
from pathlib import Path
from time import perf_counter

from pypdf import PdfReader

from pipeline.ocr import OcrPage, OcrUnavailable, TesseractOcr
from pipeline.queue import JobQueue, _connect, _utc_now


ROOT = Path(__file__).resolve().parents[1]
EXTRACTION_ALGORITHM_VERSION = "aptplans-pdf-extraction/1"


def extraction_dir(files_dir: Path | None = None) -> Path:
    configured = os.environ.get("APTPLANS_EXTRACTIONS", "").strip()
    if configured:
        return Path(configured)
    if files_dir is not None:
        return Path(files_dir).parent / "extractions"
    files = os.environ.get("APTPLANS_FILES", "").strip()
    if files:
        return Path(files).parent / "extractions"
    return ROOT / "data" / "extractions"


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ExtractionManifest:
    manifest_key: str
    content_sha256: str
    extractor_version: str
    options_sha256: str
    status: str
    pages: list[dict]
    quality: dict
    errors: list[dict]
    coordinates_available: bool
    duration_ms: int
    created_at: str

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "manifest_key": self.manifest_key,
            "content_sha256": self.content_sha256,
            "extractor_version": self.extractor_version,
            "options_sha256": self.options_sha256,
            "status": self.status,
            "page_count": len(self.pages),
            "pages": self.pages,
            "quality": self.quality,
            "errors": self.errors,
            "coordinates_available": self.coordinates_available,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> ExtractionManifest:
        return cls(
            manifest_key=str(payload["manifest_key"]),
            content_sha256=str(payload["content_sha256"]),
            extractor_version=str(payload["extractor_version"]),
            options_sha256=str(payload["options_sha256"]),
            status=str(payload["status"]),
            pages=list(payload.get("pages") or []),
            quality=dict(payload.get("quality") or {}),
            errors=list(payload.get("errors") or []),
            coordinates_available=bool(payload.get("coordinates_available")),
            duration_ms=int(payload.get("duration_ms") or 0),
            created_at=str(payload["created_at"]),
        )

    def page_text(self) -> list[str]:
        return [str(page.get("text") or "") for page in self.pages]


class ExtractionStore:
    """Index immutable manifests in SQLite and keep full payloads on disk."""

    def __init__(self, ledger_root: Path, root: Path) -> None:
        self.ledger_root = Path(ledger_root)
        self.path = JobQueue(self.ledger_root).path
        self.root = Path(root)

    def _connection(self):
        return _connect(self.path)

    def _manifest_path(self, content_sha256: str, manifest_key: str) -> Path:
        return self.root / content_sha256 / f"{manifest_key}.json"

    def _read_indexed(self, manifest_key: str) -> ExtractionManifest | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT manifest_path, manifest_sha256
                FROM extraction_manifests WHERE manifest_key=?
                """,
                (manifest_key,),
            ).fetchone()
        if row is None:
            return None
        path = self.root / str(row["manifest_path"])
        payload = path.read_bytes()
        if _sha256(payload) != str(row["manifest_sha256"]):
            raise ValueError(f"corrupt extraction manifest: {manifest_key}")
        return ExtractionManifest.from_dict(json.loads(payload))

    def extract_pdf(
        self,
        source: Path,
        *,
        content_sha256: str | None = None,
        ocr: TesseractOcr | None = None,
        minimum_native_characters: int = 40,
        minimum_image_pixels: int = 1_000_000,
    ) -> ExtractionManifest:
        source = Path(source)
        data = source.read_bytes()
        actual_sha256 = _sha256(data)
        if content_sha256 is not None and content_sha256 != actual_sha256:
            raise ValueError("artifact bytes do not match content SHA-256")
        content_sha256 = actual_sha256
        ocr_version = "disabled"
        if ocr is not None:
            try:
                ocr_version = ocr.version
            except OcrUnavailable:
                ocr_version = "unavailable"
        extractor_version = (
            f"{EXTRACTION_ALGORITHM_VERSION}+"
            f"pypdf/{package_version('pypdf')}+ocr/{ocr_version}"
        )
        options = {
            "minimum_native_characters": int(minimum_native_characters),
            "minimum_image_pixels": int(minimum_image_pixels),
            "ocr_language": getattr(ocr, "language", None),
            "ocr_dpi": getattr(ocr, "dpi", None),
            "ocr_page_segmentation_mode": getattr(
                ocr, "page_segmentation_mode", None
            ),
        }
        options_sha256 = _sha256(_canonical(options))
        manifest_key = _sha256(
            _canonical(
                {
                    "content_sha256": content_sha256,
                    "extractor_version": extractor_version,
                    "options_sha256": options_sha256,
                }
            )
        )
        cached = self._read_indexed(manifest_key)
        if cached is not None:
            return cached

        started = perf_counter()
        reader = PdfReader(source)
        pages: list[dict] = []
        errors: list[dict] = []
        method_counts: dict[str, int] = {}
        for page_number, page in enumerate(reader.pages, start=1):
            native = (page.extract_text() or "").strip()
            largest_image_pixels = 0
            if len(native) < minimum_native_characters:
                largest_image_pixels = max(
                    (
                        image.image.size[0] * image.image.size[1]
                        for image in page.images
                    ),
                    default=0,
                )
            method = "native"
            text = native
            coordinates: list[dict] | None = None
            page_quality: dict = {
                "native_characters": len(native),
                "largest_image_pixels": largest_image_pixels,
            }
            error: dict | None = None
            should_ocr = (
                len(native) < minimum_native_characters
                and largest_image_pixels >= minimum_image_pixels
            )
            if should_ocr:
                if ocr is None or ocr_version == "unavailable":
                    method = "supervised"
                    error = {
                        "code": "ocr_unavailable",
                        "message": "image-only page requires local OCR",
                    }
                else:
                    try:
                        ocr_page: OcrPage = ocr.extract_page(
                            source, page_number
                        )
                        text = ocr_page.text.strip()
                        coordinates = ocr_page.coordinates or None
                        page_quality.update(ocr_page.quality)
                        method = "ocr" if text else "supervised"
                        if not text:
                            error = {
                                "code": "ocr_empty",
                                "message": "OCR returned no text",
                            }
                    except Exception as exc:
                        method = "supervised"
                        error = {
                            "code": "ocr_failed",
                            "message": str(exc)[:500],
                        }
            elif not native:
                method = "empty"
            text_sha256 = _sha256(text.encode("utf-8"))
            row = {
                "page": page_number,
                "method": method,
                "text": text,
                "text_sha256": text_sha256,
                "character_count": len(text),
                "quality": page_quality,
                "coordinates": coordinates,
                "error": error,
            }
            pages.append(row)
            method_counts[method] = method_counts.get(method, 0) + 1
            if error is not None:
                errors.append({"page": page_number, **error})

        status = "completed"
        if errors:
            status = "partial" if any(page["text"] for page in pages) else "failed"
        duration_ms = max(0, round((perf_counter() - started) * 1000))
        manifest = ExtractionManifest(
            manifest_key=manifest_key,
            content_sha256=content_sha256,
            extractor_version=extractor_version,
            options_sha256=options_sha256,
            status=status,
            pages=pages,
            quality={
                "method_counts": method_counts,
                "total_characters": sum(
                    int(page["character_count"]) for page in pages
                ),
            },
            errors=errors,
            coordinates_available=any(
                bool(page["coordinates"]) for page in pages
            ),
            duration_ms=duration_ms,
            created_at=_utc_now(),
        )
        return self._persist(
            manifest,
            byte_count=len(data),
            media_type="application/pdf",
        )

    def _persist(
        self,
        manifest: ExtractionManifest,
        *,
        byte_count: int,
        media_type: str,
    ) -> ExtractionManifest:
        payload = _canonical(manifest.to_dict()) + b"\n"
        manifest_sha256 = _sha256(payload)
        path = self._manifest_path(
            manifest.content_sha256, manifest.manifest_key
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        relative = path.relative_to(self.root).as_posix()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO artifact_versions(
                        content_sha256, media_type, byte_count, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        manifest.content_sha256,
                        media_type,
                        int(byte_count),
                        manifest.created_at,
                    ),
                )
                artifact = connection.execute(
                    """
                    SELECT media_type, byte_count FROM artifact_versions
                    WHERE content_sha256=?
                    """,
                    (manifest.content_sha256,),
                ).fetchone()
                if (
                    artifact is None
                    or str(artifact["media_type"]) != media_type
                    or int(artifact["byte_count"]) != int(byte_count)
                ):
                    raise ValueError("artifact version metadata conflict")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO extraction_manifests(
                        manifest_key, content_sha256, extractor_version,
                        options_sha256, status, page_count, manifest_sha256,
                        manifest_path, coordinates_available, quality_json,
                        error_json, duration_ms, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        manifest.manifest_key,
                        manifest.content_sha256,
                        manifest.extractor_version,
                        manifest.options_sha256,
                        manifest.status,
                        len(manifest.pages),
                        manifest_sha256,
                        relative,
                        1 if manifest.coordinates_available else 0,
                        _canonical(manifest.quality).decode("utf-8"),
                        (
                            _canonical(manifest.errors).decode("utf-8")
                            if manifest.errors
                            else None
                        ),
                        manifest.duration_ms,
                        manifest.created_at,
                    ),
                )
                indexed = connection.execute(
                    """
                    SELECT manifest_sha256 FROM extraction_manifests
                    WHERE manifest_key=?
                    """,
                    (manifest.manifest_key,),
                ).fetchone()
                if (
                    indexed is None
                    or str(indexed["manifest_sha256"]) != manifest_sha256
                ):
                    raise ValueError("extraction manifest conflict")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return manifest

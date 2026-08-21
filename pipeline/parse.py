"""Native text from preserved PDFs. Scanned pages may yield little or nothing."""

from __future__ import annotations

import hashlib
import io
import re

from pypdf import PdfReader

from catalog import load_shape_card

# One generate() prompt stays inside the 32k window after the prompt is added.
# The whole file is still read; extra windows are sequential chunks, then a reduce.
CHUNK_CHARS = 24_000

_TAG_RE = re.compile(r"<[^>]+>")


def extract_pages(data: bytes, max_pages: int | None = None) -> list[str]:
    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages[:max_pages] if max_pages is not None else reader.pages
    return [(page.extract_text() or "").strip() for page in pages]


def extract_text(data: bytes, max_chars: int | None = None, max_pages: int | None = None) -> str:
    """Native text from every page unless the caller passes a cap."""
    parts = []
    total = 0
    for page in extract_pages(data, max_pages=max_pages):
        if not page:
            continue
        parts.append(page)
        total += len(page)
        if max_chars is not None and total >= max_chars:
            break
    text = "\n\n".join(parts)
    if max_chars is None:
        return text
    return text[:max_chars]


def outline_titles(data: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(data))
    titles: list[str] = []

    def walk(items) -> None:
        for item in items or []:
            if isinstance(item, list):
                walk(item)
                continue
            title = getattr(item, "title", None) or str(item)
            title = " ".join(title.split())
            if title:
                titles.append(title)

    walk(reader.outline)
    return titles


def viable_chunk(text: str, max_chars: int = CHUNK_CHARS) -> str:
    """First window of text that fits one generate() prompt. Prefer text_chunks for the whole file."""
    cleaned = re.sub(r"[ \t]+", " ", text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars]
    space = cut.rfind(" ")
    return cut[:space] if space > 4000 else cut


def text_chunks(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Split full extracted text into successive generate() windows."""
    rest = re.sub(r"[ \t]+", " ", text or "").strip()
    chunks: list[str] = []
    while rest:
        if len(rest) <= max_chars:
            chunks.append(rest)
            break
        cut = rest[:max_chars]
        space = cut.rfind(" ")
        take = cut[:space] if space > max_chars // 4 else cut
        chunks.append(take.strip())
        rest = rest[len(take) :].lstrip()
    return chunks


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _image_bytes(data: bytes) -> list[bytes]:
    reader = PdfReader(io.BytesIO(data))
    blobs: list[bytes] = []
    for page in reader.pages:
        resources = page.get("/Resources")
        if resources is None:
            continue
        resources = resources.get_object()
        xobjects = resources.get("/XObject") if resources else None
        if xobjects is None:
            continue
        xobjects = xobjects.get_object()
        for name in xobjects:
            obj = xobjects[name].get_object()
            if obj.get("/Subtype") != "/Image":
                continue
            try:
                blobs.append(obj.get_data())
            except Exception:
                continue
    return blobs


def content_fingerprint(data: bytes) -> tuple[str, str]:
    """Hash extracted text and embedded images. PDF wrapper metadata is ignored."""
    if not data.startswith(b"%PDF"):
        text = normalize_text(_TAG_RE.sub(" ", data.decode("utf-8", "replace")))
        return hashlib.sha256(text.encode("utf-8")).hexdigest(), hashlib.sha256(b"").hexdigest()
    text = normalize_text(extract_text(data))
    text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    images = hashlib.sha256()
    for blob in _image_bytes(data):
        images.update(hashlib.sha256(blob).digest())
    return text_sha, images.hexdigest()


def content_changed(
    previous_text: str | None,
    previous_images: str | None,
    text_sha: str | None,
    images_sha: str | None,
) -> bool | None:
    """True when text or drawings differ. None when there is nothing to compare."""
    if not previous_text and not previous_images:
        return None
    if not text_sha or not images_sha:
        return None
    return previous_text != text_sha or previous_images != images_sha


def change_note(byte_hash_changed: bool, content_differs: bool | None) -> str | None:
    if not byte_hash_changed:
        return None
    if content_differs is False:
        return "Official file was replaced; text and drawings are unchanged."
    if content_differs is True:
        return "Text or drawings changed at the official URL."
    return "Preserved bytes changed at the official URL."


def shape_hits(text: str, shape: dict | None = None) -> list[str]:
    card = shape or load_shape_card()
    blob = (text or "").lower()
    hits = []
    for element in card.get("core_elements") or []:
        token = element.replace("_", " ")
        if token in blob or element.replace("_", "") in blob.replace(" ", ""):
            hits.append(element)
    return hits

"""Native text from preserved PDFs. Scanned pages may yield little or nothing."""

from __future__ import annotations

import io
import re

from pypdf import PdfReader

from catalog import load_shape_card

# Stay well under the 32k model window after the prompt is added.
CHUNK_CHARS = 24_000


def extract_pages(data: bytes, max_pages: int | None = None) -> list[str]:
    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages[:max_pages] if max_pages is not None else reader.pages
    return [(page.extract_text() or "").strip() for page in pages]


def extract_text(data: bytes, max_chars: int = 500_000) -> str:
    parts = []
    total = 0
    for page in extract_pages(data):
        if not page:
            continue
        parts.append(page)
        total += len(page)
        if total >= max_chars:
            break
    text = "\n\n".join(parts)
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
    cleaned = re.sub(r"[ \t]+", " ", text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars]
    space = cut.rfind(" ")
    return cut[:space] if space > 4000 else cut


def shape_hits(text: str, shape: dict | None = None) -> list[str]:
    card = shape or load_shape_card()
    blob = (text or "").lower()
    hits = []
    for element in card.get("core_elements") or []:
        token = element.replace("_", " ")
        if token in blob or element.replace("_", "") in blob.replace(" ", ""):
            hits.append(element)
    return hits

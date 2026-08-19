from __future__ import annotations

from urllib.robotparser import RobotFileParser

from catalog import REFERENCE_FILES
from pipeline import fetch
from pipeline.parse import extract_text, shape_hits, viable_chunk
from pipeline.ollama import unofficial_note

INVENTORY = REFERENCE_FILES / "4s9-2008-inventory.pdf"


def test_extract_inventory_has_mulino_and_inventory_heading() -> None:
    text = extract_text(INVENTORY.read_bytes())
    assert "Mulino" in text
    assert "INVENTORY" in text
    hits = shape_hits(text)
    assert "inventory" in hits
    chunk = viable_chunk(text, max_chars=800)
    assert len(chunk) <= 800
    assert "Mulino" in chunk


def test_unofficial_note_uses_injected_generate() -> None:
    note = unofficial_note("Mulino inventory chapter", generate_fn=lambda prompt: "Stay in chapter two.")
    assert note == "Stay in chapter two."


def test_robots_cache_honors_disallow(monkeypatch) -> None:
    fetch._robots.clear()
    parser = RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /secret"])
    fetch._robots["https://example.com"] = parser
    assert fetch._robots_ok("https://example.com/secret/file.pdf", 10, None) is False
    assert fetch._robots_ok("https://example.com/plans/file.pdf", 10, None) is True
    fetch._robots.clear()

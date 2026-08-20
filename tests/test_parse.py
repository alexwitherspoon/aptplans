from __future__ import annotations

from urllib.robotparser import RobotFileParser

from catalog import REFERENCE_FILES
from pipeline import fetch
from pipeline.parse import (
    change_note,
    content_changed,
    content_fingerprint,
    extract_text,
    shape_hits,
    viable_chunk,
)
from pipeline.ollama import unofficial_note

INVENTORY = REFERENCE_FILES / "4s9-2008-inventory.pdf"


def test_content_fingerprint_differs_across_chapters() -> None:
    inventory = INVENTORY.read_bytes()
    other = (REFERENCE_FILES / "4s9-2008-alternatives.pdf").read_bytes()
    left = content_fingerprint(inventory)
    right = content_fingerprint(other)
    assert left != right
    assert content_fingerprint(inventory) == left


def test_change_note_distinguishes_shell_from_content() -> None:
    assert change_note(True, False) == "Official file was replaced; text and drawings are unchanged."
    assert change_note(True, True) == "Text or drawings changed at the official URL."
    assert content_changed("aa", "bb", "aa", "bb") is False
    assert content_changed("aa", "bb", "aa", "cc") is True
    assert content_changed(None, None, "aa", "bb") is None


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

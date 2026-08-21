from pathlib import Path

from catalog import COMPLETENESS, load_schema


def test_schema_lists_completeness_states() -> None:
    schema = load_schema()
    values = set(schema["properties"]["completeness"]["enum"])
    assert values == set(COMPLETENESS)


def test_schema_has_notice_and_content_fingerprints() -> None:
    schema = load_schema()
    assert "notice" in schema["properties"]["kind"]["enum"]
    assert "text_sha256" in schema["properties"]
    assert "images_sha256" in schema["properties"]
    assert "publisher" in schema["properties"]
    assert "published_at" in schema["properties"]
    assert "found_on" in schema["properties"]
    assert "part_of" in schema["properties"]
    assert "media" in schema["properties"]


def test_complete_requires_official_and_preserved_copy() -> None:
    schema = load_schema()
    assert "source_url" in schema["required"]
    assert "complete" in schema["properties"]["completeness"]["enum"]
    assert "link_only" in schema["properties"]["completeness"]["enum"]


def test_schema_file_is_json() -> None:
    path = Path(__file__).resolve().parents[1] / "catalog" / "schema.json"
    assert path.is_file()
    assert path.stat().st_size > 0

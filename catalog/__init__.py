"""Helpers for catalog metadata stored in this repository."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schema.json"
REFERENCES_PATH = ROOT / "references" / "cases.json"
SHAPE_CARD_PATH = ROOT / "references" / "shape_card.json"
REFERENCE_FILES = ROOT / "references" / "files"
COMPLETENESS = (
    "complete",
    "link_only",
    "preserved_only",
    "missing",
    "no_plan_known",
)


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def load_shape_card() -> dict:
    return json.loads(SHAPE_CARD_PATH.read_text(encoding="utf-8"))


def load_reference_cases() -> dict:
    return json.loads(REFERENCES_PATH.read_text(encoding="utf-8"))


def load_embedded_fixtures() -> list[dict]:
    data = load_reference_cases()
    return list(data.get("embedded") or [])

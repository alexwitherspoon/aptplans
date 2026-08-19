"""Helpers for catalog metadata stored in this repository."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schema.json"
COMPLETENESS = (
    "complete",
    "link_only",
    "preserved_only",
    "missing",
    "no_plan_known",
)


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

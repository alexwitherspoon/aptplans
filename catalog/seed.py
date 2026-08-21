"""Build the in-memory catalog from overlay airports, 50 states, and reference cases."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from catalog.geo import US_STATES
from catalog.models import Airport, Budget, Document, Grant, State
from catalog.store import (
    Catalog,
    load_airports_overlay,
    load_budgets_overlay,
    load_changes_overlay,
    load_grants_overlay,
    load_overlay,
    load_overviews_overlay,
    merge_overlay,
)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def reference_seed_enabled() -> bool:
    """Git reference rows and fixture bytes are for CI and local preview only.

    Production is the default: no git reference airports, grants, budgets, or
    embedded fixture paths unless ``APTPLANS_DEV_PREVIEW=1`` (``make dev``) or
    ``APTPLANS_REFERENCE_SEED=1``. Origin never sets those flags.
    """
    flag = os.environ.get("APTPLANS_REFERENCE_SEED", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    if flag in {"0", "false", "no"}:
        return False
    if os.environ.get("APP_ENV", "").strip().lower() == "production":
        return False
    return _truthy("APTPLANS_DEV_PREVIEW")


def _states(catalog_root: Path) -> list[State]:
    by_code = {code: State(code=code, name=name) for code, name in US_STATES.items()}
    path = catalog_root / "references" / "states.json"
    if path.is_file():
        for row in json.loads(path.read_text(encoding="utf-8")).get("states") or []:
            code = row.get("code")
            current = by_code.get(code)
            if current is None:
                continue
            by_code[code] = State.from_dict({**current.to_dict(), **row, "name": current.name})
    return [by_code[code] for code in US_STATES]


def _reference_statutes(catalog_root: Path) -> list[Document]:
    path = catalog_root / "references" / "statutes.json"
    if not path.is_file():
        return []
    rows = json.loads(path.read_text(encoding="utf-8")).get("documents") or []
    return [Document.from_dict(row) for row in rows]


def _apply_reference_cases(by_lid: dict[str, Airport], catalog_root: Path) -> list[Document]:
    cases = json.loads((catalog_root / "references" / "cases.json").read_text(encoding="utf-8"))["cases"]
    documents: list[Document] = []
    for case in cases:
        lid = case["airport_lid"]
        current = by_lid.get(lid)
        in_npias = bool(case.get("npias_role"))
        if current is None:
            by_lid[lid] = Airport(
                lid=lid,
                name=case["name"],
                city=case.get("city") or "",
                state=case["state"],
                county=case.get("county"),
                npias_role=case.get("npias_role"),
                icao=case.get("icao"),
                elevation_ft=case.get("elevation_ft"),
                website=case.get("website"),
                ownership=case.get("ownership"),
                facility_use=case.get("facility_use"),
                in_npias=in_npias,
                runways=list(case.get("runways") or []),
                fuel=case.get("fuel"),
                hangar_storage=bool(case.get("hangar_storage")),
                tiedown_storage=bool(case.get("tiedown_storage")),
                sources=["reference"],
            )
        else:
            by_lid[lid] = replace(
                current,
                name=case.get("name") or current.name,
                city=current.city or case.get("city") or "",
                county=current.county or case.get("county"),
                npias_role=current.npias_role or case.get("npias_role"),
                icao=case.get("icao") or current.icao,
                elevation_ft=(
                    current.elevation_ft
                    if current.elevation_ft is not None
                    else case.get("elevation_ft")
                ),
                website=current.website or case.get("website"),
                ownership=current.ownership or case.get("ownership"),
                facility_use=current.facility_use or case.get("facility_use"),
                in_npias=current.in_npias or in_npias,
                runways=list(current.runways or case.get("runways") or []),
                fuel=current.fuel or case.get("fuel"),
                hangar_storage=current.hangar_storage or bool(case.get("hangar_storage")),
                tiedown_storage=current.tiedown_storage or bool(case.get("tiedown_storage")),
            )
        for row in case["documents"]:
            documents.append(Document.from_dict(row))
    return documents


def _reference_grants(catalog_root: Path) -> list[Grant]:
    path = catalog_root / "references" / "grants.json"
    if not path.is_file():
        return []
    rows = json.loads(path.read_text(encoding="utf-8")).get("grants") or []
    return [Grant.from_dict(row) for row in rows]


def _reference_budgets(catalog_root: Path) -> list[Budget]:
    path = catalog_root / "references" / "budgets.json"
    if not path.is_file():
        return []
    rows = json.loads(path.read_text(encoding="utf-8")).get("budgets") or []
    return [Budget.from_dict(row) for row in rows]


def seed_catalog(catalog_root: Path, overlay_dir: Path | None = None) -> Catalog:
    use_reference = reference_seed_enabled()
    by_lid = {airport.lid: airport for airport in load_airports_overlay(overlay_dir)}
    documents: list[Document] = _reference_statutes(catalog_root)
    if use_reference:
        documents = _apply_reference_cases(by_lid, catalog_root) + documents
    airports = sorted(by_lid.values(), key=lambda item: (item.state, item.lid))
    overlay_grants = load_grants_overlay(overlay_dir)
    overlay_budgets = load_budgets_overlay(overlay_dir)
    grants = overlay_grants if overlay_grants or not use_reference else _reference_grants(catalog_root)
    budgets = overlay_budgets if overlay_budgets or not use_reference else _reference_budgets(catalog_root)
    catalog = Catalog(
        airports=airports,
        states=_states(catalog_root),
        documents=documents,
        changes=load_changes_overlay(overlay_dir),
        grants=grants,
        budgets=budgets,
        overviews=load_overviews_overlay(overlay_dir),
    )
    overlay = load_overlay(overlay_dir)
    if overlay:
        return merge_overlay(catalog, overlay)
    return catalog

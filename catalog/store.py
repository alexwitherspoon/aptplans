from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from catalog.models import Airport, ChangeEvent, Document, Grant, State

COMPLETENESS_RANK = {
    "complete": 4,
    "link_only": 3,
    "preserved_only": 2,
    "no_plan_known": 1,
    "missing": 0,
}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows),
        encoding="utf-8",
    )


@dataclass
class Catalog:
    airports: list[Airport] = field(default_factory=list)
    states: list[State] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    changes: list[ChangeEvent] = field(default_factory=list)
    grants: list[Grant] = field(default_factory=list)
    _airports_by_lid: dict[str, Airport] = field(init=False, repr=False, compare=False)
    _states_by_code: dict[str, State] = field(init=False, repr=False, compare=False)
    _documents_by_id: dict[str, Document] = field(init=False, repr=False, compare=False)
    _documents_by_lid: dict[str, list[Document]] = field(init=False, repr=False, compare=False)
    _grants_by_lid: dict[str, list[Grant]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._airports_by_lid = {airport.lid: airport for airport in self.airports}
        self._states_by_code = {state.code: state for state in self.states}
        self._documents_by_id = {document.id: document for document in self.documents}
        by_lid: dict[str, list[Document]] = {}
        for document in self.documents:
            if not document.airport_lid:
                continue
            by_lid.setdefault(document.airport_lid, []).append(document)
        self._documents_by_lid = by_lid
        grants_by_lid: dict[str, list[Grant]] = {}
        for grant in self.grants:
            grants_by_lid.setdefault(grant.airport_lid, []).append(grant)
        for lid in grants_by_lid:
            grants_by_lid[lid].sort(
                key=lambda item: (
                    item.award_date or "",
                    item.fiscal_year or 0,
                    item.grant_number or "",
                ),
                reverse=True,
            )
        self._grants_by_lid = grants_by_lid

    @property
    def airports_by_lid(self) -> dict[str, Airport]:
        return self._airports_by_lid

    @property
    def states_by_code(self) -> dict[str, State]:
        return self._states_by_code

    @property
    def documents_by_id(self) -> dict[str, Document]:
        return self._documents_by_id

    def document(self, document_id: str) -> Document:
        return self._documents_by_id[document_id]

    def documents_for_airport(self, lid: str) -> list[Document]:
        return list(self._documents_by_lid.get(lid, []))

    def grants_for_airport(self, lid: str) -> list[Grant]:
        return list(self._grants_by_lid.get(lid, []))

    def documents_for_state(self, code: str) -> list[Document]:
        return [document for document in self.documents if document.state == code]

    def airports_for_state(self, code: str) -> list[Airport]:
        return [airport for airport in self.airports if airport.state == code]

    def write(self, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        _write_jsonl(dest / "airports.jsonl", [item.to_dict() for item in self.airports])
        (dest / "states.json").write_text(
            json.dumps([item.to_dict() for item in self.states], indent=2) + "\n",
            encoding="utf-8",
        )
        _write_jsonl(dest / "documents.jsonl", [item.to_dict() for item in self.documents])
        _write_jsonl(dest / "changes.jsonl", [item.to_dict() for item in self.changes])
        _write_jsonl(dest / "grants.jsonl", [item.to_dict() for item in self.grants])

    @classmethod
    def load(cls, source: Path) -> Catalog:
        airports = [Airport.from_dict(row) for row in _read_jsonl(source / "airports.jsonl")]
        states_path = source / "states.json"
        if states_path.is_file():
            states = [State.from_dict(row) for row in json.loads(states_path.read_text(encoding="utf-8"))]
        else:
            states = [State.from_dict(row) for row in _read_jsonl(source / "states.jsonl")]
        documents = [Document.from_dict(row) for row in _read_jsonl(source / "documents.jsonl")]
        changes = [ChangeEvent.from_dict(row) for row in _read_jsonl(source / "changes.jsonl")]
        grants = [Grant.from_dict(row) for row in _read_jsonl(source / "grants.jsonl")]
        return cls(airports=airports, states=states, documents=documents, changes=changes, grants=grants)

    @classmethod
    def empty(cls) -> Catalog:
        return cls()


def merge_overlay(catalog: Catalog, overlay: dict[str, dict]) -> Catalog:
    by_id = {document.id: document for document in catalog.documents}
    for document_id, updates in overlay.items():
        current = by_id.get(document_id)
        if current is not None:
            by_id[document_id] = current.overlay(updates)
            continue
        if "source_url" in updates and "kind" in updates:
            row = {"completeness": "missing", **updates, "id": document_id}
            by_id[document_id] = Document.from_dict(row)
    return Catalog(
        airports=list(catalog.airports),
        states=list(catalog.states),
        documents=list(by_id.values()),
        changes=list(catalog.changes),
        grants=list(catalog.grants),
    )


def load_overlay(overlay_dir: Path | None) -> dict[str, dict]:
    if overlay_dir is None:
        return {}
    path = overlay_dir / "documents.jsonl"
    overlay: dict[str, dict] = {}
    for row in _read_jsonl(path):
        document_id = row.get("id")
        if document_id:
            overlay[document_id] = row
    return overlay


def load_airports_overlay(overlay_dir: Path | None) -> list[Airport]:
    if overlay_dir is None:
        return []
    return [Airport.from_dict(row) for row in _read_jsonl(overlay_dir / "airports.jsonl")]


def write_airports_overlay(overlay_dir: Path, airports: list[Airport]) -> None:
    rows = [
        airport.to_dict()
        for airport in sorted(airports, key=lambda item: (item.state, item.lid))
    ]
    _write_jsonl(overlay_dir / "airports.jsonl", rows)


def load_grants_overlay(overlay_dir: Path | None) -> list[Grant]:
    if overlay_dir is None:
        return []
    return [Grant.from_dict(row) for row in _read_jsonl(overlay_dir / "grants.jsonl")]


def write_grants_overlay(overlay_dir: Path, grants: list[Grant]) -> None:
    rows = [
        grant.to_dict()
        for grant in sorted(
            grants,
            key=lambda item: (item.airport_lid, item.fiscal_year or 0, item.grant_number or ""),
        )
    ]
    _write_jsonl(overlay_dir / "grants.jsonl", rows)


def upsert_airport_overlay(overlay_dir: Path, airport: Airport) -> None:
    by_lid = {item.lid: item for item in load_airports_overlay(overlay_dir)}
    by_lid[airport.lid] = airport
    write_airports_overlay(overlay_dir, list(by_lid.values()))


def write_overlay_update(overlay_dir: Path, document_id: str, updates: dict) -> None:
    overlay = load_overlay(overlay_dir)
    current = dict(overlay.get(document_id) or {})
    current.update(updates)
    current["id"] = document_id
    overlay[document_id] = current
    rows = [overlay[key] for key in sorted(overlay)]
    _write_jsonl(overlay_dir / "documents.jsonl", rows)


def completeness_for_airport(catalog: Catalog, lid: str) -> str:
    docs = catalog.documents_for_airport(lid)
    if not docs:
        return "missing"
    best = "missing"
    for document in docs:
        if COMPLETENESS_RANK.get(document.completeness, 0) > COMPLETENESS_RANK.get(best, 0):
            best = document.completeness
    return best


def counts(catalog: Catalog) -> dict[str, int]:
    airport_status = [completeness_for_airport(catalog, airport.lid) for airport in catalog.airports]
    return {
        "airports": len(catalog.airports),
        "states": len(catalog.states),
        "documents": len(catalog.documents),
        "complete": sum(1 for status in airport_status if status == "complete"),
        "link_only": sum(1 for status in airport_status if status == "link_only"),
        "preserved_only": sum(1 for status in airport_status if status == "preserved_only"),
        "missing": sum(1 for status in airport_status if status == "missing"),
        "no_plan_known": sum(1 for status in airport_status if status == "no_plan_known"),
        "documents_complete": sum(1 for doc in catalog.documents if doc.completeness == "complete"),
        "documents_link_only": sum(1 for doc in catalog.documents if doc.completeness == "link_only"),
    }

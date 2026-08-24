from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from catalog.models import (
    AIRPORT_COVERAGE_STAGES,
    Airport,
    Budget,
    ChangeEvent,
    Document,
    Grant,
    State,
    visible_on_site,
)

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
    budgets: list[Budget] = field(default_factory=list)
    overviews: dict[str, dict] = field(default_factory=dict)
    _airports_by_lid: dict[str, Airport] = field(init=False, repr=False, compare=False)
    _states_by_code: dict[str, State] = field(init=False, repr=False, compare=False)
    _documents_by_id: dict[str, Document] = field(init=False, repr=False, compare=False)
    _documents_by_lid: dict[str, list[Document]] = field(init=False, repr=False, compare=False)
    _grants_by_lid: dict[str, list[Grant]] = field(init=False, repr=False, compare=False)
    _budgets_by_state: dict[str, list[Budget]] = field(init=False, repr=False, compare=False)

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
            if not grant.airport_lid:
                continue
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
        budgets_by_state: dict[str, list[Budget]] = {}
        for budget in self.budgets:
            budgets_by_state.setdefault(budget.state, []).append(budget)
        for code in budgets_by_state:
            budgets_by_state[code].sort(
                key=lambda item: (item.biennium or "", item.fiscal_year or 0, item.id),
                reverse=True,
            )
        self._budgets_by_state = budgets_by_state

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

    def overview_for(self, lid: str) -> dict | None:
        return self.overviews.get(lid)

    def grants_for_state(self, code: str) -> list[Grant]:
        lids = {airport.lid for airport in self.airports_for_state(code)}
        by_lid: dict[str, list[Grant]] = {}
        for grant in self.grants:
            if grant.state != code and grant.airport_lid not in lids:
                continue
            by_lid.setdefault(grant.airport_lid, []).append(grant)
        rows: list[Grant] = []
        for lid in sorted(by_lid):
            chunk = by_lid[lid]
            chunk.sort(
                key=lambda item: (
                    item.award_date or "",
                    item.fiscal_year or 0,
                    item.grant_number or "",
                ),
                reverse=True,
            )
            rows.extend(chunk)
        return rows

    def budgets_for_state(self, code: str) -> list[Budget]:
        return list(self._budgets_by_state.get(code, []))

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
        _write_jsonl(dest / "budgets.jsonl", [item.to_dict() for item in self.budgets])
        write_overviews_overlay(dest, self.overviews)

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
        budgets = [Budget.from_dict(row) for row in _read_jsonl(source / "budgets.jsonl")]
        return cls(
            airports=airports,
            states=states,
            documents=documents,
            changes=changes,
            grants=grants,
            budgets=budgets,
            overviews=load_overviews_overlay(source),
        )

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
        budgets=list(catalog.budgets),
        overviews=dict(catalog.overviews),
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


def load_budgets_overlay(overlay_dir: Path | None) -> list[Budget]:
    if overlay_dir is None:
        return []
    return [Budget.from_dict(row) for row in _read_jsonl(overlay_dir / "budgets.jsonl")]


def write_budgets_overlay(overlay_dir: Path, budgets: list[Budget]) -> None:
    rows = [
        budget.to_dict()
        for budget in sorted(budgets, key=lambda item: (item.state, item.biennium or "", item.id))
    ]
    _write_jsonl(overlay_dir / "budgets.jsonl", rows)


def load_grants_overlay(overlay_dir: Path | None) -> list[Grant]:
    if overlay_dir is None:
        return []
    return [Grant.from_dict(row) for row in _read_jsonl(overlay_dir / "grants.jsonl")]


def write_grants_overlay(overlay_dir: Path, grants: list[Grant]) -> None:
    rows = [
        grant.to_dict()
        for grant in sorted(
            grants,
            key=lambda item: (item.airport_lid or "", item.fiscal_year or 0, item.grant_number or ""),
        )
    ]
    _write_jsonl(overlay_dir / "grants.jsonl", rows)


def load_overviews_overlay(overlay_dir: Path | None) -> dict[str, dict]:
    if overlay_dir is None:
        return {}
    rows: dict[str, dict] = {}
    for row in _read_jsonl(overlay_dir / "overviews.jsonl"):
        lid = row.get("airport_lid")
        if lid:
            rows[lid] = row
    return rows


def write_overviews_overlay(overlay_dir: Path, overviews: dict[str, dict]) -> None:
    rows = [overviews[lid] for lid in sorted(overviews)]
    _write_jsonl(overlay_dir / "overviews.jsonl", rows)


def upsert_overview_overlay(overlay_dir: Path, row: dict) -> None:
    lid = row.get("airport_lid")
    if not lid:
        return
    current = load_overviews_overlay(overlay_dir)
    current[lid] = row
    write_overviews_overlay(overlay_dir, current)


def load_changes_overlay(overlay_dir: Path | None) -> list[ChangeEvent]:
    if overlay_dir is None:
        return []
    return [ChangeEvent.from_dict(row) for row in _read_jsonl(overlay_dir / "changes.jsonl")]


def append_change(overlay_dir: Path, event: ChangeEvent) -> None:
    rows = load_changes_overlay(overlay_dir)
    rows.append(event)
    _write_jsonl(overlay_dir / "changes.jsonl", [item.to_dict() for item in rows])


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


def _airports_with_kind(catalog: Catalog, kind: str) -> int:
    lids: set[str] = set()
    for document in catalog.documents:
        if document.kind != kind or not document.airport_lid or not visible_on_site(document):
            continue
        lids.add(document.airport_lid.strip().upper())
    return len(lids)


def _pct(part: int, total: int) -> int | None:
    if total <= 0:
        return None
    return round(100 * part / total)


def has_verified_plans(catalog: Catalog, lid: str) -> bool:
    """True when a reviewed master plan or ALP is published on site."""
    lid = lid.strip().upper()
    for document in catalog.documents_for_airport(lid):
        if document.kind not in {"master_plan", "alp"}:
            continue
        if not visible_on_site(document):
            continue
        if document.completeness != "complete":
            continue
        if (document.review_status or "pending") == "pending":
            continue
        return True
    return False


def completeness_for_airport(catalog: Catalog, lid: str) -> str:
    docs = [
        document
        for document in catalog.documents_for_airport(lid)
        if document.kind in {"master_plan", "alp"} and visible_on_site(document)
    ]
    if not docs:
        return "missing"
    best = "missing"
    for document in docs:
        if COMPLETENESS_RANK.get(document.completeness, 0) > COMPLETENESS_RANK.get(best, 0):
            best = document.completeness
    return best


COVERAGE_STAGES = AIRPORT_COVERAGE_STAGES


def counts(catalog: Catalog, *, pipeline: dict | None = None) -> dict[str, int | None]:
    airport_status = [completeness_for_airport(catalog, airport.lid) for airport in catalog.airports]
    listed_documents = [document for document in catalog.documents if visible_on_site(document)]
    queue = (pipeline or {}).get("queue") if isinstance(pipeline, dict) else {}
    coverage_raw = (pipeline or {}).get("coverage") if isinstance(pipeline, dict) else {}
    coverage = coverage_raw if isinstance(coverage_raw, dict) else {}
    airports_total = len(catalog.airports)
    if coverage:
        coverage_total = sum(int(coverage.get(stage) or 0) for stage in COVERAGE_STAGES)
        reviewed = coverage_total - int(coverage.get("untouched") or 0)
        pct_reviewed = _pct(reviewed, coverage_total) if coverage_total else None
    else:
        pct_reviewed = None
    with_master_plan = _airports_with_kind(catalog, "master_plan")
    with_alp = _airports_with_kind(catalog, "alp")
    federal_grants = [grant for grant in catalog.grants if grant.level == "federal"]
    local_grants = [grant for grant in catalog.grants if grant.level == "local"]
    return {
        "airports": airports_total,
        "states": len(catalog.states),
        "documents": len(listed_documents),
        "complete": sum(1 for status in airport_status if status == "complete"),
        "link_only": sum(1 for status in airport_status if status == "link_only"),
        "preserved_only": sum(1 for status in airport_status if status == "preserved_only"),
        "missing": sum(1 for status in airport_status if status == "missing"),
        "no_plan_known": sum(1 for status in airport_status if status == "no_plan_known"),
        "documents_complete": sum(
            1 for doc in listed_documents if doc.completeness == "complete"
        ),
        "documents_link_only": sum(
            1 for doc in listed_documents if doc.completeness == "link_only"
        ),
        "saved_copies": sum(
            1
            for doc in catalog.documents
            if doc.completeness == "complete" and visible_on_site(doc)
        ),
        "listed_documents": len(listed_documents),
        "airports_with_plans": sum(1 for status in airport_status if status == "complete"),
        "airports_with_master_plan": with_master_plan,
        "airports_with_alp": with_alp,
        "pct_reviewed": pct_reviewed,
        "pct_master_plan": _pct(with_master_plan, airports_total),
        "pct_alp": _pct(with_alp, airports_total),
        "queue_pending": int((queue or {}).get("pending") or 0),
        "queue_active": int((queue or {}).get("active") or 0),
        "snapshot_pending": int((coverage or {}).get("snapshot_pending") or 0),
        "searched": int((coverage or {}).get("searched") or 0) + int((coverage or {}).get("explored") or 0),
        "grants": len(catalog.grants),
        "statutes": sum(1 for doc in listed_documents if doc.kind in {"statute", "sasp"}),
        "funding_federal_obligated": sum(
            grant.obligated or grant.amount or 0 for grant in federal_grants
        ),
        "funding_federal_airports": len({grant.airport_lid for grant in federal_grants if grant.airport_lid}),
        "funding_state_budget_total": sum(budget.total or 0 for budget in catalog.budgets),
        "funding_states_with_budgets": len({budget.state for budget in catalog.budgets}),
        "funding_local_total": sum(grant.obligated or grant.amount or 0 for grant in local_grants),
        "funding_local_airports": len({grant.airport_lid for grant in local_grants if grant.airport_lid}),
    }

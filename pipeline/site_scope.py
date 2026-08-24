"""Site rebuild scope: which HTML pages to regenerate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalog.seed import seed_catalog
from catalog.store import Catalog
from pipeline.queue import JobQueue, QueueJob
from pipeline.refresh import ROOT

SCOPE_FULL = "full"
SCOPE_ABOUT = "about"
SCOPE_PARTIAL = "partial"


@dataclass(frozen=True)
class BuildScope:
    """Regenerate only selected visitor pages. Full build uses scope=None."""

    airport_lids: frozenset = frozenset()
    state_codes: frozenset = frozenset()
    document_ids: frozenset = frozenset()
    include_about: bool = False
    include_index: bool = False
    include_airports_index: bool = False
    include_states_index: bool = False
    include_search_page: bool = False
    include_feeds_index: bool = False
    include_data: bool = False
    include_global_feeds: bool = False

    def wants_airport(self, lid: str) -> bool:
        return bool(self.airport_lids) and lid.upper() in self.airport_lids

    def wants_state(self, code: str) -> bool:
        return bool(self.state_codes) and code.upper() in self.state_codes

    def wants_document(self, document) -> bool:
        if self.document_ids and document.id in self.document_ids:
            return True
        lid = (document.airport_lid or "").upper()
        state = (document.state or "").upper()
        if lid and self.airport_lids and lid in self.airport_lids:
            return True
        if state and self.state_codes and state in self.state_codes:
            return True
        return False

    def wants_any_html(self) -> bool:
        return bool(
            self.airport_lids
            or self.state_codes
            or self.document_ids
            or self.include_about
            or self.include_index
            or self.include_airports_index
            or self.include_states_index
            or self.include_search_page
            or self.include_feeds_index
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "airport_lids": sorted(self.airport_lids),
            "state_codes": sorted(self.state_codes),
            "document_ids": sorted(self.document_ids),
            "include_about": self.include_about,
            "include_index": self.include_index,
            "include_airports_index": self.include_airports_index,
            "include_states_index": self.include_states_index,
            "include_search_page": self.include_search_page,
            "include_feeds_index": self.include_feeds_index,
            "include_data": self.include_data,
            "include_global_feeds": self.include_global_feeds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildScope:
        return cls(
            airport_lids=frozenset(str(item).upper() for item in data.get("airport_lids") or []),
            state_codes=frozenset(str(item).upper() for item in data.get("state_codes") or []),
            document_ids=frozenset(str(item) for item in data.get("document_ids") or []),
            include_about=bool(data.get("include_about")),
            include_index=bool(data.get("include_index")),
            include_airports_index=bool(data.get("include_airports_index")),
            include_states_index=bool(data.get("include_states_index")),
            include_search_page=bool(data.get("include_search_page")),
            include_feeds_index=bool(data.get("include_feeds_index")),
            include_data=bool(data.get("include_data")),
            include_global_feeds=bool(data.get("include_global_feeds")),
        )


def scope_about() -> BuildScope:
    return BuildScope(include_about=True)


def scope_for_airport(
    catalog: Catalog,
    lid: str,
    *,
    include_about: bool = False,
    include_index: bool = False,
    include_airports_index: bool = False,
    include_states_index: bool = False,
    include_data: bool = False,
    include_global_feeds: bool = False,
) -> BuildScope:
    lid = lid.strip().upper()
    airport = catalog.airports_by_lid.get(lid)
    states: set[str] = set()
    if airport and airport.state:
        states.add(airport.state.upper())
    return BuildScope(
        airport_lids=frozenset({lid}),
        state_codes=frozenset(states),
        include_about=include_about,
        include_index=include_index,
        include_airports_index=include_airports_index,
        include_states_index=include_states_index or bool(states),
        include_data=include_data,
        include_global_feeds=include_global_feeds,
    )


def scope_for_state(code: str) -> BuildScope:
    code = code.strip().upper()
    return BuildScope(state_codes=frozenset({code}), include_states_index=True)


def scope_for_document(catalog: Catalog, document_id: str) -> BuildScope:
    document = catalog.document(document_id)
    lids: set[str] = set()
    states: set[str] = set()
    if document.airport_lid:
        lids.add(document.airport_lid.upper())
        airport = catalog.airports_by_lid.get(document.airport_lid.upper())
        if airport and airport.state:
            states.add(airport.state.upper())
    if document.state:
        states.add(document.state.upper())
    return BuildScope(
        airport_lids=frozenset(lids),
        state_codes=frozenset(states),
        document_ids=frozenset({document.id}),
        include_states_index=bool(states),
    )


def scope_from_lids(
    catalog: Catalog,
    lids: list[str],
    *,
    include_about: bool = False,
    include_index: bool = False,
    include_airports_index: bool = False,
    include_data: bool = False,
) -> BuildScope:
    airport_lids: set[str] = set()
    state_codes: set[str] = set()
    for raw in lids:
        lid = raw.strip().upper()
        if not lid:
            continue
        airport_lids.add(lid)
        airport = catalog.airports_by_lid.get(lid)
        if airport and airport.state:
            state_codes.add(airport.state.upper())
    return BuildScope(
        airport_lids=frozenset(airport_lids),
        state_codes=frozenset(state_codes),
        include_about=include_about,
        include_index=include_index,
        include_airports_index=include_airports_index,
        include_states_index=bool(state_codes),
        include_data=include_data,
    )


def scope_after_link_check() -> BuildScope:
    """List pages and search data that change when URL health changes."""
    return BuildScope(
        include_index=True,
        include_airports_index=True,
        include_data=True,
        include_global_feeds=True,
    )


def scope_after_airport_job(job: QueueJob, catalog: Catalog) -> BuildScope | None:
    """Pick rebuild scope from a finished fetch, vet, or check job."""
    lid = (job.airport_lid or "").strip().upper()
    if not lid and job.document_id:
        try:
            document = catalog.document(job.document_id)
        except KeyError:
            document = None
        if document and document.airport_lid:
            lid = document.airport_lid.upper()
    if job.kind in {"vet", "review"}:
        if not lid:
            return None
        return scope_for_airport(
            catalog,
            lid,
            include_index=True,
            include_airports_index=True,
            include_data=True,
            include_global_feeds=True,
        )
    if job.kind == "check":
        if not lid:
            return scope_after_link_check()
        return scope_for_airport(
            catalog,
            lid,
            include_index=True,
            include_airports_index=True,
            include_data=True,
        )
    if job.kind == "fetch":
        if not lid:
            return None
        return scope_for_airport(catalog, lid, include_data=True)
    if lid:
        return scope_for_airport(catalog, lid)
    return None


def merge_scopes(left: BuildScope | None, right: BuildScope | None) -> BuildScope | None:
    """Combine scopes. None means a full rebuild."""
    if left is None or right is None:
        return None
    return BuildScope(
        airport_lids=left.airport_lids | right.airport_lids,
        state_codes=left.state_codes | right.state_codes,
        document_ids=left.document_ids | right.document_ids,
        include_about=left.include_about or right.include_about,
        include_index=left.include_index or right.include_index,
        include_airports_index=left.include_airports_index or right.include_airports_index,
        include_states_index=left.include_states_index or right.include_states_index,
        include_search_page=left.include_search_page or right.include_search_page,
        include_feeds_index=left.include_feeds_index or right.include_feeds_index,
        include_data=left.include_data or right.include_data,
        include_global_feeds=left.include_global_feeds or right.include_global_feeds,
    )


def apply_scope_to_job(job: QueueJob, scope: BuildScope | None) -> None:
    if scope is None:
        job.report_type = SCOPE_FULL
        job.part_of = None
        job.suggested_kind = None
        job.document_id = None
        job.state = None
        return
    if scope.include_about and not scope.wants_any_html():
        job.report_type = SCOPE_ABOUT
        job.part_of = None
        job.suggested_kind = None
        job.document_id = None
        job.state = None
        return
    job.report_type = SCOPE_PARTIAL
    job.part_of = next(iter(scope.airport_lids), None)
    job.suggested_kind = json.dumps(scope.to_dict(), separators=(",", ":"))
    job.document_id = next(iter(scope.document_ids), None)
    job.state = next(iter(scope.state_codes), None)


def scope_from_job(job: QueueJob, catalog: Catalog | None = None) -> BuildScope | None:
    report = (job.report_type or "").strip().lower()
    if report == SCOPE_FULL or (not report and not job.suggested_kind and not job.part_of):
        return None
    if report == SCOPE_ABOUT:
        return scope_about()
    if job.suggested_kind:
        try:
            return BuildScope.from_dict(json.loads(job.suggested_kind))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    if job.part_of:
        catalog = catalog or seed_catalog(ROOT / "catalog")
        return scope_for_airport(catalog, job.part_of, include_about=report == SCOPE_ABOUT)
    if job.state:
        return scope_for_state(job.state)
    if job.document_id and catalog is not None:
        return scope_for_document(catalog, job.document_id)
    return None


def pending_site_build_scope(queue: JobQueue) -> tuple[BuildScope | None, Path | None]:
    """Return (scope, path) for a pending site_build job (not active/claimed)."""
    for path in queue.pending.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if data.get("kind") != "site_build":
            continue
        job = QueueJob.from_dict(data)
        return scope_from_job(job), path
    return None, None


def find_pending_site_build_path(queue: JobQueue) -> Path | None:
    _scope, path = pending_site_build_scope(queue)
    return path


def scope_cli_flags(args, catalog: Catalog) -> BuildScope | None:
    if getattr(args, "full", False):
        return None
    lids = [item for item in (getattr(args, "lid", None) or []) if item]
    states = [item for item in (getattr(args, "state", None) or []) if item]
    docs = [item for item in (getattr(args, "document", None) or []) if item]
    if not any(
        [
            lids,
            states,
            docs,
            getattr(args, "about", False),
            getattr(args, "index", False),
            getattr(args, "airports_index", False),
            getattr(args, "states_index", False),
            getattr(args, "search", False),
            getattr(args, "feeds", False),
            getattr(args, "data", False),
            getattr(args, "global_feeds", False),
        ]
    ):
        return None
    scope = scope_from_lids(
        catalog,
        lids,
        include_about=getattr(args, "about", False),
        include_index=getattr(args, "index", False),
        include_airports_index=getattr(args, "airports_index", False),
        include_data=getattr(args, "data", False),
    )
    airport_lids = set(scope.airport_lids)
    state_codes = set(scope.state_codes)
    document_ids: set[str] = set(scope.document_ids)
    for code in states:
        state_codes.add(code.strip().upper())
    for doc_id in docs:
        doc_scope = scope_for_document(catalog, doc_id)
        airport_lids |= set(doc_scope.airport_lids)
        state_codes |= set(doc_scope.state_codes)
        document_ids |= set(doc_scope.document_ids)
    return BuildScope(
        airport_lids=frozenset(airport_lids),
        state_codes=frozenset(state_codes),
        document_ids=frozenset(document_ids),
        include_about=scope.include_about,
        include_index=scope.include_index or getattr(args, "index", False),
        include_airports_index=scope.include_airports_index or getattr(args, "airports_index", False),
        include_states_index=scope.include_states_index
        or getattr(args, "states_index", False)
        or bool(state_codes),
        include_search_page=getattr(args, "search", False),
        include_feeds_index=getattr(args, "feeds", False),
        include_data=scope.include_data or getattr(args, "data", False),
        include_global_feeds=getattr(args, "global_feeds", False),
    )

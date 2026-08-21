"""Typed catalog records. Completeness lives on documents; airport status is derived."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, TypeVar

T = TypeVar("T")


def _from_dict(cls: type[T], data: dict[str, Any]) -> T:
    allowed = {item.name for item in fields(cls)}
    return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass(frozen=True)
class Airport:
    lid: str
    name: str
    city: str
    state: str
    county: str | None = None
    npias_role: str | None = None
    icao: str | None = None
    iata: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    elevation_ft: int | None = None
    website: str | None = None
    ownership: str | None = None
    service_level: str | None = None
    in_npias: bool = False
    admitted: bool = False
    facility_use: str | None = None
    nasr_effective: str | None = None
    npias_edition: str | None = None
    runways: list[dict[str, Any]] = field(default_factory=list)
    fuel: str | None = None
    hangar_storage: bool = False
    tiedown_storage: bool = False
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Airport:
        payload = dict(data)
        if payload.get("sources") is None:
            payload.pop("sources", None)
        if payload.get("runways") is None:
            payload.pop("runways", None)
        return _from_dict(cls, payload)


FUNDING_LEVELS = ("federal", "state", "local", "other")
FUNDING_LABELS = {
    "federal": "Federal",
    "state": "State",
    "local": "Local",
    "other": "Other",
}


@dataclass(frozen=True)
class Grant:
    airport_lid: str
    fiscal_year: int | None = None
    amount: int | None = None
    description: str = ""
    grant_number: str | None = None
    award_date: str | None = None
    state: str | None = None
    programs: list[str] = field(default_factory=list)
    is_planning: bool = False
    source_url: str | None = None
    obligated: int | None = None
    outlayed: int | None = None
    level: str = "federal"
    entity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Grant:
        payload = dict(data)
        if payload.get("programs") is None:
            payload.pop("programs", None)
        level = payload.get("level") or "federal"
        payload["level"] = level if level in FUNDING_LEVELS else "other"
        return _from_dict(cls, payload)


@dataclass(frozen=True)
class State:
    code: str
    name: str
    agency: str | None = None
    agency_url: str | None = None
    sasp_url: str | None = None
    statute_guide_url: str | None = None
    budget_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> State:
        return _from_dict(cls, data)


@dataclass(frozen=True)
class BudgetLine:
    category: str
    amount: int | None = None
    note: str | None = None
    group: str = "program"
    airport_lid: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BudgetLine:
        return _from_dict(cls, data)


@dataclass(frozen=True)
class Budget:
    id: str
    state: str
    source_url: str
    fiscal_year: int | None = None
    biennium: str | None = None
    title: str | None = None
    publisher: str | None = None
    total: int | None = None
    fte: float | None = None
    lines: list[BudgetLine] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Budget:
        payload = dict(data)
        lines = payload.get("lines") or []
        payload["lines"] = [
            line if isinstance(line, BudgetLine) else BudgetLine.from_dict(line) for line in lines
        ]
        return _from_dict(cls, payload)


@dataclass(frozen=True)
class Document:
    id: str
    kind: str
    source_url: str
    completeness: str
    airport_lid: str | None = None
    state: str | None = None
    title: str | None = None
    edition: str | None = None
    source_retrieved_at: str | None = None
    source_status: str = "unknown"
    content_sha256: str | None = None
    text_sha256: str | None = None
    images_sha256: str | None = None
    preserved_url: str | None = None
    ia_item: str | None = None
    mirrors: list[str] = field(default_factory=list)
    license_or_rights: str = "unknown"
    supersedes: str | None = None
    review_status: str = "pending"
    summary: str | None = None
    publisher: str | None = None
    published_at: str | None = None
    found_on: str | None = None
    part_of: str | None = None
    media: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def overlay(self, updates: dict[str, Any]) -> Document:
        allowed = {key: value for key, value in updates.items() if key in self.__dataclass_fields__}
        return replace(self, **allowed)

    def inferred_media(self) -> str:
        if self.media in {"pdf", "html", "other"}:
            return self.media
        path = (self.source_url or "").split("?", 1)[0].lower()
        if path.endswith(".pdf"):
            return "pdf"
        if path.endswith((".html", ".htm", ".aspx", ".shtml")) or path.endswith("/"):
            return "html"
        return "other"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Document:
        payload = dict(data)
        if payload.get("mirrors") is None:
            payload.pop("mirrors", None)
        return _from_dict(cls, payload)


WORK_KINDS = frozenset({"master_plan", "alp"})
_CHAPTER_MARKERS = (
    "inventory",
    "forecast",
    "alternative",
    "facility requirement",
    "implementation",
    "introduction",
    "chapter",
    "appendix",
)


def work_key(document: Document) -> tuple[str, str] | None:
    """One lineage per airport and kind. Chapters of one edition stay separate records."""
    if document.kind not in WORK_KINDS or not document.airport_lid:
        return None
    return (document.airport_lid, document.kind)


def visible_on_site(document: Document) -> bool:
    """Public pages list curated catalog rows and vetted snapshots only."""
    review = document.review_status or "pending"
    if review == "needs_human":
        return False
    if review in {"published", "auto_pass"}:
        return True
    return review == "pending" and document.completeness == "link_only"


def looks_like_work_edition(document: Document) -> bool:
    """True for a whole plan or ALP file, not a named chapter of the same study."""
    if work_key(document) is None:
        return False
    if document.part_of:
        return False
    blob = f"{document.title or ''} {document.id}".lower().replace("_", " ").replace("-", " ")
    return not any(marker in blob for marker in _CHAPTER_MARKERS)


def find_same_content(
    documents: list[Document],
    *,
    airport_lid: str | None,
    text_sha256: str | None,
    images_sha256: str | None,
) -> Document | None:
    """Reuse a record when text and drawings already match, even at a new URL."""
    if not airport_lid or not text_sha256 or not images_sha256:
        return None
    for document in documents:
        if document.airport_lid != airport_lid:
            continue
        if document.text_sha256 == text_sha256 and document.images_sha256 == images_sha256:
            return document
    return None


def prior_work_document(documents: list[Document], incoming: Document) -> Document | None:
    """Earlier edition of the same airport plan or ALP, if this looks like a replacement."""
    key = work_key(incoming)
    if key is None or not looks_like_work_edition(incoming):
        return None
    peers = [
        document
        for document in documents
        if work_key(document) == key
        and document.id != incoming.id
        and document.source_url != incoming.source_url
        and looks_like_work_edition(document)
    ]
    if not peers:
        return None
    peers.sort(
        key=lambda document: (document.edition or "", document.source_retrieved_at or ""),
        reverse=True,
    )
    return peers[0]


@dataclass(frozen=True)
class ChangeEvent:
    id: str
    entity_type: str
    entity_id: str
    detected_at: str
    review_status: str = "pending"
    from_sha256: str | None = None
    to_sha256: str | None = None
    unofficial_note: str | None = None
    added_bytes: int | None = None
    removed_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChangeEvent:
        return _from_dict(cls, data)

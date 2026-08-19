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
    npias_role: str | None = None
    icao: str | None = None
    iata: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    website: str | None = None
    ownership: str | None = None
    service_level: str | None = None
    in_npias: bool = False
    admitted: bool = False
    facility_use: str | None = None
    nasr_effective: str | None = None
    npias_edition: str | None = None
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Airport:
        payload = dict(data)
        if payload.get("sources") is None:
            payload.pop("sources", None)
        return _from_dict(cls, payload)


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Grant:
        payload = dict(data)
        if payload.get("programs") is None:
            payload.pop("programs", None)
        return _from_dict(cls, payload)


@dataclass(frozen=True)
class State:
    code: str
    name: str
    agency: str | None = None
    agency_url: str | None = None
    sasp_url: str | None = None
    statute_guide_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> State:
        return _from_dict(cls, data)


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
    preserved_url: str | None = None
    ia_item: str | None = None
    mirrors: list[str] = field(default_factory=list)
    license_or_rights: str = "unknown"
    supersedes: str | None = None
    review_status: str = "pending"
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def overlay(self, updates: dict[str, Any]) -> Document:
        allowed = {key: value for key, value in updates.items() if key in self.__dataclass_fields__}
        return replace(self, **allowed)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Document:
        payload = dict(data)
        if payload.get("mirrors") is None:
            payload.pop("mirrors", None)
        return _from_dict(cls, payload)


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

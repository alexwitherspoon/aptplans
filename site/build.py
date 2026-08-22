"""Build the static AptPlans site into an output directory."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape as html_escape

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BUILD_PROGRESS_EVERY = 250


def _progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)

from catalog.grants import (
    SPEND_CATEGORY_LABELS,
    SPEND_CATEGORIES,
    effective_spend_category,
    faa_year_summary_url,
    grant_title,
    remaining_obligation,
    usaspending_award_url,
)
from catalog.models import FUNDING_LABELS, FUNDING_LEVELS, feed_visible, looks_like_work_edition, visible_on_site
from catalog.ourairports import http_url
from catalog.seed import reference_seed_enabled, seed_catalog
from catalog.store import Catalog, completeness_for_airport, counts, has_verified_plans
from pipeline.site_scope import BuildScope, scope_cli_flags, scope_from_lids


from pipeline.brief import airport_overview
from pipeline.classifications import classification_stats
from pipeline.pipeline_status import (
    coverage_banner,
    coverage_banner_class,
    coverage_label,
    coverage_stage,
    load_public_snapshot,
    load_status,
    pending_documents,
    plan_panel_empty,
    stage_rows,
)
from pipeline.refresh import overlay_dir_from_env
from pipeline.search import airport_record

TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
CANONICAL = "https://aptplans.org"
INTAKE_URL = (
    "https://github.com/alexwitherspoon/aptplans/issues/new"
    "?template=missing-document.yml"
)

KIND_LABELS = {
    "master_plan": "Airport master plan",
    "alp": "Airport Layout Plan",
    "statute": "Statute",
    "sasp": "State aviation plan",
    "notice": "Notice",
    "other": "Planning document",
}
FINANCE_KIND_LABELS = {
    "issued_grants": "Issued grants",
    "program_budget": "Program budget",
    "project_list": "Project list",
    "cip_proposed": "Proposed CIP",
    "pfc": "PFC",
    "bond": "Bond",
    "other": "Finance (other)",
    "not_finance": "Not finance",
}
RSS_ALL = {"title": "AptPlans - new documents", "href": "/feeds/all.xml"}
RSS_LAWS = {"title": "AptPlans - state aviation law", "href": "/feeds/laws.xml"}
OWNERSHIP_LABELS = {
    "PU": "publicly owned",
    "PR": "privately owned",
    "MA": "Air Force",
    "MN": "Navy",
    "MR": "Army",
    "CG": "Coast Guard",
}


COMPLETENESS_PHRASE = {
    "complete": "Official link and a saved copy are both on file.",
    "link_only": "Official link listed. No saved copy yet.",
    "preserved_only": "Saved copy on file. Official link is missing or dead.",
    "missing": "No master plan or Airport Layout Plan listed yet.",
    "no_plan_known": "No master plan or Airport Layout Plan is known.",
}
_DOC_KIND_ORDER = {"alp": 0, "master_plan": 1, "other": 2}


_HUB_GLOSS = "NPIAS size class, by share of US passenger boardings."
GLOSSARY = {
    "lid": (
        "FAA location identifier. The three- or four-character code used in US "
        "airport records."
    ),
    "icao": (
        "International Civil Aviation Organization identifier. Shown only when no "
        "FAA location identifier is on file."
    ),
    "npias": (
        "National Plan of Integrated Airport Systems. The FAA list of airports in "
        "the national system, generally eligible for federal airport grants."
    ),
    "large_hub": f"{_HUB_GLOSS} Large hubs are the busiest commercial airports.",
    "medium_hub": f"{_HUB_GLOSS} Medium hubs are the next group after large hubs.",
    "small_hub": f"{_HUB_GLOSS} Small hubs have a smaller share of US passenger boardings.",
    "nonhub": (
        f"{_HUB_GLOSS} Nonhub commercial airports have the smallest share of "
        "passenger boardings among primary airports."
    ),
    "reliever": (
        "NPIAS role. Relievers are general aviation airports meant to take traffic "
        "away from busy commercial airports."
    ),
    "national": "NPIAS role for a general aviation airport with a national role in the system.",
    "regional": "NPIAS role for a general aviation airport with a regional role in the system.",
    "local": "NPIAS role for a general aviation airport that mainly serves local flying.",
    "basic": "NPIAS role for a general aviation airport with a basic role in the system.",
    "primary": (
        "NPIAS service level. A primary airport has at least 10,000 passenger "
        "boardings a year."
    ),
    "commercial_service": (
        "NPIAS service level. A commercial service airport has scheduled passenger service."
    ),
    "general_aviation": (
        "NPIAS service level. A general aviation airport does not have scheduled "
        "commercial passenger service as its primary role."
    ),
    "public-use": (
        "Open to the public. Private-use airports appear here only when a plan or "
        "an issue admits them."
    ),
    "private-use": (
        "Not open to the public. Listed here because a plan or an issue admitted "
        "this airport."
    ),
    "alp": "Airport Layout Plan. The drawing set of today's airport and what is planned next.",
    "faa": (
        "Federal Aviation Administration. The US agency that catalogs airports and "
        "reviews layout plans."
    ),
    "aip": "Airport Improvement Program. FAA grants for airport planning and development.",
}


def abbr(label: str, term: str | None = None) -> Markup:
    raw = (term or label or "").strip()
    title = (
        GLOSSARY.get(raw)
        or GLOSSARY.get(raw.replace(" ", "_"))
        or GLOSSARY.get(raw.replace("_", " "))
    )
    text = html_escape(label)
    if not title:
        return text
    return Markup(f'<abbr title="{html_escape(title)}">{text}</abbr>')


def public_website(url: str | None) -> str:
    return http_url(url) or ""


def website_label(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/")
    if path and len(path) <= 32:
        return f"{host}{path}"
    return host


def county_label(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if any(
        token in lower
        for token in ("county", "parish", "borough", "census area", "municipality", "municipio")
    ):
        return text
    return f"{text} County"


def identity_facts(airport) -> list[dict[str, str | None]]:
    facts: list[dict[str, str | None]] = []
    if airport.facility_use == "PU":
        facts.append({"label": "Public-use", "term": "public-use"})
    elif airport.facility_use == "PR":
        facts.append({"label": "Private-use", "term": "private-use"})
    owned = ownership_label(airport.ownership)
    if owned:
        facts.append({"label": owned, "term": None})
    if airport.npias_role:
        facts.append({"label": role_label(airport.npias_role), "term": airport.npias_role})
    return facts


def place_line(airport, state=None) -> str:
    parts: list[str] = []
    if airport.city:
        parts.append(airport.city)
    if airport.county:
        parts.append(county_label(airport.county))
    if state is not None and getattr(state, "name", None):
        parts.append(state.name)
    elif airport.state:
        parts.append(airport.state)
    return ", ".join(parts)


def role_label(value: str | None) -> str:
    return (value or "").replace("_", " ")


def ownership_label(value: str | None) -> str:
    if not value:
        return ""
    return OWNERSHIP_LABELS.get(value.upper(), value)


def overview_grant_lines(grants: list) -> list[str]:
    ranked = sorted(
        grants,
        key=lambda grant: (
            0 if remaining_obligation(grant) else 1,
            -(grant.fiscal_year or 0),
            -(grant.amount or 0),
        ),
    )
    lines: list[str] = []
    for grant in ranked[:4]:
        title = grant_title(grant.description)
        if not title:
            continue
        line = f"FY {grant.fiscal_year} {title}" if grant.fiscal_year else title
        rem = remaining_obligation(grant)
        if rem:
            line += f" · ${int(rem):,} not yet spent"
        elif grant.amount:
            line += f" · ${int(grant.amount):,} awarded"
        lines.append(line)
    return lines


def page_overview(airport, works: list, grants: list):
    """Always extract. Do not reuse a current-month overlay row."""
    return airport_overview(works, overview_grant_lines(grants), airport=airport)


def outlook_airport_lists(airports, outlook_by_lid: dict[str, str]):
    """Airports whose listed plans score growing or declining. Maintaining stays off the home lists."""
    growing = [airport for airport in airports if outlook_by_lid.get(airport.lid) == "growing"]
    declining = [airport for airport in airports if outlook_by_lid.get(airport.lid) == "declining"]

    def sort_key(airport):
        return (airport.state or "", (airport.name or "").lower(), airport.lid)

    growing.sort(key=sort_key)
    declining.sort(key=sort_key)
    return growing, declining


def completeness_phrase(value: str | None) -> str:
    return COMPLETENESS_PHRASE.get(value or "", "")


def pdf_embed_src(src: str) -> str:
    """Open letter-style PDFs at page width in the browser viewer."""
    base = (src or "").split("#", 1)[0]
    if not base:
        return src
    return f"{base}#zoom=page-width"


def document_preview(document) -> dict | None:
    """Inline file for the document page. Prefer a hashed copy.

    Official PDFs usually send X-Frame-Options, so local `make dev` serves a
    catalog-gated copy at /files/preview/{id}.pdf instead of hotlinking.
    """
    if getattr(document, "kind", None) == "notice":
        return None
    media = document.inferred_media()
    if media not in {"pdf", "html"}:
        return None
    if document.preserved_url:
        src = document.preserved_url
        if media == "pdf":
            src = pdf_embed_src(src)
        return {"src": src, "media": media, "origin": "saved"}
    if media != "pdf":
        return None
    if (document.source_status or "") == "dead":
        return None
    if not (document.source_url or "").strip():
        return None
    if os.environ.get("APTPLANS_DEV_PREVIEW", "").strip() not in {"1", "true", "yes"}:
        return None
    doc_id = getattr(document, "id", "") or ""
    if not doc_id:
        return None
    return {
        "src": pdf_embed_src(f"/files/preview/{doc_id}.pdf"),
        "media": "pdf",
        "origin": "official",
    }


def grant_brief(description: str) -> str:
    text = " ".join((description or "").split())
    title = grant_title(text)
    if not text or text == title:
        return ""
    return text[len(title) :].lstrip(" ,")


def grant_date(grant) -> str:
    if getattr(grant, "award_date", None):
        try:
            parsed = datetime.strptime(grant.award_date, "%Y-%m-%d")
        except ValueError:
            return grant.award_date
        return f"{parsed.day} {parsed.strftime('%b %Y')}"
    if getattr(grant, "fiscal_year", None):
        return f"FY {grant.fiscal_year}"
    return ""


FUNDING_EMPTY = {
    "federal": "No federal grants listed yet.",
    "state": "No state funding is listed yet for this airport.",
    "local": "No local municipal funding is listed yet.",
    "other": "No other funding listed yet (bonds, passenger fees, or similar).",
}


def funding_sections(grants: list) -> list[dict]:
    by_level: dict[str, list] = {level: [] for level in FUNDING_LEVELS}
    for grant in grants:
        level = grant.level if grant.level in by_level else "other"
        by_level[level].append(grant)
    return [
        {
            "level": level,
            "label": FUNDING_LABELS[level],
            "grants": by_level[level],
            "stats": grant_briefing(by_level[level]),
            "empty": FUNDING_EMPTY[level],
        }
        for level in FUNDING_LEVELS
    ]


PROJECT_PREVIEW = 3


def _grant_amount(grant) -> int:
    return grant.amount or 0


def _empty_purpose_totals() -> dict[str, dict]:
    return {category: {"total": 0, "count": 0} for category in SPEND_CATEGORIES}


def state_grant_allocations(catalog: Catalog, grants: list) -> dict:
    """State dashboard: annual totals, airport carve-up, maintenance vs growth."""
    stats = grant_briefing(grants)
    purpose = _empty_purpose_totals()
    by_year: dict[int, dict] = {}
    by_airport: dict[str, dict] = {}

    for grant in grants:
        category = effective_spend_category(grant)
        amount = _grant_amount(grant)
        purpose[category]["total"] += amount
        purpose[category]["count"] += 1

        lid = grant.airport_lid
        airport = catalog.airports_by_lid.get(lid)
        airport_row = by_airport.setdefault(
            lid,
            {
                "lid": lid,
                "name": airport.name if airport else lid,
                "total": 0,
                "count": 0,
                "year_min": None,
                "year_max": None,
                "purposes": _empty_purpose_totals(),
            },
        )
        airport_row["total"] += amount
        airport_row["count"] += 1
        airport_row["purposes"][category]["total"] += amount
        airport_row["purposes"][category]["count"] += 1
        if grant.fiscal_year is not None:
            year_min = airport_row["year_min"]
            year_max = airport_row["year_max"]
            airport_row["year_min"] = (
                grant.fiscal_year if year_min is None else min(year_min, grant.fiscal_year)
            )
            airport_row["year_max"] = (
                grant.fiscal_year if year_max is None else max(year_max, grant.fiscal_year)
            )

        if grant.fiscal_year is None:
            continue
        year_row = by_year.setdefault(
            grant.fiscal_year,
            {"year": grant.fiscal_year, "total": 0, "count": 0, "airports": {}},
        )
        year_row["total"] += amount
        year_row["count"] += 1
        airport_bucket = year_row["airports"].setdefault(
            lid,
            {
                "lid": lid,
                "name": airport.name if airport else lid,
                "total": 0,
                "count": 0,
                "purposes": _empty_purpose_totals(),
            },
        )
        airport_bucket["total"] += amount
        airport_bucket["count"] += 1
        airport_bucket["purposes"][category]["total"] += amount
        airport_bucket["purposes"][category]["count"] += 1

    purpose_parts = [
        (category, purpose[category]["total"])
        for category in SPEND_CATEGORIES
        if purpose[category]["total"]
    ]
    purpose_total = sum(amount for _, amount in purpose_parts) or 1
    purpose_rows = []
    offset = 0
    for category in SPEND_CATEGORIES:
        row = purpose[category]
        if not row["total"]:
            continue
        pct = round(100 * row["total"] / purpose_total)
        purpose_rows.append(
            {
                "key": category,
                "label": SPEND_CATEGORY_LABELS[category],
                "total": row["total"],
                "count": row["count"],
                "pct": pct,
                "offset": offset,
            }
        )
        offset += pct

    year_rows = []
    for year in sorted(by_year, reverse=True):
        row = by_year[year]
        airports = sorted(row["airports"].values(), key=lambda item: (-item["total"], item["lid"]))
        year_rows.append({**row, "airports": airports})

    airport_rows = sorted(by_airport.values(), key=lambda item: (-item["total"], item["lid"]))

    return {
        "stats": stats,
        "bars": year_bars(grants),
        "purpose": purpose,
        "purpose_rows": purpose_rows,
        "purpose_total": sum(row["total"] for row in purpose_rows) or None,
        "by_year": year_rows,
        "by_airport": airport_rows,
    }


def project_groups(catalog: Catalog, grants: list, preview: int = PROJECT_PREVIEW) -> list[dict]:
    by_lid: dict[str, list] = {}
    for grant in grants:
        by_lid.setdefault(grant.airport_lid, []).append(grant)
    rows = []
    for lid in sorted(by_lid):
        airport = catalog.airports_by_lid.get(lid)
        all_grants = by_lid[lid]
        shown = all_grants[:preview] if preview else all_grants
        rows.append(
            {
                "lid": lid,
                "name": airport.name if airport else lid,
                "grants": shown,
                "more": max(len(all_grants) - len(shown), 0),
                "stats": grant_briefing(all_grants),
            }
        )
    return rows


_VOLUME_MARKERS = (
    "existing condition",
    "inventory",
    "chapter",
    "sheet",
    "appendix",
    "forecast",
    "alternative",
    "facility requirement",
    "implementation",
    "introduction",
    "table of contents",
)
_CHAPTER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_CHAPTER_NUM = re.compile(
    r"chapter\s+(?:(\d+)|(" + "|".join(_CHAPTER_WORDS) + r"))",
    re.I,
)
_APPENDIX_LETTER = re.compile(r"appendix\s+([a-z])", re.I)
_PART_TAIL = re.compile(
    r"\s+(?:"
    r"chapters?\s+\d+(?:\s*-\s*\d+)?\b.*|"
    r"chapter\s+(?:one|two|three|four|five|six|seven|eight|nine|ten)\b.*|"
    r"appendix\s+[a-z]\b.*|"
    r"existing\s+conditions?\b.*|"
    r"introduction\b.*|"
    r"inventory\b.*|"
    r"table\s+of\s+contents\b.*"
    r")$",
    re.I,
)


class FeaturedWork:
    """One study: a whole file, a set of chapters, or both."""

    def __init__(
        self,
        kind: str,
        study_year: int,
        edition: str | None,
        title: str,
        hub,
        parts: tuple,
        summary: str | None = None,
    ) -> None:
        self.kind = kind
        self.study_year = study_year
        self.edition = edition
        self.title = title
        self.hub = hub
        self.parts = parts
        self.summary = summary

    @property
    def key(self) -> tuple[str, int]:
        return (self.kind, self.study_year)


def _volume_blob(document) -> str:
    return f"{document.title or ''} {document.id}".lower().replace("_", " ").replace("-", " ")


def _is_volume(document) -> bool:
    blob = _volume_blob(document)
    return any(marker in blob for marker in _VOLUME_MARKERS)


def _edition_year(value: str | None) -> int:
    if not value:
        return 0
    digits = "".join(ch if ch.isdigit() else " " for ch in value).split()
    years = [int(part) for part in digits if len(part) == 4]
    return max(years) if years else 0


def _study_year(document) -> int:
    for part in re.split(r"[-_]", document.id):
        if part.isdigit() and len(part) == 4:
            return int(part)
    return _edition_year(document.edition)


def _recency_key(document) -> tuple:
    return (
        document.published_at or "",
        _edition_year(document.edition),
        document.source_retrieved_at or "",
        document.edition or "",
        document.title or document.id,
    )


def _part_sort_key(document) -> tuple:
    blob = _volume_blob(document)
    if "table of contents" in blob:
        return (0, 0, document.title or document.id)
    match = _CHAPTER_NUM.search(blob)
    if match:
        number = int(match.group(1)) if match.group(1) else _CHAPTER_WORDS[match.group(2).lower()]
        return (1, number, document.title or document.id)
    if "introduction" in blob:
        return (0, 1, document.title or document.id)
    match = _APPENDIX_LETTER.search(blob)
    if match:
        return (3, ord(match.group(1)), document.title or document.id)
    return (2, 50, document.title or document.id)


def _work_title(hub, parts: tuple) -> str:
    if hub is not None and hub.title:
        return hub.title
    cleaned = []
    for doc in parts:
        title = (doc.title or "").strip()
        stripped = _PART_TAIL.sub("", title).strip(" -")
        cleaned.append(stripped or title or doc.id)
    if not cleaned:
        return "Listed"
    return Counter(cleaned).most_common(1)[0][0]


def _work_edition(hub, parts: tuple) -> str | None:
    if hub is not None and hub.edition:
        return hub.edition
    editions = [doc.edition for doc in parts if doc.edition]
    if not editions:
        return None
    return Counter(editions).most_common(1)[0][0]


def _build_work(kind: str, study_year: int, group: list) -> FeaturedWork:
    wholes = [doc for doc in group if looks_like_work_edition(doc)]
    cores = [doc for doc in wholes if not _is_volume(doc)]
    chosen = cores or wholes
    hub = max(chosen, key=_recency_key) if chosen else None
    parts = tuple(
        sorted(
            (doc for doc in group if hub is None or doc.id != hub.id),
            key=_part_sort_key,
        )
    )
    return FeaturedWork(
        kind=kind,
        study_year=study_year,
        edition=_work_edition(hub, parts),
        title=_work_title(hub, parts),
        hub=hub,
        parts=parts,
        summary=hub.summary if hub is not None else None,
    )


def _work_recency(work: FeaturedWork) -> tuple:
    docs = ((work.hub,) if work.hub is not None else ()) + work.parts
    return max(_recency_key(doc) for doc in docs)


def edition_works(documents: list, kind: str) -> list[FeaturedWork]:
    """Group ALP or master-plan files into studies, newest first.

    Chapters and later section updates share a hub when `part_of` points at it.
    """
    by_id = {doc.id: doc for doc in documents}
    groups: dict[int, list] = {}
    for doc in documents:
        if doc.kind != kind:
            continue
        root = doc
        seen: set[str] = set()
        while getattr(root, "part_of", None) and root.part_of not in seen:
            seen.add(root.part_of)
            parent = by_id.get(root.part_of)
            if parent is None:
                break
            root = parent
        year = _study_year(root) or _study_year(doc)
        groups.setdefault(year, []).append(doc)
    works = [_build_work(kind, year, group) for year, group in groups.items()]
    works.sort(key=_work_recency, reverse=True)
    return works


def featured_work(documents: list, kind: str) -> FeaturedWork | None:
    """Latest study of this kind: prefer a whole file, else the chapter set."""
    works = edition_works(documents, kind)
    return works[0] if works else None


def featured_and_earlier(
    documents: list,
) -> tuple[FeaturedWork | None, FeaturedWork | None, list[FeaturedWork]]:
    alps = edition_works(documents, "alp")
    plans = edition_works(documents, "master_plan")
    latest_alp = alps[0] if alps else None
    latest_plan = plans[0] if plans else None
    featured_keys = {work.key for work in (latest_alp, latest_plan) if work is not None}
    earlier = [work for work in alps + plans if work.key not in featured_keys]
    earlier.sort(key=_work_recency, reverse=True)
    return latest_alp, latest_plan, earlier


def year_bars(grants: list) -> list[dict]:
    counts: dict[int, int] = {}
    totals: dict[int, int] = {}
    for grant in grants:
        if grant.fiscal_year is None:
            continue
        counts[grant.fiscal_year] = counts.get(grant.fiscal_year, 0) + 1
        if grant.amount:
            totals[grant.fiscal_year] = totals.get(grant.fiscal_year, 0) + grant.amount
    years = sorted(set(counts) | set(totals), reverse=True)
    peak = max(totals.values(), default=0) or 1
    return [
        {
            "year": year,
            "total": totals.get(year),
            "count": counts.get(year, 0),
            "pct": round(100 * (totals.get(year) or 0) / peak),
        }
        for year in years
    ]


def grants_by_year(grants: list) -> list[dict]:
    groups: dict[int, list] = {}
    undated = []
    for grant in grants:
        if grant.fiscal_year is None:
            undated.append(grant)
            continue
        groups.setdefault(grant.fiscal_year, []).append(grant)
    rows = [{"year": year, "grants": groups[year]} for year in sorted(groups, reverse=True)]
    if undated:
        rows.append({"year": None, "grants": undated})
    return rows


def money_overview(grants: list) -> dict:
    stats = grant_briefing(grants)
    spent = stats["spent"] or 0
    remaining = stats["remaining"] or 0
    parts = spent + remaining
    spent_pct = round(100 * spent / parts) if parts else 0
    return {
        **stats,
        "bars": year_bars(grants),
        "groups": grants_by_year(grants),
        "has_split": stats["spent"] is not None or stats["remaining"] is not None,
        "spent_pct": spent_pct,
        "remaining_pct": (100 - spent_pct) if parts else 0,
    }


def budget_groups(budget) -> list[tuple[str, list]]:
    groups: dict[str, list] = {}
    order = []
    for line in budget.lines:
        key = line.group or "program"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(line)
    labels = {"program": "By program", "fund": "By fund type", "project": "Projects"}
    return [(labels.get(key, key), groups[key]) for key in order]


def grant_briefing(grants: list) -> dict:
    years = [grant.fiscal_year for grant in grants if grant.fiscal_year is not None]
    amounts = [grant.amount for grant in grants if grant.amount]
    by_year: dict[int, int] = {}
    for grant in grants:
        if grant.fiscal_year is None or not grant.amount:
            continue
        by_year[grant.fiscal_year] = by_year.get(grant.fiscal_year, 0) + grant.amount
    planning = [grant for grant in grants if grant.is_planning]
    spent_parts = [grant.outlayed for grant in grants if grant.outlayed is not None]
    remaining_parts = [
        remaining_obligation(grant)
        for grant in grants
        if remaining_obligation(grant) is not None
    ]
    return {
        "count": len(grants),
        "total": sum(amounts) if amounts else None,
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "planning_count": len(planning),
        "planning": planning,
        "by_year": sorted(by_year.items(), reverse=True),
        "spent": sum(spent_parts) if spent_parts else None,
        "remaining": sum(remaining_parts) if remaining_parts else None,
        "with_outlays": len(spent_parts),
    }


def static_asset_versions() -> dict[str, str]:
    """Content hash of each css/js file. HTML links append ?v= so proxies cannot keep a stale sheet."""
    versions: dict[str, str] = {}
    for folder in ("css", "js"):
        src = STATIC / folder
        if not src.is_dir():
            continue
        for path in src.rglob("*"):
            if not path.is_file():
                continue
            rel = f"/{folder}/{path.relative_to(src).as_posix()}"
            versions[rel] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return versions


def bust_url(path: str, versions: dict[str, str]) -> str:
    key = str(path).split("?", 1)[0]
    ver = versions.get(key)
    if not ver:
        return key
    return f"{key}?v={ver}"


def _env(*, asset=None) -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["kind_label"] = lambda value: KIND_LABELS.get(value, value)
    env.filters["usd"] = lambda value: "" if value is None else f"${int(value):,}"
    env.filters["role_label"] = role_label
    env.filters["completeness_phrase"] = completeness_phrase
    env.filters["abbr"] = abbr
    env.filters["website_label"] = website_label
    env.filters["public_website"] = public_website
    env.filters["grant_title"] = grant_title
    env.filters["grant_brief"] = grant_brief
    env.filters["grant_date"] = grant_date
    env.filters["usaspending_url"] = usaspending_award_url
    env.filters["faa_year_url"] = faa_year_summary_url
    env.filters["grant_remaining"] = remaining_obligation
    env.filters["budget_groups"] = budget_groups
    env.filters["asset"] = asset or (lambda path: str(path).split("?", 1)[0])
    return env


def _overlay_dir() -> Path | None:
    overlay = os.environ.get("APTPLANS_CATALOG_OVERLAY", "").strip()
    return Path(overlay) if overlay else None


def _catalog() -> Catalog:
    return seed_catalog(REPO / "catalog", overlay_dir=_overlay_dir())


def _write(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def _copy_file(src: Path, dst: Path) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    data = src.read_bytes()
    if dst.is_file() and dst.read_bytes() == data:
        return False
    dst.write_bytes(data)
    return True


def _rss_state(state) -> dict:
    return {"title": f"AptPlans - {state.name}", "href": f"/feeds/states/{state.code}.xml"}


def _rss_airport(airport) -> dict:
    return {
        "title": f"AptPlans - {airport.lid} {airport.name}",
        "href": f"/feeds/airports/{airport.lid}.xml",
    }


def _sitemap_day(*values: str | None) -> str | None:
    best = None
    for raw in values:
        if not raw:
            continue
        day = str(raw).strip()[:10]
        if len(day) == 10 and day[4:5] == "-" and day[7:8] == "-":
            if best is None or day > best:
                best = day
    return best


def _sitemap_xml(pages: dict[str, str | None]) -> str:
    """URL set from published pages. lastmod is omitted when the catalog has no date."""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc in sorted(pages):
        lines.append("  <url>")
        lines.append(f"    <loc>{xml_escape(CANONICAL + loc)}</loc>")
        lastmod = pages[loc]
        if lastmod:
            lines.append(f"    <lastmod>{xml_escape(lastmod)}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def _ld_dump(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")


def _ld_website() -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "AptPlans",
        "url": f"{CANONICAL}/",
        "description": (
            "US airport master plans, Airport Layout Plans, and state aviation law."
        ),
        "inLanguage": "en-US",
        "potentialAction": {
            "@type": "SearchAction",
            "target": f"{CANONICAL}/search/?q={{search_term_string}}",
            "query-input": "required name=search_term_string",
        },
    }


def _ld_crumbs(parts: list[tuple[str, str]]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index,
                "name": name,
                "item": f"{CANONICAL}{path}",
            }
            for index, (name, path) in enumerate(parts, start=1)
        ],
    }


def _ld_airport(airport) -> dict:
    loc = f"/airports/{airport.lid}/"
    data: dict = {
        "@context": "https://schema.org",
        "@type": "Airport",
        "name": airport.name,
        "alternateName": airport.lid,
        "url": f"{CANONICAL}{loc}",
        "description": (
            f"{airport.name} ({airport.lid}) plans and funding."
        ),
        "address": {
            "@type": "PostalAddress",
            "addressLocality": airport.city or None,
            "addressRegion": airport.state or None,
            "addressCountry": "US",
        },
    }
    if airport.iata:
        data["iataCode"] = airport.iata
    if airport.icao:
        data["icaoCode"] = airport.icao
    if airport.latitude is not None and airport.longitude is not None:
        data["geo"] = {
            "@type": "GeoCoordinates",
            "latitude": airport.latitude,
            "longitude": airport.longitude,
        }
    site = public_website(airport.website)
    if site:
        data["sameAs"] = site
    address = {key: value for key, value in data["address"].items() if value}
    data["address"] = address
    return data


def _ld_state(state) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "AdministrativeArea",
        "name": state.name,
        "url": f"{CANONICAL}/states/{state.code}/",
        "containedInPlace": {"@type": "Country", "name": "United States"},
        "description": (
            f"{state.name} airports, aviation law, and grants."
        ),
    }


def _ld_document(document, airport=None) -> dict:
    data: dict = {
        "@context": "https://schema.org",
        "@type": "CreativeWork",
        "name": document.title or document.id,
        "url": f"{CANONICAL}/documents/{document.id}/",
        "identifier": document.id,
        "description": document.title or document.id,
        "isBasedOn": document.source_url,
    }
    if document.kind:
        data["additionalType"] = document.kind
    if document.preserved_url:
        data["encoding"] = {
            "@type": "MediaObject",
            "contentUrl": f"{CANONICAL}{document.preserved_url}",
            "encodingFormat": "text/html" if document.inferred_media() == "html" else "application/pdf",
        }
    if airport is not None:
        data["about"] = {
            "@type": "Airport",
            "name": airport.name,
            "url": f"{CANONICAL}/airports/{airport.lid}/",
        }
    return data


def _rfc822(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _rss(
    title: str,
    path: str,
    items: list[dict],
    page: str | None = None,
    *,
    description: str | None = None,
) -> str:
    rows = []
    for item in items[:50]:
        pub = _rfc822(item.get("date"))
        link = item["link"]
        guid = item.get("guid") or link
        lines = [
            "    <item>",
            f"      <title>{xml_escape(item['title'])}</title>",
            f"      <link>{xml_escape(link)}</link>",
            f"      <guid>{xml_escape(guid)}</guid>",
        ]
        if pub:
            lines.append(f"      <pubDate>{pub}</pubDate>")
        lines.append(f"      <description>{xml_escape(item.get('description') or '')}</description>")
        lines.append("    </item>")
        rows.append("\n".join(lines))
    body = "\n".join(rows)
    html_page = page or path
    channel_desc = description or "New documents on AptPlans. Unofficial. Follow the official source on each item."
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<rss version="2.0">\n'
        "  <channel>\n"
        f"    <title>{xml_escape(title)}</title>\n"
        f"    <link>{CANONICAL}{html_page}</link>\n"
        f"    <description>{xml_escape(channel_desc)}</description>\n"
        f"{body}\n"
        "  </channel>\n"
        "</rss>\n"
    )


def _rss_item(
    title: str,
    link: str,
    description: str,
    *,
    date: str | None = None,
    guid: str | None = None,
) -> dict:
    return {
        "title": title,
        "link": link,
        "date": date,
        "description": description,
        "guid": guid,
    }


def _grant_sort_date(grant) -> str:
    if getattr(grant, "award_date", None):
        return grant.award_date
    if getattr(grant, "fiscal_year", None):
        return f"{grant.fiscal_year}-07-01"
    return ""


def _item_for_grant(airport, grant) -> dict:
    page = f"{CANONICAL}/airports/{airport.lid}/#funding"
    title_text = grant_title(grant.description) or grant.description or "Grant"
    when = grant_date(grant)
    title_parts = [part for part in (when, f"${int(grant.amount):,} awarded" if grant.amount else None, title_text) if part]
    desc_parts = [title_text]
    if grant.entity:
        desc_parts.append(grant.entity)
    if grant.programs:
        desc_parts.append(", ".join(grant.programs))
    if grant.is_planning:
        desc_parts.append("planning grant")
    link = usaspending_award_url(grant.grant_number) or grant.source_url or page
    return _rss_item(
        " · ".join(title_parts),
        link,
        ". ".join(desc_parts) + ".",
        date=_grant_sort_date(grant) or None,
        guid=f"{page}#grant-{grant.grant_number or grant.description}",
    )


def airport_rss_items(
    airport,
    *,
    state=None,
    stage: str,
    status_message: str | None,
    grants: list,
    docs: list,
    overview=None,
    show_plan_insights: bool,
) -> tuple[list[dict], str]:
    """Airport feed: identity, review status, grants, then verified plan content."""
    page = f"{CANONICAL}/airports/{airport.lid}/"
    meta_parts = [place_line(airport, state)]
    meta_parts.extend(fact["label"] for fact in identity_facts(airport))
    if airport.elevation_ft is not None:
        meta_parts.append(f"Elevation {airport.elevation_ft} ft")
    if airport.in_npias:
        meta_parts.append("NPIAS")
    if airport.website:
        meta_parts.append(airport.website)
    identity_desc = f"{airport.name} ({airport.lid}). " + " · ".join(meta_parts) + "."
    if show_plan_insights:
        identity_desc += f" Coverage: {coverage_label(stage)}."
    else:
        banner = status_message or coverage_banner(stage) or "Plan coverage not reviewed yet."
        identity_desc += f" {banner}"
    items = [
        _rss_item(
            f"{airport.name} ({airport.lid})",
            page,
            identity_desc,
            date=airport.nasr_effective,
            guid=f"{page}#airport",
        )
    ]
    for grant in sorted(grants, key=_grant_sort_date, reverse=True):
        items.append(_item_for_grant(airport, grant))
    if show_plan_insights and overview is not None:
        overview_parts: list[str] = []
        if overview.facts:
            overview_parts.extend(f"{label}: {value}" for label, value in overview.facts)
        if overview.trajectory is not None:
            band = overview.trajectory.band.replace("_", " ")
            overview_parts.append(f"Planning outlook: {band}")
            if overview.trajectory.note:
                overview_parts.append(overview.trajectory.note)
        if overview_parts:
            items.append(
                _rss_item(
                    f"Planning overview · {airport.lid}",
                    page,
                    " ".join(overview_parts),
                    date=overview.as_of or overview.generated_at,
                    guid=f"{page}#overview",
                )
            )
    items.extend(_item_for_document(doc) for doc in docs)
    dated = [item for item in items if item.get("date")]
    undated = [item for item in items if not item.get("date")]
    dated.sort(key=lambda item: item["date"], reverse=True)
    items = dated + undated
    if show_plan_insights:
        channel_desc = f"Updates for {airport.name} ({airport.lid}): verified plans, overview, and grants."
    elif grants:
        channel_desc = (
            f"{airport.name} ({airport.lid}). FAA identity and grants on file; "
            "plan coverage not published yet."
        )
    else:
        channel_desc = (
            f"{airport.name} ({airport.lid}). Subscribe for review status and future plan updates."
        )
    return items, channel_desc


def _item_for_document(document) -> dict:
    return {
        "title": document.title or document.id,
        "link": f"{CANONICAL}/documents/{quote(document.id)}/",
        "date": document.source_retrieved_at,
        "description": document.summary
        or f"{KIND_LABELS.get(document.kind, document.kind)} - {document.completeness}",
    }


def _inputs_sha256(catalog: Catalog, year: int) -> str:
    digest = hashlib.sha256()
    digest.update(str(year).encode("utf-8"))
    for folder in (TEMPLATES, STATIC):
        if not folder.is_dir():
            continue
        for path in sorted(item for item in folder.rglob("*") if item.is_file()):
            digest.update(path.relative_to(folder).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    digest.update(Path(__file__).read_bytes())
    brief_mod = REPO / "pipeline" / "brief.py"
    if brief_mod.is_file():
        digest.update(brief_mod.read_bytes())
    digest.update(os.environ.get("APTPLANS_DEV_PREVIEW", "").strip().encode("utf-8"))
    payload = {
        "airports": [item.to_dict() for item in catalog.airports],
        "states": [item.to_dict() for item in catalog.states],
        "documents": [item.to_dict() for item in catalog.documents],
        "grants": [item.to_dict() for item in catalog.grants],
        "budgets": [item.to_dict() for item in catalog.budgets],
        "changes": [item.to_dict() for item in catalog.changes],
    }
    digest.update(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    )
    overlay_dir = _overlay_dir() or overlay_dir_from_env()
    for name in ("pipeline.json", "pipeline_status.json", "classifications.jsonl"):
        path = overlay_dir / name
        if path.is_file():
            digest.update(name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _previous_source_sha(out_dir: Path) -> str | None:
    path = out_dir / "status.json"
    if not path.is_file() or not (out_dir / "index.html").is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    sha = payload.get("source_sha")
    return sha if isinstance(sha, str) else None


def _load_search_outlooks(out_dir: Path) -> dict[str, str]:
    path = out_dir / "data" / "search.json"
    if not path.is_file():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    outlooks: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "airport":
            continue
        outlook = row.get("outlook")
        url = row.get("url") or ""
        match = re.match(r"^/airports/([^/]+)/$", url)
        if match and isinstance(outlook, str) and outlook:
            outlooks[match.group(1).upper()] = outlook
    return outlooks


def _load_sitemap_pages(out_dir: Path) -> dict[str, str | None]:
    path = out_dir / "sitemap.xml"
    if not path.is_file():
        return {}
    pages: dict[str, str | None] = {}
    text = path.read_text(encoding="utf-8")
    for block in re.findall(r"<url>.*?</url>", text, flags=re.S):
        loc = re.search(r"<loc>https://aptplans\.org([^<]*)</loc>", block)
        lastmod = re.search(r"<lastmod>([^<]+)</lastmod>", block)
        if loc:
            pages[loc.group(1)] = lastmod.group(1) if lastmod else None
    return pages


def _prune_unemitted(out_dir: Path, emitted: set[str]) -> bool:
    leftover = [
        path
        for path in out_dir.rglob("*")
        if path.is_file() and path.relative_to(out_dir).as_posix() not in emitted
    ]
    for path in leftover:
        path.unlink()
    for path in sorted((item for item in out_dir.rglob("*") if item.is_dir()), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    return bool(leftover)


def build(
    out_dir: Path,
    catalog: Catalog | None = None,
    *,
    scope: BuildScope | None = None,
) -> bool:
    """Write HTML. False when catalog, templates, and static match the last build."""
    out_dir = out_dir.resolve()
    catalog = catalog or _catalog()
    year = date.today().year
    source_sha = _inputs_sha256(catalog, year)
    partial = scope is not None
    if not partial and _previous_source_sha(out_dir) == source_sha:
        _progress("site build: unchanged (inputs match last build)")
        return False

    if partial:
        lids = ",".join(sorted(scope.airport_lids)) if scope.airport_lids else "-"
        _progress(
            f"site build: partial out={out_dir} airports={lids} "
            f"about={scope.include_about} index={scope.include_index} "
            f"data={scope.include_data}"
        )
    else:
        _progress(
            f"site build: starting out={out_dir} airports={len(catalog.airports)} "
            f"documents={len(catalog.documents)} grants={len(catalog.grants)}"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    emitted: set[str] = set()

    def emit(path: Path, text: str) -> None:
        emitted.add(path.relative_to(out_dir).as_posix())
        _write(path, text)

    def emit_copy(src: Path, dst: Path) -> None:
        emitted.add(dst.relative_to(out_dir).as_posix())
        _copy_file(src, dst)

    versions = static_asset_versions()
    versions["/data/search.json"] = source_sha[:12]
    versions["/data/pipeline.json"] = source_sha[:12]
    env = _env(asset=lambda path: bust_url(path, versions))
    overlay_dir = _overlay_dir()
    pipeline = load_public_snapshot(overlay_dir)
    status_rows = load_status(overlay_dir) if overlay_dir is not None else {}
    stats = counts(catalog, pipeline=pipeline)
    classification_counts = (
        classification_stats(overlay_dir) if overlay_dir is not None else {"month_total": 0}
    )
    listed = [doc for doc in catalog.documents if visible_on_site(doc)]
    feed_listed = [doc for doc in listed if feed_visible(doc)]
    recently = sorted(
        feed_listed,
        key=lambda doc: (doc.source_retrieved_at or "", 1 if doc.summary else 0, doc.title or ""),
        reverse=True,
    )[:12]
    context = {
        "canonical": CANONICAL,
        "year": year,
        "counts": stats,
        "pipeline": pipeline,
        "intake_url": INTAKE_URL,
        "recently": recently,
        "rss_links": [RSS_ALL],
        "json_ld": [],
        "cache_bust": source_sha[:12],
    }

    sitemap_pages: dict[str, str | None] = {}
    outlook_by_lid: dict[str, str] = {}

    def note_page(loc: str, lastmod: str | None = None) -> None:
        sitemap_pages[loc] = lastmod

    def render(template_name: str, dest: Path, canonical_path: str, **extra) -> None:
        lastmod = extra.pop("lastmod", None)
        raw_ld = extra.pop("json_ld", context["json_ld"])
        extra["json_ld"] = [
            item if isinstance(item, str) else _ld_dump(item) for item in (raw_ld or [])
        ]
        html = env.get_template(template_name).render(
            **{**context, "canonical_path": canonical_path, **extra},
        )
        emit(dest, html)
        note_page(canonical_path, lastmod)

    if not partial or scope.include_about:
        render(
            "about.html",
            out_dir / "about" / "index.html",
            "/about/",
            rss_links=[RSS_ALL, RSS_LAWS],
            pipeline_stages=stage_rows(pipeline.get("coverage")),
            classification_counts=classification_counts,
        )
    if not partial or scope.include_search_page:
        render("search.html", out_dir / "search" / "index.html", "/search/")
    if not partial or scope.include_airports_index:
        render(
            "airports.html",
            out_dir / "airports" / "index.html",
            "/airports/",
            airports=catalog.airports,
            completeness_for=lambda lid: completeness_for_airport(catalog, lid),
            coverage_for=lambda lid: coverage_stage(
                lid,
                overlay_dir=overlay_dir,
                catalog_root=REPO / "catalog",
                status_rows=status_rows,
            ),
            coverage_label=coverage_label,
        )
    if not partial or scope.include_states_index:
        render(
            "states.html",
            out_dir / "states" / "index.html",
            "/states/",
            states=catalog.states,
            airport_counts={
                state.code: len(catalog.airports_for_state(state.code)) for state in catalog.states
            },
            rss_links=[RSS_LAWS, RSS_ALL],
        )

    for index, airport in enumerate(catalog.airports, start=1):
        if partial and not scope.wants_airport(airport.lid):
            continue
        if index == 1 or index % BUILD_PROGRESS_EVERY == 0 or index == len(catalog.airports):
            _progress(f"site build: airports {index}/{len(catalog.airports)} ({airport.lid})")
        docs = sorted(
            [
                doc
                for doc in catalog.documents_for_airport(airport.lid)
                if visible_on_site(doc)
            ],
            key=lambda doc: (_DOC_KIND_ORDER.get(doc.kind, 9), doc.title or doc.id),
        )
        grants = catalog.grants_for_airport(airport.lid)
        funding = funding_sections(grants)
        latest_alp, latest_plan, earlier = featured_and_earlier(docs)
        money = money_overview(grants)
        state_docs = [
            doc
            for doc in catalog.documents
            if doc.state == airport.state and doc.kind in {"statute", "sasp"}
        ]
        state = catalog.states_by_code.get(airport.state)
        rss_links = [_rss_airport(airport)]
        if state is not None:
            rss_links.append(_rss_state(state))
        rss_links.append(RSS_ALL)
        stage = coverage_stage(
            airport.lid,
            overlay_dir=overlay_dir,
            catalog_root=REPO / "catalog",
            status_rows=status_rows,
        )
        row = status_rows.get(airport.lid.upper()) or {}
        show_plan_insights = has_verified_plans(catalog, airport.lid)
        overview = None
        if show_plan_insights:
            overview = page_overview(
                airport,
                [latest_plan, latest_alp, *earlier],
                grants,
            )
            if overview and overview.trajectory:
                outlook_by_lid[airport.lid] = overview.trajectory.band
        if latest_alp:
            plan_empty = plan_panel_empty(stage, "master_plan", alp_listed=True)
        else:
            plan_empty = plan_panel_empty(stage, "master_plan")
        alp_empty = plan_panel_empty(stage, "alp")
        render(
            "airport.html",
            out_dir / "airports" / airport.lid / "index.html",
            f"/airports/{airport.lid}/",
            airport=airport,
            documents=docs if show_plan_insights else [],
            latest_alp=latest_alp if show_plan_insights else None,
            latest_plan=latest_plan if show_plan_insights else None,
            plan_empty=plan_empty,
            alp_empty=alp_empty,
            show_plan_insights=show_plan_insights,
            overview=overview,
            earlier=earlier if show_plan_insights else [],
            funding=funding,
            money=money,
            completeness=completeness_for_airport(catalog, airport.lid),
            coverage_stage=stage,
            coverage_label=coverage_label(stage),
            coverage_banner=coverage_banner(stage, row),
            coverage_banner_class=coverage_banner_class(stage),
            pending_docs=pending_documents(catalog, airport.lid),
            facts=identity_facts(airport),
            place=place_line(airport, state),
            state=state,
            state_documents=state_docs,
            rss_links=rss_links,
            lastmod=_sitemap_day(
                airport.nasr_effective,
                *[doc.source_retrieved_at for doc in docs],
                *[doc.published_at for doc in docs],
            ),
            json_ld=[
                _ld_airport(airport),
                _ld_crumbs(
                    [("Airports", "/airports/"), (f"{airport.lid} {airport.name}", f"/airports/{airport.lid}/")]
                    if not airport.state
                    else [
                        ("Airports", "/airports/"),
                        (airport.state, f"/states/{airport.state}/"),
                        (f"{airport.lid} {airport.name}", f"/airports/{airport.lid}/"),
                    ]
                ),
            ],
        )

    if not partial or scope.include_index:
        if partial and scope.include_index:
            for airport in catalog.airports:
                if airport.lid in outlook_by_lid:
                    continue
                docs = [
                    doc
                    for doc in catalog.documents_for_airport(airport.lid)
                    if visible_on_site(doc)
                ]
                grants = catalog.grants_for_airport(airport.lid)
                latest_alp, latest_plan, earlier = featured_and_earlier(docs)
                if has_verified_plans(catalog, airport.lid):
                    overview = page_overview(
                        airport,
                        [latest_plan, latest_alp, *earlier],
                        grants,
                    )
                    if overview and overview.trajectory:
                        outlook_by_lid[airport.lid] = overview.trajectory.band
        growing, declining = outlook_airport_lists(catalog.airports, outlook_by_lid)
        render(
            "index.html",
            out_dir / "index.html",
            "/",
            states=catalog.states,
            growing=growing,
            declining=declining,
            rss_links=[RSS_ALL, RSS_LAWS],
            json_ld=[_ld_website()],
        )

    for state in catalog.states:
        if partial and not scope.wants_state(state.code):
            continue
        airports = catalog.airports_for_state(state.code)
        grants = catalog.grants_for_state(state.code)
        state_docs = [
            doc
            for doc in catalog.documents
            if doc.state == state.code and doc.kind in {"statute", "sasp"}
        ]
        render(
            "state.html",
            out_dir / "states" / state.code / "index.html",
            f"/states/{state.code}/",
            state=state,
            airports=airports,
            documents=state_docs,
            budgets=catalog.budgets_for_state(state.code),
            allocations=state_grant_allocations(catalog, grants),
            completeness_for=lambda lid, _catalog=catalog: completeness_for_airport(_catalog, lid),
            rss_links=[_rss_state(state), RSS_LAWS, RSS_ALL],
            lastmod=_sitemap_day(
                *[doc.source_retrieved_at for doc in catalog.documents if doc.state == state.code],
                *[doc.published_at for doc in catalog.documents if doc.state == state.code],
            ),
            json_ld=[
                _ld_state(state),
                _ld_crumbs([("States", "/states/"), (state.name, f"/states/{state.code}/")]),
            ],
        )

    for document in listed:
        if partial and not scope.wants_document(document):
            continue
        airport = catalog.airports_by_lid.get(document.airport_lid or "")
        rss_links = []
        if airport is not None:
            rss_links.append(_rss_airport(airport))
        state = catalog.states_by_code.get(document.state or "")
        if state is not None:
            rss_links.append(_rss_state(state))
        if document.kind in {"statute", "sasp"}:
            rss_links.append(RSS_LAWS)
        rss_links.append(RSS_ALL)
        crumbs: list[tuple[str, str]] = []
        if airport is not None:
            crumbs = [
                ("Airports", "/airports/"),
                (airport.lid, f"/airports/{airport.lid}/"),
            ]
        elif state is not None:
            crumbs = [("States", "/states/"), (state.name, f"/states/{state.code}/")]
        crumbs.append((document.title or document.id, f"/documents/{document.id}/"))
        render(
            "document.html",
            out_dir / "documents" / document.id / "index.html",
            f"/documents/{document.id}/",
            document=document,
            airport=airport,
            kind_label=KIND_LABELS.get(document.kind, document.kind),
            finance_kind_label=FINANCE_KIND_LABELS.get(document.finance_kind or "", ""),
            preview=document_preview(document),
            changes=[
                change
                for change in catalog.changes
                if change.entity_id == document.id
            ],
            rss_links=rss_links,
            lastmod=_sitemap_day(document.source_retrieved_at, document.published_at),
            json_ld=[_ld_document(document, airport), _ld_crumbs(crumbs)],
        )

    if not partial:
        for folder in ("css", "js"):
            src = STATIC / folder
            if not src.is_dir():
                continue
            for path in src.rglob("*"):
                if path.is_file():
                    emit_copy(path, out_dir / folder / path.relative_to(src))

        emit(
            out_dir / "robots.txt",
            "User-agent: *\nAllow: /\nDisallow: /data/\nDisallow: /review/\nSitemap: https://aptplans.org/sitemap.xml\n",
        )

        all_items = [_item_for_document(doc) for doc in recently] or [
            {
                "title": "AptPlans catalog",
                "link": f"{CANONICAL}/",
                "date": None,
                "description": "Airport master plans and Airport Layout Plans.",
            }
        ]
        emit(out_dir / "feeds" / "all.xml", _rss("AptPlans", "/feeds/all.xml", all_items, page="/feeds/"))
        note_page(
            "/feeds/all.xml",
            _sitemap_day(*[item.get("date") for item in all_items if isinstance(item.get("date"), str)]),
        )
        law_docs = [doc for doc in listed if doc.kind in {"statute", "sasp"}]
        emit(
            out_dir / "feeds" / "laws.xml",
            _rss(
                "AptPlans - state aviation law",
                "/feeds/laws.xml",
                [_item_for_document(doc) for doc in law_docs],
                page="/feeds/",
            ),
        )
        note_page("/feeds/laws.xml", _sitemap_day(*[doc.source_retrieved_at for doc in law_docs]))

    if not partial or scope.include_global_feeds:
        if partial and scope.include_global_feeds:
            all_items = [_item_for_document(doc) for doc in recently] or [
                {
                    "title": "AptPlans catalog",
                    "link": f"{CANONICAL}/",
                    "date": None,
                    "description": "Airport master plans and Airport Layout Plans.",
                }
            ]
            emit(out_dir / "feeds" / "all.xml", _rss("AptPlans", "/feeds/all.xml", all_items, page="/feeds/"))
            note_page(
                "/feeds/all.xml",
                _sitemap_day(*[item.get("date") for item in all_items if isinstance(item.get("date"), str)]),
            )
            law_docs = [doc for doc in listed if doc.kind in {"statute", "sasp"}]
            emit(
                out_dir / "feeds" / "laws.xml",
                _rss(
                    "AptPlans - state aviation law",
                    "/feeds/laws.xml",
                    [_item_for_document(doc) for doc in law_docs],
                    page="/feeds/",
                ),
            )
            note_page("/feeds/laws.xml", _sitemap_day(*[doc.source_retrieved_at for doc in law_docs]))

    airport_feeds_by_state: dict[str, list] = {}
    for state in catalog.states:
        if partial and not scope.wants_state(state.code):
            continue
        state_docs = [doc for doc in listed if doc.state == state.code and feed_visible(doc)]
        items = [_item_for_document(doc) for doc in state_docs]
        emit(
            out_dir / "feeds" / "states" / f"{state.code}.xml",
            _rss(
                f"AptPlans - {state.name}",
                f"/feeds/states/{state.code}.xml",
                items,
                page=f"/states/{state.code}/",
            ),
        )
        note_page(
            f"/feeds/states/{state.code}.xml",
            _sitemap_day(*[doc.source_retrieved_at for doc in state_docs]),
        )
    for airport in catalog.airports:
        if partial and not scope.wants_airport(airport.lid):
            continue
        stage = coverage_stage(
            airport.lid,
            overlay_dir=overlay_dir,
            catalog_root=REPO / "catalog",
            status_rows=status_rows,
        )
        row = status_rows.get(airport.lid.upper()) or {}
        docs_visible = [
            doc
            for doc in catalog.documents_for_airport(airport.lid)
            if visible_on_site(doc)
        ]
        feed_docs = [doc for doc in docs_visible if feed_visible(doc)]
        grants = catalog.grants_for_airport(airport.lid)
        latest_alp, latest_plan, earlier = featured_and_earlier(docs_visible)
        show_plan_insights = has_verified_plans(catalog, airport.lid)
        overview = None
        if show_plan_insights:
            overview = page_overview(
                airport,
                [latest_plan, latest_alp, *earlier],
                grants,
            )
        state = catalog.states_by_code.get(airport.state)
        items, channel_desc = airport_rss_items(
            airport,
            state=state,
            stage=stage,
            status_message=coverage_banner(stage, row),
            grants=grants,
            docs=feed_docs,
            overview=overview,
            show_plan_insights=show_plan_insights,
        )
        airport_feeds_by_state.setdefault(airport.state, []).append(airport)
        emit(
            out_dir / "feeds" / "airports" / f"{airport.lid}.xml",
            _rss(
                f"AptPlans - {airport.name}",
                f"/feeds/airports/{airport.lid}.xml",
                items,
                page=f"/airports/{airport.lid}/",
                description=channel_desc,
            ),
        )
        note_page(
            f"/feeds/airports/{airport.lid}.xml",
            _sitemap_day(
                airport.nasr_effective,
                *[doc.source_retrieved_at for doc in feed_docs],
                *[doc.published_at for doc in feed_docs],
                *[grant.award_date for grant in grants if grant.award_date],
                overview.as_of if overview else None,
                overview.generated_at if overview else None,
            ),
        )
    if not partial or scope.include_feeds_index:
        render(
            "feeds.html",
            out_dir / "feeds" / "index.html",
            "/feeds/",
            states=catalog.states,
            airport_feeds_by_state=airport_feeds_by_state,
            airport_feed_count=sum(len(rows) for rows in airport_feeds_by_state.values()),
            rss_links=[RSS_ALL, RSS_LAWS],
        )
    if partial:
        sitemap_pages = {**_load_sitemap_pages(out_dir), **sitemap_pages}
    emit(out_dir / "sitemap.xml", _sitemap_xml(sitemap_pages))

    status = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "unofficial": True,
        "reference_seed": reference_seed_enabled(),
        "overlay_dir": os.environ.get("APTPLANS_CATALOG_OVERLAY", "").strip() or None,
        "counts": stats,
        "source_sha": source_sha,
    }
    emit(out_dir / "status.json", json.dumps(status, indent=2) + "\n")

    if not partial or scope.include_data:
        if partial and scope.include_data:
            outlook_by_lid = {**_load_search_outlooks(out_dir), **outlook_by_lid}
        search_index = []
        for airport in catalog.airports:
            record = airport_record(airport, outlook=outlook_by_lid.get(airport.lid))
            search_index.append(
                {
                    "type": record["type"],
                    "title": record["title"],
                    "url": record["url"],
                    "state": record["state"],
                    "outlook": record["outlook"],
                    "text": record["text"],
                }
            )
        for document in listed:
            search_index.append(
                {
                    "type": document.kind,
                    "title": document.title or document.id,
                    "url": f"/documents/{document.id}/",
                    "state": document.state,
                    "text": " ".join(
                        part
                        for part in (document.id, document.kind, document.edition, document.summary)
                        if part
                    ),
                }
            )
        for state in catalog.states:
            search_index.append(
                {
                    "type": "state",
                    "title": state.name,
                    "url": f"/states/{state.code}/",
                    "state": state.code,
                    "text": " ".join(
                        part
                        for part in (state.code, state.agency, "budget law awards")
                        if part
                    ),
                }
            )
        for grant in catalog.grants:
            title = grant_title(grant.description)
            search_index.append(
                {
                    "type": "funding",
                    "title": f"{grant.airport_lid} {title}",
                    "url": f"/airports/{grant.airport_lid}/#funding",
                    "state": grant.state,
                    "text": " ".join(
                        part
                        for part in (
                            grant.grant_number,
                            grant.description,
                            " ".join(grant.programs or []),
                        )
                        if part
                    ),
                }
            )
        emit(out_dir / "data" / "search.json", json.dumps(search_index) + "\n")
        if pipeline:
            emit(out_dir / "data" / "pipeline.json", json.dumps(pipeline, indent=2) + "\n")
        emit(
            out_dir / "data" / "catalog.json",
            json.dumps(
                {
                    "airports": [item.to_dict() for item in catalog.airports],
                    "states": [item.to_dict() for item in catalog.states],
                    "documents": [item.to_dict() for item in listed],
                }
            )
            + "\n",
        )
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=[
                "id",
                "kind",
                "airport_lid",
                "state",
                "title",
                "source_url",
                "completeness",
                "content_sha256",
            ],
        )
        writer.writeheader()
        for document in listed:
            writer.writerow(
                {
                    "id": document.id,
                    "kind": document.kind,
                    "airport_lid": document.airport_lid or "",
                    "state": document.state or "",
                    "title": document.title or "",
                    "source_url": document.source_url,
                    "completeness": document.completeness,
                    "content_sha256": document.content_sha256 or "",
                }
            )
        emit(out_dir / "data" / "catalog.csv", buf.getvalue())
    if not partial:
        _prune_unemitted(out_dir, emitted)
    _progress(f"site build: wrote {len(emitted)} files to {out_dir}")
    return True


def main() -> None:
    from pipeline.site_build import add_scope_arguments

    parser = argparse.ArgumentParser(description="Build the AptPlans static site")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT.parent / "dist",
        help="Output directory (default: dist/ at repo root)",
    )
    add_scope_arguments(parser)
    args = parser.parse_args()
    catalog = _catalog()
    scope = scope_cli_flags(args, catalog)
    if not build(args.out, catalog=catalog, scope=scope):
        _progress("site unchanged")


if __name__ == "__main__":
    main()

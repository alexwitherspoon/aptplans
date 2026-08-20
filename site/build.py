"""Build the static AptPlans site into an output directory."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from catalog.grants import faa_year_summary_url, remaining_obligation, usaspending_award_url
from catalog.models import FUNDING_LABELS, FUNDING_LEVELS
from catalog.seed import seed_catalog
from catalog.store import Catalog, completeness_for_airport, counts

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
RSS_ALL = {"title": "AptPlans - new documents", "href": "/feeds/all.xml"}
RSS_LAWS = {"title": "AptPlans - state aviation law", "href": "/feeds/laws.xml"}
OWNERSHIP_LABELS = {
    "PU": "public",
    "PR": "private",
    "MA": "Air Force",
    "MN": "Navy",
    "MR": "Army",
    "CG": "Coast Guard",
}
SERVICE_LABELS = {
    "P": "primary",
    "CS": "commercial service",
    "R": "reliever",
    "GA": "general aviation",
}
COMPLETENESS_PHRASE = {
    "complete": "Official link and a saved copy are both on file.",
    "link_only": "Official link listed. No saved copy yet.",
    "preserved_only": "Saved copy on file. Official link is missing or dead.",
    "missing": "No master plan or Airport Layout Plan listed yet.",
    "no_plan_known": "No master plan or Airport Layout Plan is known.",
}
_DOC_KIND_ORDER = {"alp": 0, "master_plan": 1, "other": 2}


def role_label(value: str | None) -> str:
    return (value or "").replace("_", " ")


def ownership_label(value: str | None) -> str:
    if not value:
        return ""
    return OWNERSHIP_LABELS.get(value.upper(), value)


def service_label(value: str | None) -> str:
    if not value:
        return ""
    return SERVICE_LABELS.get(value.upper(), value)


def completeness_phrase(value: str | None) -> str:
    return COMPLETENESS_PHRASE.get(value or "", "")


def grant_title(description: str, entity: str | None = None) -> str:
    text = " ".join((description or "").split())
    if not text:
        return f"{entity} grant" if entity else "Grant"
    return text.split(",", 1)[0].strip() or text


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


def airport_brief(*, airport, documents, funding, state) -> list[str]:
    """Short catalog facts for the top of an airport page. Not a model summary."""
    kinds = {doc.kind for doc in documents}
    lines = []
    has_alp = "alp" in kinds
    has_plan = "master_plan" in kinds
    if has_alp and has_plan:
        lines.append("An Airport Layout Plan and a master plan are listed.")
    elif has_alp:
        lines.append("An Airport Layout Plan is listed. No master plan is listed yet.")
    elif has_plan:
        lines.append("A master plan is listed. No Airport Layout Plan is listed yet.")
    else:
        lines.append("No master plan or Airport Layout Plan is listed yet.")
    funded = [section for section in funding if section["grants"]]
    if funded:
        parts = []
        for section in funded:
            stats = section["stats"]
            label = section["label"].lower()
            count = stats["count"]
            noun = "grant" if count == 1 else "grants"
            if stats["total"]:
                parts.append(f"{count} {label} {noun} totaling ${int(stats['total']):,}")
            else:
                parts.append(f"{count} {label} {noun}")
        lines.append("Funding on file: " + "; ".join(parts) + ". A grant is not a plan.")
    else:
        lines.append("No grants listed yet.")
    if state:
        lines.append(
            f"{state.name} aviation law is on the {airport.state} page."
        )
    notices = [doc for doc in documents if doc.kind == "notice"]
    if notices:
        lines.append(
            f"{len(notices)} notice citation{'s' if len(notices) != 1 else ''} "
            "(publisher, date, and URL only)."
        )
    return lines


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


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["kind_label"] = lambda value: KIND_LABELS.get(value, value)
    env.filters["usd"] = lambda value: "" if value is None else f"${int(value):,}"
    env.filters["role_label"] = role_label
    env.filters["ownership_label"] = ownership_label
    env.filters["service_label"] = service_label
    env.filters["completeness_phrase"] = completeness_phrase
    env.filters["grant_title"] = grant_title
    env.filters["grant_brief"] = grant_brief
    env.filters["grant_date"] = grant_date
    env.filters["usaspending_url"] = usaspending_award_url
    env.filters["faa_year_url"] = faa_year_summary_url
    env.filters["grant_remaining"] = remaining_obligation
    env.filters["budget_groups"] = budget_groups
    return env


def _catalog() -> Catalog:
    overlay = os.environ.get("APTPLANS_CATALOG_OVERLAY", "").strip()
    overlay_dir = Path(overlay) if overlay else None
    return seed_catalog(REPO / "catalog", overlay_dir=overlay_dir)


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
    if airport.website:
        data["sameAs"] = airport.website
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
            "encodingFormat": "application/pdf",
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


def _rss(title: str, path: str, items: list[dict], page: str | None = None) -> str:
    rows = []
    for item in items[:50]:
        pub = _rfc822(item.get("date"))
        lines = [
            "    <item>",
            f"      <title>{xml_escape(item['title'])}</title>",
            f"      <link>{xml_escape(item['link'])}</link>",
            f"      <guid>{xml_escape(item['link'])}</guid>",
        ]
        if pub:
            lines.append(f"      <pubDate>{pub}</pubDate>")
        lines.append(f"      <description>{xml_escape(item.get('description') or '')}</description>")
        lines.append("    </item>")
        rows.append("\n".join(lines))
    body = "\n".join(rows)
    html_page = page or path
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<rss version="2.0">\n'
        "  <channel>\n"
        f"    <title>{xml_escape(title)}</title>\n"
        f"    <link>{CANONICAL}{html_page}</link>\n"
        "    <description>New documents on AptPlans. Unofficial. Follow the official source on each item.</description>\n"
        f"{body}\n"
        "  </channel>\n"
        "</rss>\n"
    )


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


def build(out_dir: Path, catalog: Catalog | None = None) -> bool:
    """Write HTML. False when catalog, templates, and static match the last build."""
    out_dir = out_dir.resolve()
    catalog = catalog or _catalog()
    year = date.today().year
    source_sha = _inputs_sha256(catalog, year)
    if _previous_source_sha(out_dir) == source_sha:
        return False

    out_dir.mkdir(parents=True, exist_ok=True)
    emitted: set[str] = set()

    def emit(path: Path, text: str) -> None:
        emitted.add(path.relative_to(out_dir).as_posix())
        _write(path, text)

    def emit_copy(src: Path, dst: Path) -> None:
        emitted.add(dst.relative_to(out_dir).as_posix())
        _copy_file(src, dst)

    env = _env()
    stats = counts(catalog)
    recently = sorted(
        catalog.documents,
        key=lambda doc: (doc.source_retrieved_at or "", 1 if doc.summary else 0, doc.title or ""),
        reverse=True,
    )[:12]
    context = {
        "canonical": CANONICAL,
        "year": year,
        "counts": stats,
        "intake_url": INTAKE_URL,
        "recently": recently,
        "rss_links": [RSS_ALL],
        "json_ld": [],
    }

    sitemap_pages: dict[str, str | None] = {}

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

    render(
        "index.html",
        out_dir / "index.html",
        "/",
        states=catalog.states,
        rss_links=[RSS_ALL, RSS_LAWS],
        json_ld=[_ld_website()],
    )
    render("about.html", out_dir / "about" / "index.html", "/about/", rss_links=[RSS_ALL, RSS_LAWS])
    render("search.html", out_dir / "search" / "index.html", "/search/")
    render(
        "airports.html",
        out_dir / "airports" / "index.html",
        "/airports/",
        airports=catalog.airports,
        completeness_for=lambda lid: completeness_for_airport(catalog, lid),
    )
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

    for airport in catalog.airports:
        docs = sorted(
            catalog.documents_for_airport(airport.lid),
            key=lambda doc: (_DOC_KIND_ORDER.get(doc.kind, 9), doc.title or doc.id),
        )
        grants = catalog.grants_for_airport(airport.lid)
        funding = funding_sections(grants)
        state_docs = [
            doc
            for doc in catalog.documents
            if doc.state == airport.state and doc.kind in {"statute", "sasp"}
        ]
        state = catalog.states_by_code.get(airport.state)
        rss_links = []
        if docs:
            rss_links.append(_rss_airport(airport))
        if state is not None:
            rss_links.append(_rss_state(state))
        rss_links.append(RSS_ALL)
        render(
            "airport.html",
            out_dir / "airports" / airport.lid / "index.html",
            f"/airports/{airport.lid}/",
            airport=airport,
            documents=docs,
            funding=funding,
            brief=airport_brief(airport=airport, documents=docs, funding=funding, state=state),
            completeness=completeness_for_airport(catalog, airport.lid),
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

    for state in catalog.states:
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
            projects=project_groups(catalog, grants),
            project_stats=grant_briefing(grants),
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

    for document in catalog.documents:
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
            changes=[
                change
                for change in catalog.changes
                if change.entity_id == document.id
            ],
            rss_links=rss_links,
            lastmod=_sitemap_day(document.source_retrieved_at, document.published_at),
            json_ld=[_ld_document(document, airport), _ld_crumbs(crumbs)],
        )

    for folder in ("css", "js"):
        src = STATIC / folder
        if not src.is_dir():
            continue
        for path in src.rglob("*"):
            if path.is_file():
                emit_copy(path, out_dir / folder / path.relative_to(src))

    emit(
        out_dir / "robots.txt",
        "User-agent: *\nAllow: /\nDisallow: /data/\nSitemap: https://aptplans.org/sitemap.xml\n",
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
    law_docs = [doc for doc in catalog.documents if doc.kind in {"statute", "sasp"}]
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
        state_docs = [doc for doc in catalog.documents if doc.state == state.code]
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
        docs = catalog.documents_for_airport(airport.lid)
        if not docs:
            continue
        airport_feeds_by_state.setdefault(airport.state, []).append(airport)
        emit(
            out_dir / "feeds" / "airports" / f"{airport.lid}.xml",
            _rss(
                f"AptPlans - {airport.name}",
                f"/feeds/airports/{airport.lid}.xml",
                [_item_for_document(doc) for doc in docs],
                page=f"/airports/{airport.lid}/",
            ),
        )
        note_page(
            f"/feeds/airports/{airport.lid}.xml",
            _sitemap_day(*[doc.source_retrieved_at for doc in docs], *[doc.published_at for doc in docs]),
        )
    render(
        "feeds.html",
        out_dir / "feeds" / "index.html",
        "/feeds/",
        states=catalog.states,
        airport_feeds_by_state=airport_feeds_by_state,
        airport_feed_count=sum(len(rows) for rows in airport_feeds_by_state.values()),
        rss_links=[RSS_ALL, RSS_LAWS],
    )
    emit(out_dir / "sitemap.xml", _sitemap_xml(sitemap_pages))

    status = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "unofficial": True,
        "counts": stats,
        "source_sha": source_sha,
    }
    emit(out_dir / "status.json", json.dumps(status, indent=2) + "\n")

    search_index = []
    for airport in catalog.airports:
        search_index.append(
            {
                "type": "airport",
                "title": f"{airport.lid} {airport.name}",
                "url": f"/airports/{airport.lid}/",
                "state": airport.state,
                "text": " ".join(
                    part
                    for part in (airport.lid, airport.icao, airport.iata, airport.city, airport.npias_role)
                    if part
                ),
            }
        )
    for document in catalog.documents:
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
    emit(
        out_dir / "data" / "catalog.json",
        json.dumps(
            {
                "airports": [item.to_dict() for item in catalog.airports],
                "states": [item.to_dict() for item in catalog.states],
                "documents": [item.to_dict() for item in catalog.documents],
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
    for document in catalog.documents:
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
    _prune_unemitted(out_dir, emitted)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the AptPlans static site")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT.parent / "dist",
        help="Output directory (default: dist/ at repo root)",
    )
    args = parser.parse_args()
    if not build(args.out):
        print("site unchanged")


if __name__ == "__main__":
    main()

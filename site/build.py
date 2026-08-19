"""Build the static AptPlans site into an output directory."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
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
    "sasp": "State aviation system plan",
    "other": "Planning document",
}
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
    "complete": "Official URL and a preserved copy are both on file.",
    "link_only": "An official URL is listed. No preserved copy yet.",
    "preserved_only": "A preserved copy is on file. The official URL is missing or dead.",
    "missing": "No master plan or Airport Layout Plan is listed yet.",
    "no_plan_known": "Neither a master plan nor an Airport Layout Plan is known.",
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


def grant_title(description: str) -> str:
    text = " ".join((description or "").split())
    if not text:
        return "FAA grant"
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
    return env


def _catalog() -> Catalog:
    overlay = os.environ.get("APTPLANS_CATALOG_OVERLAY", "").strip()
    overlay_dir = Path(overlay) if overlay else None
    return seed_catalog(REPO / "catalog", overlay_dir=overlay_dir)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _rfc822(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return parsed.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _rss(title: str, path: str, items: list[dict]) -> str:
    rows = []
    for item in items[:50]:
        rows.append(
            "\n".join(
                [
                    "    <item>",
                    f"      <title>{xml_escape(item['title'])}</title>",
                    f"      <link>{xml_escape(item['link'])}</link>",
                    f"      <guid>{xml_escape(item['link'])}</guid>",
                    f"      <pubDate>{_rfc822(item.get('date'))}</pubDate>",
                    f"      <description>{xml_escape(item.get('description') or '')}</description>",
                    "    </item>",
                ]
            )
        )
    body = "\n".join(rows)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<rss version="2.0">\n'
        "  <channel>\n"
        f"    <title>{xml_escape(title)}</title>\n"
        f"    <link>{CANONICAL}{path}</link>\n"
        "    <description>Unofficial AptPlans feed. Official sources remain the citation of record.</description>\n"
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


def build(out_dir: Path, catalog: Catalog | None = None) -> None:
    out_dir = out_dir.resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    catalog = catalog or _catalog()
    env = _env()
    year = date.today().year
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
    }

    def render(template_name: str, dest: Path, canonical_path: str, **extra) -> None:
        html = env.get_template(template_name).render(
            **context,
            canonical_path=canonical_path,
            **extra,
        )
        _write(dest, html)

    render("index.html", out_dir / "index.html", "/", states=catalog.states)
    render("about.html", out_dir / "about" / "index.html", "/about/")
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
    )

    for airport in catalog.airports:
        docs = sorted(
            catalog.documents_for_airport(airport.lid),
            key=lambda doc: (_DOC_KIND_ORDER.get(doc.kind, 9), doc.title or doc.id),
        )
        grants = catalog.grants_for_airport(airport.lid)
        state_docs = [
            doc
            for doc in catalog.documents
            if doc.state == airport.state and doc.kind in {"statute", "sasp"}
        ]
        render(
            "airport.html",
            out_dir / "airports" / airport.lid / "index.html",
            f"/airports/{airport.lid}/",
            airport=airport,
            documents=docs,
            grants=grants,
            grant_stats=grant_briefing(grants),
            completeness=completeness_for_airport(catalog, airport.lid),
            state=catalog.states_by_code.get(airport.state),
            state_documents=state_docs,
        )

    for state in catalog.states:
        airports = catalog.airports_for_state(state.code)
        render(
            "state.html",
            out_dir / "states" / state.code / "index.html",
            f"/states/{state.code}/",
            state=state,
            airports=airports,
            documents=[
                doc
                for doc in catalog.documents
                if doc.state == state.code and doc.kind in {"statute", "sasp"}
            ],
            completeness_for=lambda lid, _catalog=catalog: completeness_for_airport(_catalog, lid),
        )

    for document in catalog.documents:
        airport = catalog.airports_by_lid.get(document.airport_lid or "")
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
        )

    css_src = STATIC / "css"
    css_dst = out_dir / "css"
    if css_src.exists():
        shutil.copytree(css_src, css_dst)

    _write(
        out_dir / "robots.txt",
        "User-agent: *\nAllow: /\nSitemap: https://aptplans.org/sitemap.xml\n",
    )

    urls = [
        "/",
        "/about/",
        "/search/",
        "/airports/",
        "/states/",
        "/feeds/all.xml",
        "/feeds/laws.xml",
    ]
    urls += [f"/states/{state.code}/" for state in catalog.states]
    urls += [f"/airports/{airport.lid}/" for airport in catalog.airports]
    urls += [f"/documents/{document.id}/" for document in catalog.documents]
    sitemap = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    sitemap.extend(f"  <url><loc>{CANONICAL}{path}</loc></url>" for path in urls)
    sitemap.append("</urlset>\n")
    _write(out_dir / "sitemap.xml", "\n".join(sitemap))

    all_items = [_item_for_document(doc) for doc in recently] or [
        {
            "title": "AptPlans catalog",
            "link": f"{CANONICAL}/",
            "date": None,
            "description": "Unofficial library of airport master plans and Airport Layout Plans.",
        }
    ]
    _write(out_dir / "feeds" / "all.xml", _rss("AptPlans", "/feeds/all.xml", all_items))
    law_docs = [doc for doc in catalog.documents if doc.kind in {"statute", "sasp"}]
    _write(
        out_dir / "feeds" / "laws.xml",
        _rss("AptPlans - state aviation law", "/feeds/laws.xml", [_item_for_document(doc) for doc in law_docs]),
    )
    for state in catalog.states:
        items = [
            _item_for_document(doc)
            for doc in catalog.documents
            if doc.state == state.code
        ]
        _write(
            out_dir / "feeds" / "states" / f"{state.code}.xml",
            _rss(f"AptPlans - {state.name}", f"/feeds/states/{state.code}.xml", items),
        )
    for airport in catalog.airports:
        docs = catalog.documents_for_airport(airport.lid)
        if not docs:
            continue
        _write(
            out_dir / "feeds" / "airports" / f"{airport.lid}.xml",
            _rss(
                f"AptPlans - {airport.name}",
                f"/feeds/airports/{airport.lid}.xml",
                [_item_for_document(doc) for doc in docs],
            ),
        )

    status = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "unofficial": True,
        "counts": stats,
    }
    _write(out_dir / "status.json", json.dumps(status, indent=2) + "\n")

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
    _write(out_dir / "data" / "search.json", json.dumps(search_index) + "\n")
    _write(
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
    _write(out_dir / "data" / "catalog.csv", buf.getvalue())


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the AptPlans static site")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT.parent / "dist",
        help="Output directory (default: dist/ at repo root)",
    )
    args = parser.parse_args()
    build(args.out)


if __name__ == "__main__":
    main()

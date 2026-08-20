"""Search-engine query templates and gated verification. The model does not search."""

from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.parse import urlparse

GenerateFn = Callable[[str], str]

PLAN_KINDS = frozenset({"master_plan", "alp", "notice", "not_plan", "other"})
FINANCE_KINDS = frozenset(
    {
        "issued_grants",
        "program_budget",
        "project_list",
        "cip_proposed",
        "pfc",
        "bond",
        "other",
        "not_finance",
    }
)
FINANCE_SCOPES = frozenset({"airport", "state", "national"})


def search_configured() -> bool:
    """Origin-only. CI must leave APTPLANS_SEARCH_KEY unset."""
    return bool(os.environ.get("APTPLANS_SEARCH_KEY", "").strip())


def _host(website: str) -> str:
    host = urlparse(website.strip()).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def search_queries(*, name: str, lid: str, city: str = "", state: str = "") -> list[str]:
    """Deterministic master plan and ALP queries from NASR identity. No network."""
    lid = lid.strip()
    name = name.strip()
    quoted = f'"{name}"' if name else lid
    place = " ".join(part for part in (city.strip(), state.strip()) if part)
    queries = [
        f'{quoted} {lid} "master plan" filetype:pdf',
        f'{quoted} {lid} "airport layout plan" OR ALP filetype:pdf',
        f'{quoted} {lid} "master plan" site:.gov',
        f'{quoted} {lid} ALP OR "airport layout plan" site:.gov',
    ]
    if place:
        queries.append(f'{quoted} {place} "master plan" filetype:pdf')
    return queries


def host_queries(*, website: str, name: str, lid: str) -> list[str]:
    """Restrict plan queries to a known airport or agency host before a web-wide search."""
    host = _host(website)
    if not host:
        return []
    quoted = f'"{name.strip()}"' if name.strip() else lid
    return [
        f'site:{host} {quoted} {lid} "master plan" filetype:pdf',
        f'site:{host} {lid} ALP OR "airport layout plan" filetype:pdf',
    ]


def budget_queries(*, state_name: str, agency: str = "") -> list[str]:
    """Statewide aviation agency budget (LAB or enacted). Not a LocID project list."""
    name = state_name.strip()
    quoted_agency = f'"{agency.strip()}"' if agency.strip() else f'"{name} Department of Aviation"'
    return [
        f'{quoted_agency} "legislatively adopted budget" filetype:pdf',
        f'"{name}" aviation "legislatively adopted budget" OR "enacted budget" site:.gov filetype:pdf',
        f'{quoted_agency} appropriation OR "operating budget" site:.gov filetype:pdf',
    ]


def award_list_queries(*, state_name: str, agency: str = "") -> list[str]:
    """State-issued airport project or grant award lists keyed to a LocID when possible."""
    name = state_name.strip()
    quoted_agency = f'"{agency.strip()}"' if agency.strip() else f'"{name} Department of Aviation"'
    return [
        f'{quoted_agency} "airport grant" OR "aviation grant awards" site:.gov filetype:pdf',
        f'"{name}" "ASAP" OR "Connect Oregon" OR "aviation grant" award site:.gov',
        f'{quoted_agency} "project awards" OR "grant awards" airport site:.gov',
    ]


def cip_queries(*, name: str, lid: str, website: str = "") -> list[str]:
    """Sponsor capital improvement program. Proposed work, not an issued federal grant."""
    quoted = f'"{name.strip()}"' if name.strip() else lid
    queries = [
        f'{quoted} {lid} "capital improvement" OR CIP airport filetype:pdf',
        f'{quoted} {lid} "airport capital improvement" site:.gov',
    ]
    host = _host(website) if website else ""
    if host:
        queries.insert(0, f'site:{host} {lid} CIP OR "capital improvement" filetype:pdf')
    return queries


def pfc_queries(*, name: str, lid: str, website: str = "") -> list[str]:
    """Passenger facility charge and similar local funding records."""
    quoted = f'"{name.strip()}"' if name.strip() else lid
    queries = [
        f'{quoted} {lid} "passenger facility charge" OR PFC filetype:pdf',
        f'{quoted} {lid} PFC application OR "PFC collection" site:.gov',
    ]
    host = _host(website) if website else ""
    if host:
        queries.insert(0, f'site:{host} {lid} PFC OR "passenger facility charge"')
    return queries


def law_queries(*, state_name: str, agency: str = "") -> list[str]:
    """Statewide aviation system plan and airport statute guides."""
    name = state_name.strip()
    quoted_agency = f'"{agency.strip()}"' if agency.strip() else f'"{name} Department of Aviation"'
    return [
        f'"{name}" "state aviation system plan" OR SASP filetype:pdf',
        f'{quoted_agency} "airport" statute OR "landing field" site:.gov',
        f'"{name}" aviation code OR "airport zoning" site:.gov',
    ]


def airport_query_families(
    *,
    name: str,
    lid: str,
    city: str = "",
    state: str = "",
    website: str = "",
) -> dict[str, list[str]]:
    """Plan first, then local finance, on the airport host when known."""
    families = {
        "plan": search_queries(name=name, lid=lid, city=city, state=state),
        "cip": cip_queries(name=name, lid=lid, website=website),
        "pfc": pfc_queries(name=name, lid=lid, website=website),
    }
    if website:
        families["plan"] = host_queries(website=website, name=name, lid=lid) + families["plan"]
    return families


def state_query_families(*, state_name: str, agency: str = "", website: str = "") -> dict[str, list[str]]:
    """Statewide budget, award lists, and law. Host-restricted copies first when an agency URL exists."""
    families = {
        "budget": budget_queries(state_name=state_name, agency=agency),
        "awards": award_list_queries(state_name=state_name, agency=agency),
        "law": law_queries(state_name=state_name, agency=agency),
    }
    host = _host(website) if website else ""
    if not host:
        return families
    quoted_agency = f'"{agency.strip()}"' if agency.strip() else f'"{state_name.strip()}"'
    families["budget"] = [
        f'site:{host} {quoted_agency} budget OR appropriation filetype:pdf'
    ] + families["budget"]
    families["awards"] = [
        f'site:{host} {quoted_agency} grant OR award airport filetype:pdf'
    ] + families["awards"]
    families["law"] = [
        f'site:{host} "aviation system plan" OR SASP OR statute filetype:pdf'
    ] + families["law"]
    return families


def parse_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in model output")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("model JSON is not an object")
    return payload


def verify_prompt(*, lid: str, name: str, url: str, excerpt: str) -> str:
    return (
        "You verify an already-fetched page or PDF excerpt. Do not search the web. "
        "Do not browse. Return only one line of compact JSON. Keep reason under 12 words.\n"
        f"airport_lid: {lid}\n"
        f"airport_name: {name}\n"
        f"found_url: {url}\n\n"
        "Schema:\n"
        '{"official_plan": bool, "kind": "master_plan"|"alp"|"notice"|"not_plan"|"other", '
        '"same_airport": bool, "publisher": str|null, "published_at": str|null, '
        '"pdf_urls": [str], "new_edition": bool, "reason": str}\n\n'
        "official_plan is true only if this is an airport master plan or ALP for that LID. "
        "new_edition is true if this is a later study replacing an earlier plan, not a "
        "chapter of the same study. For news or a press page, set kind to notice and fill "
        "publisher and published_at only. Do not quote or summarize article body. "
        "A budget, CIP, grant list, or award table is not a plan: kind not_plan.\n\n"
        f"{excerpt}"
    )


def verify_finance_prompt(
    *,
    url: str,
    excerpt: str,
    lid: str = "",
    name: str = "",
    state: str = "",
) -> str:
    return (
        "You verify an already-fetched finance page or PDF excerpt. Do not search the web. "
        "Do not browse. Return only one line of compact JSON. Keep reason under 12 words. "
        "Do not include dollar amounts, totals, or row-level "
        "figures in the JSON. Classification only.\n"
        f"airport_lid: {lid or 'none'}\n"
        f"airport_name: {name or 'none'}\n"
        f"state: {state or 'none'}\n"
        f"found_url: {url}\n\n"
        "Schema:\n"
        '{"official_finance": bool, '
        '"finance_kind": "issued_grants"|"program_budget"|"project_list"|"cip_proposed"|"pfc"|"bond"|"other"|"not_finance", '
        '"scope": "airport"|"state"|"national", '
        '"same_entity": bool, "publisher": str|null, "published_at": str|null, '
        '"pdf_urls": [str], "has_locid_rows": bool, "reason": str}\n\n'
        "official_finance is true only if this is an official budget, issued-grant table, "
        "LocID project award list, sponsor CIP, PFC, or bond record. "
        "issued_grants is money already awarded. cip_proposed is a plan of future projects, "
        "not an award. program_budget is agency program totals, not per-airport projects. "
        "project_list is named projects with allocations in the source. "
        "A master plan or ALP is not_finance. News with no table is not_finance.\n\n"
        f"{excerpt}"
    )


def _http_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.startswith("http")]


def verify_candidate(
    *,
    lid: str,
    name: str,
    url: str,
    excerpt: str,
    generate_fn: GenerateFn,
) -> dict[str, Any]:
    """Classify fetched plan-shaped bytes. Call only after gates pass. Never overrides a failed gate."""
    raw = generate_fn(verify_prompt(lid=lid, name=name, url=url, excerpt=excerpt))
    data = parse_json_object(raw)
    kind = data.get("kind") if data.get("kind") in PLAN_KINDS else "other"
    return {
        "official_plan": bool(data.get("official_plan")),
        "kind": kind,
        "same_airport": bool(data.get("same_airport")),
        "publisher": data.get("publisher") if isinstance(data.get("publisher"), str) else None,
        "published_at": data.get("published_at") if isinstance(data.get("published_at"), str) else None,
        "pdf_urls": _http_urls(data.get("pdf_urls")),
        "new_edition": bool(data.get("new_edition")),
        "reason": data.get("reason") if isinstance(data.get("reason"), str) else "",
    }


def verify_finance(
    *,
    url: str,
    excerpt: str,
    generate_fn: GenerateFn,
    lid: str = "",
    name: str = "",
    state: str = "",
) -> dict[str, Any]:
    """Classify fetched finance-shaped bytes. No dollar amounts. Never overrides a failed gate."""
    raw = generate_fn(
        verify_finance_prompt(url=url, excerpt=excerpt, lid=lid, name=name, state=state)
    )
    data = parse_json_object(raw)
    kind = data.get("finance_kind") if data.get("finance_kind") in FINANCE_KINDS else "other"
    scope = data.get("scope") if data.get("scope") in FINANCE_SCOPES else "airport"
    return {
        "official_finance": bool(data.get("official_finance")),
        "finance_kind": kind,
        "scope": scope,
        "same_entity": bool(data.get("same_entity")),
        "publisher": data.get("publisher") if isinstance(data.get("publisher"), str) else None,
        "published_at": data.get("published_at") if isinstance(data.get("published_at"), str) else None,
        "pdf_urls": _http_urls(data.get("pdf_urls")),
        "has_locid_rows": bool(data.get("has_locid_rows")),
        "reason": data.get("reason") if isinstance(data.get("reason"), str) else "",
    }

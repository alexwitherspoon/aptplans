"""Search-engine query templates and gated verification. The model does not search."""

from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import urlparse

from pipeline.gates import SSI_RE, looks_like_pdf

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
SEARCH_HIT_TYPES = frozenset({"artifact", "hub_page", "both", "notice", "not_plan"})
SEARCH_KIND_GUESSES = frozenset({"master_plan", "alp", "chapter", "unknown"})
SEARCH_FETCH = frozenset({"yes", "no", "needs_human"})
_PACKET_URL_RE = re.compile(r"https?://[^\s\]\)\>\"']+", re.I)
_NOT_PLAN_RE = re.compile(
    r"environmental assessment|\bNEPA\b|\bFONSI\b|legislatively adopted budget|"
    r"\bnewsletter\b|wikipedia\.org|pavement (?:management|condition)",
    re.I,
)
_ENCYCLOPEDIA_HOSTS = ("wikipedia.org",)
_SITE_RE = re.compile(r"\bsite:([a-z0-9.-]+)", re.I)
_HINT_PLANISH_RE = re.compile(
    r"master plan|\bAMP\b|\bALP\b|airport layout|airport diagram|\bchapter\b",
    re.I,
)
_HINT_BAD_RE = re.compile(r"https?://|\.pdf\b|wikipedia\.org", re.I)
_BARE_HOST_RE = re.compile(r"\b(?:https?://)?(?:www\.)?([a-z0-9-]+\.(?:gov|org|com|net|us))\b", re.I)
MAX_HINT_QUERIES = 2


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
        f'{quoted} {lid} AMP OR "airport master plan" filetype:pdf',
        f'{quoted} {lid} "airport layout plan" OR ALP filetype:pdf',
        f'{quoted} {lid} "airport diagram" ALP filetype:pdf',
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
        f'site:{host} {quoted} {lid} AMP OR "airport master plan" filetype:pdf',
        f'site:{host} {lid} ALP OR "airport layout plan" filetype:pdf',
        f'site:{host} {lid} "airport diagram" ALP filetype:pdf',
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


def packet_urls(
    *,
    artifact_url: str = "",
    page_url: str = "",
    prose: str = "",
) -> list[str]:
    """http(s) URLs present in the search packet. The model may not add others."""
    found: list[str] = []
    seen: set[str] = set()
    for raw in (artifact_url, page_url, *(_PACKET_URL_RE.findall(prose or ""))):
        url = raw.rstrip(".,;)]}'\"")
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        found.append(url)
    return found


def _not_plan_packet(urls: set[str], prose: str) -> bool:
    blob = f"{prose} {' '.join(urls)}"
    if _NOT_PLAN_RE.search(blob):
        return True
    return any(any(host in _host(url) for host in _ENCYCLOPEDIA_HOSTS) for url in urls)


def _mentions_airport(lid: str, name: str, urls: set[str], prose: str) -> bool:
    blob = f"{prose} {' '.join(urls)}".lower()
    if lid.lower() in blob:
        return True
    return bool(name.strip()) and name.strip().lower() in blob


def allowed_hit_urls(
    *,
    artifact_url: str = "",
    page_url: str = "",
    prose: str = "",
) -> list[str]:
    """Packet URLs that may be queued. Prose-only off-host links are dropped."""
    listed = packet_urls(artifact_url=artifact_url, page_url=page_url, prose=prose)
    seeds = [url for url in (artifact_url, page_url) if url.startswith("http")]
    if not seeds:
        return listed
    hosts = {_host(url) for url in seeds}
    trusted: list[str] = []
    for url in listed:
        host = _host(url)
        if url in seeds or host in hosts or host.endswith(".gov") or host.endswith(".mil"):
            trusted.append(url)
    return trusted


def search_hit_prompt(
    *,
    lid: str,
    name: str,
    query: str,
    artifact_url: str = "",
    page_url: str = "",
    prose: str = "",
    city: str = "",
    state: str = "",
    provider: str = "",
) -> str:
    urls = allowed_hit_urls(artifact_url=artifact_url, page_url=page_url, prose=prose)
    listed = "\n".join(f"- {item}" for item in urls) or "- none"
    place = " ".join(part for part in (city.strip(), state.strip()) if part)
    return (
        "You score a third-party search hit. Do not search the web. Do not browse. "
        "Return only compact JSON. Keep reason under 12 words.\n"
        f"airport_lid: {lid}\n"
        f"airport_name: {name}\n"
        f"place: {place or 'none'}\n"
        f"search_query: {query}\n"
        f"provider: {provider or 'unknown'}\n"
        f"artifact_url: {artifact_url or 'none'}\n"
        f"page_url: {page_url or 'none'}\n\n"
        "URLs in this packet (copy from this list only; never invent a path):\n"
        f"{listed}\n\n"
        "Schema:\n"
        '{"same_airport": bool, "hit_type": "artifact"|"hub_page"|"both"|"notice"|"not_plan", '
        '"kind_guess": "master_plan"|"alp"|"chapter"|"unknown", '
        '"artifact_urls": [str], "page_urls": [str], '
        '"fetch": "yes"|"no"|"needs_human", "reason": str}\n\n'
        "Rubric:\n"
        "same_airport is true only if the hit is about that LID or that airport name. "
        "A different LID in the URL or title is false.\n"
        "hit_type artifact is a PDF or drawing file. hub_page is HTML that lists plans. "
        "both is a page that also names packet PDF URLs. "
        "notice is news or press. not_plan is EA, NEPA, FONSI, newsletter, budget, "
        "pavement-only, Wikipedia, or a chart that is not an ALP.\n"
        "kind_guess master_plan is a whole study or AMP. alp is an Airport Layout Plan "
        "drawing set. On an official airport page, a link labeled Airport Diagram that "
        "points at an ALP PDF is alp, not a VFR chart. "
        "chapter is a named chapter, appendix, inventory volume, or ALP narrative. "
        "An ALP narrative is chapter, not alp.\n"
        "artifact_urls are packet URLs that look like files to confirm (usually .pdf). "
        "page_urls are packet URLs that are HTML hubs. Do not invent URLs.\n"
        "fetch yes only if same_airport is true and at least one packet URL is worth "
        "fetching for plan or ALP confirmation. fetch no for not_plan, notice without a "
        "plan PDF in the packet, or a different airport. "
        "needs_human if the filename looks like SSI or there is no URL.\n\n"
        f"prose:\n{prose or '(none)'}\n"
    )


def evaluate_search_hit(
    *,
    lid: str,
    name: str,
    query: str,
    generate_fn: GenerateFn,
    artifact_url: str = "",
    page_url: str = "",
    prose: str = "",
    city: str = "",
    state: str = "",
    provider: str = "",
) -> dict[str, Any]:
    """Triage a search packet. Does not publish. Never invents URLs. Never overrides a failed gate."""
    allowed = set(
        allowed_hit_urls(
            artifact_url=artifact_url, page_url=page_url, prose=prose
        )
    )
    raw = generate_fn(
        search_hit_prompt(
            lid=lid,
            name=name,
            query=query,
            artifact_url=artifact_url,
            page_url=page_url,
            prose=prose,
            city=city,
            state=state,
            provider=provider,
        )
    )
    try:
        data = parse_json_object(raw)
    except ValueError:
        return {
            "same_airport": False,
            "hit_type": "not_plan",
            "kind_guess": "unknown",
            "artifact_urls": [],
            "page_urls": [],
            "fetch": "needs_human",
            "reason": "model JSON missing",
        }
    hit_type = data.get("hit_type") if data.get("hit_type") in SEARCH_HIT_TYPES else "not_plan"
    kind = data.get("kind_guess") if data.get("kind_guess") in SEARCH_KIND_GUESSES else "unknown"
    fetch = data.get("fetch") if data.get("fetch") in SEARCH_FETCH else "no"
    artifacts = [url for url in _http_urls(data.get("artifact_urls")) if url in allowed]
    pages = [url for url in _http_urls(data.get("page_urls")) if url in allowed]
    same = bool(data.get("same_airport"))
    if any(SSI_RE.search(url) or SSI_RE.search(prose or "") for url in allowed):
        fetch = "needs_human"
    elif not allowed:
        fetch = "needs_human"
    elif not same:
        fetch = "no"
    elif _not_plan_packet(allowed, prose):
        fetch = "no"
        hit_type = "not_plan"
        kind = "unknown"
        if not _mentions_airport(lid, name, allowed, prose):
            same = False
    elif hit_type == "notice" and not any(looks_like_pdf(url) for url in allowed):
        fetch = "no"
    elif (
        same
        and fetch == "no"
        and hit_type in {"artifact", "hub_page", "both"}
        and (page_url in allowed or any(looks_like_pdf(url) for url in allowed))
    ):
        fetch = "yes"
    if fetch == "yes":
        if artifact_url in allowed and looks_like_pdf(artifact_url) and artifact_url not in artifacts:
            artifacts = [artifact_url, *[url for url in artifacts if url != artifact_url]]
        if page_url in allowed and not looks_like_pdf(page_url) and page_url not in pages:
            pages = [page_url, *[url for url in pages if url != page_url]]
        for url in allowed:
            if looks_like_pdf(url) and url not in artifacts:
                artifacts.append(url)
        if not artifacts and not pages:
            artifacts = [url for url in allowed if looks_like_pdf(url)]
            pages = [url for url in allowed if url not in artifacts]
        if not artifacts and not pages:
            fetch = "no"
        elif (
            hit_type == "not_plan"
            and page_url in allowed
            and not looks_like_pdf(page_url)
            and not any(looks_like_pdf(url) for url in allowed)
        ):
            hit_type = "hub_page"
    if fetch != "yes":
        artifacts = []
        pages = []
    return {
        "same_airport": same,
        "hit_type": hit_type,
        "kind_guess": kind,
        "artifact_urls": artifacts,
        "page_urls": pages,
        "fetch": fetch,
        "reason": data.get("reason") if isinstance(data.get("reason"), str) else "",
    }


def _packet_hosts(*, website: str, hits: list[dict[str, str]]) -> set[str]:
    hosts: set[str] = set()
    if website:
        host = _host(website)
        if host:
            hosts.add(host)
    blobs: list[str] = []
    for hit in hits:
        url = hit.get("url") or ""
        if url.startswith("http"):
            host = _host(url)
            if host:
                hosts.add(host)
        blobs.append(f"{hit.get('title') or ''} {url} {hit.get('snippet') or ''}")
    for blob in blobs:
        for url in packet_urls(prose=blob):
            host = _host(url)
            if host:
                hosts.add(host)
        for match in _BARE_HOST_RE.finditer(blob):
            hosts.add(match.group(1).lower().removeprefix("www."))
    return {
        host
        for host in hosts
        if host and not any(skip in host for skip in _ENCYCLOPEDIA_HOSTS)
    }


def _host_allowed(host: str, allowed: set[str]) -> bool:
    host = host.lower().removeprefix("www.")
    if not host or host in {"gov", "com", "org", "net", "us"}:
        return False
    for item in allowed:
        if host == item or host.endswith("." + item) or item.endswith("." + host):
            return True
    return False


def _clean_hint_query(
    raw: str, *, lid: str, allowed_hosts: set[str], ran: set[str], packet_text: str = ""
) -> str:
    query = " ".join((raw or "").split())
    if not query or query in ran:
        return ""
    if lid.lower() not in query.lower():
        return ""
    if _HINT_BAD_RE.search(query) or SSI_RE.search(query):
        return ""
    if not _HINT_PLANISH_RE.search(query):
        return ""
    if len(query) > 160:
        return ""
    for host in _SITE_RE.findall(query):
        if not _host_allowed(host, allowed_hosts):
            return ""
    for year in re.findall(r"\b(?:19|20)\d{2}\b", query):
        if packet_text and year not in packet_text:
            return ""
    return query


def search_hint_prompt(
    *,
    lid: str,
    name: str,
    ran_queries: list[str],
    missing: list[str],
    hits: list[dict[str, str]],
    city: str = "",
    state: str = "",
) -> str:
    lines = []
    for hit in hits[:8]:
        title = (hit.get("title") or "")[:120]
        snippet = (hit.get("snippet") or "")[:240]
        url = hit.get("url") or ""
        lines.append(f"- {title}\n  url: {url}\n  snippet: {snippet}")
    packet = "\n".join(lines) or "- none"
    ran = "\n".join(f"- {item}" for item in ran_queries) or "- none"
    place = " ".join(part for part in (city.strip(), state.strip()) if part)
    lack = ", ".join(missing) if missing else "none"
    return (
        "You propose follow-up web search queries from packets we already have. "
        "Do not search the web. Do not browse. Do not invent URLs or file paths. "
        "Return only compact JSON. Keep reason under 12 words.\n"
        f"airport_lid: {lid}\n"
        f"airport_name: {name}\n"
        f"place: {place or 'none'}\n"
        f"still_missing: {lack}\n\n"
        "Queries already run:\n"
        f"{ran}\n\n"
        "Hits (titles, URLs, snippets). Copy hosts and distinctive phrases from here only:\n"
        f"{packet}\n\n"
        "Schema:\n"
        '{"stop": bool, "queries": [{"query": str, "why": str}], "reason": str}\n\n'
        "Rubric:\n"
        "still_missing is authoritative. If it is not none, stop must be false and you "
        "must return one or two queries. A chapter PDF or an airport HTML page is not a "
        "whole master plan. An easement, board packet, or presentation is not an ALP. "
        "Each query must include the LID. Use site: only with a host that appears in the "
        "hits. If a snippet names another hostname, the first query should be site: that "
        "host plus the LID and master plan. "
        "If snippets mention a newer year than chapter paths, search that year plus "
        "AMP or final. If still_missing includes alp, search airport layout plan on that host. "
        "Do not return http URLs. Do not return .pdf paths. Do not query a different airport.\n"
    )


def evaluate_search_hints(
    *,
    lid: str,
    name: str,
    generate_fn: GenerateFn,
    hits: list[dict[str, str]],
    ran_queries: list[str] | None = None,
    missing: list[str] | None = None,
    website: str = "",
    city: str = "",
    state: str = "",
) -> dict[str, Any]:
    """Gated next-query hints. The model does not search and cannot enqueue a fetch."""
    ran = list(ran_queries or [])
    lack = list(missing or [])
    allowed_hosts = _packet_hosts(website=website, hits=hits)
    packet_text = " ".join(
        f"{hit.get('title') or ''} {hit.get('url') or ''} {hit.get('snippet') or ''}" for hit in hits
    )
    raw = generate_fn(
        search_hint_prompt(
            lid=lid,
            name=name,
            ran_queries=ran,
            missing=lack,
            hits=hits,
            city=city,
            state=state,
        )
    )
    try:
        data = parse_json_object(raw)
    except ValueError:
        return {"stop": True, "queries": [], "reason": "model JSON missing"}
    ran_set = set(ran)
    kept: list[dict[str, str]] = []
    raw_items = data.get("queries") or []
    if isinstance(raw_items, str):
        raw_items = [raw_items]
    for item in raw_items:
        if isinstance(item, str):
            query_raw, why = item, ""
        elif isinstance(item, dict):
            query_raw, why = str(item.get("query") or ""), (
                item.get("why") if isinstance(item.get("why"), str) else ""
            )
        else:
            continue
        query = _clean_hint_query(
            query_raw,
            lid=lid,
            allowed_hosts=allowed_hosts,
            ran=ran_set,
            packet_text=packet_text,
        )
        if not query:
            continue
        kept.append({"query": query, "why": why[:80]})
        ran_set.add(query)
        if len(kept) >= MAX_HINT_QUERIES:
            break
    return {
        "stop": not kept,
        "queries": kept,
        "reason": data.get("reason") if isinstance(data.get("reason"), str) else "",
    }


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


SPEND_CATEGORIES = frozenset({"maintenance", "growth", "planning", "other"})
OUTLOOK_BANDS = frozenset({"growing", "declining", "maintaining"})
HUB_KIND_GUESSES = frozenset({"master_plan", "alp", "chapter", "unknown"})
BUDGET_LINE_KINDS = frozenset({"program", "fund", "project", "airport_allocation"})


def grant_spend_prompt(
    *,
    description: str,
    lid: str = "",
    fiscal_year: int | None = None,
) -> str:
    fy = f"FY {fiscal_year}" if fiscal_year else "unknown"
    return (
        "Classify one FAA airport grant project summary. Do not search the web. "
        "Do not browse. Return only one line of compact JSON. Keep reason under 12 words. "
        "Do not include dollar amounts.\n"
        f"airport_lid: {lid or 'none'}\n"
        f"fiscal_year: {fy}\n"
        f"project_summary: {description.strip() or 'none'}\n\n"
        "Schema:\n"
        '{"spend_category": "maintenance"|"growth"|"planning"|"other", "reason": str}\n\n'
        "Rubric (rubric_version: 2):\n"
        "planning — master plan, airport layout plan, ALP, environmental assessment for planning, "
        "or planning study only (no construction).\n"
        "maintenance — preserve or restore existing capacity: rehabilitate, reseal, "
        "resurface, repave, reconstruct, repair, replace, improve, or upgrade existing pavement, "
        "runway, taxiway, terminal shell, lighting, or similar without adding gates or new footprint.\n"
        "growth — add or expand capacity: new runway, terminal, gate, hangar, apron, concourse, "
        "lengthen, widen, expand, or additional facility footprint.\n"
        "other — land acquisition, equipment-only, zero-emissions gear, noise mitigation, or unclear scope.\n"
        "Reconstruct an existing taxiway or runway is maintenance. Construct a new runway "
        "is growth. Improve or upgrade without clear expansion is usually maintenance.\n"
    )


def classify_grant_spend(
    *,
    description: str,
    generate_fn: GenerateFn,
    lid: str = "",
    fiscal_year: int | None = None,
    rule_category: str = "other",
) -> dict[str, str]:
    from pipeline.classify import ClassificationResult, classify_with_rubric

    def fallback() -> ClassificationResult:
        return ClassificationResult(category=rule_category, classifier="rules")

    result = classify_with_rubric(
        prompt=grant_spend_prompt(description=description, lid=lid, fiscal_year=fiscal_year),
        labels=SPEND_CATEGORIES,
        category_key="spend_category",
        generate_fn=generate_fn,
        rule_fallback=fallback,
    )
    return {"spend_category": result.category, "reason": result.reason, "classifier": result.classifier}


def plan_outlook_prompt(*, excerpt: str, lid: str = "", name: str = "") -> str:
    return (
        "Classify airport planning trajectory from a verified plan excerpt. "
        "Do not search. Return only compact JSON. Keep reason under 12 words. "
        "No dollar amounts.\n"
        f"airport_lid: {lid or 'none'}\n"
        f"airport_name: {name or 'none'}\n\n"
        "Schema:\n"
        '{"band": "growing"|"declining"|"maintaining", "reason": str}\n\n'
        "Rubric:\n"
        "growing — forecasts or plans add capacity, new facilities, or significant expansion.\n"
        "declining — closure, downsizing, reduced activity, or contraction called out.\n"
        "maintaining — status quo, rehabilitation, or replace-in-kind without net growth.\n\n"
        f"{excerpt[:12_000]}"
    )


def classify_plan_outlook(
    *,
    excerpt: str,
    generate_fn: GenerateFn,
    lid: str = "",
    name: str = "",
    rule_band: str = "maintaining",
) -> dict[str, str]:
    from pipeline.classify import ClassificationResult, classify_with_rubric

    def fallback() -> ClassificationResult:
        return ClassificationResult(category=rule_band, classifier="rules")

    result = classify_with_rubric(
        prompt=plan_outlook_prompt(excerpt=excerpt, lid=lid, name=name),
        labels=OUTLOOK_BANDS,
        category_key="band",
        generate_fn=generate_fn,
        rule_fallback=fallback,
    )
    return {"band": result.category, "reason": result.reason, "classifier": result.classifier}


def hub_link_prompt(*, url: str, label: str, found_on: str = "") -> str:
    return (
        "Classify one hub page link toward airport planning documents. "
        "Do not search. Return only compact JSON. Keep reason under 12 words.\n"
        f"url: {url}\n"
        f"label: {label.strip() or 'none'}\n"
        f"found_on: {found_on or 'none'}\n\n"
        "Schema:\n"
        '{"kind_guess": "master_plan"|"alp"|"chapter"|"unknown", "reason": str}\n\n'
        "Rubric:\n"
        "master_plan — whole airport master plan or AMP file or hub.\n"
        "alp — airport layout plan, airport diagram, or ALP set.\n"
        "chapter — one chapter, appendix, or section of a larger study.\n"
        "unknown — not plan-shaped or unclear.\n"
    )


def classify_hub_link(
    *,
    url: str,
    label: str,
    generate_fn: GenerateFn,
    found_on: str = "",
    rule_kind: str = "unknown",
) -> dict[str, str]:
    from pipeline.classify import ClassificationResult, classify_with_rubric

    def fallback() -> ClassificationResult:
        return ClassificationResult(category=rule_kind, classifier="rules")

    result = classify_with_rubric(
        prompt=hub_link_prompt(url=url, label=label, found_on=found_on),
        labels=HUB_KIND_GUESSES,
        category_key="kind_guess",
        generate_fn=generate_fn,
        rule_fallback=fallback,
    )
    return {"kind_guess": result.category, "reason": result.reason, "classifier": result.classifier}


def budget_line_prompt(*, category: str, note: str = "", state: str = "") -> str:
    return (
        "Classify one state aviation budget table row. Do not search. "
        "Return only compact JSON. Keep reason under 12 words. No dollar amounts.\n"
        f"state: {state or 'none'}\n"
        f"row_label: {category.strip() or 'none'}\n"
        f"note: {note.strip() or 'none'}\n\n"
        "Schema:\n"
        '{"line_kind": "program"|"fund"|"project"|"airport_allocation", "reason": str}\n\n'
        "Rubric:\n"
        "program — agency program or activity line.\n"
        "fund — fund type or revenue source subtotal.\n"
        "project — named capital project without a single airport LocID.\n"
        "airport_allocation — row names a specific airport or LocID allocation.\n"
    )


def classify_budget_line(
    *,
    category: str,
    generate_fn: GenerateFn,
    note: str = "",
    state: str = "",
    rule_kind: str = "program",
) -> dict[str, str]:
    from pipeline.classify import ClassificationResult, classify_with_rubric

    def fallback() -> ClassificationResult:
        return ClassificationResult(category=rule_kind, classifier="rules")

    result = classify_with_rubric(
        prompt=budget_line_prompt(category=category, note=note, state=state),
        labels=BUDGET_LINE_KINDS,
        category_key="line_kind",
        generate_fn=generate_fn,
        rule_fallback=fallback,
    )
    return {"line_kind": result.category, "reason": result.reason, "classifier": result.classifier}

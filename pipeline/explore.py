"""Hub HTML explore. Capture pages without a PDF target. Do not publish hits."""

from __future__ import annotations

from dataclasses import dataclass, field
import html as html_lib
import re
from urllib.parse import urljoin, urlparse, unquote

from pipeline.gates import NEWS_RE, sniff_media
from pipeline.queries import allowed_hit_urls, evaluate_search_hit
from pipeline.queue import QueueJob
from pipeline.stages import worth_confirm

_A_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
_HREF_RE = re.compile(r"""\bhref=["']([^"']+)["']""", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SP_WEB = re.compile(r'"sharePointWebUrl"\s*:\s*"([^"]+)"', re.I)
_SP_LIST = re.compile(r'"sharePointListUrl"\s*:\s*"([^"]+)"', re.I)
_SP_VIEW = re.compile(r'"sharePointViewName"\s*:\s*"([^"]+)"', re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_NOT_PLAN_RE = re.compile(
    r"environmental assessment|\bNEPA\b|\bFONSI\b|legislatively adopted budget|"
    r"pavement (?:management|condition)",
    re.I,
)
_ALP_RE = re.compile(r"\balp\b|airport layout plan|airport diagram", re.I)
_PART_RE = re.compile(
    r"chapter|appendix|appendices|inventory|forecast|alternative|facility requirement|"
    r"existing conditions|introduction|cip\b|implementation",
    re.I,
)
_PLAN_RE = re.compile(r"master plan|\bamp\b|airport master plan", re.I)
_SKIP_HREF = re.compile(r"^(mailto:|tel:|javascript:|#)", re.I)
_KIND_FOR_JOB = {
    "master_plan": "master_plan",
    "alp": "alp",
    "chapter": "master_plan",
    "unknown": "other",
}


@dataclass(frozen=True)
class HubLink:
    url: str
    label: str
    found_on: str
    media: str
    role: str
    kind_guess: str
    view_name: str | None = None


@dataclass
class ExploreResult:
    page_url: str
    title: str
    excerpt: str
    links: list[HubLink] = field(default_factory=list)
    followups: list[HubLink] = field(default_factory=list)

    def artifacts(self) -> list[HubLink]:
        return [item for item in self.links if item.role in {"artifact", "part"}]

    def packets(self) -> list[dict[str, str]]:
        """Search-hit packets. A hub with no PDF is still a packet."""
        packets = [
            {
                "artifact_url": item.url if item.media == "pdf" else "",
                "page_url": self.page_url if item.media != "pdf" else item.found_on,
                "prose": item.label,
            }
            for item in self.artifacts()
        ]
        packets.insert(
            0,
            {
                "artifact_url": "",
                "page_url": self.page_url,
                "prose": " ".join(
                    part for part in (self.title, self.excerpt) if part
                )[:800],
            },
        )
        return packets


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _clean_text(blob: str) -> str:
    text = html_lib.unescape(_TAG_RE.sub(" ", blob or ""))
    return re.sub(r"\s+", " ", text).strip()


def page_title(html: str) -> str:
    match = _TITLE_RE.search(html or "")
    return _clean_text(match.group(1)) if match else ""


def page_excerpt(html: str, limit: int = 400) -> str:
    text = _clean_text(html)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return cut[:space] if space > 80 else cut


def media_for_url(url: str) -> str:
    path = unquote(urlparse(url).path).lower()
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith((".html", ".htm", ".aspx", ".shtml")) or path.endswith("/"):
        return "html"
    return "html" if not path.rsplit("/", 1)[-1].count(".") else "other"


def classify_link(url: str, label: str, *, found_on: str = "") -> HubLink:
    blob = f"{label} {url}"
    media = media_for_url(url)
    if any(host in _host(url) for host in ("wikipedia.org",)):
        role, kind = "not_plan", "unknown"
    elif _NOT_PLAN_RE.search(blob):
        role, kind = "not_plan", "unknown"
    elif NEWS_RE.search(urlparse(url).path) or NEWS_RE.search(label):
        role, kind = "notice", "unknown"
    elif media == "pdf" and _ALP_RE.search(blob):
        role, kind = "artifact", "alp"
    elif media == "pdf" and _PART_RE.search(blob):
        role, kind = "part", "chapter"
    elif media == "pdf" and _PLAN_RE.search(blob):
        role, kind = "artifact", "master_plan"
    elif media == "pdf":
        role, kind = "artifact", "unknown"
    elif _PLAN_RE.search(blob) or _ALP_RE.search(blob):
        role, kind = "hub_page", "master_plan" if _PLAN_RE.search(blob) else "alp"
    else:
        role, kind = "hub_page", "unknown"
    return HubLink(
        url=url,
        label=label,
        found_on=found_on,
        media=media,
        role=role,
        kind_guess=kind,
    )


def html_links(html: str, page_url: str) -> list[HubLink]:
    found: list[HubLink] = []
    seen: set[str] = set()
    for attrs, inner in _A_RE.findall(html or ""):
        href_match = _HREF_RE.search(attrs)
        if href_match is None:
            continue
        href = html_lib.unescape(href_match.group(1)).strip()
        if not href or _SKIP_HREF.search(href):
            continue
        url = urljoin(page_url, href.split("#", 1)[0])
        if not url.startswith("http") or url in seen:
            continue
        if url.rstrip("/") == page_url.rstrip("/"):
            continue
        seen.add(url)
        found.append(classify_link(url, _clean_text(inner), found_on=page_url))
    return found


def sharepoint_followups(html: str, page_url: str) -> list[HubLink]:
    """List/view pointers embedded in the hub. Not invented PDF paths."""
    text = html_lib.unescape(html or "")
    followups: list[HubLink] = []
    seen: set[str] = set()
    origin = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
    for blob in re.findall(r"webPartProperties:\s*\{([^}]*)\}", text):
        web = _SP_WEB.search(blob)
        lst = _SP_LIST.search(blob)
        if lst is None:
            continue
        view = _SP_VIEW.search(blob)
        list_path = re.sub(r"\s+", " ", lst.group(1)).strip()
        if list_path.startswith("http"):
            list_url = list_path
        elif list_path.startswith("/"):
            list_url = origin + list_path
        elif web is not None:
            list_url = urljoin(origin + web.group(1).rstrip("/") + "/", list_path)
        else:
            list_url = urljoin(page_url, list_path)
        if list_url in seen:
            continue
        seen.add(list_url)
        view_name = view.group(1) if view else None
        followups.append(
            HubLink(
                url=list_url,
                label=view_name or list_path.rsplit("/", 1)[-1],
                found_on=page_url,
                media="html",
                role="followup",
                kind_guess="unknown",
                view_name=view_name,
            )
        )
    return followups


def explore_page(html: str, page_url: str) -> ExploreResult:
    """Parse one fetched hub. Does not GET further URLs and does not publish."""
    return ExploreResult(
        page_url=page_url,
        title=page_title(html),
        excerpt=page_excerpt(html),
        links=html_links(html, page_url),
        followups=sharepoint_followups(html, page_url),
    )


def hub_document_kind(result: ExploreResult) -> str:
    """Facility pages stay other. A plan microsite can be master_plan."""
    blob = f"{result.title} {result.excerpt}"
    title = result.title.lower()
    if "airport" in title and "[" in title:
        return "other"
    if _PLAN_RE.search(blob) and not _NOT_PLAN_RE.search(blob):
        return "master_plan"
    return "other"


def confirm_jobs(
    result: ExploreResult,
    *,
    airport_lid: str | None,
    state: str | None = None,
    generate_fn=None,
    name: str = "",
) -> list[QueueJob]:
    """Enqueue confirm fetches. Model triage is optional; labels still gate."""
    jobs: list[QueueJob] = []
    seen: set[str] = set()
    for item in result.artifacts():
        trusted = allowed_hit_urls(
            artifact_url=item.url if item.media == "pdf" else "",
            page_url=result.page_url,
            prose=item.label,
        )
        if item.url not in trusted:
            continue
        if not worth_confirm(role=item.role, kind_guess=item.kind_guess, label=item.label):
            continue
        fetch = "yes"
        if generate_fn is not None:
            scored = evaluate_search_hit(
                lid=airport_lid or "",
                name=name,
                query=item.label,
                generate_fn=generate_fn,
                artifact_url=item.url if item.media == "pdf" else "",
                page_url=result.page_url,
                prose=item.label,
                state=state or "",
            )
            fetch = scored["fetch"]
        if fetch != "yes" or item.url in seen:
            continue
        seen.add(item.url)
        jobs.append(
            QueueJob(
                kind="fetch",
                document_id=None,
                source_url=item.url,
                airport_lid=airport_lid,
                state=state,
                suggested_kind=_KIND_FOR_JOB.get(item.kind_guess, "other"),
                found_on=result.page_url,
            )
        )
    return jobs


def followup_explore_jobs(
    result: ExploreResult,
    *,
    airport_lid: str | None,
    state: str | None = None,
) -> list[QueueJob]:
    """One GET of a SharePoint list/view found on the hub. Not a publish."""
    jobs: list[QueueJob] = []
    seen: set[str] = set()
    hub = result.page_url.rstrip("/")
    for item in result.followups:
        url = (item.url or "").strip()
        if not url.startswith("http") or url in seen:
            continue
        if url.rstrip("/") == hub:
            continue
        seen.add(url)
        jobs.append(
            QueueJob(
                kind="explore",
                document_id=None,
                source_url=url,
                airport_lid=airport_lid,
                state=state,
                suggested_kind="other",
                found_on=result.page_url,
            )
        )
    return jobs


def explore_payload(data: bytes, page_url: str) -> ExploreResult | None:
    if sniff_media(data) != "html":
        return None
    return explore_page(data.decode("utf-8", "replace"), page_url)

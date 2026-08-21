"""Adaptive search ladder. Each round is a signal; none of this is a publish.

A single web-wide PDF query is a poor first step: it ranks old chapters and
board decks above the current plan, and it never returns a hub page. Start
open (name + LID + master plan), lock onto hosts the hits actually used, then
fill missing kinds. Explore those hubs before searching again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse
import re

from pipeline.explore import classify_link
from pipeline.queries import evaluate_search_hints, packet_urls
from pipeline.stages import worth_confirm

SearchFn = Callable[[str], list["SearchHit"]]
EscalateFn = Callable[["SearchIdentity", "SearchSession"], list["SearchHit"]]

SKIP_HOSTS = frozenset(
    {
        "wikipedia.org",
        "facebook.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "instagram.com",
        "linkedin.com",
    }
)
_YEAR_RE = re.compile(r"\b(19[89]\d|20[0-2]\d)\b")
_BARE_HOST_RE = re.compile(r"\b(?:https?://)?(?:www\.)?([a-z0-9-]+\.(?:gov|org|com|net|us))\b", re.I)


@dataclass(frozen=True)
class SearchIdentity:
    lid: str
    name: str
    city: str = ""
    state: str = ""
    website: str = ""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    query: str = ""


@dataclass(frozen=True)
class SearchStep:
    kind: str
    query: str
    why: str


@dataclass
class SearchSignals:
    hosts: list[str] = field(default_factory=list)
    years: list[str] = field(default_factory=list)
    hub_urls: list[str] = field(default_factory=list)
    artifact_urls: list[str] = field(default_factory=list)
    kinds: set[str] = field(default_factory=set)
    n_hits: int = 0
    n_skipped: int = 0

    @property
    def has_alp(self) -> bool:
        return "alp" in self.kinds

    @property
    def has_whole_plan(self) -> bool:
        return "master_plan" in self.kinds

    @property
    def has_chapter(self) -> bool:
        return "chapter" in self.kinds

    @property
    def has_hub(self) -> bool:
        return bool(self.hub_urls)

    @property
    def best_host(self) -> str:
        return self.hosts[0] if self.hosts else ""


@dataclass
class SearchRound:
    step: SearchStep
    hits: list[SearchHit]


@dataclass
class SearchSession:
    identity: SearchIdentity
    rounds: list[SearchRound]
    signals: SearchSignals

    @property
    def queries(self) -> list[str]:
        return [round.step.query for round in self.rounds]

    @property
    def hits(self) -> list[SearchHit]:
        found: list[SearchHit] = []
        seen: set[str] = set()
        for round in self.rounds:
            for hit in round.hits:
                if hit.url in seen:
                    continue
                seen.add(hit.url)
                found.append(hit)
        return found


def host_of(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _quoted_name(identity: SearchIdentity) -> str:
    name = identity.name.strip()
    return f'"{name}"' if name else identity.lid


def seed_steps(identity: SearchIdentity) -> list[SearchStep]:
    """Two cheap openers. Do not start with filetype:pdf; that hides hub pages."""
    quoted = _quoted_name(identity)
    lid = identity.lid
    steps = [
        SearchStep(
            kind="open_web",
            query=f'{quoted} {lid} "master plan"',
            why="Hub or labeled plan; HTML allowed",
        )
    ]
    host = host_of(identity.website) if identity.website else ""
    if host and host not in SKIP_HOSTS:
        steps.append(
            SearchStep(
                kind="seed_host",
                query=f'site:{host} {lid} "master plan"',
                why="Restrict to the known airport or agency host",
            )
        )
    return steps


def classify_hit(hit: SearchHit) -> tuple[str, str]:
    labeled = classify_link(hit.url, hit.title or hit.snippet, found_on="")
    return labeled.role, labeled.kind_guess


def _hit_blob(hit: SearchHit) -> str:
    return f"{hit.title} {hit.url} {hit.snippet}"


def hit_worth_confirm(hit: SearchHit) -> bool:
    role, kind = classify_hit(hit)
    return worth_confirm(role=role, kind_guess=kind, label=_hit_blob(hit))


def hit_worth_explore(hit: SearchHit, identity: SearchIdentity) -> bool:
    """HTML hubs and document indexes. PDFs are confirm candidates, not explore."""
    if hit.url.lower().endswith(".pdf"):
        return False
    role, kind = classify_hit(hit)
    path = urlparse(hit.url).path.lower()
    if role == "hub_page" and kind in {"master_plan", "alp"}:
        return True
    if "master-plan" in path or "masterplan" in path or path.rstrip("/").endswith("/documents"):
        return True
    lid = identity.lid.lower()
    if role == "hub_page" and lid and lid in _hit_blob(hit).lower():
        return True
    return False


def _year_candidates(*texts: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for year in _YEAR_RE.findall(text or ""):
            if year not in seen:
                seen.add(year)
                found.append(year)
    return found


def _hosts_from_hit(hit: SearchHit, *, lid: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    blob = f"{hit.title} {hit.snippet} {hit.url}"
    candidates = [host_of(hit.url)]
    for url in packet_urls(prose=hit.snippet):
        candidates.append(host_of(url))
    if lid.lower() in blob.lower() or "master plan" in blob.lower():
        candidates.extend(match.group(1).lower() for match in _BARE_HOST_RE.finditer(blob))
    for host in candidates:
        if not host or host in seen or host in SKIP_HOSTS:
            continue
        seen.add(host)
        found.append(host)
    return found


def extract_signals(hits: list[SearchHit], identity: SearchIdentity) -> SearchSignals:
    host_score: dict[str, int] = {}
    years: list[str] = []
    year_seen: set[str] = set()
    hub_urls: list[str] = []
    artifact_urls: list[str] = []
    kinds: set[str] = set()
    skipped = 0
    website_host = host_of(identity.website) if identity.website else ""
    if website_host and website_host not in SKIP_HOSTS:
        host_score[website_host] = host_score.get(website_host, 0) + 1
    for hit in hits:
        role, kind = classify_hit(hit)
        weight = 1
        if hit_worth_explore(hit, identity):
            hub_urls.append(hit.url)
            kinds.add("hub")
            weight = 4
        if hit_worth_confirm(hit):
            kinds.add(kind)
            if role in {"artifact", "part"}:
                artifact_urls.append(hit.url)
            weight = max(weight, 3)
        elif role in {"not_plan", "notice"}:
            skipped += 1
            weight = 0
        for host in _hosts_from_hit(hit, lid=identity.lid):
            host_score[host] = host_score.get(host, 0) + max(weight, 1)
        if hit_worth_confirm(hit) or hit_worth_explore(hit, identity):
            for year in _year_candidates(hit.title, hit.url, hit.snippet):
                if year not in year_seen:
                    year_seen.add(year)
                    years.append(year)
    hosts = sorted(host_score, key=lambda host: (-host_score[host], host))
    return SearchSignals(
        hosts=hosts,
        years=years,
        hub_urls=list(dict.fromkeys(hub_urls)),
        artifact_urls=list(dict.fromkeys(artifact_urls)),
        kinds=kinds,
        n_hits=len(hits),
        n_skipped=skipped,
    )


def _already(queries: set[str], query: str) -> bool:
    return query in queries


def followup_steps(
    identity: SearchIdentity,
    signals: SearchSignals,
    ran: set[str],
    *,
    limit: int = 2,
) -> list[SearchStep]:
    """At most a couple of narrower queries from what the last hits taught us."""
    steps: list[SearchStep] = []
    lid = identity.lid
    known = host_of(identity.website) if identity.website else ""

    def add(kind: str, query: str, why: str) -> None:
        if _already(ran, query) or any(item.query == query for item in steps):
            return
        steps.append(SearchStep(kind=kind, query=query, why=why))

    for host in signals.hosts:
        if host == known or f"site:{host}" in " ".join(ran):
            continue
        add(
            "lock_host",
            f'site:{host} {lid} "master plan"',
            f"Hits clustered on {host}",
        )
        if len(steps) >= limit:
            return steps[:limit]

    host = signals.best_host
    if host and not signals.has_alp:
        add(
            "fill_alp",
            f'site:{host} {lid} "airport layout plan"',
            "Have a plan-shaped host but no ALP yet",
        )
    if host and signals.has_chapter and not signals.has_whole_plan:
        year = next((item for item in signals.years if item != "2008"), "")
        if year:
            add(
                "fill_whole",
                f'site:{host} {lid} {year} AMP OR "final" "master plan"',
                f"Chapters on {host}; look for a {year} whole file",
            )
        else:
            add(
                "fill_whole",
                f'site:{host} {lid} "final" AMP OR "complete" "master plan"',
                f"Chapters on {host}; look for a bound AMP",
            )
    if host and not signals.has_alp:
        add(
            "fill_diagram",
            f'site:{host} {lid} "airport diagram"',
            "ALP still missing; try the diagram label",
        )
    return steps[:limit]


def needs_hint(signals: SearchSignals) -> bool:
    """True when a hub or chapters exist but a whole plan or ALP is still missing."""
    if signals.n_hits == 0:
        return False
    return not (signals.has_whole_plan and signals.has_alp)


def hint_steps(
    identity: SearchIdentity,
    hits: list[SearchHit],
    ran: set[str],
    signals: SearchSignals,
    generate_fn: Callable[[str], str],
) -> list[SearchStep]:
    """One gated model pass. Returns search queries, never fetch URLs."""
    missing: list[str] = []
    if not signals.has_whole_plan:
        missing.append("whole_plan")
    if not signals.has_alp:
        missing.append("alp")
    scored = evaluate_search_hints(
        lid=identity.lid,
        name=identity.name,
        generate_fn=generate_fn,
        hits=[
            {"title": hit.title, "url": hit.url, "snippet": hit.snippet} for hit in hits[:8]
        ],
        ran_queries=list(ran),
        missing=missing,
        website=identity.website,
        city=identity.city,
        state=identity.state,
    )
    return [
        SearchStep(kind="llm_hint", query=item["query"], why=item["why"] or "gated hint")
        for item in scored["queries"]
    ]


def done_searching(signals: SearchSignals) -> bool:
    """Search stops when a hub is in hand, or both plan kinds. Explore is next."""
    if signals.has_hub:
        return True
    if signals.has_whole_plan and signals.has_alp:
        return True
    return False


def run_search_plan(
    identity: SearchIdentity,
    search_fn: SearchFn,
    *,
    max_steps: int = 5,
    generate_fn: Callable[[str], str] | None = None,
    escalate_fn: EscalateFn | None = None,
) -> SearchSession:
    """Run seed queries, then follow-ups. Does not GET pages and does not publish.

    escalate_fn is a last-resort packet source (Gemini search). It must return
    SearchHit URLs only. It does not classify or decide fetches.
    """
    pending = list(seed_steps(identity))
    rounds: list[SearchRound] = []
    ran: set[str] = set()
    hits: list[SearchHit] = []
    signals = extract_signals([], identity)
    hinted = False
    while pending and len(rounds) < max_steps:
        step = pending.pop(0)
        if step.query in ran:
            continue
        ran.add(step.query)
        found = [
            SearchHit(title=item.title, url=item.url, snippet=item.snippet, query=step.query)
            for item in search_fn(step.query)
        ]
        rounds.append(SearchRound(step=step, hits=found))
        hits.extend(found)
        signals = extract_signals(hits, identity)
        if done_searching(signals):
            if generate_fn and needs_hint(signals) and not hinted:
                pending = hint_steps(identity, hits, ran, signals, generate_fn)
                hinted = True
                if pending:
                    continue
            break
        pending.extend(followup_steps(identity, signals, ran))
        if generate_fn and needs_hint(signals) and not hinted and not pending:
            pending.extend(hint_steps(identity, hits, ran, signals, generate_fn))
            hinted = True
    session = SearchSession(identity=identity, rounds=rounds, signals=signals)
    if escalate_fn and not done_searching(session.signals):
        extra = [
            SearchHit(title=item.title, url=item.url, snippet=item.snippet, query=item.query)
            for item in escalate_fn(identity, session)
        ]
        if extra:
            query = extra[0].query or (session.queries[0] if session.queries else "")
            rounds.append(
                SearchRound(
                    step=SearchStep(
                        kind="escalate",
                        query=query,
                        why="Last-resort search packets; not a classification",
                    ),
                    hits=extra,
                )
            )
            hits.extend(extra)
            session = SearchSession(
                identity=identity,
                rounds=rounds,
                signals=extract_signals(hits, identity),
            )
    return session

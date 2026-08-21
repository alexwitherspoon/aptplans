"""Named evidence checks on a URL, label, and full source bytes. Not a publish.

Each check is a tight boolean or small count. Weights are a linear sum. A veto is a
large negative weight, not a separate control path. Filename is a prior; extracted
PDF text, HTML, and outline can outvote it when they are strong. SSI stays a weight
large enough that nothing else in the default set publishes. Gold labels point at
official URLs; bodies come from hashed fixtures or local data/score copies, not
stored excerpts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
import hashlib
import json
import re

from catalog import REFERENCE_FILES, ROOT, load_embedded_fixtures, load_shape_card
from pipeline.gates import NEWS_RE, SSI_RE, filename_from_url
from pipeline.parse import extract_text, outline_titles
from pipeline.queries import _NOT_PLAN_RE

REPO = ROOT.parent
SCORE_CACHE = REPO / "data" / "score"
EXTRACT_CACHE = SCORE_CACHE / "extract"

SCORE_GOLD_PATH = ROOT / "references" / "score_gold.json"
SCORE_SAMPLE_PATH = ROOT / "references" / "score_sample.json"
KINDS = ("master_plan", "alp", "chapter", "hub", "notice", "not_plan")

_LID_PATH_RE = re.compile(r"/([A-Z0-9]{3,4})(?:/|$)", re.I)
_PATH_STOP = frozenset(
    {
        "pdfs",
        "docs",
        "files",
        "file",
        "page",
        "pages",
        "html",
        "news",
        "about",
        "sites",
        "data",
        "pdf",
        "cms",
        "img",
        "css",
        "js",
        "src",
        "api",
        "maps",
        "list",
        "lists",
        "wp",
        "uploads",
        "content",
        "static",
        "media",
        "images",
        "image",
        "assets",
        "themes",
        "default",
        "user",
        "nodes",
        "view",
        "documents",
        "document",
        "library",
        "publications",
        "aero",
        "airports",
        "airport",
        "aviation",
        "business",
        "passengers",
        "planning",
        "projects",
        "app",
        "wiki",
        "org",
        "gov",
        "com",
        "net",
    }
)
_AMP_RE = re.compile(
    r"(?:^|[^a-z0-9])amp(?:[^a-z0-9]|$)|airport master plan|final amp|master[_\s-]?plan",
    re.I,
)
_ALP_RE = re.compile(
    r"(?:^|[^a-z0-9])alp(?:[^a-z0-9]|$)|airport layout plan|airport diagram",
    re.I,
)
_CHAPTER_RE = re.compile(
    r"\bchapters?\b|(?:^|[^a-z0-9])chp\s*\d|\bappendix\b|introduction|inventory|forecast|"
    r"existing conditions|executive summary|facility requirement|conditions.?report",
    re.I,
)
_EA_RE = re.compile(r"environmental assessment|\bNEPA\b|\bFONSI\b|\bEA\b", re.I)
_ECON_RE = re.compile(r"economic impact|\bIMPLAN\b|payroll and output|\bAEIS\b", re.I)
_BUDGET_RE = re.compile(r"legislatively adopted budget|\bPFC\b|passenger facility", re.I)
_PAVE_RE = re.compile(r"pavement (?:management|condition)|\bPCI\b", re.I)
_STATEWIDE_RE = re.compile(
    r"\bSASP\b|aviation system plan|state aviation system|"
    r"statewide airport|statewide aviation|statewide economic impact",
    re.I,
)
_APPENDIX_RE = re.compile(r"\bappendix\b", re.I)
_PDF_HREF_RE = re.compile(r"""href=["']([^"']+\.pdf[^"']*)["']""", re.I)
_NAME_STOP = frozenset(
    {
        "airport",
        "international",
        "regional",
        "municipal",
        "field",
        "airfield",
        "the",
        "and",
        "state",
        "county",
        "city",
        "port",
    }
)
_ABSTRACT_NAME_RE = re.compile(
    r"^[0-9a-f]{8,}|oda_doc_|appendix [a-z]\.\d|_a11y\b",
    re.I,
)
_GUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
_ENCYCLOPEDIA_HOSTS = ("wikipedia.org",)
_SHAPE_PATTERNS = {
    "inventory": re.compile(r"\binventory\b|existing conditions", re.I),
    "forecasts": re.compile(r"\bforecast", re.I),
    "facility_requirements": re.compile(r"facility requirement|demand.?capacity", re.I),
    "alternatives": re.compile(r"\balternatives?\b", re.I),
    "alp": re.compile(r"airport layout plan|\balp\b", re.I),
    "implementation": re.compile(r"implementation|capital improvement|\bcip\b", re.I),
}


@dataclass(frozen=True)
class Packet:
    """One labeled URL plus full extracted source when bytes are on disk."""

    lid: str
    name: str = ""
    url: str = ""
    label: str = ""
    body: str = ""
    outline: tuple[str, ...] = ()
    source_bytes: int = 0

    @property
    def blob(self) -> str:
        parts = [self.label, self.url, self.body, " ".join(self.outline)]
        return " ".join(part for part in parts if part)


def _fold(text: str) -> str:
    return re.sub(r"[_\-]+", " ", (text or "").lower())


def _host(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _path(url: str) -> str:
    return unquote(urlparse(url or "").path)


def _filename(url: str) -> str:
    return filename_from_url(url or "")


def _name_tokens(name: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in re.findall(r"[a-z0-9]+", _fold(name))
        if len(token) > 3 and token not in _NAME_STOP
    )


def _token_hits(tokens: tuple[str, ...], text: str) -> int:
    hay = (text or "").lower()
    return sum(1 for token in tokens if token in hay)


def _text(packet: Packet) -> str:
    return f"{packet.label} {packet.body} {' '.join(packet.outline)}"


def shape_hits(text: str) -> int:
    """How many AC 150/5070-6B core elements appear in body or outline."""
    card = load_shape_card()
    blob = text or ""
    n = 0
    for element in card.get("core_elements") or []:
        pattern = _SHAPE_PATTERNS.get(element)
        if pattern is not None and pattern.search(blob):
            n += 1
    return n


def other_lid_in_url(packet: Packet) -> bool:
    lid = (packet.lid or "").upper()
    for match in _LID_PATH_RE.finditer(_path(packet.url)):
        token = match.group(1).upper()
        if token == lid or token.lower() in _PATH_STOP:
            continue
        if token.isascii() and not token.isdigit() and len(token) >= 3:
            return True
    return False


def features(packet: Packet) -> dict[str, float]:
    """Tight checks. Values are 0/1 or a small count."""
    url = packet.url or ""
    path = _path(url)
    name = _filename(url)
    host = _host(url)
    label = packet.label or ""
    text = _text(packet)
    blob = packet.blob
    lid = (packet.lid or "").strip()
    airport = (packet.name or "").strip()
    lower_path = path.lower()
    media_pdf = (
        lower_path.endswith(".pdf")
        or name.lower().endswith(".pdf")
        or bool(_GUID_RE.search(url) and not re.search(r"\.(html?|aspx|shtml)$", lower_path))
    )
    media_html = (not media_pdf) and bool(url)
    # HTML chrome is not the document. PDF body is the source; incidental NEPA or
    # pavement talk in a plan chapter is not the same as an EA or PCI report.
    surface = f"{label} {url} {name}"
    shape = float(shape_hits(text))
    plan_shaped = bool(
        (_AMP_RE.search(name) and not _CHAPTER_RE.search(name))
        or _CHAPTER_RE.search(name)
        or (_ALP_RE.search(name) and "narrative" not in name.lower())
        or re.search(r"airport diagram", label, re.I)
        or shape >= 3
    )
    body = "" if media_html else (packet.body or "")
    folded = _fold(blob)
    tokens = _name_tokens(airport)
    html = packet.body if media_html else ""
    pdf_hrefs = _PDF_HREF_RE.findall(html) if html else []
    pdf_href_blob = " ".join(pdf_hrefs)
    first = (body or html)[:4000]
    return {
        "lid_in_url": float(bool(lid) and lid.lower() in url.lower()),
        "lid_in_text": float(bool(lid) and lid.lower() in text.lower()),
        "lid_in_filename": float(bool(lid) and lid.lower() in name.lower()),
        "lid_in_label": float(bool(lid) and lid.lower() in label.lower()),
        "name_in_text": float(bool(airport) and _fold(airport) in folded),
        "name_in_filename": float(bool(tokens) and _token_hits(tokens, name) >= 1),
        "name_token_host": float(min(_token_hits(tokens, host), 3)),
        "name_token_host_strong": float(min(_token_hits(tuple(t for t in tokens if len(t) >= 6), host), 3)),
        "name_token_path": float(min(_token_hits(tokens, lower_path), 3)),
        "other_lid_in_url": float(other_lid_in_url(packet)),
        "host_gov": float(host.endswith(".gov") or host.endswith(".mil")),
        "host_encyclopedia": float(any(item in host for item in _ENCYCLOPEDIA_HOSTS)),
        "ssi_name": float(bool(SSI_RE.search(f"{name} {url}"))),
        "news_path": float(bool(NEWS_RE.search(name) or NEWS_RE.search(path))),
        "filename_amp": float(bool(_AMP_RE.search(name) and not _CHAPTER_RE.search(name))),
        "filename_alp": float(bool(_ALP_RE.search(name) and "narrative" not in name.lower())),
        "filename_ea": float(bool(_EA_RE.search(name) or _EA_RE.search(path))),
        "filename_chapter": float(bool(_CHAPTER_RE.search(name))),
        "filename_abstract": float(
            bool(_ABSTRACT_NAME_RE.search(name) or _GUID_RE.search(url) or _GUID_RE.search(name))
        ),
        "filename_pfc": float(bool(re.search(r"\bpfc\b|passenger.facility", name, re.I))),
        "path_master_plan": float("master" in lower_path and "plan" in lower_path),
        "path_projects": float("/projects/" in lower_path),
        "path_environmental": float("environmental" in lower_path),
        "label_minutes": float(bool(re.search(r"\bminutes\b|\bagenda\b", label, re.I))),
        "label_notice": float(bool(re.search(r"\bnotice\b|\bpress\b|NOTAM", label, re.I))),
        "label_diagram": float(bool(re.search(r"airport diagram", label, re.I))),
        "label_economic_impact": float(bool(_ECON_RE.search(label))),
        "label_amp": float(bool(_AMP_RE.search(label) and not _CHAPTER_RE.search(label))),
        "label_alp": float(bool(_ALP_RE.search(label))),
        "label_chapter": float(bool(_CHAPTER_RE.search(label))),
        "label_appendix": float(bool(_APPENDIX_RE.search(label) or _APPENDIX_RE.search(name))),
        "label_statewide": float(bool(_STATEWIDE_RE.search(f"{label} {name}"))),
        "text_amp": float(bool(_AMP_RE.search(text))),
        "first_page_amp": float(bool(_AMP_RE.search(first))),
        "packet_not_plan": float(bool(_NOT_PLAN_RE.search(surface))),
        "body_nepa": float(bool(_EA_RE.search(body)) and not plan_shaped),
        "body_economic_impact": float(bool(_ECON_RE.search(body if body else label)) and not plan_shaped),
        "body_budget": float(bool(_BUDGET_RE.search(body if body else surface)) and not plan_shaped),
        "body_pavement": float(bool(_PAVE_RE.search(body if body else surface)) and not plan_shaped),
        "body_statewide": float(bool(_STATEWIDE_RE.search(first[:1500] if body else f"{label} {name}"))),
        "shape_count": shape,
        "outline_n": float(min(len(packet.outline), 8)),
        "body_short": float(bool(media_pdf) and len(body) < 400),
        "body_long": float(len(body) >= 8000),
        "scanned_pdf": float(
            bool(media_pdf) and packet.source_bytes > 2_000_000 and len(body) < 800
        ),
        "media_pdf": float(media_pdf),
        "media_html": float(media_html),
        "sharepoint": float("sharepoint" in url.lower() or "lists/" in lower_path),
        "html_pdf_links": float(min(len(pdf_hrefs), 8) if media_html else 0.0),
        "html_amp_links": float(
            min(sum(1 for href in pdf_hrefs if _AMP_RE.search(href)), 4) if media_html else 0.0
        ),
        "html_alp_links": float(
            min(sum(1 for href in pdf_hrefs if _ALP_RE.search(href)), 4) if media_html else 0.0
        ),
        "html_plan_hrefs": float(bool(_AMP_RE.search(pdf_href_blob) or _ALP_RE.search(pdf_href_blob))),
    }


# Large negatives are veto-strength. Filename EA is heavy, not infinite.
WEIGHTS_CONFIRM = {
    "lid_in_url": 2.0,
    "lid_in_text": 1.5,
    "lid_in_filename": 1.5,
    "lid_in_label": 1.0,
    "name_in_text": 1.0,
    "name_in_filename": 1.0,
    "name_token_host": 0.6,
    "other_lid_in_url": -6.0,
    "host_encyclopedia": -12.0,
    "ssi_name": -25.0,
    "news_path": -8.0,
    "filename_amp": 4.0,
    "filename_alp": 4.0,
    "filename_ea": -8.0,
    "filename_chapter": 3.0,
    "filename_abstract": 0.0,
    "path_master_plan": 2.5,
    "label_minutes": -8.0,
    "label_notice": -4.0,
    "label_diagram": 3.0,
    "label_amp": 2.5,
    "label_alp": 2.5,
    "label_chapter": 2.0,
    "label_appendix": -2.0,
    "label_statewide": -5.0,
    "filename_pfc": -6.0,
    "text_amp": 3.0,
    "first_page_amp": 2.0,
    "label_economic_impact": -6.0,
    "packet_not_plan": -6.0,
    "body_nepa": -5.0,
    "body_economic_impact": -6.0,
    "body_budget": -6.0,
    "body_pavement": -6.0,
    "body_statewide": -5.0,
    "shape_count": 0.8,
    "outline_n": 0.2,
    "scanned_pdf": 0.8,
    "media_pdf": 1.5,
    "sharepoint": 0.5,
}
WEIGHTS_EXPLORE = {
    "media_html": 3.0,
    "path_master_plan": 3.0,
    "lid_in_url": 1.5,
    "lid_in_text": 1.5,
    "lid_in_filename": 1.0,
    "name_in_text": 1.0,
    "name_token_host": 0.5,
    "host_encyclopedia": -12.0,
    "ssi_name": -25.0,
    "news_path": -4.0,
    "media_pdf": -4.0,
    "label_minutes": -6.0,
    "label_notice": -4.0,
    "packet_not_plan": -3.0,
    "html_pdf_links": 0.3,
    "html_amp_links": 1.2,
    "html_alp_links": 1.2,
    "html_plan_hrefs": 2.0,
    "label_amp": 1.5,
}
WEIGHTS_PUBLISH = {
    "ssi_name": -25.0,
    "host_encyclopedia": -12.0,
    "other_lid_in_url": -8.0,
    "filename_ea": -10.0,
    "body_nepa": -8.0,
    "packet_not_plan": -8.0,
    "body_economic_impact": -8.0,
    "body_budget": -8.0,
    "body_pavement": -8.0,
    "label_minutes": -10.0,
    "news_path": -10.0,
    "filename_amp": 4.0,
    "text_amp": 2.5,
    "first_page_amp": 1.5,
    "label_amp": 2.5,
    "filename_alp": 4.0,
    "label_alp": 2.5,
    "filename_chapter": 3.0,
    "label_chapter": 2.0,
    "label_diagram": 3.0,
    "label_statewide": -8.0,
    "filename_pfc": -8.0,
    "body_statewide": -6.0,
    "path_master_plan": 2.0,
    "shape_count": 1.2,
    "outline_n": 0.2,
    "scanned_pdf": 1.0,
    "lid_in_url": 2.0,
    "lid_in_text": 1.5,
    "media_pdf": 1.0,
    "media_html": -3.0,
}
KIND_WEIGHTS = {
    "not_plan": {
        "filename_ea": 8.0,
        "body_nepa": 6.0,
        "packet_not_plan": 6.0,
        "body_economic_impact": 6.0,
        "label_economic_impact": 5.0,
        "body_budget": 6.0,
        "body_pavement": 6.0,
        "filename_pfc": 8.0,
        "label_statewide": 6.0,
        "body_statewide": 5.0,
        "host_encyclopedia": 10.0,
        "filename_abstract": 8.0,
        "other_lid_in_url": 4.0,
        "label_appendix": 3.0,
    },
    "notice": {
        "news_path": 8.0,
        "label_minutes": 6.0,
        "label_notice": 6.0,
    },
    "alp": {
        "filename_alp": 6.0,
        "label_diagram": 5.0,
        "label_alp": 5.0,
        "scanned_pdf": 2.0,
        "body_short": 1.5,
        "html_alp_links": 1.0,
    },
    "chapter": {
        "filename_chapter": 7.0,
        "label_chapter": 5.0,
        "shape_count": 0.3,
        "outline_n": 0.2,
    },
    "master_plan": {
        "filename_amp": 6.0,
        "label_amp": 5.0,
        "text_amp": 4.0,
        "first_page_amp": 3.0,
        "path_master_plan": 3.0,
        "shape_count": 1.0,
        "body_long": 2.0,
        "filename_chapter": -4.0,
        "filename_alp": -4.0,
        "label_chapter": -2.0,
    },
    "hub": {
        "media_html": 5.0,
        "path_master_plan": 3.0,
        "html_plan_hrefs": 3.0,
        "html_pdf_links": 0.4,
        "html_amp_links": 1.0,
        "media_pdf": -5.0,
    },
}
CONFIRM_THRESHOLD = 2.5
EXPLORE_THRESHOLD = 2.5
PUBLISH_THRESHOLD = 3.5
KIND_THRESHOLD = 2.0


@dataclass
class ScoreConfig:
    """Injectable weights and thresholds so training can mutate a copy."""

    weights_confirm: dict[str, float]
    weights_explore: dict[str, float]
    weights_publish: dict[str, float]
    kind_weights: dict[str, dict[str, float]]
    confirm_threshold: float = CONFIRM_THRESHOLD
    explore_threshold: float = EXPLORE_THRESHOLD
    publish_threshold: float = PUBLISH_THRESHOLD
    kind_threshold: float = KIND_THRESHOLD

    @classmethod
    def default(cls) -> "ScoreConfig":
        return cls(
            weights_confirm=dict(WEIGHTS_CONFIRM),
            weights_explore=dict(WEIGHTS_EXPLORE),
            weights_publish=dict(WEIGHTS_PUBLISH),
            kind_weights={kind: dict(weights) for kind, weights in KIND_WEIGHTS.items()},
        )


def weighted_sum(feats: dict[str, float], weights: dict[str, float]) -> float:
    return sum(feats.get(key, 0.0) * weight for key, weight in weights.items())


def same_airport(packet: Packet, feats: dict[str, float] | None = None) -> bool:
    feats = feats if feats is not None else features(packet)
    lid_hit = bool(
        feats["lid_in_url"]
        or feats["lid_in_text"]
        or feats["lid_in_filename"]
        or feats["lid_in_label"]
    )
    if feats["other_lid_in_url"] and not lid_hit:
        return False
    if feats["label_statewide"] or feats["body_statewide"]:
        return bool(feats["lid_in_url"] or feats["lid_in_filename"])
    if feats["filename_pfc"] and not (feats["lid_in_url"] or feats["lid_in_filename"]):
        return False
    return bool(
        lid_hit
        or feats["name_in_text"]
        or feats["name_in_filename"]
        or feats["name_token_host"] >= 2
        or feats["name_token_host_strong"] >= 1
        or (feats["name_token_host"] >= 1 and feats["name_token_path"] >= 1)
        or feats["name_token_path"] >= 2
    )


def kind_scores(feats: dict[str, float], config: ScoreConfig | None = None) -> dict[str, float]:
    tables = (config or ScoreConfig.default()).kind_weights
    return {kind: weighted_sum(feats, weights) for kind, weights in tables.items()}


def decide_kind(feats: dict[str, float], config: ScoreConfig | None = None) -> str:
    cfg = config or ScoreConfig.default()
    scores = kind_scores(feats, cfg)
    kind_floor = cfg.kind_threshold
    if feats["media_html"]:
        if scores["not_plan"] >= kind_floor and scores["not_plan"] >= scores["notice"]:
            return "not_plan"
        if scores["notice"] >= kind_floor:
            return "notice"
        return "hub"
    if (
        feats["filename_pfc"]
        or feats["filename_ea"]
        or feats["label_economic_impact"]
        or feats["label_statewide"]
    ):
        return "not_plan"
    if (feats["filename_alp"] or feats["label_diagram"] or feats["label_alp"]) and scores[
        "not_plan"
    ] < scores["alp"] + 2:
        return "alp"
    if (
        (feats["filename_chapter"] or feats["label_chapter"])
        and not feats["filename_ea"]
        and not feats["filename_alp"]
        and scores["chapter"] >= scores["not_plan"]
    ):
        return "chapter"
    kind, score = max(
        ((name, value) for name, value in scores.items() if name != "hub"),
        key=lambda item: item[1],
    )
    if score < kind_floor:
        if feats["filename_ea"] or feats["packet_not_plan"] or feats["filename_abstract"]:
            return "not_plan"
        if feats["filename_chapter"] or feats["label_chapter"]:
            return "chapter"
        if feats["filename_alp"] or feats["label_diagram"] or feats["label_alp"]:
            return "alp"
        if feats["filename_amp"] or feats["label_amp"] or feats["path_master_plan"] or feats["text_amp"]:
            return "master_plan"
        return "not_plan"
    return kind


def score_packet(
    packet: Packet,
    config: ScoreConfig | None = None,
    *,
    feats: dict[str, float] | None = None,
) -> dict[str, object]:
    cfg = config or ScoreConfig.default()
    feats = feats if feats is not None else features(packet)
    confirm_score = weighted_sum(feats, cfg.weights_confirm)
    explore_score = weighted_sum(feats, cfg.weights_explore)
    publish_score = weighted_sum(feats, cfg.weights_publish)
    kind = decide_kind(feats, cfg)
    same = same_airport(packet, feats)
    confirm = (
        same
        and confirm_score >= cfg.confirm_threshold
        and not feats["ssi_name"]
        and kind in {"master_plan", "alp", "chapter"}
        and bool(feats["media_pdf"])
    )
    explore = (
        same
        and explore_score >= cfg.explore_threshold
        and not feats["ssi_name"]
        and not confirm
        and bool(feats["media_html"])
        and kind not in {"not_plan", "notice"}
    )
    publish = (
        same
        and publish_score >= cfg.publish_threshold
        and kind in {"master_plan", "alp", "chapter"}
        and not feats["ssi_name"]
        and bool(feats["media_pdf"])
    )
    return {
        "features": feats,
        "same_airport": same,
        "kind": kind,
        "kind_scores": kind_scores(feats, cfg),
        "confirm": confirm,
        "explore": explore,
        "publish": publish,
        "confirm_score": confirm_score,
        "explore_score": explore_score,
        "publish_score": publish_score,
    }


def load_score_gold(path: Path | None = None) -> dict:
    target = path or SCORE_GOLD_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def load_score_sample(path: Path | None = None) -> dict:
    target = path or SCORE_SAMPLE_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def gold_source_path(row: dict, *, cache: bool = True) -> Path | None:
    """Full original bytes: committed fixture, then optional local data/score cache."""
    fixture = row.get("fixture") or ""
    if fixture:
        for item in load_embedded_fixtures():
            if item.get("document_id") == fixture:
                rel = item.get("path") or ""
                for path in (REFERENCE_FILES.parent / rel, ROOT / rel):
                    if rel and path.is_file():
                        return path
    rel = row.get("source") or ""
    if rel:
        path = Path(rel)
        if not path.is_absolute():
            path = REPO / rel
        if path.is_file():
            return path
    stem = row.get("id") or ""
    sha = (row.get("reject_sha256") or "").strip()
    if sha and cache:
        for suffix in (".pdf", ".html", ".bin"):
            cached = SCORE_CACHE / "review" / "rejects" / f"{sha}{suffix}"
            if cached.is_file():
                return cached
    if stem:
        for suffix in (".pdf", ".html", ".htm", ".aspx"):
            path = REFERENCE_FILES / f"{stem}{suffix}"
            if path.is_file():
                return path
        if cache:
            for suffix in (".pdf", ".html", ".htm", ".aspx"):
                path = SCORE_CACHE / f"{stem}{suffix}"
                if path.is_file():
                    return path
    return None


def _extract_limits(size: int) -> tuple[int | None, bool]:
    """Page cap and whether to skip the PDF outline for huge drawings."""
    if size > 40_000_000:
        return 12, True
    if size > 12_000_000:
        return 40, False
    return None, False


def load_extract(path: Path) -> dict:
    """Native text and outline for a source file. Cached under data/score/extract."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    dest = EXTRACT_CACHE / f"{digest[:24]}.json"
    try:
        EXTRACT_CACHE.mkdir(parents=True, exist_ok=True)
        if dest.is_file():
            payload = json.loads(dest.read_text(encoding="utf-8"))
            if payload.get("sha256") == digest:
                return payload
    except OSError:
        dest = None
    max_pages, skip_outline = _extract_limits(len(data))
    body = ""
    outline: list[str] = []
    if data.startswith(b"%PDF"):
        body = extract_text(data, max_pages=max_pages)
        if not skip_outline:
            outline = outline_titles(data)
    elif data:
        body = data.decode("utf-8", "replace")
    payload = {
        "sha256": digest,
        "bytes": len(data),
        "max_pages": max_pages,
        "body": body,
        "outline": outline,
    }
    if dest is not None:
        try:
            dest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return payload


def packet_from_source(data: bytes, **kwargs) -> Packet:
    max_pages, skip_outline = _extract_limits(len(data))
    body = ""
    outline: tuple[str, ...] = ()
    if data.startswith(b"%PDF"):
        body = extract_text(data, max_pages=max_pages)
        if not skip_outline:
            outline = tuple(outline_titles(data))
    elif data:
        body = data.decode("utf-8", "replace")
    kwargs.setdefault("source_bytes", len(data))
    return Packet(body=body, outline=outline, **kwargs)


def packet_from_gold(row: dict, *, cache: bool = True) -> Packet:
    path = gold_source_path(row, cache=cache)
    if path is None:
        return Packet(
            lid=row.get("lid") or "",
            name=row.get("name") or "",
            url=row.get("url") or "",
            label=row.get("label") or "",
        )
    extracted = load_extract(path)
    return Packet(
        lid=row.get("lid") or "",
        name=row.get("name") or "",
        url=row.get("url") or "",
        label=row.get("label") or "",
        body=extracted.get("body") or "",
        outline=tuple(extracted.get("outline") or ()),
        source_bytes=int(extracted.get("bytes") or path.stat().st_size),
    )


def load_gold_packets(
    *,
    cache: bool = True,
    extra: list[dict] | None = None,
) -> list[tuple[dict, Packet]]:
    cases = list(load_score_gold().get("cases") or [])
    seen = {(row.get("url") or "").rstrip("/") for row in cases}
    for row in extra or []:
        url = (row.get("url") or "").rstrip("/")
        if not url or url in seen:
            continue
        cases.append(row)
        seen.add(url)
    return [(case, packet_from_gold(case, cache=cache)) for case in cases]

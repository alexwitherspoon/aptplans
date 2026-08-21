"""Unofficial airport overview: inventory facts from listed files and NASR.

Does not invent news. Missing numbers stay off the page. CI never calls a model.
Fact sheets read native text from every page of a listed PDF, once, then cache it.
NASR runway dimensions, elevation, and fuel/storage flags fill fields the files
do not support.
"""

from __future__ import annotations

import io
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

from catalog import ROOT as CATALOG_ROOT, load_embedded_fixtures
from catalog.seed import reference_seed_enabled

_TAG_RE = re.compile(r"<[^>]+>")
_TOC = re.compile(r"\.{4,}|table of contents", re.I)

_RWY_LONG_WIDE = re.compile(
    r"runway\s+(\d{1,2}\s*[/–-]\s*\d{1,2}[LCR]?)\s+is\s+([\d,]+)\s+feet\s+long"
    r"\s+and\s+([\d,]+)\s+feet\s+wide",
    re.I,
)
_RWY_USABLE = re.compile(
    r"usable\s+length(?:\s+is|\s+of)?\s+([\d,]+)\s+feet",
    re.I,
)
_RWY_WIDE = re.compile(r"(?:the\s+)?runway\s+is\s+([\d,]+)\s+feet\s+wide", re.I)
_RWY_ID = re.compile(
    r"(?:has\s+one\s+(?:paved\s+)?runway|one\s+(?:paved\s+)?runway)\s*[,:(]?\s*"
    r"(\d{1,2}\s*[/–-]\s*\d{1,2}[LCR]?)",
    re.I,
)
_RWY_BY = re.compile(
    r"runway\s+(\d{1,2}[LCR]?\s*[/–-]\s*\d{1,2}[LCR]?)\s*[,:]?\s*"
    r"([\d,]+)\s*(?:feet|ft)\s*(?:by|x)\s*([\d,]+)",
    re.I,
)
_RWY_COUNT = re.compile(
    r"\bhas\s+(one|two|three|four|\d+)\s+(?:parallel\s+)?runways?\b",
    re.I,
)
_RWY_PAIR = re.compile(
    r"runways?\s+(\d{1,2}[LCR]?)\s*[/–-]\s*(\d{1,2}[LCR]?)",
    re.I,
)
_RWY_DIM_PRIME = re.compile(
    r"([\d,]+)\s*['′]\s*[x×]\s*([\d,]+)\s*['′]",
    re.I,
)
_BASED_NOW = re.compile(r"currently\s+([\d,]+)\s+based aircraft", re.I)
_BASED_AT = re.compile(r"([\d,]+)\s+based aircraft at the airport", re.I)
_HANGARED = re.compile(
    r"([\d,]+)\s+(?:are |were )?stored in hangars",
    re.I,
)
_TIEDOWN_REST = re.compile(
    r"stored in hangars,\s+while the remaining\s+(\w+)\s+are stored",
    re.I,
)
_OPS_ANNUAL = re.compile(
    r"([\d,]+)\s+annual(?: aircraft)? operations(?:\s+in\s+(\d{4}))?",
    re.I,
)
_OPS_OVER = re.compile(r"over\s+([\d,]+)\s+annual operations", re.I)
_HANGAR_BUILDINGS = re.compile(r"([\d,]+)\s+hangar buildings", re.I)
_T_UNIT = re.compile(
    r"T-hangar\s+\((\d+)\s+unit|\((\d+)\s+unit\)\s+T-hangar|(\d+)-unit\s+T-hangar",
    re.I,
)
_NEGATE = re.compile(
    r"\b(?:no|not|never|without|neither|nor|absolutely no)\b[\s\S]{0,48}$",
    re.I,
)
_PREFERRED = re.compile(
    r"(?:include alternative [a-e].{0,220}"
    r"|preferred(?: development)? alternative.{0,220}"
    r"|recommended (?:development )?(?:alternative|plan|program|concept).{0,200}"
    r"|selected alternative.{0,200}"
    r"|master plan concept.{0,200}"
    r"|pac voted.{0,200})",
    re.I | re.S,
)
_DECLINE_PATTERNS = (
    (
        re.compile(
            r"\b(?:close|closing|closure of) (?:the )?airport\b"
            r"|\bairport clos(?:e|ing|ure)\b",
            re.I,
        ),
        4,
    ),
    (
        re.compile(
            r"\b(?:shorten(?:ed|ing)?|reduc(?:e|ed|ing) (?:the )?length of)"
            r" (?:the )?runway\b"
            r"|\breduced runway length\b",
            re.I,
        ),
        3,
    ),
    (
        re.compile(
            r"\b(?:[\d,]+\s+acres of )?industrial development\b"
            r"|\bnon-aeronautical (?:use|development)\b"
            r"|\bmore commercial/industrial\b"
            r"|\bland release\b"
            r"|\bsurplus property\b",
            re.I,
        ),
        2,
    ),
    (re.compile(r"\bless aviation\b|\bdownsiz(?:e|ing)\b", re.I), 3),
    (
        re.compile(
            r"\bconvert(?:ing)? all .{0,48}(?:commercial|industrial)"
            r"|\brelocat(?:e|ing) the airport\b"
            r"|\bdecommission(?:ing)? (?:the )?(?:airport|runway)\b",
            re.I,
        ),
        3,
    ),
)
_GROW_PATTERNS = (
    (
        re.compile(
            r"\brunway extension\b"
            r"|\b(?:extend(?:ed|ing)?|lengthen(?:ed|ing)?) the runway\b",
            re.I,
        ),
        3,
    ),
    (
        re.compile(
            r"\b(?:new|parallel|second|third) runway\b|\badditional runways\b",
            re.I,
        ),
        3,
    ),
    (
        re.compile(
            r"\b(?:additional|new|future|proposed) (?:t-)?hangars?\b"
            r"|\b(?:t-|conventional |future |new |proposed |additional )hangar development\b"
            r"|\bhangar expansion\b",
            re.I,
        ),
        2,
    ),
    (re.compile(r"\bmore aviation\b|\bless commercial/industrial\b", re.I), 3),
    (
        re.compile(
            r"\b(?:terminal expansion|expand(?:ed|ing)? (?:the )?terminal|new terminal)\b"
            r"|\b(?:capacity expansion|expand(?:ed|ing)? capacity)\b"
            r"|\bimprove terminal\b|\bconcourse extension\b"
            r"|\bnew concourse\b|\bgate expansion\b|\badditional gates\b",
            re.I,
        ),
        2,
    ),
    (
        re.compile(
            r"\b(?:apron expansion|new apron|expand(?:ed|ing)? (?:the )?apron)\b"
            r"|\b(?:taxiway|taxilane) extension\b"
            r"|\bnew (?:taxiway|taxilane)\b",
            re.I,
        ),
        2,
    ),
)
_HOLD_PATTERNS = (
    (
        re.compile(
            r"\bmaintain(?:ing)? existing(?: length| runway| facilities| airport)?\b",
            re.I,
        ),
        2,
    ),
    (
        re.compile(
            r"\bcontinue to (?:operate|serve|manage)\b"
            r"|\bremain(?:s)? a(?:n)? (?:reliever|general aviation)\b",
            re.I,
        ),
        2,
    ),
    (
        re.compile(
            r"\bno plans to extend\b|\bwill not extend\b|\bnot extend the runway\b",
            re.I,
        ),
        2,
    ),
    (re.compile(r"\bno(?:-|\s+)(?:action|build) alternative\b", re.I), 1),
    (
        re.compile(
            r"\b(?:reconstruct|rehabilitat(?:e|ion of)) (?:the )?(?:runway|pavement)\b",
            re.I,
        ),
        1,
    ),
)

_WORDS = {
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


@dataclass(frozen=True)
class Trajectory:
    band: str
    position: float
    note: str
    needle_x: float
    needle_y: float

    def to_dict(self) -> dict:
        return {
            "band": self.band,
            "position": self.position,
            "note": self.note,
            "needle_x": self.needle_x,
            "needle_y": self.needle_y,
        }


@dataclass(frozen=True)
class AirportOverview:
    facts: tuple[tuple[str, str], ...]
    as_of: str | None
    generated_at: str | None = None
    trajectory: Trajectory | None = None

    def to_row(self, lid: str, generated_at: str) -> dict:
        row = {
            "airport_lid": lid,
            "generated_at": generated_at,
            "as_of": self.as_of,
            "facts": [list(item) for item in self.facts],
            "upcoming": [],  # overlay JSON still has this field; CIP is not shown
        }
        if self.trajectory is not None:
            row["trajectory"] = self.trajectory.to_dict()
        return row


_FACT_ORDER = (
    "Runways",
    "Elevation",
    "Hangars",
    "Based aircraft",
    "Hangared aircraft",
    "Operations",
    "Facilities",
)


def files_dir() -> Path:
    raw = os.environ.get("FILES_PATH") or os.environ.get("APTPLANS_FILES") or ""
    if raw.strip():
        return Path(raw)
    return CATALOG_ROOT.parent / "data" / "files"


def source_path_for(document) -> Path | None:
    if reference_seed_enabled():
        for row in load_embedded_fixtures():
            if row.get("document_id") == document.id:
                path = CATALOG_ROOT / "references" / row["path"]
                if path.is_file():
                    return path
    preview = files_dir() / "preview" / f"{document.id}.pdf"
    if (
        os.environ.get("APTPLANS_DEV_PREVIEW", "").strip().lower() in {"1", "true", "yes"}
        and preview.is_file()
        and preview.stat().st_size > 1000
    ):
        return preview
    digest = getattr(document, "content_sha256", None)
    if digest:
        for suffix in (".pdf", ".html"):
            path = files_dir() / f"{digest}{suffix}"
            if path.is_file():
                return path
    return None


def _num(raw: str) -> int | None:
    digits = (raw or "").replace(",", "").strip()
    if digits.isdigit():
        value = int(digits)
        return value if 0 < value < 5_000_000 else None
    return _WORDS.get(digits.lower())


def _fmt(value: int) -> str:
    return f"{value:,}"


def _rwy_id(raw: str) -> str:
    return re.sub(r"\s+", "", raw.replace("–", "/").replace("-", "/"))


def excerpt_from_pages(pages: list[str]) -> str:
    """Join native text from every page. Skip empty and contents-dot leaders."""
    parts: list[str] = []
    for raw in pages:
        text = (raw or "").strip()
        if len(text) < 40:
            continue
        if _TOC.search(text) and text.count(".") > 80:
            continue
        parts.append(text)
    return "\n\n".join(parts)


def _facts_cache_path(path: Path) -> Path:
    from pipeline.textstore import text_dir

    return text_dir() / "facts" / f"{path.name}.{path.stat().st_size}.txt"


def _cache_fresh(cache: Path, source: Path) -> bool:
    if not cache.is_file():
        return False
    try:
        return cache.stat().st_mtime >= source.stat().st_mtime
    except OSError:
        return False


def pdf_fact_text(source: Path | bytes, *, cache_path: Path | None = None) -> str:
    """Cache key is filename plus byte size so a replaced PDF re-extracts."""
    path = source if isinstance(source, Path) else None
    if path is not None:
        cache_path = cache_path or _facts_cache_path(path)
        if _cache_fresh(cache_path, path):
            return cache_path.read_text(encoding="utf-8", errors="replace")
    elif cache_path is not None and cache_path.is_file():
        return cache_path.read_text(encoding="utf-8", errors="replace")
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    reader = PdfReader(path) if path is not None else PdfReader(io.BytesIO(source))
    body = excerpt_from_pages(
        [(page.extract_text() or "") for page in reader.pages]
    )
    if cache_path is not None and body:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(body, encoding="utf-8")
        except OSError:
            pass
    return body


def document_excerpt(document) -> str:
    note = (getattr(document, "summary", None) or "").strip()
    digest = getattr(document, "content_sha256", None)
    if digest:
        from pipeline.textstore import read_pages, text_dir

        rows = read_pages(text_dir(), digest)
        if rows:
            by_page = {int(row["page"]): str(row["text"]) for row in rows if row.get("text")}
            n = max(by_page, default=0)
            pages = [by_page.get(i + 1, "") for i in range(n)]
            body = excerpt_from_pages(pages)
            return "\n\n".join(part for part in (note, body) if part)
    path = source_path_for(document)
    if path is None:
        return note
    suffix = path.suffix.lower()
    body = ""
    try:
        if suffix == ".pdf":
            body = pdf_fact_text(path)
        elif suffix in {".html", ".htm"}:
            body = _TAG_RE.sub(" ", path.read_bytes().decode("utf-8", "replace"))
    except Exception:
        # Corrupt or unreadable listed files omit inventory rather than fail HTML generate.
        body = ""
    return "\n\n".join(part for part in (note, body) if part)


def work_excerpt(work) -> str:
    docs = []
    if getattr(work, "hub", None) is not None:
        docs.append(work.hub)
    docs.extend(list(getattr(work, "parts", ()) or ()))
    parts = [document_excerpt(doc) for doc in docs]
    return "\n\n".join(part for part in parts if part)


def _usable_rwy(length: int | None, width: int | None) -> bool:
    return bool(length and width and length >= 500 and 20 <= width <= 400)


def format_runways(rows: list[dict]) -> str | None:
    found: list[tuple[str, int, int, str | None]] = []
    seen: set[str] = set()
    for row in rows:
        ident = _rwy_id(str(row.get("id") or ""))
        try:
            length = int(row.get("length_ft"))
            width = int(row.get("width_ft"))
        except (TypeError, ValueError):
            continue
        if not ident or ident in seen or not _usable_rwy(length, width):
            continue
        seen.add(ident)
        surface = str(row.get("surface") or "").strip() or None
        found.append((ident, length, width, surface))
    return _format_runway_rows(found)


def _surface_label(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    # Already a display phrase such as Asphalt, not a NASR code.
    if text[0].isupper() and not text.isupper() and "-" not in text:
        return text
    compact = text.upper().replace(" ", "").replace("_", "-")
    for code, label in (
        ("ASPH-CONC", "Asphalt and concrete"),
        ("CONC-ASPH", "Concrete and asphalt"),
        ("ASPH", "Asphalt"),
        ("CONC", "Concrete"),
        ("GRAVEL", "Gravel"),
        ("GRVL", "Gravel"),
        ("TURF", "Turf"),
        ("DIRT", "Dirt"),
        ("WATER", "Water"),
        ("TREATED", "Treated"),
        ("MATS", "Mats"),
        ("SNOW", "Snow"),
        ("ICE", "Ice"),
    ):
        if compact == code or compact.startswith(f"{code}-"):
            return label
    return None


def format_runway_line(ident: str, length: int, width: int, surface: str | None = None) -> str:
    line = f"{ident}, {_fmt(length)} by {_fmt(width)} ft"
    label = _surface_label(surface)
    if label:
        line += f" · {label}"
    return line


def _format_runway_rows(rows: list[tuple[str, int, int, str | None]]) -> str | None:
    if not rows:
        return None
    return "\n".join(
        format_runway_line(ident, length, width, surface)
        for ident, length, width, surface in rows[:3]
    )


def _runway_line_has_surface(value: str | None) -> bool:
    return bool(value and re.search(r"ft\s*·\s*\S", value))


_RUNWAY_SEGMENT = re.compile(
    r"(\d{1,2}[LCR]?/\d{1,2}[LCR]?),\s*([\d,]+)\s+by\s+([\d,]+)\s+ft",
    re.I,
)


def decorate_runways_with_surface(plan: str, rows: list[dict]) -> str:
    """Keep plan dimensions; add NASR surface by ident when the plan line has none."""
    by_id = {_rwy_id(str(row.get("id") or "")): row for row in rows}
    parts: list[str] = []
    for match in _RUNWAY_SEGMENT.finditer(plan or ""):
        ident = _rwy_id(match.group(1))
        length = _num(match.group(2))
        width = _num(match.group(3))
        if not ident or not length or not width:
            continue
        surface = (by_id.get(ident) or {}).get("surface")
        parts.append(format_runway_line(ident, length, width, str(surface) if surface else None))
    return "\n".join(parts) if parts else plan


def extract_runways(text: str) -> str | None:
    blob = text or ""
    found: list[tuple[str, int, int, str | None]] = []
    seen: set[str] = set()
    for match in list(_RWY_LONG_WIDE.finditer(blob)) + list(_RWY_BY.finditer(blob)):
        ident = _rwy_id(match.group(1))
        length = _num(match.group(2))
        width = _num(match.group(3))
        if not ident or ident in seen or not _usable_rwy(length, width):
            continue
        seen.add(ident)
        found.append((ident, length, width, None))
    if found:
        return _format_runway_rows(found)
    ident_match = _RWY_ID.search(blob)
    length = _num(m.group(1)) if (m := _RWY_USABLE.search(blob)) else None
    width = _num(m.group(1)) if (m := _RWY_WIDE.search(blob)) else None
    if ident_match and length is not None and width is not None and _usable_rwy(length, width):
        return format_runway_line(_rwy_id(ident_match.group(1)), length, width)
    ids: list[str] = []
    for match in _RWY_PAIR.finditer(blob):
        ident = _rwy_id(f"{match.group(1)}/{match.group(2)}")
        if ident not in ids:
            ids.append(ident)
        if len(ids) >= 3:
            break
    dims: list[tuple[int, int]] = []
    for match in _RWY_DIM_PRIME.finditer(blob):
        length = _num(match.group(1))
        width = _num(match.group(2))
        if not _usable_rwy(length, width):
            continue
        dims.append((length, width))
        if len(dims) >= 3:
            break
    if ids and dims:
        n = min(len(ids), len(dims), 3)
        return _format_runway_rows(
            [(ids[i], dims[i][0], dims[i][1], None) for i in range(n)]
        )
    count_match = _RWY_COUNT.search(blob)
    if count_match:
        n = _num(count_match.group(1))
        if n:
            return str(n) if n != 1 else "1"
    return None


def facts_from_catalog(airport) -> dict[str, str]:
    """NASR identity already on the airport record."""
    if airport is None:
        return {}
    facts: dict[str, str] = {}
    runways = format_runways(getattr(airport, "runways", None) or [])
    if runways:
        facts["Runways"] = runways
    elevation = getattr(airport, "elevation_ft", None)
    if elevation is not None:
        facts["Elevation"] = f"{_fmt(int(elevation))} ft"
    bits: list[str] = []
    fuel = (getattr(airport, "fuel", None) or "").strip()
    if fuel:
        bits.append(fuel)
    if getattr(airport, "hangar_storage", False):
        bits.append("hangar storage")
    if getattr(airport, "tiedown_storage", False):
        bits.append("tiedowns")
    if bits:
        facts["Facilities"] = " · ".join(bits)
    return facts


def merge_fact_rows(merged: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple((label, merged[label]) for label in _FACT_ORDER if label in merged)


def _runways_need_fill(value: str | None) -> bool:
    """True when missing or a bare count, so NASR dimensions can land."""
    if not value or not str(value).strip():
        return True
    return bool(re.fullmatch(r"\d+", str(value).strip()))


def _prefer_runways(plan: str | None, nasr: str | None, rows: list[dict]) -> str | None:
    """Plan dimensions win. NASR fills a count-only line or attaches surface."""
    if _runways_need_fill(plan):
        return nasr or plan
    if plan and not _runway_line_has_surface(plan):
        return decorate_runways_with_surface(plan, rows)
    return plan


def apply_catalog_facts(overview: AirportOverview | None, airport) -> AirportOverview | None:
    extra = facts_from_catalog(airport)
    if overview is None:
        if not extra:
            return None
        return AirportOverview(
            facts=merge_fact_rows(extra),
            as_of=getattr(airport, "nasr_effective", None),
        )
    merged = dict(overview.facts)
    rows = getattr(airport, "runways", None) or []
    for key, value in extra.items():
        if key == "Runways":
            runways = _prefer_runways(merged.get("Runways"), value, rows)
            if runways:
                merged["Runways"] = runways
            continue
        merged.setdefault(key, value)
    facts = merge_fact_rows(merged)
    as_of = overview.as_of or (getattr(airport, "nasr_effective", None) if extra else None)
    if not facts and overview.trajectory is None:
        return None
    return AirportOverview(
        facts=facts,
        as_of=as_of,
        generated_at=overview.generated_at,
        trajectory=overview.trajectory,
    )


def extract_based_aircraft(text: str) -> str | None:
    blob = text or ""
    match = _BASED_NOW.search(blob) or _BASED_AT.search(blob)
    if match:
        count = _num(next(g for g in match.groups() if g))
        if count and count <= 5000:
            return f"{_fmt(count)} based"
    hangared = _num(m.group(1)) if (m := _HANGARED.search(blob)) else None
    extra = _num(m.group(1)) if (m := _TIEDOWN_REST.search(blob)) else None
    if hangared and extra:
        return f"{_fmt(hangared + extra)} based"
    return None


def extract_hangared(text: str) -> str | None:
    match = _HANGARED.search(text or "")
    if not match:
        return None
    hangared = _num(match.group(1))
    if not hangared:
        return None
    rest = _TIEDOWN_REST.search(text or "")
    extra = _num(rest.group(1)) if rest else None
    if extra:
        return f"{_fmt(hangared)} in hangars · {_fmt(extra)} on tiedowns"
    return f"{_fmt(hangared)} in hangars"


def extract_operations(text: str) -> str | None:
    current = None
    blob = text or ""
    over = _OPS_OVER.search(blob)
    if over and "currently" in blob[max(0, over.start() - 80) : over.start()].lower():
        n = _num(over.group(1))
        if n:
            current = (n, None)
    best: tuple[int, int | None] | None = current
    for match in _OPS_ANNUAL.finditer(blob):
        n = _num(match.group(1))
        year = int(match.group(2)) if match.group(2) else None
        if not n or n < 100:
            continue
        window = blob[max(0, match.start() - 120) : match.start() + 40].lower()
        if "forecast" in window or "projected" in window or "increase from" in window:
            continue
        if "in 2006" in window and current:
            continue
        if best is None or (year and (best[1] is None or year >= best[1])):
            best = (n, year)
    if best is None:
        return None
    n, year = best
    if year:
        return f"{_fmt(n)} a year ({year})"
    return f"{_fmt(n)} a year"


def extract_hangars(text: str) -> str | None:
    units = [n for raw in _T_UNIT.findall(text or "") for g in raw if (n := _num(g))]
    buildings = _num(m.group(1)) if (m := _HANGAR_BUILDINGS.search(text or "")) else None
    if units:
        total = sum(units)
        bits = [f"{len(units)} T-hangars ({_fmt(total)} units)"]
        if buildings and buildings > len(units):
            bits.append(f"{buildings} buildings")
        return " · ".join(bits)
    if buildings:
        return f"{_fmt(buildings)} buildings"
    return None


def extract_facilities(text: str) -> str | None:
    blob = (text or "").lower()
    items: list[str] = []
    if re.search(r"non-towered|no control tower", blob):
        items.append("Non-towered")
    if "pilot lounge" in blob:
        items.append("pilot lounge")
    if re.search(r"aviation fuel|fuel tank|aircraft fuel", blob):
        items.append("fuel")
    if re.search(r"no fixed based operators|no fbo", blob):
        items.append("no FBO")
    elif re.search(r"\bfbo\b|fixed base operator", blob):
        items.append("FBO")
    if "tiedown" in blob:
        items.append("tiedowns")
    if not items:
        return None
    return " · ".join(items[:5])


def extract_facts(text: str) -> dict[str, str]:
    facts: dict[str, str] = {}
    mapping = (
        ("Runways", extract_runways),
        ("Based aircraft", extract_based_aircraft),
        ("Hangared aircraft", extract_hangared),
        ("Operations", extract_operations),
        ("Hangars", extract_hangars),
        ("Facilities", extract_facilities),
    )
    for label, fn in mapping:
        value = fn(text)
        if value:
            facts[label] = value
    return facts


def make_trajectory(band: str, position: float, note: str = "") -> Trajectory:
    pos = max(-1.0, min(1.0, position))
    angle = math.radians(180 - (pos + 1) * 90)
    return Trajectory(
        band=band,
        position=round(pos, 3),
        note=note,
        needle_x=round(100 + 72 * math.cos(angle), 1),
        needle_y=round(100 - 72 * math.sin(angle), 1),
    )


def _band_for(position: float) -> str:
    if position < -0.35:
        return "declining"
    if position > 0.35:
        return "growing"
    return "maintaining"


def _pattern_hits(pattern: re.Pattern, text: str, *, cap: int = 2, skip_negated: bool = True) -> int:
    n = 0
    for match in pattern.finditer(text):
        if skip_negated and _NEGATE.search(text[max(0, match.start() - 48) : match.start()]):
            continue
        n += 1
        if n >= cap:
            break
    return n


def _weighted_hits(patterns: tuple[tuple[re.Pattern, int], ...], text: str, *, skip_negated: bool = True) -> int:
    return sum(
        weight * _pattern_hits(pattern, text, skip_negated=skip_negated)
        for pattern, weight in patterns
    )


def _outlook_text(text: str) -> str:
    """Join PDF line-break hyphens and NBSP so preferred/hangar phrases still match."""
    blob = (text or "").replace("\u00a0", " ").replace("\u202f", " ")
    blob = re.sub(r"(\w)-\s+", r"\1", blob)
    return re.sub(r"\s+", " ", blob)


def score_signals(text: str) -> tuple[int, int, int]:
    """Decline, grow, and hold weights from listed plan language."""
    blob = _outlook_text(text)
    if len(blob) < 80:
        return (0, 0, 0)
    preferred = " ".join(m.group(0) for m in _PREFERRED.finditer(blob[:24_000]))
    decline = 0
    grow = 0
    hold = 0
    for source, weight in ((preferred, 3), (blob, 1)):
        if not source.strip():
            continue
        decline += weight * _weighted_hits(_DECLINE_PATTERNS, source)
        grow += weight * _weighted_hits(_GROW_PATTERNS, source)
        hold += weight * _weighted_hits(_HOLD_PATTERNS, source, skip_negated=False)
    return (decline, grow, hold)


def trajectory_from_scores(decline: int, grow: int, hold: int) -> Trajectory | None:
    total = decline + grow + hold
    if total < 3:
        return None
    if hold and decline == 0 and grow == 0:
        raw = 0.0
    else:
        raw = (grow - decline) / max(decline + grow + hold, 1)
    position = max(-1.0, min(1.0, raw))
    return make_trajectory(_band_for(position), position)


def extract_trajectory(text: str) -> Trajectory | None:
    """Needle from listed plans. Weak or missing signal stays off the page."""
    decline, grow, hold = score_signals(text)
    return trajectory_from_scores(decline, grow, hold)


def combine_trajectories(excerpts: list[str]) -> Trajectory | None:
    """Newest listed plan first, then ALP, then earlier editions."""
    decline = 0
    grow = 0
    hold = 0
    for index, excerpt in enumerate(excerpts):
        weight = 4 if index == 0 else 3 if index == 1 else 1
        d, g, h = score_signals(excerpt)
        decline += d * weight
        grow += g * weight
        hold += h * weight
    return trajectory_from_scores(decline, grow, hold)


def _as_of(work) -> str | None:
    if work is None:
        return None
    edition = getattr(work, "edition", None)
    if edition:
        return str(edition)
    year = getattr(work, "study_year", None)
    return str(year) if year else None


def airport_overview(
    works: list,
    grant_lines: list[str] | None = None,
    airport=None,
) -> AirportOverview | None:
    merged: dict[str, str] = {}
    as_of = None
    first_as_of = None
    excerpts: list[str] = []
    for work in works:
        if work is None:
            continue
        excerpt = work_excerpt(work)
        if not excerpt.strip():
            continue
        excerpts.append(excerpt)
        if first_as_of is None:
            first_as_of = _as_of(work)
        facts = extract_facts(excerpt)
        if facts and as_of is None:
            as_of = _as_of(work)
        for key, value in facts.items():
            merged.setdefault(key, value)
    trajectory = combine_trajectories(excerpts)
    if grant_lines:
        grant_outlook = combine_trajectories([" ".join(grant_lines)])
        if trajectory is None:
            trajectory = grant_outlook
        elif grant_outlook is not None and trajectory.band == "maintaining" and grant_outlook.band != "maintaining":
            trajectory = grant_outlook
    if as_of is None and trajectory is not None:
        as_of = first_as_of
    facts = merge_fact_rows(merged)
    if not facts and trajectory is None:
        return apply_catalog_facts(None, airport)
    overview = AirportOverview(
        facts=facts,
        as_of=as_of,
        trajectory=trajectory,
    )
    return apply_catalog_facts(overview, airport)


def overview_is_stale(row: dict | None, now=None) -> bool:
    """True when missing or not generated this calendar month (Pacific)."""
    from datetime import datetime

    from pipeline.refresh import PACIFIC

    if not row:
        return True
    stamp = (row.get("generated_at") or "").strip()
    if not stamp:
        return True
    clock = now or datetime.now(PACIFIC)
    try:
        generated = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return True
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=PACIFIC)
    else:
        generated = generated.astimezone(PACIFIC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=PACIFIC)
    else:
        clock = clock.astimezone(PACIFIC)
    return (generated.year, generated.month) != (clock.year, clock.month)

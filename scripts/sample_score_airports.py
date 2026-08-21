"""Stratified airport identity sample. Not the scoring corpus. Not CI. Not a publish."""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catalog.geo import STATE_TO_FAA_REGION, US_STATES
from catalog.ourairports import OURAIRPORTS_CSV_URL, _lid_for_row, http_url

SEED = 20260820
SAMPLE_N = 200
OUT = ROOT / "catalog" / "references" / "score_airport_sample.json"
SIZE_FOR_TYPE = {
    "large_airport": "large",
    "medium_airport": "medium",
    "small_airport": "small",
    "seaplane_base": "small",
}
MUST_INCLUDE = (
    "PDX",
    "TTD",
    "HIO",
    "4S9",
    "4S2",
    "DEN",
    "BVY",
    "ANC",
    "AUS",
    "HNL",
    "LEX",
    "MKC",
)
# Per FAA region: large, medium, small. Totals 9 * (3+6+13) = 198, then pad.
QUOTA = {"large": 3, "medium": 6, "small": 13}


def fetch_csv() -> str:
    req = Request(
        OURAIRPORTS_CSV_URL,
        headers={"User-Agent": "aptplans.org eval (https://aptplans.org)"},
    )
    with urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_rows(text: str) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("iso_country") or "").strip().upper() != "US":
            continue
        kind = (row.get("type") or "").strip().lower()
        size = SIZE_FOR_TYPE.get(kind)
        if not size:
            continue
        region_code = (row.get("iso_region") or "").strip().upper()
        state = region_code.split("-")[-1] if region_code.startswith("US-") else ""
        if state not in US_STATES:
            continue
        lid = _lid_for_row(row)
        if not lid or lid in seen:
            continue
        website = http_url(row.get("home_link")) or ""
        iata = (row.get("iata_code") or "").strip().upper()
        if size == "small" and not website and not iata:
            continue
        seen.add(lid)
        found.append(
            {
                "lid": lid,
                "name": (row.get("name") or "").strip(),
                "city": (row.get("municipality") or "").strip(),
                "state": state,
                "size": size,
                "oa_type": kind,
                "faa_region": STATE_TO_FAA_REGION.get(state, "unknown"),
                "website": http_url(row.get("home_link")) or "",
                "iata": (row.get("iata_code") or "").strip().upper(),
                "lat": (row.get("latitude_deg") or "").strip(),
                "lon": (row.get("longitude_deg") or "").strip(),
            }
        )
    return found


def sample_airports(rows: list[dict], *, n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    by_lid = {row["lid"]: row for row in rows}
    picked: dict[str, dict] = {}
    for lid in MUST_INCLUDE:
        row = by_lid.get(lid)
        if row:
            picked[lid] = row
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        buckets[(row["faa_region"], row["size"])].append(row)
    for bucket in buckets.values():
        rng.shuffle(bucket)
    for region in sorted({row["faa_region"] for row in rows if row["faa_region"] != "unknown"}):
        for size, want in QUOTA.items():
            have = sum(1 for row in picked.values() if row["faa_region"] == region and row["size"] == size)
            for row in buckets.get((region, size), []):
                if have >= want or len(picked) >= n:
                    break
                if row["lid"] in picked:
                    continue
                picked[row["lid"]] = row
                have += 1
    leftover = [row for row in rows if row["lid"] not in picked]
    rng.shuffle(leftover)
    for row in leftover:
        if len(picked) >= n:
            break
        picked[row["lid"]] = row
    return sorted(picked.values(), key=lambda row: (row["state"], row["lid"]))[:n]


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a stratified airport identity sample")
    parser.add_argument("--n", type=int, default=SAMPLE_N)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    rows = parse_rows(fetch_csv())
    sample = sample_airports(rows, n=args.n, seed=args.seed)
    payload = {
        "description": (
            "Airport identity sample only. Not the scoring corpus. Full original "
            "plan, ALP, and hub bytes are labeled in score_gold.json."
        ),
        "seed": args.seed,
        "source": OURAIRPORTS_CSV_URL,
        "n": len(sample),
        "airports": sample,
    }
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"wrote": str(args.out), "n": len(sample)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

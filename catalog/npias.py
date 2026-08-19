"""Parse FAA NPIAS Appendix A. Origin fetches the spreadsheet; git does not store the list."""

from __future__ import annotations

import json
from pathlib import Path

from catalog.geo import STATE_NAME_TO_CODE
from catalog.xlsx import rows_from_xlsx

NPIAS_SOURCE = (
    "https://www.faa.gov/sites/faa.gov/files/airports/planning_capacity"
    "/npias/current/ARP-NPIAS-2025-2029-AppendixA.xlsx"
)
HUB_ROLES = {
    "L": "large_hub",
    "M": "medium_hub",
    "S": "small_hub",
    "N": "nonhub",
}
COL_STATE = 1
COL_CITY = 2
COL_NAME = 3
COL_LID = 4
COL_OWNERSHIP = 5
COL_SVC = 6
COL_HUB = 7
COL_ROLE = 8


def role_for(hub: str, role: str, service_level: str) -> str | None:
    if hub:
        return HUB_ROLES.get(hub, hub.lower())
    if role:
        return role.strip().lower()
    if service_level == "P":
        return "primary"
    if service_level == "R":
        return "reliever"
    if service_level == "CS":
        return "commercial_service"
    if service_level == "GA":
        return "general_aviation"
    return None


def parse_appendix_a_bytes(data: bytes) -> list[dict]:
    """Read FAA Appendix A sheet 'All NPIAS Airports'. Skip planned +LocID sites."""
    rows = rows_from_xlsx(data)
    records: list[dict] = []
    for index, row in sorted(rows.items()):
        if index == 1:
            continue
        lid = (row.get(COL_LID) or "").strip()
        if not lid or lid.startswith("+"):
            continue
        state_name = (row.get(COL_STATE) or "").strip()
        state = STATE_NAME_TO_CODE.get(state_name)
        if not state:
            raise ValueError(f"unknown NPIAS state name {state_name!r} for {lid}")
        hub = (row.get(COL_HUB) or "").strip() or None
        role = (row.get(COL_ROLE) or "").strip()
        service_level = (row.get(COL_SVC) or "").strip() or None
        records.append(
            {
                "lid": lid,
                "name": (row.get(COL_NAME) or "").strip(),
                "city": (row.get(COL_CITY) or "").strip(),
                "state": state,
                "npias_role": role_for(hub or "", role, service_level or ""),
                "service_level": service_level,
                "hub": hub,
                "ownership": (row.get(COL_OWNERSHIP) or "").strip() or None,
            }
        )
    records.sort(key=lambda item: (item["state"], item["lid"]))
    return records


def parse_appendix_a(xlsx_path: Path) -> list[dict]:
    return parse_appendix_a_bytes(xlsx_path.read_bytes())


def load_npias(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def write_npias(records: list[dict], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(item, ensure_ascii=True, separators=(",", ":")) for item in records]
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert FAA NPIAS Appendix A XLSX to JSONL",
        epilog=f"Current FAA file: {NPIAS_SOURCE}",
    )
    parser.add_argument("xlsx", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    records = parse_appendix_a(args.xlsx)
    write_npias(records, args.out)
    print(f"wrote {len(records)} NPIAS airports to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

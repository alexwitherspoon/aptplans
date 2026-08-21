"""Export human-labeled production outcomes as score_gold candidates. Not CI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.outcomes import export_gold_candidates, overlay_dir_from_env


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print gold-shaped cases from origin outcomes.jsonl labels"
    )
    parser.add_argument("--overlay", type=Path)
    args = parser.parse_args()
    cases = export_gold_candidates(args.overlay or overlay_dir_from_env())
    print(json.dumps({"n": len(cases), "cases": cases}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

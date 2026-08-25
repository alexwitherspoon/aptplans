"""Deterministic Oregon clean-cutover benchmark and gap report."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from pypdf import PdfReader

from catalog.grants import parse_aip_grants_bytes
from catalog.models import Airport, Budget, Document, Grant
from catalog.seed import seed_catalog_snapshot
from pipeline.domain_store import DomainStore, entity_key
from pipeline.parse import extract_pages
from pipeline.queue import JobQueue
from pipeline.refresh import ROOT
from pipeline.release_store import ReleaseStore

MANIFEST_PATH = ROOT / "catalog" / "references" / "oregon_benchmark.json"
REFERENCES = MANIFEST_PATH.parent
INCOMPLETE_MODALITIES = frozenset(
    {"missing", "normalized_fixture_only", "source_fixture_only"}
)
MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "benchmark_id",
        "frozen_at",
        "state",
        "scope",
        "network_policy",
        "model_policy",
        "repeat_clean_runs",
        "reference_inputs",
        "airports",
        "artifacts",
        "official_source_expectations",
        "funding_expectations",
        "required_modalities",
        "claims",
        "known_gaps",
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name}: expected object")
    return payload


def _verify_frozen_input(row: dict) -> Path:
    relative = Path(str(row["path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe benchmark input path: {relative}")
    path = REFERENCES / relative
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"benchmark input is not a regular file: {relative}")
    if path.stat().st_size != int(row["bytes"]):
        raise ValueError(f"{relative}: byte count changed")
    if _sha256(path) != row["sha256"]:
        raise ValueError(f"{relative}: SHA-256 changed")
    return path


def _validate_manifest(manifest: dict) -> None:
    if int(manifest.get("schema_version") or 0) != 1:
        raise ValueError("unsupported Oregon benchmark schema")
    unknown = set(manifest) - MANIFEST_FIELDS
    missing = MANIFEST_FIELDS - set(manifest)
    if unknown or missing:
        raise ValueError(
            f"invalid Oregon benchmark fields: missing={sorted(missing)} "
            f"unknown={sorted(unknown)}"
        )
    if manifest["state"] != "OR":
        raise ValueError("Oregon benchmark state must be OR")
    if manifest["network_policy"] != "forbidden":
        raise ValueError("Oregon benchmark must forbid network access")
    if manifest["model_policy"] != "forbidden":
        raise ValueError("Oregon benchmark must forbid model access")
    if int(manifest["repeat_clean_runs"]) < 2:
        raise ValueError("Oregon benchmark requires two clean runs")
    claims = manifest["claims"]
    if claims.get("oregon_complete") is not False:
        raise ValueError("benchmark cannot claim Oregon completeness")
    for name in (
        "milestone_4_oregon_vertical_proof",
        "milestone_5_statewide_expansion",
        "milestone_6_completion_contract",
    ):
        if claims.get(name) != "blocked":
            raise ValueError(f"benchmark must keep {name} blocked")
    for row in manifest["reference_inputs"]:
        _verify_frozen_input(row)


def _site_builder():
    path = ROOT / "site" / "build.py"
    spec = importlib.util.spec_from_file_location("aptplans_oregon_benchmark_site", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("site builder cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build


@contextmanager
def _benchmark_environment():
    keys = {
        "APP_ENV": "test",
        "APTPLANS_REFERENCE_SEED": "0",
        "APTPLANS_DEV_PREVIEW": "0",
        "APTPLANS_LLM": "0",
    }
    removed = ("APTPLANS_DOMAIN_STORE", "APTPLANS_DOMAIN_GENERATION", "MEILI_URL")
    previous = {key: os.environ.get(key) for key in (*keys, *removed)}
    try:
        os.environ.update(keys)
        for key in removed:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _reference_rows(manifest: dict) -> tuple[list[Airport], list[Document], list[Grant], list[Budget]]:
    wanted = set(manifest["airports"])
    artifacts = {
        str(row["document_id"]): row for row in manifest["artifacts"]
    }
    cases = _load_json(REFERENCES / "cases.json").get("cases") or []
    airports: list[Airport] = []
    documents: list[Document] = []
    for case in cases:
        if case.get("airport_lid") not in wanted:
            continue
        airports.append(
            Airport.from_dict({**case, "lid": str(case["airport_lid"])})
        )
        for row in case.get("documents") or []:
            document_id = str(row.get("id") or "")
            artifact = artifacts.get(document_id)
            if artifact is None:
                continue
            documents.append(
                Document.from_dict(
                    {
                        **row,
                        "content_sha256": artifact["sha256"],
                        "preserved_url": f"/files/{artifact['sha256']}.pdf",
                        "completeness": "complete",
                        "review_status": "pending",
                        "media": "pdf",
                    }
                )
            )
    grants = [
        Grant.from_dict(row)
        for row in (_load_json(REFERENCES / "grants.json").get("grants") or [])
        if row.get("airport_lid") in wanted
    ]
    budgets = [
        Budget.from_dict(row)
        for row in (_load_json(REFERENCES / "budgets.json").get("budgets") or [])
        if row.get("state") == "OR"
    ]
    if {airport.lid for airport in airports} != wanted:
        raise ValueError("benchmark airport denominator does not match reference cases")
    if {document.id for document in documents} != set(artifacts):
        raise ValueError("benchmark artifacts do not match reference documents")
    return airports, documents, grants, budgets


def _artifact_gate(manifest: dict, *, full: bool) -> dict:
    started = perf_counter()
    metrics: list[dict] = []
    for artifact in manifest["artifacts"]:
        path = _verify_frozen_input(artifact)
        size = path.stat().st_size
        extraction = artifact.get("extract")
        if full and artifact.get("extract_full_only"):
            extraction = artifact["extract_full_only"]
        row = {
            "id": artifact["id"],
            "modality": artifact["modality"],
            "bytes": size,
            "extracted": bool(extraction),
        }
        if extraction:
            pages = extract_pages(path.read_bytes())
            text = "\n".join(pages)
            normalized = text.lower()
            if len(pages) != int(extraction["pages"]):
                raise ValueError(f"{artifact['id']}: extracted page count changed")
            if len(text) < int(extraction["minimum_characters"]):
                raise ValueError(f"{artifact['id']}: extracted text fell below golden floor")
            missing = [
                phrase
                for phrase in extraction["required_phrases"]
                if str(phrase).lower() not in normalized
            ]
            if missing:
                raise ValueError(
                    f"{artifact['id']}: missing golden phrases: {', '.join(missing)}"
                )
            row.update({"pages": len(pages), "characters": len(text)})
        metrics.append(row)
    return {
        "status": "passed",
        "seconds": round(perf_counter() - started, 3),
        "artifacts": metrics,
    }


def _official_source_gate(manifest: dict, *, full: bool) -> dict:
    started = perf_counter()
    inputs = {
        str(row["path"]): row for row in manifest["reference_inputs"]
    }
    expectations = manifest["official_source_expectations"]

    workbook = expectations["faa_grant_workbook"]
    workbook_row = inputs.get(str(workbook["path"]))
    if workbook_row is None:
        raise ValueError("FAA grant workbook is not a frozen reference input")
    workbook_path = _verify_frozen_input(workbook_row)
    grants = parse_aip_grants_bytes(
        workbook_path.read_bytes(),
        fiscal_year=int(workbook["fiscal_year"]),
    )
    airport_grants = [
        grant
        for grant in grants
        if grant.airport_lid == str(workbook["airport_lid"])
    ]
    workbook_actual = {
        "total_grant_rows": len(grants),
        "airport_lid": str(workbook["airport_lid"]),
        "airport_grant_count": len(airport_grants),
        "airport_award_total": sum(
            int(grant.amount or 0) for grant in airport_grants
        ),
    }
    for key in ("total_grant_rows", "airport_grant_count", "airport_award_total"):
        if workbook_actual[key] != int(workbook[key]):
            raise ValueError(
                f"FAA grant workbook golden changed: "
                f"{key}={workbook_actual[key]}"
            )

    def extract_pdf(name: str, label: str) -> dict[str, object]:
        expected = expectations[name]
        frozen_row = inputs.get(str(expected["path"]))
        if frozen_row is None:
            raise ValueError(f"{label} is not a frozen reference input")
        path = _verify_frozen_input(frozen_row)
        actual: dict[str, object] = {
            "bytes": path.stat().st_size,
            "extracted": False,
        }
        if not full:
            return actual
        extraction = expected["extract_full_only"]
        pages = extract_pages(path.read_bytes())
        text = "\n".join(pages)
        normalized = text.lower()
        if len(pages) != int(extraction["pages"]):
            raise ValueError(f"{label} extracted page count changed")
        if len(text) < int(extraction["minimum_characters"]):
            raise ValueError(f"{label} text fell below golden floor")
        missing = [
            phrase
            for phrase in extraction["required_phrases"]
            if str(phrase).lower() not in normalized
        ]
        if missing:
            raise ValueError(
                f"{label} missing golden phrases: " + ", ".join(missing)
            )
        actual.update(
            {
                "extracted": True,
                "pages": len(pages),
                "characters": len(text),
            }
        )
        return actual

    budget_actual = extract_pdf("odav_budget_pdf", "ODAV budget PDF")
    scan_actual = extract_pdf("historical_plan_scan", "historical plan scan")
    brookings = expectations["brookings_airport_budget"]
    brookings_row = inputs.get(str(brookings["path"]))
    if brookings_row is None:
        raise ValueError("Brookings budget is not a frozen reference input")
    brookings_path = _verify_frozen_input(brookings_row)
    brookings_actual: dict[str, object] = {
        "bytes": brookings_path.stat().st_size,
        "inspected": False,
    }
    if full:
        reader = PdfReader(brookings_path)
        if len(reader.pages) != int(brookings["document_pages"]):
            raise ValueError("Brookings budget page count changed")
        producer = str((reader.metadata or {}).get("/Producer") or "")
        if str(brookings["producer_contains"]).lower() not in producer.lower():
            raise ValueError("Brookings budget scanner metadata changed")
        narrative_page = int(brookings["airport_narrative_page"])
        narrative = reader.pages[narrative_page - 1].extract_text() or ""
        if "airport budget 2025-26" not in narrative.lower():
            raise ValueError("Brookings airport budget narrative moved")
        image_metrics: list[dict[str, int]] = []
        minimum_pixels = int(brookings["minimum_full_page_image_pixels"])
        for page_number in brookings["airport_image_only_pages"]:
            page = reader.pages[int(page_number) - 1]
            if (page.extract_text() or "").strip():
                raise ValueError(
                    f"Brookings airport page {page_number} is no longer image-only"
                )
            image_pixels = [
                image.image.size[0] * image.image.size[1]
                for image in page.images
            ]
            largest = max(image_pixels, default=0)
            if largest < minimum_pixels:
                raise ValueError(
                    f"Brookings airport page {page_number} lost its full-page scan"
                )
            image_metrics.append(
                {"page": int(page_number), "largest_image_pixels": largest}
            )
        brookings_actual.update(
            {
                "inspected": True,
                "pages": len(reader.pages),
                "producer": producer,
                "airport_narrative_page": narrative_page,
                "airport_image_only_pages": image_metrics,
            }
        )
    return {
        "status": "passed",
        "seconds": round(perf_counter() - started, 3),
        "faa_grant_workbook": workbook_actual,
        "odav_budget_pdf": budget_actual,
        "historical_plan_scan": scan_actual,
        "brookings_airport_budget": brookings_actual,
    }


def _funding_gate(
    manifest: dict,
    grants: list[Grant],
    budgets: list[Budget],
) -> dict:
    expected = manifest["funding_expectations"]
    budget = next(
        item for item in budgets if item.id == expected["state_budget_id"]
    )
    payload = budget.to_dict()
    program_total = sum(
        int(line.get("amount") or 0)
        for line in payload["lines"]
        if line.get("group") == "program"
    )
    fund_total = sum(
        int(line.get("amount") or 0)
        for line in payload["lines"]
        if line.get("group") == "fund"
    )
    actual = {
        "state_budget_total": int(budget.total or 0),
        "state_budget_program_total": program_total,
        "state_budget_fund_total": fund_total,
        "pdx_grant_count": len(grants),
        "pdx_award_total": sum(int(item.amount or 0) for item in grants),
        "pdx_obligation_total": sum(int(item.obligated or 0) for item in grants),
        "pdx_outlay_total": sum(int(item.outlayed or 0) for item in grants),
    }
    for key, value in actual.items():
        if value != int(expected[key]):
            raise ValueError(f"funding golden changed: {key}={value}")
    if program_total != fund_total or program_total != int(budget.total or 0):
        raise ValueError("budget representations no longer reconcile")
    return {
        "status": "passed",
        **actual,
        "consolidated_budget_total": int(budget.total or 0),
        "double_counted_total": int(budget.total or 0) + program_total + fund_total,
    }


def _domain_release_once(
    airports: list[Airport],
    documents: list[Document],
    grants: list[Grant],
    budgets: list[Budget],
) -> dict:
    started = perf_counter()
    with TemporaryDirectory(prefix="aptplans-oregon-benchmark-") as raw:
        root = Path(raw)
        domain = DomainStore(root / "ledger")
        updates: dict[tuple[str, str], dict] = {}
        for entity_type, key_field, rows in (
            ("airports", "lid", [item.to_dict() for item in airports]),
            ("documents", "id", [item.to_dict() for item in documents]),
            ("grants", "grant_number", [item.to_dict() for item in grants]),
            ("budgets", "id", [item.to_dict() for item in budgets]),
        ):
            for row in rows:
                updates[(entity_type, entity_key(entity_type, row, key_field))] = row
        domain_snapshot = domain.commit(
            updates,
            reason="Oregon substrate benchmark",
            actor="benchmark",
            dataset_state={
                name: {"status": "ready", "rows": len(rows)}
                for name, rows in (
                    ("airports", airports),
                    ("documents", documents),
                    ("grants", grants),
                    ("budgets", budgets),
                )
            },
        )
        snapshot = seed_catalog_snapshot(ROOT / "catalog", domain_snapshot)
        if {item.lid for item in snapshot.catalog.airports} != {
            item.lid for item in airports
        }:
            raise ValueError("domain snapshot changed the Oregon airport denominator")
        expected_documents = {item.id for item in documents}
        if not expected_documents.issubset(snapshot.catalog.documents_by_id):
            raise ValueError("domain snapshot lost Oregon documents")
        for document_id in expected_documents:
            document = snapshot.catalog.documents_by_id[document_id]
            if document.review_status != "pending" or document.completeness != "complete":
                raise ValueError("replayed benchmark documents must remain complete and pending")

        build = _site_builder()
        releases = ReleaseStore(root / "ledger", root / "releases")

        def build_release(site: Path, _public_files: Path) -> None:
            if not build(site, catalog=snapshot.catalog):
                raise RuntimeError("benchmark site unexpectedly reported unchanged")

        manifest = releases.stage(domain_snapshot.generation_id, build_release)
        releases.activate(domain_snapshot.generation_id)
        current = releases.root / "current"
        for relative in (
            "site/index.html",
            "site/states/OR/index.html",
            "site/airports/PDX/index.html",
            "site/airports/TTD/index.html",
            "site/airports/4S9/index.html",
            "site/airports/4S2/index.html",
            "site/data/search.json",
        ):
            if not (current / relative).is_file():
                raise ValueError(f"release omitted {relative}")
        if manifest["public_files"]:
            raise ValueError("pending benchmark artifacts entered public files")
        search_rows = json.loads(
            (current / "site" / "data" / "search.json").read_text(encoding="utf-8")
        )
        public_ids = {str(row.get("id") or "") for row in search_rows}
        leaked = sorted(expected_documents & public_ids)
        if leaked:
            raise ValueError(
                f"pending benchmark artifacts entered static search: {', '.join(leaked)}"
            )
        for document_id in expected_documents:
            if (current / "site" / "documents" / document_id).exists():
                raise ValueError(f"pending document page was released: {document_id}")
        semantic = {
            "entities": [
                {
                    "entity_type": entity_type,
                    "entity_key": key,
                    "payload": payload,
                }
                for (entity_type, key), payload in sorted(updates.items())
            ],
            "site": [
                {
                    "path": row["path"],
                    "sha256": row["sha256"],
                    "bytes": row["bytes"],
                }
                for row in manifest["site"]
            ],
            "public_files": manifest["public_files"],
        }
        semantic_digest = hashlib.sha256(
            json.dumps(
                semantic,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "status": "passed",
            "seconds": round(perf_counter() - started, 3),
            "generation_id": domain_snapshot.generation_id,
            "airports": len(airports),
            "documents": len(documents),
            "grants": len(grants),
            "budgets": len(budgets),
            "site_files": len(manifest["site"]),
            "semantic_digest": semantic_digest,
            "ledger_integrity": JobQueue(domain.root).integrity_check(),
        }


def _domain_release_gate(
    airports: list[Airport],
    documents: list[Document],
    grants: list[Grant],
    budgets: list[Budget],
    *,
    repeat: int,
) -> dict:
    runs = [
        _domain_release_once(airports, documents, grants, budgets)
        for _index in range(repeat)
    ]
    digests = {str(row["semantic_digest"]) for row in runs}
    if len(digests) != 1:
        raise ValueError("independent clean runs produced different semantic results")
    return {
        "status": "passed",
        "repeat_clean_runs": repeat,
        "semantic_digest": next(iter(digests)),
        "runs": runs,
    }


def run(*, full: bool = False, require_complete_corpus: bool = False) -> dict:
    manifest = _load_json(MANIFEST_PATH)
    _validate_manifest(manifest)
    with _benchmark_environment():
        airports, documents, grants, budgets = _reference_rows(manifest)
        claims = dict(manifest["claims"])
        if not full:
            claims["milestone_2_clean_cutover_rerun"] = "core_smoke_only"
        result = {
            "benchmark_id": manifest["benchmark_id"],
            "frozen_at": manifest["frozen_at"],
            "scope": manifest["scope"],
            "artifact_gate": _artifact_gate(manifest, full=full),
            "official_source_gate": _official_source_gate(manifest, full=full),
            "funding_gate": _funding_gate(manifest, grants, budgets),
            "domain_release_gate": _domain_release_gate(
                airports,
                documents,
                grants,
                budgets,
                repeat=int(manifest["repeat_clean_runs"]),
            ),
            "modality_coverage": manifest["required_modalities"],
            "claims": claims,
            "known_gaps": manifest["known_gaps"],
        }
    gaps = [
        name
        for name, status in manifest["required_modalities"].items()
        if status in INCOMPLETE_MODALITIES
    ]
    if full:
        result["status"] = "passed" if not gaps else "passed_with_known_gaps"
    else:
        result["status"] = "core_smoke_passed"
    result["incomplete_modalities"] = gaps
    if require_complete_corpus and gaps:
        raise RuntimeError(
            "Oregon benchmark corpus is incomplete: " + ", ".join(gaps)
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="also extract large plan artifacts and the ODAV budget",
    )
    parser.add_argument(
        "--require-complete-corpus",
        action="store_true",
        help="fail while required source modalities remain missing",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = run(
            full=args.full,
            require_complete_corpus=args.require_complete_corpus,
        )
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
        return 1
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

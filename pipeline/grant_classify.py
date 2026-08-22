"""Enrich grant rows with rubric spend classification."""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from datetime import datetime, timezone

from catalog.grants import (
    effective_spend_category,
    grant_input_hash,
    grant_spend_category,
    merge_grant_spend,
    needs_llm_spend_classification,
)
from catalog.models import Grant
from catalog.store import load_grants_overlay, write_grants_overlay
from pipeline.classifications import record_classification
from pipeline.ollama import llm_calls_enabled
from pipeline.queries import classify_grant_spend

log = logging.getLogger("aptplans.grant_classify")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _grant_id(grant: Grant) -> str:
    return grant.grant_number or f"{grant.airport_lid}:{grant.fiscal_year}:{grant.description[:40]}"


def _llm_generate():
    if not llm_calls_enabled():
        return None
    try:
        from pipeline.ollama import generate

        return generate
    except Exception:
        log.exception("Ollama unavailable for grant spend classification")
        return None


def _classified_grant(
    grant: Grant,
    *,
    category: str,
    classifier: str,
    reason: str = "",
) -> Grant:
    return replace(
        grant,
        spend_category=category,
        spend_reason=reason or None,
        spend_classifier=classifier,
        spend_classified_at=_utc_now(),
        spend_input_hash=grant_input_hash(grant),
    )


def enrich_grant_spend(
    grant: Grant,
    *,
    generate_fn=None,
    overlay_dir=None,
    llm_enabled: bool | None = None,
    force: bool = False,
) -> Grant:
    """Hybrid: rules first, LLM for ambiguous or other rows when enabled."""
    input_hash = grant_input_hash(grant)
    if (
        not force
        and grant.spend_input_hash == input_hash
        and grant.spend_classifier in {"llm", "human"}
        and grant.spend_category
    ):
        return grant

    rule_category = grant_spend_category(grant)
    use_llm = llm_enabled if llm_enabled is not None else llm_calls_enabled()
    if not use_llm or generate_fn is None or not (grant.description or "").strip():
        return _classified_grant(grant, category=rule_category, classifier="rules")

    if not force and not needs_llm_spend_classification(grant, rule_category):
        return _classified_grant(grant, category=rule_category, classifier="rules")

    try:
        scored = classify_grant_spend(
            description=grant.description,
            generate_fn=generate_fn,
            lid=grant.airport_lid,
            fiscal_year=grant.fiscal_year,
            rule_category=rule_category,
        )
        category = scored["spend_category"]
        classifier = scored.get("classifier") or "llm"
        reason = scored.get("reason") or ""
    except Exception:
        log.exception("grant spend LLM failed grant=%s", _grant_id(grant))
        return _classified_grant(grant, category=rule_category, classifier="rules")

    updated = _classified_grant(
        grant,
        category=category,
        classifier=classifier,
        reason=reason,
    )
    if overlay_dir is not None:
        record_classification(
            overlay_dir,
            evaluation="grant_spend",
            input_id=_grant_id(grant),
            category=effective_spend_category(updated),
            classifier=classifier,
            reason=reason,
        )
    return updated


def enrich_grants(
    grants: list[Grant],
    *,
    generate_fn=None,
    overlay_dir=None,
    llm_enabled: bool | None = None,
    sleep=None,
    pause_seconds: float = 0,
    force: bool = False,
    prior_by_number: dict[str, Grant] | None = None,
) -> list[Grant]:
    prior = prior_by_number or {}
    rows = []
    for grant in grants:
        merged = merge_grant_spend(prior.get(grant.grant_number or ""), grant)
        rows.append(
            enrich_grant_spend(
                merged,
                generate_fn=generate_fn,
                overlay_dir=overlay_dir,
                llm_enabled=llm_enabled,
                force=force,
            )
        )
        if sleep and generate_fn is not None and pause_seconds:
            sleep(pause_seconds)
    return rows


def reclassify_grants_overlay(
    overlay_dir,
    *,
    force: bool = False,
    fetch_sleep=None,
    pause_seconds: float = 0,
) -> int:
    """Re-run spend classification on overlay grants without fetching FAA data."""
    grants = load_grants_overlay(overlay_dir)
    if not grants:
        return 0
    generate_fn = _llm_generate()
    enriched = enrich_grants(
        grants,
        generate_fn=generate_fn,
        overlay_dir=overlay_dir,
        llm_enabled=generate_fn is not None,
        sleep=fetch_sleep,
        pause_seconds=pause_seconds,
        force=force,
    )
    write_grants_overlay(overlay_dir, enriched)
    log.info("reclassified spend for %s grants (force=%s)", len(enriched), force)
    return len(enriched)


def apply_grant_spend_label(
    overlay_dir,
    *,
    grant_number: str,
    spend_category: str,
    reason: str = "",
) -> bool:
    """Human gold label for one grant row."""
    grants = load_grants_overlay(overlay_dir)
    if not grant_number:
        return False
    updated: list[Grant] = []
    found = False
    for grant in grants:
        if grant.grant_number != grant_number:
            updated.append(grant)
            continue
        found = True
        labeled = _classified_grant(
            grant,
            category=spend_category,
            classifier="human",
            reason=reason,
        )
        updated.append(labeled)
        record_classification(
            overlay_dir,
            evaluation="grant_spend",
            input_id=_grant_id(labeled),
            category=spend_category,
            classifier="human",
            reason=reason,
        )
    if not found:
        return False
    write_grants_overlay(overlay_dir, updated)
    return True


def grant_spend_spot_check_queue(overlay_dir) -> list[dict]:
    """LLM-classified grants in the other bucket for human review."""
    rows = []
    for grant in load_grants_overlay(overlay_dir):
        if grant.spend_classifier != "llm":
            continue
        if (grant.spend_category or "") != "other":
            continue
        rows.append(
            {
                "grant_number": grant.grant_number,
                "airport_lid": grant.airport_lid,
                "fiscal_year": grant.fiscal_year,
                "description": grant.description,
                "spend_category": grant.spend_category,
                "spend_reason": grant.spend_reason,
                "spend_classified_at": grant.spend_classified_at,
            }
        )
    return rows

"""Enrich grant rows with rubric spend classification."""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from datetime import datetime, timezone

from catalog.grants import (
    effective_spend_category,
    grant_spend_category,
    needs_llm_spend_classification,
)
from catalog.models import Grant
from pipeline.classifications import record_classification
from pipeline.queries import classify_grant_spend

log = logging.getLogger("aptplans.grant_classify")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _grant_id(grant: Grant) -> str:
    return grant.grant_number or f"{grant.airport_lid}:{grant.fiscal_year}:{grant.description[:40]}"


def enrich_grant_spend(
    grant: Grant,
    *,
    generate_fn=None,
    overlay_dir=None,
    llm_enabled: bool | None = None,
) -> Grant:
    """Hybrid: rules first, LLM for ambiguous or other rows when enabled."""
    rule_category = grant_spend_category(grant)
    if grant.spend_classifier in {"llm", "human"} and grant.spend_category:
        if not needs_llm_spend_classification(grant, rule_category):
            return grant

    use_llm = llm_enabled if llm_enabled is not None else os.environ.get("APTPLANS_LLM") == "1"
    if not use_llm or generate_fn is None or not (grant.description or "").strip():
        return replace(
            grant,
            spend_category=rule_category,
            spend_classifier="rules",
            spend_classified_at=_utc_now(),
        )

    if not needs_llm_spend_classification(grant, rule_category):
        return replace(
            grant,
            spend_category=rule_category,
            spend_classifier="rules",
            spend_classified_at=_utc_now(),
        )

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
        category = rule_category
        classifier = "rules"
        reason = ""

    updated = replace(
        grant,
        spend_category=category,
        spend_reason=reason or None,
        spend_classifier=classifier,
        spend_classified_at=_utc_now(),
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
) -> list[Grant]:
    rows = []
    for grant in grants:
        rows.append(
            enrich_grant_spend(
                grant,
                generate_fn=generate_fn,
                overlay_dir=overlay_dir,
                llm_enabled=llm_enabled,
            )
        )
        if sleep and generate_fn is not None and pause_seconds:
            sleep(pause_seconds)
    return rows

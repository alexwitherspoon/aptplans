from __future__ import annotations

from catalog.grants import effective_spend_category, grant_input_hash, needs_llm_spend_classification
from catalog.models import Grant
from catalog.store import write_grants_overlay
from pipeline.grant_classify import (
    apply_grant_spend_label,
    enrich_grant_spend,
    grant_spend_spot_check_queue,
    reclassify_grants_overlay,
)


def test_effective_spend_category_prefers_stored() -> None:
    grant = Grant(
        airport_lid="PDX",
        description="Improve Terminal",
        spend_category="growth",
    )
    assert effective_spend_category(grant) == "growth"


def test_needs_llm_for_other_and_ambiguous() -> None:
    other = Grant(airport_lid="PDX", description="Zero Emissions Infrastructure")
    assert needs_llm_spend_classification(other, "other") is True
    improve = Grant(airport_lid="PDX", description="Improve Terminal")
    assert needs_llm_spend_classification(improve, "maintenance") is True
    taxi = Grant(airport_lid="PDX", description="Reconstruct Taxiway")
    assert needs_llm_spend_classification(taxi, "maintenance") is False


def test_enrich_grant_spend_rules_only() -> None:
    grant = Grant(airport_lid="PDX", description="Reconstruct Taxiway")
    updated = enrich_grant_spend(grant, generate_fn=None, llm_enabled=False)
    assert updated.spend_category == "maintenance"
    assert updated.spend_classifier == "rules"


def test_enrich_grant_spend_llm_mock(tmp_path) -> None:
    grant = Grant(airport_lid="PDX", description="Improve Terminal")

    def generate(_prompt: str) -> str:
        return '{"spend_category":"maintenance","reason":"terminal rehab"}'

    updated = enrich_grant_spend(
        grant,
        generate_fn=generate,
        overlay_dir=tmp_path,
        llm_enabled=True,
    )
    assert updated.spend_category == "maintenance"
    assert updated.spend_classifier == "llm"


def test_enrich_grant_spend_skips_unchanged_hash(tmp_path) -> None:
    grant = Grant(
        airport_lid="PDX",
        grant_number="3-41-0048-094-2024",
        description="Improve Terminal",
        spend_category="maintenance",
        spend_classifier="llm",
        spend_input_hash=grant_input_hash(
            Grant(airport_lid="PDX", grant_number="3-41-0048-094-2024", description="Improve Terminal")
        ),
    )

    def generate(_prompt: str) -> str:
        raise AssertionError("LLM should not run when input hash matches")

    updated = enrich_grant_spend(grant, generate_fn=generate, overlay_dir=tmp_path, llm_enabled=True)
    assert updated.spend_category == "maintenance"
    assert updated.spend_classifier == "llm"


def test_reclassify_grants_overlay(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_LLM", "1")
    write_grants_overlay(
        tmp_path,
        [Grant(airport_lid="PDX", grant_number="G-1", description="Improve Terminal")],
    )

    def generate(_prompt: str) -> str:
        return '{"spend_category":"maintenance","reason":"terminal rehab"}'

    import pipeline.grant_classify as mod

    monkeypatch.setattr(mod, "_llm_generate", lambda: generate)
    count = reclassify_grants_overlay(tmp_path, pause_seconds=0)
    assert count == 1


def test_grant_spend_spot_check_queue(tmp_path) -> None:
    write_grants_overlay(
        tmp_path,
        [
            Grant(
                airport_lid="PDX",
                grant_number="G-1",
                description="Zero Emissions Infrastructure",
                spend_category="other",
                spend_classifier="llm",
            ),
            Grant(
                airport_lid="PDX",
                grant_number="G-2",
                description="Reconstruct Taxiway",
                spend_category="maintenance",
                spend_classifier="rules",
            ),
        ],
    )
    queue = grant_spend_spot_check_queue(tmp_path)
    assert len(queue) == 1
    assert queue[0]["grant_number"] == "G-1"


def test_apply_grant_spend_label(tmp_path) -> None:
    write_grants_overlay(
        tmp_path,
        [Grant(airport_lid="PDX", grant_number="G-1", description="Zero Emissions Infrastructure")],
    )
    assert apply_grant_spend_label(
        tmp_path,
        grant_number="G-1",
        spend_category="other",
        reason="human confirmed",
    )
    rows = grant_spend_spot_check_queue(tmp_path)
    assert rows == []

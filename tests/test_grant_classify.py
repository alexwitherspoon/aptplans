from __future__ import annotations

from catalog.grants import effective_spend_category, needs_llm_spend_classification
from catalog.models import Grant
from pipeline.grant_classify import enrich_grant_spend


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

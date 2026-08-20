from __future__ import annotations

import json

from pipeline.queries import (
    airport_query_families,
    award_list_queries,
    budget_queries,
    cip_queries,
    host_queries,
    law_queries,
    parse_json_object,
    search_queries,
    state_query_families,
    verify_candidate,
    verify_finance,
)


def test_budget_queries_name_the_agency() -> None:
    queries = budget_queries(state_name="Oregon", agency="Oregon Department of Aviation")
    assert any("Oregon Department of Aviation" in item and "legislatively adopted budget" in item for item in queries)
    assert any("site:.gov" in item for item in queries)
    assert not any("CIP" in item for item in queries)


def test_award_list_queries_are_not_budgets() -> None:
    queries = award_list_queries(state_name="Oregon", agency="Oregon Department of Aviation")
    assert any("grant" in item.lower() for item in queries)
    assert any("site:.gov" in item for item in queries)


def test_cip_queries_prefer_known_host() -> None:
    queries = cip_queries(
        name="Portland International",
        lid="PDX",
        website="https://www.portofportland.com/PDX",
    )
    assert queries[0].startswith("site:portofportland.com")
    assert any("capital improvement" in item.lower() or "CIP" in item for item in queries)


def test_law_queries_include_sasp() -> None:
    queries = law_queries(state_name="Oregon", agency="Oregon Department of Aviation")
    assert any("aviation system plan" in item.lower() for item in queries)


def test_search_queries_include_lid_and_filetype() -> None:
    queries = search_queries(name="Portland International", lid="PDX", city="Portland", state="OR")
    assert any("PDX" in item and "master plan" in item and "filetype:pdf" in item for item in queries)
    assert any("airport layout plan" in item for item in queries)
    assert any("Portland OR" in item for item in queries)


def test_host_queries_strip_www() -> None:
    queries = host_queries(
        website="https://www.portofportland.com/PDX",
        name="Portland International",
        lid="PDX",
    )
    assert queries
    assert all("site:portofportland.com" in item for item in queries)
    assert all("www." not in item.split()[0] for item in queries)


def test_airport_query_families_put_host_plans_first() -> None:
    families = airport_query_families(
        name="Portland International",
        lid="PDX",
        city="Portland",
        state="OR",
        website="https://www.portofportland.com/PDX",
    )
    assert families["plan"][0].startswith("site:portofportland.com")
    assert families["cip"]
    assert families["pfc"]


def test_state_query_families_cover_budget_awards_and_law() -> None:
    families = state_query_families(
        state_name="Oregon",
        agency="Oregon Department of Aviation",
        website="https://www.oregon.gov/aviation",
    )
    assert families["budget"][0].startswith("site:oregon.gov")
    assert families["awards"]
    assert families["law"]


def test_verify_candidate_uses_injected_generate() -> None:
    payload = {
        "official_plan": True,
        "kind": "master_plan",
        "same_airport": True,
        "publisher": "Port of Portland",
        "published_at": "2024-01-15",
        "pdf_urls": ["https://example.com/plan.pdf", "not-a-url"],
        "new_edition": True,
        "reason": "Title page names PDX.",
    }

    def generate(_prompt: str) -> str:
        return "Here you go\n" + json.dumps(payload)

    result = verify_candidate(
        lid="PDX",
        name="Portland International",
        url="https://example.com/hub",
        excerpt="<html>master plan</html>",
        generate_fn=generate,
    )
    assert result["official_plan"] is True
    assert result["kind"] == "master_plan"
    assert result["pdf_urls"] == ["https://example.com/plan.pdf"]
    assert result["publisher"] == "Port of Portland"
    assert result["new_edition"] is True


def test_verify_finance_drops_amounts_and_unknown_kinds() -> None:
    payload = {
        "official_finance": True,
        "finance_kind": "project_list",
        "scope": "state",
        "same_entity": True,
        "publisher": "Oregon Department of Aviation",
        "published_at": "2025-10-02",
        "pdf_urls": ["https://example.gov/awards.pdf"],
        "has_locid_rows": True,
        "reason": "Award table names LocIDs.",
        "amount": 15409728,
        "total": 45874157,
    }

    def generate(prompt: str) -> str:
        assert "Do not include dollar amounts" in prompt
        return json.dumps(payload)

    result = verify_finance(
        url="https://example.gov/awards.pdf",
        excerpt="ASAP awards by airport",
        lid="",
        name="",
        state="OR",
        generate_fn=generate,
    )
    assert result["official_finance"] is True
    assert result["finance_kind"] == "project_list"
    assert result["scope"] == "state"
    assert result["has_locid_rows"] is True
    assert "amount" not in result
    assert "total" not in result


def test_verify_candidate_marks_budget_as_not_plan() -> None:
    def generate(_prompt: str) -> str:
        return json.dumps(
            {
                "official_plan": False,
                "kind": "not_plan",
                "same_airport": False,
                "publisher": "Oregon Department of Aviation",
                "published_at": None,
                "pdf_urls": [],
                "new_edition": False,
                "reason": "Legislatively adopted budget, not an ALP.",
            }
        )

    result = verify_candidate(
        lid="PDX",
        name="Portland International",
        url="https://example.gov/lab.pdf",
        excerpt="2025-27 Legislatively Adopted Budget",
        generate_fn=generate,
    )
    assert result["official_plan"] is False
    assert result["kind"] == "not_plan"


def test_parse_json_object_extracts_embedded_object() -> None:
    assert parse_json_object('noise {"official_plan": false} trailing') == {"official_plan": False}

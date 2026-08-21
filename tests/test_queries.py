from __future__ import annotations

import json

from pipeline.queries import (
    airport_query_families,
    allowed_hit_urls,
    award_list_queries,
    budget_queries,
    cip_queries,
    evaluate_search_hit,
    evaluate_search_hints,
    host_queries,
    law_queries,
    packet_urls,
    parse_json_object,
    search_hit_prompt,
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
    assert any("AMP" in item and "airport master plan" in item for item in queries)
    assert any("airport layout plan" in item for item in queries)
    assert any("airport diagram" in item for item in queries)
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


def test_allowed_hit_urls_drop_off_host_prose_links() -> None:
    alp = "https://www.oregon.gov/aviation/airports/Documents/4S9/ODA_Doc_4S9_ALP.pdf"
    fake = "https://example.invalid/made-up-4S9-master-plan.pdf"
    trusted = allowed_hit_urls(
        artifact_url=alp,
        prose=f"Also try {fake}",
    )
    assert alp in trusted
    assert fake not in trusted
    assert fake in packet_urls(artifact_url=alp, prose=f"Also try {fake}")


def test_evaluate_search_hit_keeps_page_url_and_strips_on_no() -> None:
    page = "https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx"
    alp = "https://www.oregon.gov/aviation/airports/Documents/4S9/ODA_Doc_4S9_ALP.pdf"
    fake = "https://example.invalid/nope.pdf"

    def generate_yes(_prompt: str) -> str:
        assert "Airport Diagram" in search_hit_prompt(
            lid="4S9",
            name="Mulino State Airport",
            query="4S9 ALP",
            artifact_url=alp,
            page_url=page,
            prose="Airport Diagram",
        )
        return json.dumps(
            {
                "same_airport": True,
                "hit_type": "artifact",
                "kind_guess": "alp",
                "artifact_urls": [alp, fake],
                "page_urls": [],
                "fetch": "yes",
                "reason": "ALP PDF.",
            }
        )

    yes = evaluate_search_hit(
        lid="4S9",
        name="Mulino State Airport",
        query="4S9 ALP",
        generate_fn=generate_yes,
        artifact_url=alp,
        page_url=page,
        prose=f"Airport Diagram. Ignore {fake}",
        city="Mulino",
        state="OR",
    )
    assert yes["fetch"] == "yes"
    assert alp in yes["artifact_urls"]
    assert page in yes["page_urls"]
    assert fake not in yes["artifact_urls"]

    def generate_no(_prompt: str) -> str:
        return json.dumps(
            {
                "same_airport": True,
                "hit_type": "not_plan",
                "kind_guess": "unknown",
                "artifact_urls": [alp],
                "page_urls": [page],
                "fetch": "no",
                "reason": "NEPA EA.",
            }
        )

    no = evaluate_search_hit(
        lid="4S9",
        name="Mulino State Airport",
        query="4S9 EA",
        generate_fn=generate_no,
        artifact_url=alp,
        page_url=page,
        prose="Draft Environmental Assessment",
    )
    assert no["fetch"] == "no"
    assert no["artifact_urls"] == []
    assert no["page_urls"] == []


def test_evaluate_search_hit_ssi_filename_needs_human() -> None:
    def generate(_prompt: str) -> str:
        return json.dumps(
            {
                "same_airport": True,
                "hit_type": "artifact",
                "kind_guess": "alp",
                "artifact_urls": ["https://example.com/PDX_ALP_SSI_sheet.pdf"],
                "page_urls": [],
                "fetch": "yes",
                "reason": "Looks like an ALP.",
            }
        )

    result = evaluate_search_hit(
        lid="PDX",
        name="Portland International Airport",
        query="PDX ALP",
        generate_fn=generate,
        artifact_url="https://example.com/PDX_ALP_SSI_sheet.pdf",
        prose="ALP sheet",
    )
    assert result["fetch"] == "needs_human"
    assert result["artifact_urls"] == []


def test_evaluate_search_hit_invalid_json_is_needs_human() -> None:
    result = evaluate_search_hit(
        lid="4S9",
        name="Mulino State Airport",
        query="4S9",
        generate_fn=lambda _prompt: "not json",
        artifact_url="https://www.oregon.gov/aviation/airports/Documents/4S9/ODA_Doc_4S9_ALP.pdf",
    )
    assert result["fetch"] == "needs_human"
    assert result["artifact_urls"] == []


def _search_hit(payload: dict) -> str:
    return json.dumps(payload)


def test_evaluate_search_hit_fetches_hub_page_without_pdf() -> None:
    page = "https://www.portofhoodriver.com/airport-master-plan"

    def generate(_prompt: str) -> str:
        return _search_hit(
            {
                "same_airport": True,
                "hit_type": "hub_page",
                "kind_guess": "master_plan",
                "artifact_urls": [],
                "page_urls": [],
                "fetch": "no",
                "reason": "No artifact URLs provided",
            }
        )

    result = evaluate_search_hit(
        lid="4S2",
        name="Ken Jernstedt Airfield",
        query="Ken Jernstedt Airfield master plan",
        generate_fn=generate,
        page_url=page,
        prose="Port of Hood River Airport Master Plan page for Ken Jernstedt Airfield (4S2).",
    )
    assert result["fetch"] == "yes"
    assert result["page_urls"] == [page]
    assert result["artifact_urls"] == []


def test_evaluate_search_hit_rejects_agency_budget_as_airport_plan() -> None:
    def generate(_prompt: str) -> str:
        return _search_hit(
            {
                "same_airport": True,
                "hit_type": "artifact",
                "kind_guess": "master_plan",
                "artifact_urls": ["https://www.oregon.gov/aviation/example/2025-27-LAB.pdf"],
                "page_urls": [],
                "fetch": "yes",
                "reason": "Budget document for PDX airport plan.",
            }
        )

    result = evaluate_search_hit(
        lid="PDX",
        name="Portland International Airport",
        query="PDX master plan budget",
        generate_fn=generate,
        artifact_url="https://www.oregon.gov/aviation/example/2025-27-LAB.pdf",
        prose="Oregon Department of Aviation 2025-27 Legislatively Adopted Budget. Not an ALP.",
    )
    assert result["same_airport"] is False
    assert result["fetch"] == "no"
    assert result["hit_type"] == "not_plan"
    assert result["artifact_urls"] == []


def test_evaluate_search_hit_gates_ea_and_wikipedia_off_fetch() -> None:
    def generate_ea(_prompt: str) -> str:
        return _search_hit(
            {
                "same_airport": True,
                "hit_type": "artifact",
                "kind_guess": "master_plan",
                "artifact_urls": [
                    "https://www.oregon.gov/aviation/Airports/Documents/4S9/Projects/Draft%20EA%206-11-2019%20completePart-1.pdf"
                ],
                "page_urls": [],
                "fetch": "yes",
                "reason": "Looks like a plan PDF.",
            }
        )

    ea = evaluate_search_hit(
        lid="4S9",
        name="Mulino State Airport",
        query="4S9 master plan 2019",
        generate_fn=generate_ea,
        artifact_url="https://www.oregon.gov/aviation/Airports/Documents/4S9/Projects/Draft%20EA%206-11-2019%20completePart-1.pdf",
        prose="Draft Environmental Assessment June 2019. NEPA EA, not a master plan.",
    )
    assert ea["same_airport"] is True
    assert ea["fetch"] == "no"
    assert ea["hit_type"] == "not_plan"

    def generate_wiki(_prompt: str) -> str:
        return _search_hit(
            {
                "same_airport": True,
                "hit_type": "hub_page",
                "kind_guess": "unknown",
                "artifact_urls": [],
                "page_urls": ["https://en.wikipedia.org/wiki/Mulino_State_Airport"],
                "fetch": "yes",
                "reason": "Airport page.",
            }
        )

    wiki = evaluate_search_hit(
        lid="4S9",
        name="Mulino State Airport",
        query="Mulino State Airport 4S9",
        generate_fn=generate_wiki,
        page_url="https://en.wikipedia.org/wiki/Mulino_State_Airport",
        prose="Mulino State Airport (FAA LID: 4S9) Wikipedia encyclopedia article.",
    )
    assert wiki["same_airport"] is True
    assert wiki["fetch"] == "no"
    assert wiki["hit_type"] == "not_plan"
    assert wiki["page_urls"] == []


def test_evaluate_search_hit_relabels_html_only_hub() -> None:
    page = "https://pdx2045.org/"

    def generate(_prompt: str) -> str:
        return _search_hit(
            {
                "same_airport": True,
                "hit_type": "not_plan",
                "kind_guess": "master_plan",
                "artifact_urls": [],
                "page_urls": [page],
                "fetch": "yes",
                "reason": "Official airport microsite.",
            }
        )

    result = evaluate_search_hit(
        lid="PDX",
        name="Portland International Airport",
        query="PDX 2045 master plan",
        generate_fn=generate,
        page_url=page,
        prose="PDX 2045 Master Plan Update. Port of Portland planning microsite.",
    )
    assert result["fetch"] == "yes"
    assert result["hit_type"] == "hub_page"
    assert result["page_urls"] == [page]


def _hint_hits() -> list[dict[str, str]]:
    return [
        {
            "title": "Chapter Two INVENTORY",
            "url": "https://www.oregon.gov/aviation/Airports/Documents/4S9/Master%20Plan/2008/Chapter%202%20-%20Inventory.pdf",
            "snippet": "Chapter Two Airport Master Plan Update INVENTORY Mulino Airport.",
        },
        {
            "title": "ODAV Easement Acquisition Projects Overview",
            "url": "https://www.oregon.gov/aviation/state-aviation-board/Documents/2025/11_06/ODAV%20Easement%20Acquisition%20CWE%20Projects%20Overview%202025-11-06.pdf",
            "snippet": "Obstructions confirmed in AMP (2019) at Mulino State Airport 4S9.",
        },
    ]


def test_evaluate_search_hints_keeps_lid_host_and_drops_urls() -> None:
    def generate(_prompt: str) -> str:
        return json.dumps(
            {
                "stop": False,
                "queries": [
                    {
                        "query": "https://www.oregon.gov/aviation/airports/Documents/4S9/Master%20Plan/2019/Mulino%20Final%20AMP%20July%202019.pdf",
                        "why": "Invented path",
                    },
                    {
                        "query": 'site:example.com 4S9 "master plan"',
                        "why": "Off host",
                    },
                    {
                        "query": "site:oregon.gov 4S9 2019 AMP",
                        "why": "Snippet names AMP 2019",
                    },
                ],
                "reason": "Need later whole file",
            }
        )

    result = evaluate_search_hints(
        lid="4S9",
        name="Mulino State Airport",
        generate_fn=generate,
        hits=_hint_hits(),
        ran_queries=['"Mulino State Airport" 4S9 "master plan"'],
        missing=["whole_plan", "alp"],
        website="https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx",
        state="OR",
    )
    assert [item["query"] for item in result["queries"]] == ["site:oregon.gov 4S9 2019 AMP"]
    assert result["stop"] is False


def test_evaluate_search_hints_drops_year_not_in_packet() -> None:
    def generate(_prompt: str) -> str:
        return json.dumps(
            {
                "stop": False,
                "queries": [{"query": "4S9 master plan 2046", "why": "Invented year"}],
                "reason": "bad year",
            }
        )

    result = evaluate_search_hints(
        lid="4S9",
        name="Mulino State Airport",
        generate_fn=generate,
        hits=_hint_hits(),
        website="https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx",
    )
    assert result["queries"] == []


def test_evaluate_search_hints_locks_snippet_host() -> None:
    def generate(_prompt: str) -> str:
        return json.dumps(
            {
                "stop": False,
                "queries": [
                    {
                        "query": 'site:pdx2045.org PDX "master plan"',
                        "why": "Packet names the microsite",
                    }
                ],
                "reason": "Port packet points at pdx2045.org",
            }
        )

    result = evaluate_search_hints(
        lid="PDX",
        name="Portland International Airport",
        generate_fn=generate,
        hits=[
            {
                "title": "Commission agenda",
                "url": "https://cdn.portofportland.com/commission/May%202026%20Commission%20Agenda.pdf",
                "snippet": "PDX 2045 Master Plan. Visit pdx2045.org to learn more.",
            }
        ],
        ran_queries=['site:portofportland.com PDX "master plan"'],
        missing=["whole_plan", "alp"],
        website="https://www.portofportland.com/PDX",
    )
    assert result["queries"][0]["query"] == 'site:pdx2045.org PDX "master plan"'


def test_evaluate_search_hints_invalid_json_stops() -> None:
    result = evaluate_search_hints(
        lid="4S9",
        name="Mulino State Airport",
        generate_fn=lambda _prompt: "not json",
        hits=_hint_hits(),
    )
    assert result["queries"] == []
    assert result["stop"] is True

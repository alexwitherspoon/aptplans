from __future__ import annotations

from pipeline.search_client import fixture_search, load_fixture
from pipeline.search_plan import (
    SearchHit,
    SearchIdentity,
    extract_signals,
    followup_steps,
    hit_worth_confirm,
    hit_worth_explore,
    run_search_plan,
    seed_steps,
)
from pipeline.stages import worth_confirm

MULINO = SearchIdentity(
    lid="4S9",
    name="Mulino State Airport",
    city="Mulino",
    state="OR",
    website="https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx",
)
PDX = SearchIdentity(
    lid="PDX",
    name="Portland International Airport",
    city="Portland",
    state="OR",
    website="https://www.portofportland.com/PDX",
)


def test_seed_steps_do_not_start_with_filetype_pdf() -> None:
    steps = seed_steps(MULINO)
    assert steps[0].kind == "open_web"
    assert "filetype:pdf" not in steps[0].query
    assert steps[0].query == '"Mulino State Airport" 4S9 "master plan"'
    assert steps[1].query.startswith("site:oregon.gov")


def test_board_presentation_is_not_a_confirm() -> None:
    hit = SearchHit(
        title="Airports Update",
        url="https://www.oregon.gov/aviation/state-aviation-board/Documents/2026/04_02/Presentations/2026-4%20Airports%20Presentation.pdf",
        snippet="Mulino hangar construction",
    )
    assert hit_worth_confirm(hit) is False
    assert worth_confirm(role="artifact", kind_guess="unknown", label=f"{hit.title} {hit.url}") is False


def test_odav_airport_page_is_worth_exploring() -> None:
    hit = SearchHit(
        title="Aviation : Mulino State Airport [4S9] : Airports : State of Oregon",
        url="https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx",
        snippet="Owned by the Oregon Department of Aviation.",
    )
    assert hit_worth_explore(hit, MULINO) is True
    assert hit_worth_confirm(hit) is False


def test_pdx_snippet_host_locks_followup_search() -> None:
    hits = [
        SearchHit(
            title="Commission agenda",
            url="https://cdn.portofportland.com/commission/May%202026%20Commission%20Agenda.pdf",
            snippet="PDX 2045 Master Plan. Visit pdx2045.org to learn more.",
        )
    ]
    signals = extract_signals(hits, PDX)
    assert any(host == "pdx2045.org" for host in signals.hosts)
    ran = {step.query for step in seed_steps(PDX)}
    follow = followup_steps(PDX, signals, ran)
    assert any("site:pdx2045.org" in step.query and "PDX" in step.query for step in follow)


def test_fixture_ladder_finds_4s9_hub_and_skips_decks() -> None:
    session = run_search_plan(MULINO, fixture_search)
    assert session.queries[0] == '"Mulino State Airport" 4S9 "master plan"'
    assert any("mulino-4s9.aspx" in hit.url for hit in session.hits)
    confirms = [hit.url for hit in session.hits if hit_worth_confirm(hit)]
    assert any("Chapter%202" in url for url in confirms)
    assert not any("Presentation" in url for url in confirms)
    assert any(hit_worth_explore(hit, MULINO) for hit in session.hits)
    assert len(session.queries) == 2


def test_fixture_queries_cover_seed_strings() -> None:
    fixture = load_fixture()
    for identity in (MULINO, PDX):
        for step in seed_steps(identity):
            assert step.query in fixture, step.query


def test_gated_hint_runs_once_after_hub_when_alp_missing() -> None:
    def generate(_prompt: str) -> str:
        return (
            '{"stop": false, "queries": ['
            '{"query": "site:oregon.gov 4S9 2019 AMP", "why": "Snippet names AMP 2019"}],'
            ' "reason": "Need later whole file"}'
        )

    session = run_search_plan(MULINO, fixture_search, generate_fn=generate)
    assert any(round.step.kind == "llm_hint" for round in session.rounds)
    assert session.queries.count("site:oregon.gov 4S9 2019 AMP") == 1
    assert sum(1 for round in session.rounds if round.step.kind == "llm_hint") == 1

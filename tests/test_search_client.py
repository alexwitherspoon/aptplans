from __future__ import annotations

from pipeline.search_client import (
    charge_search,
    gemini_escalate,
    gemini_query_cap,
    hits_from_gemini_payload,
    live_search_enabled,
    load_search_meter,
    brave_query_cap,
    search_provider,
)
from pipeline.search_plan import (
    SearchHit,
    SearchIdentity,
    SearchSession,
    SearchSignals,
    SearchStep,
    SearchRound,
    done_searching,
    hit_worth_confirm,
    run_search_plan,
)

MULINO = SearchIdentity(
    lid="4S9",
    name="Mulino State Airport",
    city="Mulino",
    state="OR",
    website="https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx",
)

GEMINI_4S9 = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "text": (
                            '{"hits":['
                            '{"title":"Mulino State Airport",'
                            '"url":"https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx",'
                            '"snippet":"ODAV airport page"},'
                            '{"title":"Master Plan Document",'
                            '"url":"https://www.oregon.gov/aviation/Airports/Documents/4S9/Projects/Draft%20EA%206-11-2019%20completePart-2.pdf",'
                            '"snippet":"Gemini called this a master plan"}'
                            "]}"
                        )
                    }
                ]
            },
            "groundingMetadata": {
                "groundingChunks": [
                    {
                        "web": {
                            "uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
                            "title": "oregon.gov",
                        }
                    },
                    {
                        "web": {
                            "uri": "https://www.oregon.gov/aviation/airports/Documents/4S9/ODA_Doc_4S9_ALP.pdf",
                            "title": "ALP",
                        }
                    },
                ]
            },
        }
    ]
}


def test_gemini_payload_keeps_destination_urls_drops_redirects() -> None:
    hits = hits_from_gemini_payload(GEMINI_4S9, query='"Mulino State Airport" 4S9 "master plan"')
    urls = [hit.url for hit in hits]
    assert "https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx" in urls
    assert "https://www.oregon.gov/aviation/airports/Documents/4S9/ODA_Doc_4S9_ALP.pdf" in urls
    assert not any("vertexaisearch" in url for url in urls)


def test_gemini_prose_does_not_classify_an_ea_as_confirm() -> None:
    hits = hits_from_gemini_payload(GEMINI_4S9, query="4S9")
    ea = next(hit for hit in hits if "Draft%20EA" in hit.url)
    assert "master plan" not in ea.title.lower()
    assert ea.snippet == ""
    assert hit_worth_confirm(ea) is False


def test_escalate_runs_only_when_brave_stalls() -> None:
    called = {"n": 0}

    def search(_query: str) -> list[SearchHit]:
        return []

    def escalate(_identity, _session) -> list[SearchHit]:
        called["n"] += 1
        return [
            SearchHit(
                title="Mulino",
                url="https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx",
                snippet="Owned by ODAV",
                query="escalate",
            )
        ]

    stalled = run_search_plan(MULINO, search, escalate_fn=escalate)
    assert called["n"] == 1
    assert any(round.step.kind == "escalate" for round in stalled.rounds)
    assert stalled.signals.has_hub is True

    def hub_search(_query: str) -> list[SearchHit]:
        return [
            SearchHit(
                title="Mulino State Airport",
                url="https://www.oregon.gov/aviation/airports/pages/mulino-4s9.aspx",
                snippet="ODAV",
            )
        ]

    called["n"] = 0
    found = run_search_plan(MULINO, hub_search, escalate_fn=escalate)
    assert called["n"] == 0
    assert done_searching(found.signals)
    assert not any(round.step.kind == "escalate" for round in found.rounds)


def test_brave_query_cap_is_25_dollar_budget(monkeypatch) -> None:
    monkeypatch.delenv("APTPLANS_SEARCH_MONTHLY_CAP", raising=False)
    monkeypatch.delenv("APTPLANS_BRAVE_MONTHLY_BUDGET_USD", raising=False)
    monkeypatch.delenv("APTPLANS_BRAVE_MONTHLY_CREDIT_USD", raising=False)
    monkeypatch.delenv("APTPLANS_BRAVE_USD_PER_1K", raising=False)
    assert brave_query_cap() == 6000
    monkeypatch.setenv("APTPLANS_SEARCH_MONTHLY_CAP", "100")
    assert brave_query_cap() == 100


def test_gemini_query_cap_is_25_dollar_budget(monkeypatch) -> None:
    monkeypatch.delenv("APTPLANS_GEMINI_MONTHLY_CAP", raising=False)
    monkeypatch.delenv("APTPLANS_GEMINI_MONTHLY_BUDGET_USD", raising=False)
    monkeypatch.delenv("APTPLANS_GEMINI_USD_PER_1K", raising=False)
    monkeypatch.delenv("APTPLANS_GEMINI_FREE_PROMPTS", raising=False)
    monkeypatch.delenv("APTPLANS_GEMINI_QUERIES_PER_PROMPT", raising=False)
    # 5,000 free + int(25/14*1000/4) paid overage = 5,446 escalate prompts.
    assert gemini_query_cap() == 5446
    monkeypatch.setenv("APTPLANS_GEMINI_MONTHLY_CAP", "10")
    assert gemini_query_cap() == 10


def test_search_meter_respects_monthly_cap(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    monkeypatch.setenv("APTPLANS_GEMINI_MONTHLY_CAP", "1")
    assert charge_search("gemini") is True
    assert charge_search("gemini") is False
    meter = load_search_meter()
    assert meter["gemini"] == 1


def test_gemini_escalate_noop_without_key(monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_GEMINI_KEY", "")
    empty = SearchSession(
        identity=MULINO,
        rounds=[
            SearchRound(
                step=SearchStep(kind="open_web", query="q", why="seed"),
                hits=[],
            )
        ],
        signals=SearchSignals(),
    )
    assert gemini_escalate(MULINO, empty) == []


def test_live_search_enabled_on_production(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("APTPLANS_LIVE_SEARCH", raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert live_search_enabled() is True
    assert search_provider() == "brave"


def test_live_search_disabled_in_ci(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("APTPLANS_LIVE_SEARCH", raising=False)
    assert live_search_enabled() is False
    assert search_provider() == "fixture"


def test_live_search_local_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("APTPLANS_LIVE_SEARCH", "1")
    assert live_search_enabled() is True

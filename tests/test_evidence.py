from __future__ import annotations

from catalog.geo import FAA_AIRPORTS_REGIONS, STATE_TO_FAA_REGION, US_STATES
from pipeline.evidence import (
    Packet,
    features,
    gold_source_path,
    load_gold_packets,
    load_score_gold,
    load_score_sample,
    packet_from_gold,
    score_packet,
)


def test_filename_ea_is_heavy_not_infinite() -> None:
    packet = Packet(
        lid="4S9",
        name="Mulino State Airport",
        url="https://www.oregon.gov/aviation/Airports/Documents/4S9/Projects/Draft%20EA%206-11-2019%20completePart-2.pdf",
        label="Draft Environmental Assessment",
    )
    feats = features(packet)
    assert feats["filename_ea"] == 1.0
    assert feats["ssi_name"] == 0.0
    scored = score_packet(packet)
    assert scored["kind"] == "not_plan"
    assert scored["confirm"] is False
    assert scored["publish"] is False


def test_abstract_alp_filename_still_scores_alp() -> None:
    packet = Packet(
        lid="4S9",
        name="Mulino State Airport",
        url="https://www.oregon.gov/aviation/airports/Documents/4S9/ODA_Doc_4S9_ALP.pdf",
        label="Airport Diagram",
    )
    feats = features(packet)
    assert feats["filename_alp"] == 1.0
    assert feats["filename_abstract"] == 1.0
    scored = score_packet(packet)
    assert scored["kind"] == "alp"
    assert scored["confirm"] is True


def test_full_fixture_bytes_label_a_silent_filename() -> None:
    row = next(case for case in load_score_gold()["cases"] if case["id"] == "ttd-bound")
    assert gold_source_path(row) is not None
    packet = packet_from_gold(row, cache=False)
    assert len(packet.body) > 1000
    assert features(packet)["text_amp"] == 1.0
    assert score_packet(packet)["kind"] == "master_plan"


def test_gold_packets_match_frozen_weights() -> None:
    misses = []
    for case in load_score_gold()["cases"]:
        scored = score_packet(packet_from_gold(case, cache=False))
        gold = case["gold"]
        for field in ("same_airport", "kind", "confirm", "explore", "publish"):
            if scored[field] != gold[field]:
                misses.append(f"{case['id']}.{field}: got {scored[field]!r} want {gold[field]!r}")
    assert misses == []


def test_gold_does_not_store_excerpts() -> None:
    for case in load_score_gold()["cases"]:
        assert "excerpt" not in case
        assert "outline" not in case
        assert "body" not in case


def test_load_gold_packets_appends_outcome_cases() -> None:
    n = len(load_score_gold()["cases"])
    extra = [
        {
            "id": "outcome-extra",
            "lid": "XYZ",
            "name": "Example Field",
            "url": "https://aviation.example.gov/xyz-master-plan.pdf",
            "label": "Master Plan",
            "gold": {
                "same_airport": True,
                "kind": "master_plan",
                "confirm": True,
                "explore": False,
                "publish": True,
            },
        }
    ]
    loaded = load_gold_packets(cache=False, extra=extra)
    assert len(loaded) == n + 1
    assert loaded[-1][0]["id"] == "outcome-extra"


def test_gold_training_sources_are_full_originals() -> None:
    found = 0
    for case in load_score_gold()["cases"]:
        if not case.get("fixture") and not case.get("source"):
            continue
        path = gold_source_path(case, cache=False)
        assert path is not None, case["id"]
        assert path.is_file(), case["id"]
        data = path.read_bytes()
        assert len(data) > 800, case["id"]
        assert not data.lstrip().startswith(b"..."), case["id"]
        found += 1
    assert found >= 15


def test_score_sample_points_at_full_sources_not_identity() -> None:
    payload = load_score_sample()
    assert "airports" not in payload
    assert "excerpt" not in payload
    assert payload.get("gold") == "score_gold.json"


def test_pfc_filename_is_not_a_plan() -> None:
    packet = Packet(
        lid="HNL",
        name="Daniel K. Inouye International Airport",
        url="https://hidot.hawaii.gov/airports/files/2013/01/PFC-App-Final-Agency-Decision.pdf",
        label="FAA Final Agency Decision",
    )
    scored = score_packet(packet)
    assert scored["kind"] == "not_plan"
    assert scored["same_airport"] is False
    assert scored["publish"] is False


def test_statewide_system_plan_is_not_one_airport() -> None:
    packet = Packet(
        lid="BHM",
        name="Birmingham-Shuttlesworth International Airport",
        url="https://www.dot.state.al.us/publications/Aero/pdf/aldoteconomicimpactstudytechnicalreport.pdf",
        label="ALDOT Aviation System Plan and Economic Impact Study",
        body="Birmingham-Shuttlesworth International Airport (BHM) enplanements",
    )
    scored = score_packet(packet)
    assert scored["same_airport"] is False
    assert scored["kind"] == "not_plan"


def test_faa_regions_cover_fifty_states() -> None:
    covered = set(STATE_TO_FAA_REGION)
    assert set(US_STATES) <= covered
    assert len(FAA_AIRPORTS_REGIONS) == 9

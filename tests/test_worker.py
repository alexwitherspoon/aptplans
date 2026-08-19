from __future__ import annotations

from pathlib import Path

from pipeline.worker import cold_start_overlays
from tests.test_airports import PDX_NASR, _nasr_zip, _npias_xlsx
from tests.test_grants import grant_bytes_for


def _fetch_ok() -> tuple:
    npias = _npias_xlsx(
        [
            {
                "state_name": "Oregon",
                "city": "Portland",
                "name": "Portland International",
                "lid": "PDX",
                "ownership": "PU",
                "svc": "P",
                "hub": "L",
                "role": "",
            }
        ]
    )
    nasr = _nasr_zip([PDX_NASR])
    listing = b'<a href="https://nfdc.faa.gov/webContent/28DaySub/extra/06_Aug_2026_APT_CSV.zip">z</a>'
    urls: list[str] = []

    def fetch(url: str, timeout: int = 60) -> tuple[bytes, int]:
        urls.append(url)
        if "NASR_Subscription" in url:
            return listing, 200
        if url.endswith("_APT_CSV.zip"):
            return nasr, 200
        if "npias" in url.lower():
            return npias, 200
        found = grant_bytes_for(url)
        if found is not None:
            return found
        raise AssertionError(url)

    return fetch, urls


def test_cold_start_fetches_when_overlay_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_REFRESH_AIRPORTS", "1")
    fetch, urls = _fetch_ok()
    assert cold_start_overlays(tmp_path, fetch=fetch, sleep=lambda _s: None, pause_before=0) is True
    assert any("NASR_Subscription" in url for url in urls)
    assert (tmp_path / "airports.jsonl").is_file()


def test_cold_start_fetches_when_overlay_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_REFRESH_AIRPORTS", "1")
    (tmp_path / "airports.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "grants.jsonl").write_text("\n", encoding="utf-8")
    fetch, urls = _fetch_ok()
    assert cold_start_overlays(tmp_path, fetch=fetch, sleep=lambda _s: None, pause_before=0) is True
    assert any("NASR_Subscription" in url for url in urls)


def test_cold_start_skips_when_overlays_current(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_REFRESH_AIRPORTS", "1")
    (tmp_path / "airports.jsonl").write_text(
        '{"lid":"PDX","name":"Portland Intl","city":"Portland","state":"OR"}\n',
        encoding="utf-8",
    )
    (tmp_path / "grants.jsonl").write_text(
        '{"airport_lid":"PDX","fiscal_year":2025}\n',
        encoding="utf-8",
    )

    def fake_fetch(url: str, timeout: int = 60) -> tuple[bytes, int]:
        raise AssertionError(f"must not fetch {url}")

    assert (
        cold_start_overlays(tmp_path, fetch=fake_fetch, sleep=lambda _s: None, pause_before=0)
        is False
    )


def test_cold_start_off_without_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("APTPLANS_REFRESH_AIRPORTS", raising=False)

    def fake_fetch(url: str, timeout: int = 60) -> tuple[bytes, int]:
        raise AssertionError(f"must not fetch {url}")

    assert (
        cold_start_overlays(tmp_path, fetch=fake_fetch, sleep=lambda _s: None, pause_before=0)
        is False
    )

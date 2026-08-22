from __future__ import annotations

from pathlib import Path

from pipeline.boot_jobs import run_overlay_refresh, run_overview_refresh
from pipeline.worker import run_loop
from tests.test_airports import PDX_NASR, _nasr_zip, _npias_xlsx, _ourairports_csv
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
        if "ourairports" in url.lower() or "davidmegginson" in url.lower():
            return _ourairports_csv(), 200
        found = grant_bytes_for(url)
        if found is not None:
            return found
        raise AssertionError(url)

    return fetch, urls


def test_overlay_refresh_fetches_when_overlay_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_REFRESH_AIRPORTS", "1")
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    fetch, urls = _fetch_ok()
    monkeypatch.setattr("pipeline.boot_jobs.fetch_bytes", fetch)
    monkeypatch.setattr("pipeline.boot_jobs.post_json", None)
    monkeypatch.setattr("pipeline.boot_jobs.time.sleep", lambda _s: None)
    assert run_overlay_refresh(tmp_path) == "ok"
    assert any("NASR_Subscription" in url for url in urls)
    assert (tmp_path / "airports.jsonl").is_file()


def test_overlay_refresh_fetches_when_overlay_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_REFRESH_AIRPORTS", "1")
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    (tmp_path / "airports.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "grants.jsonl").write_text("\n", encoding="utf-8")
    fetch, urls = _fetch_ok()
    monkeypatch.setattr("pipeline.boot_jobs.fetch_bytes", fetch)
    monkeypatch.setattr("pipeline.boot_jobs.post_json", None)
    monkeypatch.setattr("pipeline.boot_jobs.time.sleep", lambda _s: None)
    assert run_overlay_refresh(tmp_path) == "ok"
    assert any("NASR_Subscription" in url for url in urls)


def test_overlay_refresh_skips_when_overlays_current(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_REFRESH_AIRPORTS", "1")
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
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

    monkeypatch.setattr("pipeline.boot_jobs.fetch_bytes", fake_fetch)
    assert run_overlay_refresh(tmp_path) == "skipped"


def test_overlay_refresh_off_without_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("APTPLANS_REFRESH_AIRPORTS", raising=False)
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    assert run_overlay_refresh(tmp_path) == "skipped"


def test_overview_refresh_writes_missing_then_skips(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path))
    assert run_overview_refresh(tmp_path) == "ok"
    assert (tmp_path / "overviews.jsonl").is_file()
    assert run_overview_refresh(tmp_path) == "skipped"


def test_run_loop_sleeps_only_when_idle() -> None:
    n = {"i": 0}
    sleeps: list[float] = []

    def process() -> bool:
        n["i"] += 1
        if n["i"] >= 3:
            raise KeyboardInterrupt
        return n["i"] == 1

    try:
        run_loop(process=process, sleep=sleeps.append, idle=60, job_pause=0)
    except KeyboardInterrupt:
        pass
    assert n["i"] == 3
    assert sleeps == [60]


def test_run_loop_backoff_on_job_retry() -> None:
    from pipeline.queue import JobRetry

    n = {"i": 0}
    sleeps: list[float] = []

    def process() -> bool:
        n["i"] += 1
        if n["i"] == 1:
            raise JobRetry(2)
        raise KeyboardInterrupt

    try:
        run_loop(process=process, sleep=sleeps.append, idle=9, job_pause=0)
    except KeyboardInterrupt:
        pass
    assert sleeps == [120]


def test_run_loop_polls_intake_hourly(monkeypatch) -> None:
    pulls: list[bool] = []
    now = {"t": 0.0}

    def fake_next(pull_intake: bool = False, pull_discovery: bool = False, **_kwargs) -> bool:
        pulls.append((pull_intake, pull_discovery))
        if now["t"] > 4000:
            raise KeyboardInterrupt
        return False

    def sleeper(seconds: float) -> None:
        now["t"] += seconds

    monkeypatch.setattr("pipeline.worker.process_next", fake_next)
    try:
        run_loop(sleep=sleeper, idle=60, intake_idle=3600, discovery_idle=999999, job_pause=0, now=lambda: now["t"])
    except KeyboardInterrupt:
        pass
    assert pulls[0] == (False, False)
    assert (True, False) in pulls
    assert pulls.count((True, False)) == 2


def test_main_boot_enqueue_failure_keeps_worker(monkeypatch) -> None:
    from pipeline import worker

    called: list[str] = []

    def boom() -> list[str]:
        raise RuntimeError("queue down")

    monkeypatch.setattr(worker, "enqueue_boot_jobs", boom)
    monkeypatch.setattr(worker, "_refresh_pipeline", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker, "run_loop", lambda: called.append("loop"))
    worker.main()
    assert called == ["loop"]


def test_repo_root_honors_aptplans_repo(monkeypatch, tmp_path: Path) -> None:
    from pipeline.refresh import repo_root

    site = tmp_path / "site"
    site.mkdir()
    (site / "build.py").write_text("# build\n", encoding="utf-8")
    monkeypatch.setenv("APTPLANS_REPO", str(tmp_path))
    assert repo_root() == tmp_path

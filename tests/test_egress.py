from __future__ import annotations

from pathlib import Path

import pytest
from urllib.error import URLError

from catalog import REFERENCE_FILES
from pipeline.fetch import fetch_bytes, fetch_meta, post_json
from tests.support.mock_egress import start_mock_egress

INVENTORY = REFERENCE_FILES / "4s9-2008-inventory.pdf"


def test_fetch_direct_when_proxy_unset(monkeypatch) -> None:
    monkeypatch.delenv("APTPLANS_FETCH_PROXY", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    url = INVENTORY.resolve().as_uri()
    data, status = fetch_bytes(url, proxy_url="")
    assert status == 200
    assert data.startswith(b"%PDF")


def test_fetch_routes_http_through_proxy(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    with start_mock_egress() as mock:
        target = "http://fixture.test/nasr"
        mock.set_response(target, b"via-proxy")
        monkeypatch.setenv("APTPLANS_FETCH_PROXY", mock.url)
        data, status = fetch_bytes(target)
        assert status == 200
        assert data == b"via-proxy"
        assert any(target in line for line in mock.proxy_requests)


def test_fetch_issues_connect_for_https(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    with start_mock_egress() as mock:
        monkeypatch.setenv("APTPLANS_FETCH_PROXY", mock.url)
        with pytest.raises(URLError):
            fetch_bytes("https://fixture.test/nasr", timeout=2, honor_robots=False)
        assert mock.connect_hosts == ["fixture.test"]


def test_fetch_fails_when_proxy_down(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("APTPLANS_FETCH_PROXY", "http://127.0.0.1:1")
    with pytest.raises(URLError):
        fetch_bytes("http://fixture.test/nasr", timeout=2)


def test_fetch_requires_proxy_in_production(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("APTPLANS_FETCH_PROXY", raising=False)
    with pytest.raises(RuntimeError, match="APTPLANS_FETCH_PROXY is required"):
        fetch_bytes("http://fixture.test/nasr")


def test_fetch_meta_and_post_json_use_proxy(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    with start_mock_egress() as mock:
        target = "http://fixture.test/meta"
        mock.set_response(target, b"body")
        monkeypatch.setenv("APTPLANS_FETCH_PROXY", mock.url)
        status, final = fetch_meta(target, method="GET")
        assert status == 200
        assert final == target
        post_target = "http://fixture.test/post"
        mock.set_response(post_target, b'{"ok": true}')
        payload = post_json(post_target, {"a": 1})
        assert payload == {"ok": True}


def test_egress_required_flag_overrides_non_production(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APTPLANS_EGRESS_REQUIRED", "1")
    monkeypatch.delenv("APTPLANS_FETCH_PROXY", raising=False)
    with pytest.raises(RuntimeError, match="APTPLANS_FETCH_PROXY is required"):
        fetch_bytes("http://fixture.test/nasr")

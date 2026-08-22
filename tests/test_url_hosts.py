from __future__ import annotations

from pipeline.url_hosts import host_matches, is_example_url, is_wikipedia_url, url_hostname


def test_url_hostname() -> None:
    assert url_hostname("https://WWW.Example.com/path") == "www.example.com"


def test_host_matches() -> None:
    assert host_matches("https://example.com/plan.pdf", "example.com")
    assert host_matches("https://sub.example.com/x", "example.com")
    assert not host_matches("https://notexample.com/x", "example.com")
    assert not host_matches("https://evil-example.com/x", "example.com")


def test_is_example_url() -> None:
    assert is_example_url("https://example.com/plan.pdf")
    assert is_example_url("https://www.oregon.gov/aviation/example/doc.pdf")
    assert not is_example_url("https://portofportland.com/plan.pdf")


def test_is_wikipedia_url() -> None:
    assert is_wikipedia_url("https://en.wikipedia.org/wiki/Airport")
    assert not is_wikipedia_url("https://example.com/wiki/Airport")

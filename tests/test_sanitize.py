from __future__ import annotations

from pipeline.sanitize import html_has_secrets, redact_html_secrets


def test_redact_html_strips_google_maps_key() -> None:
    raw = "gmap_api = 'REDACTED';"
    cleaned = redact_html_secrets(raw)
    assert "AIzaSy" not in cleaned
    assert not html_has_secrets(cleaned)


def test_redact_html_strips_stripe_key() -> None:
    raw = 'Stripe("REDACTED", { stripeAccount: "REDACTED" })'
    cleaned = redact_html_secrets(raw)
    assert "pk_live" not in cleaned
    assert "acct_" not in cleaned

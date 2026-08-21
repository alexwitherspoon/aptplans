from __future__ import annotations

from pipeline.sanitize import html_has_secrets, redact_html_secrets

_FAKE_MAPS = "AIzaSyFAKEKEYFORUNITTESTSONLY"
_FAKE_STRIPE = "pk_live_FAKEKEYFORUNITTESTS"
_FAKE_ACCT = "acct_FAKEFORUNITTESTS"


def test_redact_html_strips_google_maps_key() -> None:
    raw = f"gmap_api = '{_FAKE_MAPS}';"
    cleaned = redact_html_secrets(raw)
    assert _FAKE_MAPS not in cleaned
    assert "AIzaSy" not in cleaned
    assert not html_has_secrets(cleaned)


def test_redact_html_strips_stripe_key() -> None:
    raw = f'Stripe("{_FAKE_STRIPE}", {{ stripeAccount: "{_FAKE_ACCT}" }})'
    cleaned = redact_html_secrets(raw)
    assert _FAKE_STRIPE not in cleaned
    assert _FAKE_ACCT not in cleaned

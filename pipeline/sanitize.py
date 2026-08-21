"""Strip third-party keys from saved HTML before it lands in git."""

from __future__ import annotations

import re

_REDACTED = "REDACTED"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"AIzaSy[A-Za-z0-9_-]+"), _REDACTED),
    (re.compile(r"pk_(?:live|test)_[A-Za-z0-9]+"), _REDACTED),
    (re.compile(r"sk_(?:live|test)_[A-Za-z0-9]+"), _REDACTED),
    (re.compile(r'"apiKey"\s*:\s*"[^"]+"'), '"apiKey":"REDACTED"'),
    (re.compile(r'licenseKey"\s*:\s*"[^"]+"'), 'licenseKey":"REDACTED"'),
    (re.compile(r'licenseKey":"[^"]+"'), 'licenseKey":"REDACTED"'),
    (re.compile(r'name="csrf-token"\s+content="[^"]+"'), 'name="csrf-token" content="REDACTED"'),
    (re.compile(r'data-sitekey="[^"]+"'), 'data-sitekey="REDACTED"'),
    (re.compile(r'RecaptchaSiteKey:\s*"[^"]+"'), 'RecaptchaSiteKey: "REDACTED"'),
    (re.compile(r'AntiForgeryToken:\s*"[^"]+"'), 'AntiForgeryToken: "REDACTED"'),
    (re.compile(r'"token":"[a-f0-9]{32}"'), '"token":"REDACTED"'),
    (re.compile(r"stripeAccount:\s*\"acct_[A-Za-z0-9]+\""), 'stripeAccount: "REDACTED"'),
    (re.compile(r"\}\)\('([0-9a-f-]{36})'\);"), "})('REDACTED');"),
)

_FORBIDDEN = re.compile(
    r"AIzaSy[A-Za-z0-9_-]+|"
    r"pk_(?:live|test)_[A-Za-z0-9]+|"
    r"sk_(?:live|test)_[A-Za-z0-9]+|"
    r'"apiKey"\s*:\s*"(?!REDACTED)[^"]+"|'
    r'licenseKey"\s*:\s*"(?!REDACTED)[^"]+"|'
    r'data-sitekey="(?!REDACTED)[^"]+"|'
    r'RecaptchaSiteKey:\s*"(?!REDACTED)[^"]+"|'
    r'AntiForgeryToken:\s*"(?!REDACTED)[^"]+"|'
    r'stripeAccount:\s*"(?!REDACTED)[^"]+"'
)


def redact_html_secrets(text: str) -> str:
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


def html_has_secrets(text: str) -> bool:
    return bool(_FORBIDDEN.search(text))

"""Shared rubric classification: prompt, parse, whitelist, fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pipeline.queries import parse_json_object

GenerateFn = Callable[[str], str]


@dataclass(frozen=True)
class ClassificationResult:
    category: str
    reason: str = ""
    classifier: str = "rules"
    extra: dict[str, Any] | None = None


def whitelist_category(value: Any, labels: frozenset[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in labels else default


def classify_with_rubric(
    *,
    prompt: str,
    labels: frozenset[str],
    category_key: str,
    generate_fn: GenerateFn | None,
    rule_fallback: Callable[[], ClassificationResult],
    reason_key: str = "reason",
    extra_keys: tuple[str, ...] = (),
) -> ClassificationResult:
    """Call the model when generate_fn is set; otherwise use rule_fallback."""
    if generate_fn is None:
        return rule_fallback()
    try:
        raw = generate_fn(prompt)
        data = parse_json_object(raw)
    except (ValueError, TypeError):
        return rule_fallback()
    category = whitelist_category(data.get(category_key), labels, rule_fallback().category)
    reason = data.get(reason_key) if isinstance(data.get(reason_key), str) else ""
    extra = {key: data.get(key) for key in extra_keys if key in data}
    return ClassificationResult(
        category=category,
        reason=reason[:120],
        classifier="llm",
        extra=extra or None,
    )

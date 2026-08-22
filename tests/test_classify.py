from __future__ import annotations

from pipeline.classify import ClassificationResult, classify_with_rubric


def test_classify_with_rubric_uses_fallback_without_model() -> None:
    result = classify_with_rubric(
        prompt="ignored",
        labels=frozenset({"a", "b"}),
        category_key="cat",
        generate_fn=None,
        rule_fallback=lambda: ClassificationResult(category="a", classifier="rules"),
    )
    assert result.category == "a"
    assert result.classifier == "rules"


def test_classify_with_rubric_whitelists_model_output() -> None:
    result = classify_with_rubric(
        prompt="x",
        labels=frozenset({"maintenance", "growth"}),
        category_key="spend_category",
        generate_fn=lambda _prompt: '{"spend_category":"growth","reason":"new runway"}',
        rule_fallback=lambda: ClassificationResult(category="maintenance", classifier="rules"),
    )
    assert result.category == "growth"
    assert result.classifier == "llm"
    assert result.reason == "new runway"


def test_classify_with_rubric_rejects_unknown_labels() -> None:
    result = classify_with_rubric(
        prompt="x",
        labels=frozenset({"maintenance", "growth"}),
        category_key="spend_category",
        generate_fn=lambda _prompt: '{"spend_category":"magic"}',
        rule_fallback=lambda: ClassificationResult(category="maintenance", classifier="rules"),
    )
    assert result.category == "maintenance"

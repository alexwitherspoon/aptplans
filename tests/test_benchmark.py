from pipeline.benchmark import summarize, tok_s


def test_tok_s_and_summarize() -> None:
    assert tok_s(10, 2_000_000_000) == 5.0
    assert tok_s(0, 1_000_000_000) == 0.0
    stats = summarize(
        {
            "prompt_eval_count": 80,
            "prompt_eval_duration": 20_000_000_000,
            "eval_count": 10,
            "eval_duration": 2_000_000_000,
            "total_duration": 22_000_000_000,
            "load_duration": 1_000_000_000,
            "done_reason": "stop",
            "response": "unofficial note ok",
        }
    )
    assert stats["prompt_tok_s"] == 4.0
    assert stats["eval_tok_s"] == 5.0
    assert stats["load_s"] == 1.0
    assert stats["wall_s"] == 22.0
    assert stats["done_reason"] == "stop"
    assert stats["has_thinking_field"] is False

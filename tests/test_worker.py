from __future__ import annotations

from pipeline.ollama import DEFAULT_MODEL, ollama_model
from pipeline.run_once import main


def test_run_once_is_idle_success() -> None:
    assert main() == 0


def test_ollama_defaults_match_pin() -> None:
    assert DEFAULT_MODEL == "bonsai-27b"
    assert ollama_model() == "bonsai-27b"

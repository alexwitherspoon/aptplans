from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "ollama.json"
COMPOSE = ROOT / "docker" / "docker-compose.yml"
COMPOSE_PROD = ROOT / "docker" / "docker-compose.prod.yml"


def test_ollama_config_pins_bonsai_gguf() -> None:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert data["model"] == "bonsai-27b"
    assert data["repo_id"] == "prism-ml/Bonsai-27B-gguf"
    assert data["filename"] == "Bonsai-27B-Q1_0.gguf"
    assert int(data["num_ctx"]) >= 32768
    assert int(data["num_thread"]) == 12
    assert int(data["num_gpu"]) == 0
    assert int(data["keep_alive"]) == -1
    assert "unofficial" in data["system"].lower()


def test_compose_stack_is_site_worker_ollama() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    prod = COMPOSE_PROD.read_text(encoding="utf-8")
    for name in ("site:", "worker:", "ollama:"):
        assert name in text
    assert "profiles:" not in text
    assert "aptplans_llm" in text
    assert "internal: true" in text
    assert "11434:11434" not in text
    assert "11434:11434" not in prod
    assert "OLLAMA_HOST=http://ollama:11434" in text
    assert "APTPLANS_FETCH_PROXY=${APTPLANS_FETCH_PROXY:-}" in text
    assert "INTAKE_GITHUB_TOKEN=${INTAKE_GITHUB_TOKEN:-}" in prod
    assert "OLLAMA_NO_CLOUD=1" in text
    assert "OLLAMA_KEEP_ALIVE=-1" in text
    assert "OLLAMA_MAX_LOADED_MODELS=1" in text
    assert "OLLAMA_NUM_PARALLEL=1" in text
    assert "cpuset: ${OLLAMA_CPUSET:-4-15,20-31}" in prod
    assert "cpuset: ${SITE_CPUSET:-0-3,16-19}" in prod
    assert "cpuset: ${WORKER_CPUSET:-0-3,16-19}" in prod

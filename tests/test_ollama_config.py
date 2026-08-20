from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "ollama.json"
COMPOSE = ROOT / "docker" / "docker-compose.yml"
COMPOSE_PROD = ROOT / "docker" / "docker-compose.prod.yml"
COMPOSE_LOCAL = ROOT / "docker" / "docker-compose.local.yml"


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


def test_local_ollama_writes_origin_modelfile(tmp_path, monkeypatch) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("local_ollama", ROOT / "scripts" / "local_ollama.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    monkeypatch.setenv("MODELS_PATH", str(tmp_path))
    path = module.write_modelfile(module._config())
    text = path.read_text(encoding="utf-8")
    assert "FROM /models/Bonsai-27B-Q1_0.gguf" in text
    assert "PARAMETER num_ctx 32768" in text
    assert "PARAMETER num_thread 12" in text


def test_compose_stack_is_site_worker_ollama() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    prod = COMPOSE_PROD.read_text(encoding="utf-8")
    for name in ("site:", "search:", "worker:", "ollama:"):
        assert name in text
    assert "profiles:" not in text
    assert "aptplans_llm" in text
    assert "internal: true" in text
    assert "11434:11434" not in text
    assert "11434:11434" not in prod
    assert "7700:7700" not in text
    assert "7700:7700" not in prod
    assert "getmeili/meilisearch:v1.11.3" in text
    assert "MEILI_URL=http://search:7700" in text
    assert "MEILI_URL=http://search:7700" in prod
    assert "APTPLANS_TEXT=/var/lib/aptplans/text" in text
    assert "APTPLANS_TEXT=/var/lib/aptplans/text" in prod
    assert "OLLAMA_HOST=http://ollama:11434" in text
    assert "APTPLANS_FETCH_PROXY=${APTPLANS_FETCH_PROXY:-}" in text
    assert "INTAKE_GITHUB_TOKEN=${INTAKE_GITHUB_TOKEN:-}" in prod
    assert "APTPLANS_CATALOG_OVERLAY=/var/lib/aptplans/catalog" in prod
    assert "APTPLANS_QUEUE=/var/lib/aptplans/queue" in prod
    assert "APTPLANS_SITE=/var/lib/aptplans/site" in prod
    assert "APTPLANS_LLM=1" in prod
    assert "OLLAMA_NO_CLOUD=1" in text
    assert "OLLAMA_KEEP_ALIVE=-1" in text
    assert "OLLAMA_MAX_LOADED_MODELS=1" in text
    assert "OLLAMA_NUM_PARALLEL=1" in text
    assert "cpuset: ${OLLAMA_CPUSET:-4-15,20-31}" in prod
    assert "cpuset: ${SITE_CPUSET:-0-3,16-19}" in prod
    assert "cpuset: ${SEARCH_CPUSET:-0-3,16-19}" in prod
    assert "cpuset: ${WORKER_CPUSET:-0-3,16-19}" in prod
    local = COMPOSE_LOCAL.read_text(encoding="utf-8")
    assert "APTPLANS_LLM=1" not in local
    assert "APTPLANS_REFRESH_AIRPORTS=" not in local
    assert "MODELS_PATH" in local
    assert "127.0.0.1:11434:11434" in local
    assert "0.0.0.0:11434" not in local
    assert "7700:7700" not in local
    assert "aptplanslocalkey1" in local


def test_generate_honors_predict_and_ctx_env(monkeypatch) -> None:
    import pipeline.ollama as ollama

    captured: dict = {}

    class Resp:
        def read(self) -> bytes:
            return b'{"response":"ok"}'

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

    def fake_urlopen(req, timeout=0):
        captured["body"] = json.loads(req.data.decode())
        captured["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(ollama.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    monkeypatch.setenv("APTPLANS_LLM_PREDICT", "48")
    monkeypatch.setenv("APTPLANS_LLM_CTX", "2048")
    assert ollama.generate("hello") == "ok"
    assert captured["body"]["think"] is False
    assert captured["body"]["options"]["num_predict"] == 48
    assert captured["body"]["options"]["num_ctx"] == 2048

    monkeypatch.setenv("APTPLANS_LLM_THINK", "1")
    assert ollama.generate("hello") == "ok"
    assert captured["body"]["think"] is True

    monkeypatch.delenv("APTPLANS_LLM_PREDICT")
    monkeypatch.delenv("APTPLANS_LLM_CTX")
    monkeypatch.delenv("APTPLANS_LLM_THINK")
    assert ollama.generate("hello", json_mode=True, think=False) == "ok"
    assert captured["body"]["think"] is False
    assert captured["body"]["format"] == "json"
    assert "options" not in captured["body"]

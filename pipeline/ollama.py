"""Ollama client for the serial worker. Keep-alive is always -1 (never unload)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_HOST = "http://ollama:11434"
DEFAULT_MODEL = "bonsai-27b"


def ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", DEFAULT_HOST).rstrip("/")


def ollama_model() -> str:
    return os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)


def think_enabled(think: bool | None = None) -> bool:
    """Thinking is off unless APTPLANS_LLM_THINK=1. /api/generate currently puts CoT in `response`."""
    if think is not None:
        return think
    raw = os.environ.get("APTPLANS_LLM_THINK", "").strip().lower()
    return raw in {"1", "true", "yes"}


def complete(
    prompt: str,
    timeout: int = 1800,
    num_predict: int | None = None,
    json_mode: bool = False,
    think: bool | None = None,
) -> dict:
    """Raw Ollama /api/generate body. Keep-alive is always -1."""
    if num_predict is None:
        raw = os.environ.get("APTPLANS_LLM_PREDICT", "").strip()
        if raw.isdigit():
            num_predict = int(raw)
    raw_ctx = os.environ.get("APTPLANS_LLM_CTX", "").strip()
    payload: dict = {
        "model": ollama_model(),
        "prompt": prompt,
        "stream": False,
        "keep_alive": -1,
        "think": think_enabled(think),
    }
    if json_mode:
        payload["format"] = "json"
    options: dict = {}
    if num_predict:
        options["num_predict"] = num_predict
    if raw_ctx.isdigit():
        options["num_ctx"] = int(raw_ctx)
    if options:
        payload["options"] = options
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{ollama_host()}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"ollama generate failed: {exc.code} {exc.read()[:200]!r}") from exc


def generate(
    prompt: str,
    timeout: int = 1800,
    num_predict: int | None = None,
    json_mode: bool = False,
    think: bool | None = None,
) -> str:
    """One non-streaming completion. The model stays resident (keep_alive -1)."""
    payload = complete(
        prompt,
        timeout=timeout,
        num_predict=num_predict,
        json_mode=json_mode,
        think=think,
    )
    text = (payload.get("response") or "").strip()
    if not text:
        thinking = (payload.get("thinking") or "").strip()
        if thinking:
            raise RuntimeError(
                "ollama generate returned an empty response after thinking; "
                "raise APTPLANS_LLM_PREDICT or leave it unset"
            )
        raise RuntimeError("ollama generate returned an empty response")
    return text


def unofficial_note_prompt(chunk: str) -> str:
    return (
        "Write one unofficial paragraph that helps a person find the right chapter "
        "in this airport planning excerpt. Stay grounded in the text. Do not give "
        "legal advice. Do not name a model.\n\n"
        f"{chunk}"
    )


def unofficial_note(chunk: str, generate_fn=generate) -> str:
    return generate_fn(unofficial_note_prompt(chunk))


def load_model(timeout: int = 1200) -> None:
    """Load the pinned model and keep it resident. Empty generate is a warmup."""
    body = json.dumps({"model": ollama_model(), "keep_alive": -1}).encode()
    req = urllib.request.Request(
        f"{ollama_host()}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"ollama warmup failed: {exc.code} {exc.read()[:200]!r}") from exc

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


def generate(prompt: str, timeout: int = 1200) -> str:
    """One non-streaming completion. The model stays resident (keep_alive -1)."""
    body = json.dumps(
        {
            "model": ollama_model(),
            "prompt": prompt,
            "stream": False,
            "keep_alive": -1,
        }
    ).encode()
    req = urllib.request.Request(
        f"{ollama_host()}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"ollama generate failed: {exc.code} {exc.read()[:200]!r}") from exc
    text = (payload.get("response") or "").strip()
    if not text:
        raise RuntimeError("ollama generate returned an empty response")
    return text


def unofficial_note(chunk: str, generate_fn=generate) -> str:
    prompt = (
        "Write one unofficial paragraph that helps a person find the right chapter "
        "in this airport planning excerpt. Stay grounded in the text. Do not give "
        "legal advice. Do not name a model.\n\n"
        f"{chunk}"
    )
    return generate_fn(prompt)


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

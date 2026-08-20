"""Ollama throughput on the worker. Origin only. Not CI. Not a quality judge."""

from __future__ import annotations

import json
import urllib.request

from pipeline.ollama import complete, load_model, ollama_host, ollama_model, unofficial_note_prompt

# Short inventory-shaped excerpt so origin does not need a PDF on disk.
EXCERPT = (
    "Chapter Two Airport Master Plan Update INVENTORY Mulino Airport. "
    "This chapter summarizes background data, location and access, "
    "airfield and landside facilities, airspace, land use and zoning, "
    "environmental issues, historical aviation activity, and financial data. "
    "It is the foundation for later chapters."
)


def tok_s(count: int, duration_ns: int) -> float:
    if duration_ns <= 0 or count <= 0:
        return 0.0
    return count / (duration_ns / 1e9)


def summarize(payload: dict) -> dict:
    prompt_n = int(payload.get("prompt_eval_count") or 0)
    eval_n = int(payload.get("eval_count") or 0)
    prompt_ns = int(payload.get("prompt_eval_duration") or 0)
    eval_ns = int(payload.get("eval_duration") or 0)
    total_ns = int(payload.get("total_duration") or 0)
    load_ns = int(payload.get("load_duration") or 0)
    return {
        "done_reason": payload.get("done_reason") or "",
        "prompt_tokens": prompt_n,
        "eval_tokens": eval_n,
        "prompt_tok_s": round(tok_s(prompt_n, prompt_ns), 3),
        "eval_tok_s": round(tok_s(eval_n, eval_ns), 3),
        "load_s": round(load_ns / 1e9, 3),
        "wall_s": round(total_ns / 1e9, 3),
        "has_thinking_field": bool((payload.get("thinking") or "").strip()),
        "response": (payload.get("response") or "").strip(),
    }


def _print_run(label: str, stats: dict, preview: int = 400) -> None:
    text = stats["response"][:preview].replace("\n", " ")
    print(f"--- {label} ---", flush=True)
    print(
        "done_reason={done_reason} prompt_tokens={prompt_tokens} "
        "prompt_tok_s={prompt_tok_s} eval_tokens={eval_tokens} "
        "eval_tok_s={eval_tok_s} load_s={load_s} wall_s={wall_s} "
        "has_thinking_field={has_thinking_field}".format(**stats),
        flush=True,
    )
    print(f"response: {text}", flush=True)


def _ps() -> str:
    req = urllib.request.Request(f"{ollama_host()}/api/ps", method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    names = [item.get("name") for item in data.get("models") or []]
    return ",".join(str(name) for name in names if name) or "(none)"


def main() -> int:
    print(
        "Ollama is serial (NUM_PARALLEL=1). Skip this if aptplans-pipeline.service is active.",
        flush=True,
    )
    print(f"host={ollama_host()} model={ollama_model()} ps={_ps()}", flush=True)
    print("warmup", flush=True)
    load_model(timeout=1800)
    print(f"warmup ok ps={_ps()}", flush=True)

    probe = summarize(
        complete(
            "Reply with exactly: unofficial note ok",
            timeout=1800,
            num_predict=32,
            think=False,
        )
    )
    _print_run("think=false predict=32 ping", probe)

    note = unofficial_note_prompt(EXCERPT)
    off = summarize(
        complete(note, timeout=7200, num_predict=None, think=False)
    )
    _print_run("think=false predict=unset unofficial_note", off, preview=1200)
    on = summarize(
        complete(note, timeout=28800, num_predict=None, think=True)
    )
    _print_run("think=true predict=unset unofficial_note", on, preview=1200)
    print(
        "compare unofficial_note "
        f"think=false wall_s={off['wall_s']} eval_tok_s={off['eval_tok_s']} "
        f"eval_tokens={off['eval_tokens']} done={off['done_reason']} | "
        f"think=true wall_s={on['wall_s']} eval_tok_s={on['eval_tok_s']} "
        f"eval_tokens={on['eval_tokens']} done={on['done_reason']}",
        flush=True,
    )
    print("ollama bench ok", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

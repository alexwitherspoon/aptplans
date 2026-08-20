"""Download the origin Bonsai GGUF and import it into local Compose Ollama."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "ollama.json"
COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(ROOT / "docker" / "docker-compose.yml"),
    "-f",
    str(ROOT / "docker" / "docker-compose.local.yml"),
]


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def models_dir() -> Path:
    return Path(os.environ.get("MODELS_PATH", ROOT / "data" / "models"))


def gguf_path(cfg: dict) -> Path:
    return models_dir() / cfg["filename"]


def write_modelfile(cfg: dict) -> Path:
    dest = models_dir() / f"Modelfile.{cfg['model']}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    system = cfg["system"].strip()
    dest.write_text(
        "\n".join(
            [
                f"FROM /models/{cfg['filename']}",
                f"PARAMETER num_ctx {cfg['num_ctx']}",
                f"PARAMETER temperature {cfg['temperature']}",
                f"PARAMETER top_p {cfg['top_p']}",
                f"PARAMETER top_k {cfg['top_k']}",
                f"PARAMETER min_p {cfg['min_p']}",
                f"PARAMETER repeat_penalty {cfg['repeat_penalty']}",
                f"PARAMETER num_thread {cfg['num_thread']}",
                f"PARAMETER num_gpu {cfg['num_gpu']}",
                f"PARAMETER num_batch {cfg['num_batch']}",
                'SYSTEM """',
                system,
                '"""',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return dest


def download() -> Path:
    cfg = _config()
    dest = gguf_path(cfg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_modelfile(cfg)
    if dest.is_file() and dest.stat().st_size > 1_000_000:
        print(f"already have {dest}")
        return dest
    url = f"https://huggingface.co/{cfg['repo_id']}/resolve/main/{cfg['filename']}"
    partial = dest.with_suffix(dest.suffix + ".partial")
    print(f"downloading {cfg['filename']}")
    subprocess.run(
        [
            "curl",
            "-fL",
            "--retry",
            "5",
            "--retry-delay",
            "5",
            "-C",
            "-",
            "-A",
            "aptplans.org",
            "-o",
            str(partial),
            url,
        ],
        check=True,
    )
    partial.replace(dest)
    return dest


def _ollama_ready() -> bool:
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "ollama", "ollama", "list"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def wait_for_ollama() -> None:
    for _ in range(60):
        if _ollama_ready():
            return
        time.sleep(2)
    raise SystemExit("Ollama did not become ready")


def create() -> None:
    cfg = _config()
    dest = gguf_path(cfg)
    if not dest.is_file():
        raise SystemExit(f"missing {dest}; run download first")
    write_modelfile(cfg)
    wait_for_ollama()
    listed = subprocess.check_output([*COMPOSE, "exec", "-T", "ollama", "ollama", "list"], text=True)
    if listed.splitlines() and any(line.startswith(cfg["model"]) for line in listed.splitlines()):
        print(f"{cfg['model']} already in Ollama")
        return
    print(f"creating ollama model {cfg['model']}", flush=True)
    subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "ollama",
            "ollama",
            "create",
            cfg["model"],
            "-f",
            f"/models/Modelfile.{cfg['model']}",
        ],
        check=True,
    )
    print(f"ollama model {cfg['model']} ready")


def smoke() -> None:
    """Host-side diagnostic: published loopback Ollama plus worker LLM paths."""
    os.environ.setdefault("OLLAMA_HOST", "http://127.0.0.1:11434")
    os.environ.setdefault("OLLAMA_MODEL", _config()["model"])
    # Laptop CPU is not the KS-6. Shrink ctx/decode so diagnostics can finish;
    # origin leaves both unset so Modelfile num_ctx 32768 applies.
    os.environ["APTPLANS_LLM_THINK"] = "0"
    os.environ["APTPLANS_LLM_PREDICT"] = os.environ.get("APTPLANS_LLM_PREDICT") or "48"
    os.environ["APTPLANS_LLM_CTX"] = os.environ.get("APTPLANS_LLM_CTX") or "2048"
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    wait_for_ollama()
    import urllib.request

    tags = urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10).read().decode()
    if "bonsai-27b" not in tags:
        raise SystemExit("bonsai-27b is not loaded in local Ollama; run make model")
    print(
        f"tags ok (predict={os.environ['APTPLANS_LLM_PREDICT']} ctx={os.environ['APTPLANS_LLM_CTX']})",
        flush=True,
    )

    from pipeline.ollama import generate, load_model, unofficial_note
    from pipeline.parse import extract_text, viable_chunk
    from pipeline.queries import verify_candidate, verify_finance
    from pipeline.queue import JobQueue, QueueJob
    from pipeline.run_once import run_once

    def generate_local(prompt: str) -> str:
        return generate(prompt, timeout=1800, num_predict=48, think=False)

    def generate_json(prompt: str) -> str:
        text = generate(prompt, timeout=2400, json_mode=True, num_predict=160, think=False)
        print("verify raw:", text[:800], flush=True)
        return text

    print("warmup (first load can take several minutes)", flush=True)
    load_model(timeout=1800)
    print("warmup ok", flush=True)

    inventory = ROOT / "catalog" / "references" / "files" / "4s9-2008-inventory.pdf"
    chunk = viable_chunk(extract_text(inventory.read_bytes()), max_chars=1200)
    print("unofficial_note (cpu decode is slow)", flush=True)
    note = unofficial_note(chunk, generate_fn=generate_local)
    print("unofficial_note:", note[:400], flush=True)
    if len(note) < 20:
        raise SystemExit("unofficial note was too short")

    print("verify_candidate", flush=True)
    plan = verify_candidate(
        lid="4S9",
        name="Mulino State Airport",
        url="https://www.oregon.gov/aviation/airports/4s9-inventory.pdf",
        excerpt=chunk,
        generate_fn=generate_json,
    )
    print("verify_candidate:", plan, flush=True)
    if plan["kind"] not in {"master_plan", "alp", "other", "not_plan"}:
        raise SystemExit(f"unexpected plan kind {plan['kind']}")

    print("verify_finance", flush=True)
    finance = verify_finance(
        url="https://example.gov/lab.pdf",
        excerpt=(
            "Oregon Department of Aviation 2025-27 Legislatively Adopted Budget. "
            "Program Operations. Aviation System Action Program. Not an ALP."
        ),
        state="OR",
        name="Oregon Department of Aviation",
        generate_fn=generate_json,
    )
    print("verify_finance:", finance, flush=True)
    if "amount" in finance or "total" in finance:
        raise SystemExit("finance verify leaked amounts")

    from tempfile import TemporaryDirectory
    from catalog.seed import seed_catalog

    os.environ["APTPLANS_LLM"] = "1"
    print("run_once with APTPLANS_LLM=1", flush=True)
    with TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        queue = JobQueue(scratch / "queue")
        queue.enqueue(
            QueueJob(
                kind="fetch",
                document_id="4s9-2008-inventory",
                source_url=inventory.resolve().as_uri(),
                airport_lid="4S9",
            )
        )
        code = run_once(
            queue_dir=scratch / "queue",
            files_dir=scratch / "files",
            overlay_dir=scratch / "overlay",
            catalog_root=ROOT / "catalog",
        )
        if code != 0:
            raise SystemExit(f"run_once failed with {code}")
        doc = seed_catalog(ROOT / "catalog", overlay_dir=scratch / "overlay").document(
            "4s9-2008-inventory"
        )
        print("run_once completeness:", doc.completeness, flush=True)
        print("run_once summary:", (doc.summary or "")[:400], flush=True)
        if not doc.summary:
            raise SystemExit("run_once with APTPLANS_LLM=1 did not write a summary")
    print("llm smoke ok", flush=True)


def _fixture_prompts() -> list[tuple[str, str, bool]]:
    from pipeline.ollama import unofficial_note_prompt
    from pipeline.parse import extract_text, viable_chunk
    from pipeline.queries import verify_finance_prompt, verify_prompt

    inventory = ROOT / "catalog" / "references" / "files" / "4s9-2008-inventory.pdf"
    chunk = viable_chunk(extract_text(inventory.read_bytes()), max_chars=1200)
    return [
        ("unofficial_note", unofficial_note_prompt(chunk), False),
        (
            "verify_candidate",
            verify_prompt(
                lid="4S9",
                name="Mulino State Airport",
                url="https://www.oregon.gov/aviation/airports/4s9-inventory.pdf",
                excerpt=chunk,
            ),
            True,
        ),
        (
            "verify_finance",
            verify_finance_prompt(
                url="https://example.gov/lab.pdf",
                excerpt=(
                    "Oregon Department of Aviation 2025-27 Legislatively Adopted Budget. "
                    "Program Operations. Aviation System Action Program. Not an ALP."
                ),
                state="OR",
                name="Oregon Department of Aviation",
            ),
            True,
        ),
    ]


def compare() -> None:
    """Same prompts with think off then on. Accuracy check, not a speed test."""
    os.environ.setdefault("OLLAMA_HOST", "http://127.0.0.1:11434")
    os.environ.setdefault("OLLAMA_MODEL", _config()["model"])
    os.environ.pop("APTPLANS_LLM_PREDICT", None)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    wait_for_ollama()
    from pipeline.ollama import complete, load_model
    from pipeline.queries import parse_json_object

    print("warmup", flush=True)
    load_model(timeout=1800)
    print("warmup ok", flush=True)

    off_raw = os.environ.get("APTPLANS_LLM_COMPARE_OFF", "160").strip()
    off_predict = int(off_raw) if off_raw.isdigit() else None
    on_raw = os.environ.get("APTPLANS_LLM_COMPARE_ON", "").strip()
    on_predict = int(on_raw) if on_raw.isdigit() else None
    modes = ((False, off_predict), (True, on_predict))
    if os.environ.get("APTPLANS_LLM_COMPARE_ON_ONLY", "").strip() == "1":
        modes = ((True, on_predict),)
    only = os.environ.get("APTPLANS_LLM_COMPARE_ONLY", "").strip()
    tasks = _fixture_prompts()
    if only:
        tasks = [item for item in tasks if item[0] == only]
        if not tasks:
            raise SystemExit(f"unknown compare task {only}")

    for name, prompt, json_mode in tasks:
        for think, predict in modes:
            print(f"=== {name} think={think} predict={predict} ===", flush=True)
            started = time.monotonic()
            payload = complete(
                prompt,
                timeout=28800,
                num_predict=predict,
                json_mode=json_mode,
                think=think,
            )
            elapsed = time.monotonic() - started
            thinking = (payload.get("thinking") or "").strip()
            text = (payload.get("response") or "").strip()
            print(f"elapsed_s={elapsed:.0f} done_reason={payload.get('done_reason')}", flush=True)
            print(
                f"prompt_eval={payload.get('prompt_eval_count')} "
                f"eval={payload.get('eval_count')}",
                flush=True,
            )
            print(
                "keys:",
                sorted(k for k in payload if k != "context"),
                flush=True,
            )
            if thinking:
                print("thinking:", thinking, flush=True)
            print("response:", text, flush=True)
            if json_mode and text:
                try:
                    parsed = parse_json_object(text)
                    print("parsed:", parsed, flush=True)
                    if name == "verify_finance" and (
                        "amount" in parsed or "total" in parsed
                    ):
                        print("WARN: finance JSON included amount keys", flush=True)
                except ValueError as exc:
                    print(f"WARN: {exc}", flush=True)
            if not text:
                print("WARN: empty response", flush=True)
    print("llm compare ok", flush=True)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"download", "create", "smoke", "compare"}:
        print(
            "usage: python3 scripts/local_ollama.py download|create|smoke|compare",
            file=sys.stderr,
        )
        return 2
    if argv[1] == "download":
        download()
    elif argv[1] == "create":
        create()
    elif argv[1] == "smoke":
        smoke()
    else:
        compare()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

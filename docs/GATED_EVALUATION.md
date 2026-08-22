# Gated rubric evaluation

AptPlans makes many **judgment calls** on unstructured text: is this PDF a master plan? Is this grant maintenance or growth? Should we fetch this search hit? The preferred pattern is **rubric-driven, gated LLM evaluation**: a stable rubric in the prompt, schema JSON out, deterministic code gates on top, optional rule-based fallback, and a human gold loop for quality.

This document is the canonical description of that pattern. New evaluations should follow it unless there is a strong reason not to (see [When rules stay rules](#when-rules-stay-rules)).

## Pattern

```
┌─────────────┐    outer gates     ┌──────────────┐    Ollama     ┌─────────────┐
│ Input bytes │ ────────────────► │ *_prompt()   │ ────────────► │ parse JSON  │
│ or text     │  (host, SSI, LID) │ + rubric     │  generate_fn │ whitelist   │
└─────────────┘                   └──────────────┘               └──────┬──────┘
                                                                        │
                    ┌───────────────────────────────────────────────────┘
                    ▼
            ┌───────────────┐     miss/invalid     ┌────────────────┐
            │ Store on      │ ◄─────────────────── │ Rule fallback  │
            │ overlay row   │                      │ (optional)     │
            └───────────────┘                      └────────────────┘
                    │
                    ▼
            ┌───────────────┐
            │ Human gold    │  POST /v1/label → *_gold.json → scripts/eval_*
            │ (optional)    │
            └───────────────┘
```

### 1. Rubric in the prompt

The rubric is **plain language in the prompt**, not scattered regex. It states:

- What question is being answered (one task per call).
- Allowed output labels (closed set).
- Decision rules with examples and edge cases.
- What the model must **not** do (search, invent URLs, return dollar amounts, override gates).

Keep rubrics in `pipeline/queries.py` next to their `*_prompt()` and `evaluate_*()` functions. Reference shared label sets from code (`PLAN_KINDS`, `SPEND_CATEGORIES`, etc.) so tests and prompts stay aligned.

### 2. Schema JSON out

Every classification call returns **compact JSON** with a fixed schema. Use `parse_json_object()` to extract it from model text.

Typical fields:

| Field | Role |
| --- | --- |
| Primary label | e.g. `kind`, `spend_category`, `finance_kind`, `fetch` |
| Booleans | e.g. `official_plan`, `same_airport`, `same_entity` |
| `reason` | Short audit string (≤12 words in existing prompts) |
| Metadata | publisher, dates, URLs — only when the rubric allows |

`generate_fn` is injectable in tests; production uses `pipeline.ollama.generate`.

### 3. Code gates (never trust the model)

After parsing, **whitelist** every enum field. Reject or rewrite URLs that were not in the input packet. Hard gates run **before** the model and are **never** overridden by model output:

- SSI-shaped filenames and content (`pipeline/gates.py`)
- Known airport / state / LID context
- Allowed hosts from the packet
- Size caps, robots.txt, fetch proxy

If JSON is missing or invalid, return a safe default (`other`, `not_plan`, `needs_human`) or a rule fallback — never crash the worker or refresh job.

### 4. Master switch and pacing

| Control | Meaning |
| --- | --- |
| `APTPLANS_LLM=1` | Origin may call Ollama. CI and default local Compose leave this unset. |
| `APTPLANS_LLM_THINK=0` | Default. Thinking on pollutes `response` with chain-of-thought. |
| `APTPLANS_LLM_TIMEOUT` | Per-call wait (default 3600s on origin). |
| `APTPLANS_JOB_PAUSE_SEC` / `PAUSE_SECONDS` | Pace serial work between LLM calls. |

Batch enrichment (grants, overviews refresh) should use the same pacing as USAspending batch posts — not unbounded parallel calls.

### 5. Persist results

Classifications belong on the **overlay record**, not recomputed at site build:

- Write once when ingested or vetted.
- Include `classified_at` / `reason` when audit matters.
- Site build reads stored fields first; rules fill gaps for rows not yet classified.

### 6. Human gold loop

- `POST /v1/label` on the review API for disagreements.
- Export to `catalog/references/*_gold.json` or `data/score/review/` via `make pull-outcomes`.
- `scripts/eval_*.py` replay gold against `evaluate_*()` — **not CI** for live model calls.
- Train or tune rule weights separately (`scripts/train_evidence.py`) where linear scoring still helps.

### 7. Adding a new evaluation (checklist)

1. **Name the task** — one sentence: “Given X, decide Y from {labels}.”
2. **Define labels** — frozen set in code + rubric text.
3. **Write `thing_prompt()`** — context fields, schema, rubric block (mirror `verify_finance_prompt`).
4. **Write `evaluate_thing()`** — `generate_fn`, `parse_json_object`, whitelist, outer-gate respect, safe default.
5. **Wire call site** — worker job, refresh script, or monthly overlay pass; gate on `APTPLANS_LLM=1`.
6. **Add overlay field(s)** on the relevant model (`Grant`, `Document`, etc.).
7. **Rule fallback** — fast path for obvious cases; LLM for `other` / ambiguous (hybrid).
8. **Tests** — mock `generate_fn` in `tests/test_queries.py`; fixture gold for eval script.
9. **Document** — add a row to the audit table below.

## When rules stay rules

Not everything should be an LLM call.

| Keep deterministic | Why |
| --- | --- |
| SSI / security filename gates | Fail closed; no model override |
| `robots.txt`, proxy, hash, size limits | Infrastructure |
| `coverage_stage`, `completeness_for_airport` | Derived from overlay + catalog facts |
| URL host allowlists in search hints | Prevent invented fetches |
| SHA-256, change detection | Cryptographic / structural |

Use **hybrid**: rules for cheap obvious cases, rubric LLM for ambiguous unstructured text.

## Audit: evaluations in AptPlans

Status key: **Implemented** = follows this pattern today. **Partial** = prompt exists but not wired or incomplete. **Rules** = regex/heuristics/derived; candidate for rubric LLM. **N/A** = not a classification task.

### Document and plan pipeline

| Task | Question | Labels / output | Location | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Plan / ALP verify | Is this excerpt an official plan for this LID? | `official_plan`, `kind`, `same_airport`, … | `verify_candidate()` · `run_once` vet job | **Implemented** | Production path when `APTPLANS_LLM=1` |
| Finance verify | Is this excerpt official finance, and what kind? | `finance_kind`, `scope`, `official_finance`, … | `verify_finance()` | **Partial** | `make llm` only; not in worker refresh |
| Search hit triage | Same airport? Fetch? | `hit_type`, `kind_guess`, `fetch` | `evaluate_search_hit()` | **Implemented** | URL allowlist gate; eval in `scripts/eval_search_hits.py` |
| Search hint | What query next? | `queries[]`, `stop` | `evaluate_search_hints()` | **Implemented** | Model does not search; eval in `scripts/eval_search_hints.py` |
| Hub link classify | Role and kind guess from URL + label | `role`, `kind_guess` | `classify_link()` · `explore.py` | **Rules** | Fast pre-filter; ambiguous PDFs could use LLM |
| File gates | SSI, newsletter, kind block | pass / fail | `gates.evaluate_*()` | **Rules** | Never LLM |
| Evidence score | Publish / explore / confirm? | linear score → bucket | `evidence.score_packet()` | **Rules** | Trained weights; complements LLM vet |
| Unofficial note | One paragraph summary | prose | `ollama.unofficial_note*` | **Different** | Generative, not closed-label; chunk+reduce |
| Planning outlook | Growing / declining / maintaining? | `trajectory.band` | `brief.score_signals()` | **Rules** | Keyword counts on plan text; rubric LLM candidate |
| Coverage stage | Public pipeline stage | `untouched` … `published` | `pipeline_status.coverage_stage()` | **N/A** | Derived from touch history + docs |
| Completeness | `missing` / `link_only` / `complete` | enum | `store.completeness_for_airport()` | **N/A** | Derived from document rows |

### Grants and funding

| Task | Question | Labels / output | Location | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Planning grant flag | Is description a planning study? | `is_planning` bool | `is_planning()` at ingest | **Rules** | Set in `parse_aip_grants_bytes()` |
| Grant spend category | Maintenance vs growth vs planning? | `maintenance` \| `growth` \| `planning` \| `other` | `grant_spend_category()` · `site/build.py` | **Rules** | **Next rubric LLM**; store on `Grant.spend_category` |
| State budget lines | Program vs project vs fund row? | `BudgetLine.group` | budget fixtures | **N/A** | Structured ingest today |
| USAspending merge | Obligated / outlayed | dollars | `usaspending.py` | **N/A** | API facts, not interpretation |

### Search and intake

| Task | Question | Labels / output | Location | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Gemini escalate | Destination URLs only | URL list | `search_client.py` | **N/A** | Explicitly not a classifier |
| Intake issue | Well-formed LID + URL? | queue / comment | GitHub intake | **Rules** | Form parse only |
| Job outcome bucket | accepted / uncertain / … | bucket | `outcomes.record_outcome()` | **N/A** | Aggregates job result + evidence |

## Priority migration targets

Ordered by value and fit with the pattern:

1. **Grant spend category** — short FAA descriptions; regex already fragile; high user value on state dashboards. Add `classify_grant_spend()` + `Grant.spend_category` at `refresh_grants()` time; hybrid with regex fallback.
2. **Finance verify in worker** — `verify_finance()` already matches the pattern; wire after fetch when ingesting state budget / award PDFs.
3. **Planning outlook / trajectory** — replace or augment `brief.score_signals()` with rubric LLM over plan excerpts (only when `show_plan_insights` gates pass).
4. **Ambiguous hub links** — LLM only when `classify_link()` returns `unknown` on a PDF artifact worth fetching.
5. **Budget PDF line items** — when unstructured state budgets are preserved, use finance rubric + row-kind sub-rubric.

## File map

| Concern | Path |
| --- | --- |
| Prompts + `evaluate_*()` | `pipeline/queries.py` |
| Ollama client | `pipeline/ollama.py` |
| Worker vet | `pipeline/run_once.py` |
| Grant ingest | `pipeline/refresh_grants.py`, `catalog/grants.py` |
| Explore heuristics | `pipeline/explore.py` |
| Evidence weights | `pipeline/evidence.py`, `catalog/references/score_gold.json` |
| Review API labels | `pipeline/review_api.py` |
| Eval scripts (not CI) | `scripts/eval_search_hints.py`, `scripts/eval_search_hits.py`, `scripts/local_ollama.py` |
| Unit tests | `tests/test_queries.py` |

## Related docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — pipeline stages and model-call constraints
- [TESTING.md](TESTING.md) — what CI covers vs live eval scripts
- [LLM_EVALUATION_ROADMAP.md](LLM_EVALUATION_ROADMAP.md) — phased implementation plan for closing audit gaps
- [pipeline/README.md](../pipeline/README.md) — worker behavior summary

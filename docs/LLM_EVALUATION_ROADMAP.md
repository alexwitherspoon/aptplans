# LLM evaluation implementation roadmap

Plan for closing the gaps in [GATED_EVALUATION.md](GATED_EVALUATION.md). Each workstream follows the same machinery: rubric prompt, `evaluate_*()`, overlay persistence, rule fallback, gold fixtures, eval script.

## Shared infrastructure (do first)

These pieces unblock every migration target. Implement once, reuse everywhere.

### S1. Evaluation registry

| Item | Work |
| --- | --- |
| **Goal** | One place listing all evaluation tasks, labels, overlay fields, and call sites |
| **Files** | `pipeline/evaluations.py` (new), extend `docs/GATED_EVALUATION.md` audit table |
| **Deliverable** | `EVALUATIONS: dict[str, EvaluationSpec]` with `name`, `labels`, `prompt_fn`, `evaluate_fn`, `overlay_field`, `fallback_fn` |

### S2. Overlay classification fields convention

| Item | Work |
| --- | --- |
| **Goal** | Consistent optional fields on overlay records |
| **Pattern** | `{task}_category`, `{task}_reason`, `{task}_classified_at`, `{task}_classifier` (`rules` \| `llm` \| `human`) |
| **Files** | `catalog/models.py` — add to `Grant`, `Document` as needed |
| **Migration** | `from_dict` tolerates missing fields; old rows get rule fallback at read time |

### S3. Generic classify helper

| Item | Work |
| --- | --- |
| **Goal** | DRY wrapper: gate → prompt → generate → parse → whitelist → fallback → audit row |
| **Files** | `pipeline/classify.py` (new) |
| **API** | `classify_record(spec, input: dict, *, generate_fn, rule_fallback) -> ClassificationResult` |
| **Tests** | `tests/test_classify.py` with mock `generate_fn` |

### S4. Human gold export for evaluations

| Item | Work |
| --- | --- |
| **Goal** | Extend review API labels to evaluation gold, not just evidence scoring |
| **Files** | `pipeline/review_api.py`, `scripts/export_outcome_gold.py` |
| **Gold shape** | `{evaluation, input_hash, input_text, gold_category, labeler, at}` |
| **Fixture path** | `catalog/references/eval_gold/grant_spend.jsonl`, etc. |

### S5. Eval script template

| Item | Work |
| --- | --- |
| **Goal** | `scripts/eval_classification.py --task grant_spend` replays gold, prints accuracy |
| **Pattern** | Mirror `scripts/eval_search_hints.py`; not CI |
| **Output** | Confusion matrix, failures list for rubric tuning |

**Estimate:** 1–2 focused sessions. No origin behavior change until workstreams below wire in.

---

## Workstream A: Grant spend category (priority 1)

**Gap:** `grant_spend_category()` is regex-only at build time. State dashboards need intent, not keyword presence.

### A1. Model and prompt

| Step | Detail |
| --- | --- |
| Prompt | `grant_spend_prompt(description, lid?, fiscal_year?)` in `queries.py` |
| Evaluate | `classify_grant_spend(generate_fn, grant) -> {spend_category, reason}` |
| Labels | `maintenance`, `growth`, `planning`, `other` — reuse `SPEND_CATEGORIES` |
| Rubric | Embed decision rules from GATED_EVALUATION (reconstruct vs new runway, planning studies, ambiguous improve/upgrade) |
| Gates | Non-empty description; `APTPLANS_LLM=1`; no dollar amounts in JSON |

### A2. Grant overlay fields

```python
spend_category: str | None = None
spend_reason: str | None = None
spend_classified_at: str | None = None
spend_classifier: str | None = None  # rules | llm | human
```

### A3. Ingest wiring

| Step | Detail |
| --- | --- |
| Where | `pipeline/refresh_grants.py` after `parse_aip_grants_bytes`, before `write_grants_overlay` |
| Hybrid | Regex classifies obvious rows; LLM only when regex returns `other` or matches `_AMBIGUOUS_SPEND_RE` (`improve`, `upgrade`, `modernize`) |
| Failure | Log warning; keep regex result; set `spend_classifier=rules` |
| Pace | `PAUSE_SECONDS` between LLM calls; skip re-classify when description unchanged and `spend_category` set |

### A4. Build read path

| Step | Detail |
| --- | --- |
| Where | `site/build.py` `state_grant_allocations()` |
| Logic | `effective_spend_category(grant)` → stored field or `grant_spend_category(grant)` fallback |

### A5. Gold and eval

| Step | Detail |
| --- | --- |
| Fixture | `catalog/references/eval_gold/grant_spend.jsonl` from PDX reference grants + hand labels |
| Eval | `scripts/eval_classification.py --task grant_spend` |
| Review | `POST /v1/label` with `gold: {evaluation: "grant_spend", spend_category: "growth"}` |

### A6. RSS (optional)

Include `spend_category` in grant RSS items when set.

**Dependencies:** S1–S3 recommended; A can ship with inline pattern if registry waits.

**Acceptance:** Oregon state page maintenance/growth totals unchanged for obvious grants; ambiguous rows differ from regex-only; CI mocks LLM; origin refresh writes `spend_classifier=llm` rows.

---

## Workstream B: Finance verify in worker (priority 2)

**Gap:** `verify_finance()` exists and passes `make llm` but is not in the production worker loop.

### B1. Trigger points

| When | Detail |
| --- | --- |
| After preserve | Document `kind` unknown or `finance_candidate` from explore |
| State budget fetch | New job kind `vet_finance` or extend `vet` with finance rubric when excerpt matches budget/grant-table shape |
| Refresh | Monthly state budget PDF re-vet when hash changes |

### B2. Overlay fields on Document

```python
finance_kind: str | None = None
finance_scope: str | None = None
finance_verified_at: str | None = None
finance_reason: str | None = None
```

### B3. Publish rules

| `finance_kind` | Action |
| --- | --- |
| `issued_grants` | Link to grant ingest or airport funding section |
| `program_budget` | State budget section |
| `not_finance` | Do not list as finance artifact |
| `cip_proposed` | Separate “proposed” badge, not awarded |

### B4. Gold

Fixture PDFs: Oregon budget, FAA grant history xlsx export (text excerpt). `eval_gold/finance_verify.jsonl`.

**Dependencies:** S3; overlaps with document vet job queue.

---

## Workstream C: Planning outlook / trajectory (priority 3)

**Gap:** `brief.score_signals()` keyword-counts plan text for growing/declining/maintaining.

### C1. Prompt

| Step | Detail |
| --- | --- |
| Input | Plan excerpt(s) from verified docs only (`has_verified_plans` gate) |
| Output | `{band: growing\|declining\|maintaining, reason, horizon_years?}` |
| Rubric | Capacity adds vs contraction vs status quo; cite forecast language, not dollar amounts |

### C2. Storage

| Step | Detail |
| --- | --- |
| Where | `overviews.jsonl` trajectory block |
| Refresh | `pipeline/overviews.py` monthly; LLM when plan text hash changes |
| Fallback | Keep `score_signals()` when `APTPLANS_LLM` unset |

### C3. Site gate

Already gated by `show_plan_insights` on airport pages. RSS overview item follows same gate.

**Dependencies:** Verified plan text extract path; S3.

---

## Workstream D: Ambiguous hub links (priority 4)

**Gap:** `classify_link()` regex on URL + anchor text; many PDFs return `unknown`.

### D1. Trigger

Only when `classify_link()` yields `role=artifact`, `kind_guess=unknown`, and URL is on allowed host.

### D2. Prompt

Input: URL, label, optional snippet from hub page. Output: `{kind_guess, role, fetch_priority, reason}`.

### D3. Wiring

`pipeline/explore.py` after `html_links()`; do not block explore on LLM — queue `classify_link` jobs or classify inline with timeout.

**Dependencies:** S3; lower volume than grants.

---

## Workstream E: Budget PDF line items (priority 5)

**Gap:** State budgets are structured fixtures today; unstructured PDFs will need row classification.

### E1. Two-step

1. `verify_finance` → confirms official budget table.
2. `classify_budget_line_prompt` → per row: `program` \| `fund` \| `project` \| `airport_allocation`.

### E2. Storage

Extend `Budget` / `BudgetLine` with `line_kind`, `spend_category` (maintenance/growth optional).

**Dependencies:** B complete; state budget PDF preserve path.

---

## Workstream F: Audit and observability

| Item | Work |
| --- | --- |
| Outcomes | Append `classification` events to `outcomes.jsonl`: `{evaluation, input_id, category, classifier, reason}` |
| Review API | `GET /v1/evaluations` — counts by task and classifier |
| About page | Optional “classifications this month” stat from overlay |
| Logs | Structured log line per LLM classification (no full description in log if long) |

---

## Suggested implementation order

```
Phase 0 (infra)     S1 → S2 → S3 → S5
Phase 1 (grants)    A1 → A2 → A4 → A3 → A5 → A6
Phase 2 (finance)   B1 → B2 → B3 → B4
Phase 3 (outlook)   C1 → C2 → C3
Phase 4 (explore)   D1 → D2 → D3
Phase 5 (budget)    E1 → E2
Ongoing             F + S4 gold loop
```

## Risk and constraints

| Risk | Mitigation |
| --- | --- |
| Origin LLM load | Hybrid regex-first; classify only changed/ambiguous rows |
| Rubric drift | Version rubric in prompt footer (`rubric_version: 1`); gold eval catches regressions |
| Stale classifications | Re-run when `description` or source hash changes |
| CI | Never live Ollama; mock `generate_fn`; gold eval scripts manual |
| Wrong publish | Classifications are enrichment only; never override SSI/kind/review gates |

## Open decisions

1. **Single vs per-task model calls** — start per-task; batch prompts later if volume hurts.
2. **Human review queue** — auto-queue `spend_classifier=llm` + `other` for spot check via review API?
3. **Planning flag** — merge `is_planning` regex into LLM rubric only, or keep regex as pre-filter?
4. **Registry location** — `pipeline/evaluations.py` vs extend `queries.py` only until registry proves useful.

Track progress by updating the Status column in [GATED_EVALUATION.md](GATED_EVALUATION.md) as each workstream ships.

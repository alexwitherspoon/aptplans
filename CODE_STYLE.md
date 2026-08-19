# Code style

Standards for AptPlans. Follow these in pull requests. Indentation and line endings are also in [`.editorconfig`](.editorconfig).

This is an **unofficial document library**, not flight-planning weather and not legal advice. Reliability still matters: hashes, official URLs, and published copy must be trustworthy. Do not present the site as an FAA, state, or airport publication.

## Product and copy

- Official sources are the citation of record. A document is `complete` only with both an official URL and a hash-verified preserved copy.
- Name **airport master plans** and **Airport Layout Plans (ALPs)** as coequal works. Use the FAA term Airport Layout Plan on first mention, then ALP. An ALP without a narrative master plan is still a catalogued document (`kind: alp`), not `no_plan_known`.
- Summaries and change notes are unofficial. Do not brand a model or call the site an "AI product" on public pages.
- Keep templates few and CSS thin. Search, map, and document pages are static HTML. Do not add a JavaScript SPA.
- User-facing crawlers identify as `aptplans.org`.
- The pipeline is gated logic. The local model is a subroutine the worker may call for a typed question after those gates pass. It does not run the pipeline, browse, or override a failed check. If a TOC is missing, the worker still sends a viable 32k text chunk rather than stopping for a human.
- In prose, headings, and table cells, do not use the Unicode em dash. Use a single ASCII hyphen (`-`) for a break or aside. Keep `--` only where Markdown or a CLI example needs it.

`docs/` describes required behavior in the present tense (what the system does and why). Track gaps in GitHub issues, not in architecture docs.

## Python

Python 3.12+. New modules live under `site/`, `catalog/`, `pipeline/`, or `tests/`. Do not name a package `site` as an importable distribution; `site/build.py` is a script.

- 4-space indent, UTF-8, type hints on new public functions.
- `pathlib.Path` over string paths. `from __future__ import annotations` is fine.
- Fail explicitly. Do not swallow hash mismatches, HTTP errors, or missing official URLs.
- Jinja is loaded with autoescape for HTML. Do not mark untrusted document text as safe.
- Comments explain why, not what. Remove comments that no longer match the code. Do not leave "changed X to Y" notes; git history covers that.

## HTML, CSS, and Jinja

- Templates extend a small base. One accent color, readable measure, no card grids or stock heroes.
- Class names are ordinary English (`site-header`, `lede`). No utility-class frameworks.
- CSS in `site/static/css/`. 2-space indent. Prefer a few custom properties over a design-token pile.
- Every document page states that the site is unofficial. About copy stays short.

## Shell, Make, and host scripts

Host scripts under `scripts/host/` are idempotent and must be safe to re-run from CD.

- `set -euo pipefail` on new bash.
- 2-space indent in `.sh` files. Makefiles use tabs.
- Quote variables. Prefer `$(id -u)` tests over assuming `sudo` exists (console bootstrap may be root; CD is always `aptplans`).
- Do not `ufw --force reset` from CD. Do not install Python, Caddy, nginx, or certbot on the origin host.
- Log in UTC timestamps or systemd's journal. No emoji in operational scripts.

## Catalog and pipeline

- Schema changes go in `catalog/schema.json` and tests.
- Completeness states stay `complete`, `link_only`, `preserved_only`, `missing`, `no_plan_known`. `no_plan_known` means neither a master plan nor an ALP is known.
- Same official URL plus a new SHA-256 is a new version.
- Do not commit PDFs, WARCs, `.gguf` weights, extracted full text, or `.env` files.

## Tests

- `make test` must pass. Add tests next to new behavior (schema, builder, later fetch/hash).
- CI must not need the KS-6, PDFs, or model weights.
- Do not require Docker for unit tests.

## Git

```
Short summary (50 chars or less)

Why the change exists, if that is not obvious. Wrap at 72.
```

Branches: `feature/`, `fix/`, `docs/`. One logical change per PR. Do not commit Cursor/plan scratch, `dist/`, or anything matching the diagnostic doc patterns in `.gitignore`.

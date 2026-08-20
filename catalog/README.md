Catalog metadata lives in this directory. Source PDFs do not, except hashed fixtures under `references/files/`.

A document is **complete** only when both an official `source_url` and a hash-verified preserved copy exist. See `schema.json`, `change_event.schema.json`, and [Architecture](../docs/ARCHITECTURE.md).

This GitHub repository holds reference fixtures only. Airport identity is not committed. Origin fetches FAA NASR APT (the public-use superset) and NPIAS Appendix A into overlay `airports.jsonl`, and AIP grant histories into `grants.jsonl`. NPIAS is a likelihood flag, not a gate. CI and a git-only site build use `references/` (about seven airports). See `data/README.md` and [FAA terms and systems](../docs/FAA.md).

- `references/` holds known-good official master plans and Airport Layout Plans used as development fixtures. They stay `link_only` until the worker stores a hash. `references/grants.json` is a small LocID sample so git-only builds can render airport Funding and the state Projects and allocations list; origin `grants.jsonl` replaces it. `references/budgets.json` holds a statewide aviation budget breakdown (Oregon 2025-27 LAB) so state hubs can render program totals; origin `budgets.jsonl` replaces it. `references/states.json` names each state's aviation agency. `references/statutes.json` holds a few official law and SASP citations so state hubs are not empty.
- Origin overlay JSONL (`APTPLANS_CATALOG_OVERLAY`) records airport identity after a monthly refresh, plus hashes and completeness after a fetch. GitHub stays the public code index; the overlay is origin disk. An optional private git on origin may back up `/var/lib/aptplans/files`; it is not this repository.

Do not add origin corpus files, overlay JSONL, or compiled statute texts here.

Catalog metadata lives in this directory. Source PDFs do not.

A document is **complete** only when both an official `source_url` and a hash-verified preserved copy exist. See `schema.json` and [Architecture](../docs/ARCHITECTURE.md).

Known-good official master plans and Airport Layout Plans used as development fixtures live in [`references/`](references/). They stay `link_only` until the worker stores a hash. A small set of full PDFs is committed under [`references/files/`](files/) for deterministic tests. Do not add origin corpus files here.

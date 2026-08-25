# Immutable extraction contract

Status: accepted for the Milestone 3 extraction cutover.

## Decision

Artifact identity is the SHA-256 of the preserved bytes. The worker records each
content hash once in `artifact_versions`. An extraction is identified by:

- artifact SHA-256;
- extractor version, including the local OCR engine version or its disabled state;
- SHA-256 of canonical extraction options.

The resulting manifest key is the SHA-256 of those three values. Replaying the
same tuple returns the existing immutable manifest without invoking a parser or
OCR engine again.

SQLite stores the artifact and manifest index. Full manifests remain private on
disk under `APTPLANS_EXTRACTIONS/<artifact-sha>/<manifest-key>.json`. They contain
every one-based page, page text and hash, extraction method, quality signals,
errors, and word coordinates when OCR produced them. Neither this directory nor
the job ledger is served by Caddy.

`artifact_versions` and `extraction_manifests` are append-only. Corrected parser
or routing behavior requires a new extractor version or options hash; it never
rewrites prior results.

## Routing

The first pass is deterministic:

1. Keep native PDF text when it meets the configured character floor.
2. Route low-text pages with a sufficiently large raster image to local OCR.
3. Mark an image-only page `supervised` when OCR is unavailable, fails, or
   returns no text.
4. Keep genuinely empty low-image pages as `empty`.

The production worker uses Poppler to render selected pages and Tesseract to
produce TSV. TSV word boxes and confidence values are retained as evidence.
OCR is self-hosted and controlled by `APTPLANS_OCR`; no document bytes leave the
worker.

The legacy page JSONL used by search is now a projection of the extraction
manifest. It remains an interchange/search input, not authoritative extraction
state.

## Operational boundary

Production enables OCR in the worker container. `APTPLANS_EXTRACTIONS` must be a
persistent volume backed up with the artifact and ledger data. The review API
does not write extraction state.

The Brookings FY2025-26 adopted budget is the first image-only gate. Its airport
narrative is native text on page 56; pages 57-58 are full-page airport fund
scans (page 59 begins a different fund). CI uses a
deterministic fake OCR backend to test routing, coordinates, immutability, and
cache reuse. Deployment-hardware measurements are still required before setting
final DPI, timeout, batch, and escalation thresholds.

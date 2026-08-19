# Architecture

AptPlans is a static document library at [aptplans.org](https://aptplans.org). `aptplans.com` redirects there. The hard problem is discovery, durability, and change over time, not the website chrome.

Official sources remain the citation of record. This service also keeps a preservation copy so files survive when sponsor sites disappear, and so replacements can be diffed.

## What we cover

- Every airport in the current NPIAS (about 3,287 public-use facilities), not only primary hubs.
- Every published **airport master plan** and **Airport Layout Plan (ALP)** we can find for those airports. An ALP is first-class even when no narrative master plan is published.
- Aviation, airport, and airport land-use statutes in all 50 states. Municipal zoning of every city is out of scope.

An ALP is the FAA drawing set that depicts existing facilities and planned development. FAA approval of an ALP indicates that depicted existing and proposed development conforms to airport design standards (or an approved modification to standards). For federally obligated airports, keeping a current ALP is a grant-assurance requirement. Proposed development must appear on an FAA-approved ALP to be eligible for Airport Improvement Program (AIP) funding. FAA strongly recommends airport master plans ([AC 150/5070-6B](https://www.faa.gov/airports/resources/advisory_circulars/index.cfm/go/document.current/documentnumber/150_5070-6)); an ALP update is normally an element of that study, and a narrative report often accompanies the drawing set.

Related studies (Part 150, NEPA, minimum standards, CIP standalones) may be named on an airport page later. They are not ingested in the first catalog.

There is no federal repository of these PDFs. Master plans and ALPs live on airport sites, city and county portals, consultant microsites, and state aviation offices.

## System shape

```
GitHub (this repo)          Origin host (KS-6)           Cloudflare
------------------          -------------------           ----------
builder, compose,           hashed PDFs / WARCs          cache in front
systemd unit, crawlers      extracted text               of Caddy
catalog JSON                local CPU parse jobs
statute snapshots           generated HTML/RSS
summaries after review      Ollama GGUF (internal net)
```

Visitors hit Cloudflare, then Caddy on the origin. The public site is static HTML, RSS, and PDF downloads. There is no app server with sessions, no accounts, and no public chat.

```
discover -> fetch/hash -> store on disk -> parse -> summary/diff -> review -> publish catalog + HTML + RSS
```

Jobs are a single serial queue. Compose runs three services: `site` (Caddy), `worker`, and `ollama`. A systemd timer execs into `worker` for one document (or one statute snapshot) at a time.

## Repository layout

```
aptplans/
├── site/                 # Jinja templates, CSS, static builder
├── catalog/              # metadata schema and (later) records
├── pipeline/             # fetch / parse / publish job
├── docker/               # Caddy, worker, and Ollama Compose stack
├── scripts/host/         # idempotent Debian 13 bootstrap used by CD
├── config/host/          # sshd, sysctl, UFW helpers, unattended-upgrades
├── systemd/              # pipeline timer and Monday reboot
├── docs/
├── tests/
└── dist/                 # generated HTML (not committed)
```

**In git:** builder, Compose files, systemd units, pipeline, catalog metadata, statute snapshots, reviewed summaries, RSS source data.

**Not in git:** PDFs, WARCs, model weights, extracted full text. Those stay on the origin disk under `/var/lib/aptplans/files`.

## Dual-source records

Every published document carries both pointers. The UI shows **Official source** first, then **Archived copy**.

Minimum fields (see [`catalog/schema.json`](../catalog/schema.json)):

| Field | Role |
| --- | --- |
| `source_url` | Sponsor / FAA / legislature URL |
| `source_retrieved_at` | Last successful fetch |
| `source_status` | `live` / `moved` / `dead` / `replaced` |
| `content_sha256` | Version identity of preserved bytes |
| `preserved_url` | Copy served from this origin |
| `license_or_rights` | public record / government edict / unknown / takedown |
| `supersedes` | Prior version id |
| `review_status` | pending / auto_pass / needs_human / published |

Completeness:

- `complete` - official URL recorded **and** preserved copy stored and hash-verified
- `link_only` - official file found, copy not ingested yet (incomplete)
- `preserved_only` - official URL is dead or replaced; our copy remains
- `missing` - nothing located yet
- `no_plan_known` - sponsor or state confirms neither a master plan nor an ALP is known

An ALP on its own is a complete kind of record (`kind: alp`). Do not file that case as `no_plan_known`.

`ia_item` is reserved. This project does not upload to the Internet Archive in the first deployment. Wayback CDX is used only as a discovery source for historical official PDFs; any bytes we keep are copied onto origin disk.

Same official URL with a new hash is a new version.

## Discovery

Seed, in order:

1. NPIAS airport list (role, hub, development need)
2. FAA NASR APT data and ADIP 5010 fields
3. [OurAirports](https://ourairports.com/data/) public-domain identifiers (ICAO / IATA / FAA LID / coordinates)

Find documents, in order:

1. State aviation / DOT aeronautics sites
2. Airport, city, and county document centers and planning or ALP microsites
3. Targeted search for master plan and Airport Layout Plan / ALP PDFs
4. Wayback CDX for vanished official URLs
5. Community intake via GitHub issues
6. Public-records requests for the remainder

Crawlers send an identifiable User-Agent of `aptplans.org`, honor robots.txt, and request one host at a time with backoff.

## Site

A small Python/Jinja builder (`site/build.py`) writes HTML, CSS, RSS, and a sitemap into `dist/`. Templates stay few. CSS stays thin. The coverage map can use Leaflet later; this is not a JavaScript application.

Intended pages:

- Home: search first, coverage map, recently changed, corpus counts
- Airport: identity, NPIAS role, master plan and ALP versions, unofficial summary, Official + Archived, RSS
- State: agency, SASP, statute guide, that state’s airports, RSS
- Document: permalink, dates, version timeline, unofficial summary, PDFs

Feeds (static, generated at build):

- `/feeds/all.xml`
- `/feeds/laws.xml`
- `/feeds/states/{st}.xml`
- `/feeds/airports/{lid}.xml`
- `/feeds/topics/{slug}.xml`

Email is not a product. People who want mail can point IFTTT at a feed.

Summaries and change notes are unofficial. They are produced by this project to help someone find the right chapter in a long PDF. Document pages do not brand a model or an “AI product.” The About page states the unofficial status.

## Origin host

Debian 13 (trixie) on an OVH Eco KS-6 (US East). GitHub Actions CD bootstraps the box from a bare install and keeps it there: Docker Engine, UFW, fail2ban, unattended-upgrades. Caddy, the worker, and Ollama are Compose services, not host packages. Timezone is `America/Los_Angeles`. The host reboots Monday at noon Pacific.

Disk is the two host spindles in software RAID1. HTML, the catalog checkout, model weights, and PDFs share that mirror. Expected corpus is well under the usable capacity.

Parse and summary jobs run locally on CPU, one at a time, from the preserved copy. Native text is preferred; otherwise layout/OCR on CPU, then a local document pass for TOC, facts, and a one-page unofficial summary. Pairwise change notes run when a prior version exists. Thinking-heavy passes are reserved for hard diffs, not bulk pages. Large PDFs are not fed whole into a vision stack; selected page or ALP sheet images may be captioned after render.

### Model calls

Gated logic runs the pipeline. The local model does not search, browse, or decide what to fetch. The worker calls it only for specific questions after outer gates pass (known airport or state, allowed host, fetched bytes, size cap, not SSI-shaped). Each call uses a fixed prompt, a 32k context window, and must return schema JSON. A failed gate is never overridden by the model.

Useful calls include: classifying a file against a frozen plan/ALP shape card (and example TOCs from `complete` records); pulling draft-plan or public-comment links already present in fetched HTML; unofficial section summaries and a one-page reduce.

The worker extracts a TOC when the PDF outline, a contents page, or numbered chapter headings exist. The first model call gets title page plus TOC. The model may request at most one extra round of slices, and only ids or page ranges already in that TOC, still inside 32k. If no TOC or other high-signal structure is found, the worker still sends a viable chunk (the next unused window of extracted text that fits 32k) and continues chunk-then-reduce across the document. Missing a TOC is not a reason for `needs_human`.

`needs_human` is rare: SSI-shaped files, hash mismatch, or a URL the worker never fetched. Low-confidence wording still yields an unofficial note when the gates passed. Newsletters and news articles fail *kind* gates (`newsletter` / `news`); they are not ingested as the plan.

The local model is **1-bit Bonsai 27B** (`prism-ml/Bonsai-27B-gguf`, Apache-2.0) served by a single CPU Ollama container. CD downloads the GGUF and runs `ollama create` when the model is missing. Stock Ollama cannot load ternary (`Q2_0`) Bonsai, so this host does not use that family. Ollama has no published ports: it joins an internal Compose network (`aptplans_llm`) that only the `worker` service can reach. The public `site` service does not join that network. On the KS-6, Ollama is cpuset-pinned to NUMA nodes 1-3 (12 physical cores / 24 threads; llama.cpp uses 12 threads). Caddy, the worker, and the host keep NUMA node 0. `OLLAMA_KEEP_ALIVE=-1` keeps Bonsai resident after the first load.

A job may take hours. The public site does not wait. After the first national backfill, steady state is a weekly URL/hash poll. Success is the count of `complete` records over months, not documents per hour.

What we do not run: Kubernetes, Swarm, Redis, Postgres, Prometheus, a public chatbot, or a second box “for scale.”

## Cloudflare

Both domains use Cloudflare DNS. `aptplans.org` is orange-clouded to the origin. `aptplans.com` (apex and www) 301s to `https://aptplans.org`.

Hashed PDFs under `/files/{sha256}.pdf` get long immutable cache headers. HTML and RSS stay shorter. Cloudflare is a cache, not a second copy of the bytes, and not the place the pipeline runs.

## Legal posture

This is not legal advice. State statutes are generally public domain as government edicts. Airport master plans and Airport Layout Plans are usually public records of a sponsor; consultant copyright lines still appear. We cite the source, keep copies for preservation, access, and diffing, publish a takedown path, never present the site as official, and skip SSI/security-looking appendices.

## Replaceable origin

Rebuild: Debian 13 plus an SSH key, then GitHub Actions CD (or `scripts/host/remote-deploy.sh`). The catalog in git is the index. The origin disk is the file store. There is no offsite document replica in the first deployment.

# Architecture

AptPlans is a static document library at [aptplans.org](https://aptplans.org). `aptplans.com` redirects there. The hard problem is discovery, durability, and change over time, not the website chrome.

Official sources remain the citation of record. This service also keeps a preservation copy so files survive when sponsor sites disappear, and so replacements can be diffed.

## What we cover

- Public-use airports and seaplane bases in current FAA NASR APT (the identity superset we would consider), not only primary hubs.
- NPIAS Appendix A as a likelihood overlay: those airports are more likely to publish a master plan or ALP. An airport not in NPIAS can still have a plan; a GitHub issue or a found official URL is enough to admit it.
- Every published **airport master plan** and **Airport Layout Plan (ALP)** we can find for those airports. An ALP is first-class even when no narrative master plan is published.
- Aviation, airport, and airport land-use statutes in all 50 states. Municipal zoning of every city is out of scope.

An ALP is the FAA drawing set that depicts existing facilities and planned development. FAA approval of an ALP indicates that depicted existing and proposed development conforms to airport design standards (or an approved modification to standards). For federally obligated airports, keeping a current ALP is a grant-assurance requirement. Proposed development must appear on an FAA-approved ALP to be eligible for Airport Improvement Program (AIP) funding. FAA strongly recommends airport master plans ([AC 150/5070-6B](https://www.faa.gov/airports/resources/advisory_circulars/index.cfm/go/document.current/documentnumber/150_5070-6)); an ALP update is normally an element of that study, and a narrative report often accompanies the drawing set.

Related studies (Part 150, NEPA, minimum standards, CIP standalones) may be named on an airport page later. They are not ingested in the first catalog.

There is no federal repository of these PDFs. Master plans and ALPs live on airport sites, city and county portals, consultant microsites, and state aviation offices. Glossary: [FAA terms and systems](FAA.md).

## System shape

```
GitHub (this repo)          Origin host (KS-6)           Cloudflare
------------------          -------------------           ----------
builder, compose,           hashed PDFs / WARCs          cache in front
systemd unit, pipeline      overlay JSONL                of Caddy
reference fixtures only     generated HTML/RSS
                            optional private git for
                            blob backup (not GitHub)
                            Ollama GGUF (internal net)
```

Visitors hit Cloudflare, then Caddy on the origin. The public site is static HTML, RSS, and PDF downloads. There is no app server with sessions, no accounts, and no public chat.

```
discover -> fetch/hash -> store on disk -> parse -> summary/diff -> review -> publish catalog + HTML + RSS
```

Jobs are a single serial on-disk queue. Compose runs four services: `site` (Caddy), `search` (Meilisearch), `worker`, and `ollama`. The worker process is the scheduler: concurrency 1, start the next job when the last one finishes, sleep when the queue is empty. GitHub intake is polled at most hourly while idle (`APTPLANS_INTAKE_IDLE_SEC`). On origin, when the worker container starts, it checks FAA overlays and fetches only if a file is missing, empty, or from a prior month (one request at a time, pause between hosts). Document jobs do not refresh those overlays. Local Compose does not live-fetch FAA. A daily timer probes official URLs (HEAD, then a tiny GET if HEAD is unsupported): live, moved, or dead. Dead URLs with a preserved copy become `preserved_only`; without a copy they become `missing` and the worker tries mirrors, then Wayback CDX (`APTPLANS_WAYBACK=1` on origin) to queue a fetch. 5xx is not dead. A monthly timer refreshes NASR, NPIAS, and AIP grants if that month's overlay is not already on disk. Check and refresh take the same flock as the drain loop so they do not overlap a fetch or Ollama call. Overlay writes finish under that flock; the HTML rebuild runs after the lock drops. Uncaught job errors retry from `active/` with backoff and give up after three attempts (`needs_human`).

## Publish path

This GitHub repository is code and test fixtures only. Airport identity is fetched on origin (NASR + NPIAS) into `/var/lib/aptplans/catalog/airports.jsonl` and is not committed. CD builds HTML on the GitHub runner from the git catalog (reference airports plus 50 states) and rsyncs `dist/` to `/var/lib/aptplans/site`. Origin then rebuilds from git plus overlay so the full NASR list and worker hashes are not wiped.

The worker runs on the origin. After a fetch it writes completeness and hashes to `/var/lib/aptplans/catalog` (overlay JSONL), stores bytes at `/var/lib/aptplans/files/{sha256}.pdf`, and writes gated native page text at `/var/lib/aptplans/text/{sha256}.jsonl`. It does not commit catalog JSON or PDFs to GitHub. After each successful preserve it upserts Meilisearch and rebuilds HTML from git plus overlay into `/var/lib/aptplans/site`.

Origin may keep a **separate private git** of `/var/lib/aptplans/files` for blob backup. That repo is not `github.com/alexwitherspoon/aptplans` and is not synced to GitHub.

## Repository layout

```
aptplans/
├── site/                 # Jinja templates, CSS, static builder
├── catalog/              # schema, reference cases, statute slots
├── pipeline/             # fetch / parse / publish job
├── docker/               # Caddy, Meilisearch, worker, and Ollama Compose stack
├── scripts/host/         # idempotent Debian 13 bootstrap used by CD
├── config/host/          # sshd, sysctl, UFW helpers, unattended-upgrades
├── systemd/              # daily links, monthly airports, Monday reboot
├── docs/
├── tests/
└── dist/                 # generated HTML (not committed)
```

**In this GitHub repo:** builder, Compose files, systemd units, pipeline, 50 state hubs, reference-case official URLs, hashed reference PDFs under `catalog/references/files/`. Overlay airport identity, completeness, and unofficial notes after a fetch live on origin disk until the next HTML rebuild.

**Not in this GitHub repo:** the origin corpus (PDFs, WARCs), model weights, extracted full text, NASR/NPIAS/grant overlays, compiled statute texts. Those stay on origin disk under `/var/lib/aptplans/`. An optional private origin git may snapshot the file store; it is not this repository.

## Dual-source records

Every published document carries both pointers. The UI shows **Official source** first, then **Archived copy**.

Minimum fields (see [`catalog/schema.json`](../catalog/schema.json)):

| Field | Role |
| --- | --- |
| `source_url` | Sponsor / FAA / legislature URL |
| `source_retrieved_at` | Last successful fetch |
| `source_status` | `live` / `moved` / `dead` / `replaced` |
| `content_sha256` | SHA-256 of preserved file bytes (storage identity) |
| `text_sha256` | SHA-256 of normalized extracted text |
| `images_sha256` | SHA-256 of embedded PDF image bytes |
| `preserved_url` | Copy served from this origin |
| `publisher` | Source entity (used for `kind: notice`) |
| `published_at` | Date the source published (not our retrieval) |
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

Same official URL keeps the same `document_id`. A new byte hash is stored either way. It is a **content version** when `text_sha256` or `images_sha256` changes (narrative or drawings). If only the PDF wrapper changed, we keep the new official file and record that text and drawings are unchanged.

One **work** is an airport's master plan or that airport's ALP (`work_key`: LID + kind). A later study for the same airport is a new version of that work (`supersedes`), not a second work. Named chapters of one edition stay separate records and do not supersede each other. The same text and drawings at a new URL reuse the existing record and add a mirror.

## Discovery

Seed, in order:

1. FAA NASR APT (28-day) public-use airports and seaplane bases: LID, name, ICAO, coordinates, status. This is the full superset we would consider.
2. NPIAS Appendix A (role, hub). Merge onto NASR. NPIAS is not a gate; it marks airports more likely to have a published master plan or ALP.
3. FAA AIP grant histories (LocID, amount, project description) plus USAspending spent and remaining obligation when origin can match the grant number. Shown on the airport page under Funding / Federal, and on the state page under Projects and allocations. Planning-worded rows are a hint that a study was funded, not the PDF.
4. Statewide aviation agency budgets (legislatively adopted or enacted). Shown on the state page as a program and fund-type breakdown. Federal dollars in a state agency budget are not AIP awards to a LocID. A cited project list can add `BudgetLine` rows (`group: project`, optional `airport_lid`); do not invent those from program totals.
5. State, local, and other airport-level awards (state aviation grants, municipal CIP, passenger facility charges, bonds) when an official record is found. Same Funding section, other levels, and the same LocID list on the state page. Many sources; coverage will lag federal AIP.
6. [OurAirports](https://ourairports.com/data/) public-domain identifiers (ICAO / IATA / FAA LID / coordinates)

Find documents, in order:

1. Known hosts first: the airport `website` from NASR and the state `agency_url` (site-restricted crawl, then site-restricted search queries)
2. Deterministic search-engine queries from NASR identity (name, LID, city, state) and from state agency identity. Templates live in `pipeline/queries.py`, grouped by target: master plan and ALP, statewide budget, state award lists, sponsor CIP, PFC, SASP and statute. Origin may call **one** search API behind `APTPLANS_SEARCH_KEY` (Brave Search or Google CSE). Do not scrape HTML result pages. Do not run a local metasearch daemon. CI must not set that key or live-query search APIs.
3. Wayback CDX for vanished official URLs
4. Community intake via GitHub issues (form fields are hints: add, stale, wrong, outdated, or other)
5. Public-records requests for the remainder

After a candidate URL is fetched and outer gates pass, a gated model call may verify the **already-fetched** page or PDF. There are two tracks. **Plans:** is this an official master plan or ALP for this LID; are there PDF links on a hub page; is this a later edition of the same work rather than a chapter; for a news page, publisher and published date only. **Finance:** is this an official budget, issued-grant table, LocID project list, CIP, PFC, or bond record; what shape; whether rows are LocID-keyed. The finance verifier does not return dollar amounts. Amounts are published only from a deterministic parse or a cited transcription of that file. A budget or award list is not a plan. The model does not search, browse, or override a failed gate.

An issue is a hint for the serial queue, not a publish switch. The worker parses form fields only. A well-formed FAA LID plus official URL is enough to queue even when the LID is not in NPIAS; the worker admits that airport into the overlay. If it fetched and preserved the file, confirmed a dead URL, classified the file as not a plan, or skipped an SSI-shaped filename, it comments and closes the issue. If a human is needed (no LID, no URL, hash mismatch, or a URL it never fetched), it comments, mentions @alexwitherspoon, and leaves the issue open. Suggested kind on the issue is a note the classifier can reject.

Origin fetches NASR, NPIAS, and AIP grant histories when those overlay files are missing or have not been written this calendar month. Grant refresh then POSTs award IDs to USAspending for obligated and outlayed amounts. That check runs when the worker starts, before a `run_once` job, and on the monthly timer (1st, Pacific). Restarts with current files skip the download. CI must not live-fetch FAA or USAspending.

Crawlers send an identifiable User-Agent of `aptplans.org`, honor robots.txt, and request one host at a time with backoff.

Known-good official URLs used as development fixtures live in [`catalog/references/`](../catalog/references/). A subset of those files is committed under `catalog/references/files/` so tests can hash bytes without a network fetch. Completeness stays `link_only` until the worker stores an origin copy. Classification uses the AC 150/5070-6B shape card in that directory.

## Site

A small Python/Jinja builder (`site/build.py`) writes HTML, CSS, RSS, and a sitemap into `dist/`. The sitemap is generated from the pages and feeds that build just published (HTML plus `/feeds/*.xml`), with `lastmod` when the catalog has a date. It does not list CSS, JS, or `/data/` dumps. Templates stay few. CSS stays thin. There is no map. This is not a JavaScript application. A second run with the same catalog, templates, and static files is a no-op: it does not wipe the tree or render pages.

Native pages set canonical URLs, Open Graph, `rel="alternate"` RSS, and JSON-LD (WebSite search on the home page; Airport, AdministrativeArea, or CreativeWork plus breadcrumbs on those records). `robots.txt` allows the site, points at the sitemap, and keeps `/data/` out of crawlers.

`/search/` queries Meilisearch through Caddy (`POST /search/query`). The daemon has no published host port. Caddy injects the master key; the browser never talks to port 7700. Ranking prefers LID and title, then preserved page text, then unofficial summary, so a phrase such as "remove building" or "modify runway" can hit language inside a master plan, ALP, or cited project. Search fields offer as-you-type suggestions through that same POST (prefix match; catalog JSON if the daemon is down). Notices are metadata only. SSI-shaped files are never extracted or indexed. If the daemon is down, the page falls back to `/data/search.json` (titles and labels only). Extracted text stays off the Caddy docroot. Rebuild the index with `python3 -m pipeline.search --reindex` on the worker.

The builder writes:

- Home: search, coverage counts, recently recorded, state index, RSS
- Airport: identity (LID, ICAO, location, NPIAS, ownership), a short catalog briefing, master plan and ALP with official and preserved links, funding (federal, state, local, other), notice citations, state aviation hub, RSS
- State: agency, SASP, statute guide, statewide aviation budget (program totals), LocID project awards (preview, with the rest on the airport page), that state's airports, RSS
- Document: permalink, dates, version pointers, unofficial summary, Official + Archived
- Feeds: HTML map of RSS (`/feeds/`) so a reader can pick recently recorded, law, one state, or one airport

Feeds (static, generated at build). Each native page advertises the matching feed with `rel="alternate"` so an RSS reader can subscribe from that URL:

- `/feeds/` (HTML index of the feed tree)
- `/feeds/all.xml` (recently recorded)
- `/feeds/laws.xml` (statutes and SASP)
- `/feeds/states/{st}.xml`
- `/feeds/airports/{lid}.xml` (airports that have documents)

Email is not a product. People who want mail can point IFTTT at a feed.

Summaries and change notes are unofficial. They are produced by this project to help someone find the right chapter in a long PDF. Document pages do not brand a model or an "AI product." The About page states the unofficial status.

## Origin host

Debian 13 (trixie) on an OVH Eco KS-6 (US East). GitHub Actions CD bootstraps the box from a bare install and keeps it there: Docker Engine, UFW, fail2ban, unattended-upgrades. Caddy, the worker, and Ollama are Compose services, not host packages. Timezone is `America/Los_Angeles`. The host reboots Monday at noon Pacific.

Disk is the two host spindles in software RAID1. HTML, the catalog checkout, model weights, and PDFs share that mirror. Expected corpus is well under the usable capacity.

Parse and summary jobs run locally on CPU, one at a time, from the preserved copy. Native text is preferred; otherwise layout/OCR on CPU, then a local document pass for TOC, facts, and a one-page unofficial summary. Pairwise change notes run when a prior version exists. Thinking stays off on `/api/generate` until chain-of-thought can be kept out of the published paragraph. Large PDFs are not fed whole into a vision stack; selected page or ALP sheet images may be captioned after render.

### Model calls

Gated logic runs the pipeline. The local model does not search, browse, or decide what to fetch. The worker calls it only for specific questions after outer gates pass (known airport or state, allowed host, fetched bytes, size cap, not SSI-shaped). Each call uses a fixed prompt, a 32k context window, and must return schema JSON. Requests set `think: false` on `/api/generate`. With this Bonsai GGUF, turning thinking on puts chain-of-thought into `response` instead of a separate field, which would publish reasoning onto unofficial notes. JSON verify on the Mulino/LAB fixtures matched think-off. Local `make llm` also forces thinking off. `APTPLANS_LLM_THINK=1` is only for a quality compare. A failed gate is never overridden by the model.

Useful calls include: classifying a file against a frozen plan/ALP shape card (and example TOCs from `complete` records); classifying a finance excerpt as issued grants, program budget, project list, CIP, PFC, or not-finance without returning dollar amounts; pulling draft-plan or public-comment links already present in fetched HTML; verifying a search hit against the airport LID; unofficial section summaries and a one-page reduce. Verify calls send `format: json`.

The worker extracts a TOC when the PDF outline, a contents page, or numbered chapter headings exist. The first model call gets title page plus TOC. The model may request at most one extra round of slices, and only ids or page ranges already in that TOC, still inside 32k. If no TOC or other high-signal structure is found, the worker still sends a viable chunk (the next unused window of extracted text that fits 32k) and continues chunk-then-reduce across the document. Missing a TOC is not a reason for `needs_human`. Origin jobs set `APTPLANS_LLM=1` so a successful preserve writes that unofficial paragraph onto the overlay. CI leaves the flag unset. Local Compose also leaves `APTPLANS_LLM` unset unless you opt in for a diagnostic job. Local Compose publishes Ollama at `127.0.0.1:11434` for that work. Origin does not.

`needs_human` is rare: SSI-shaped files, hash mismatch, or a URL the worker never fetched. Low-confidence wording still yields an unofficial note when the gates passed. Newsletters fail *kind* gates (`newsletter`); they are not a plan. News and press pages are situational awareness only: if catalogued at all they are `kind: notice` with publisher, published date, and URL. Do not parse or store article body. Do not write an unofficial summary of news.

The local model is **1-bit Bonsai 27B** (`prism-ml/Bonsai-27B-gguf`, Apache-2.0) served by a single CPU Ollama container. CD downloads the GGUF and runs `ollama create` when the model is missing. Stock Ollama cannot load ternary (`Q2_0`) Bonsai, so this host does not use that family. Origin Ollama has no published ports: it joins an internal Compose network (`aptplans_llm`) that only the `worker` service can reach. Local Compose also binds `127.0.0.1:11434` for diagnostics. The public `site` service does not join that network. Search (Meilisearch) shares NUMA node 0 with Caddy and the worker. On the KS-6, Ollama is cpuset-pinned to NUMA nodes 1-3 (12 physical cores / 24 threads; llama.cpp uses 12 threads). Caddy, the worker, and the host keep NUMA node 0. A laptop running the same GGUF is a different CPU and has no NUMA pin; `make llm` checks wiring, not origin throughput. Local smoke may set `APTPLANS_LLM_CTX` and `APTPLANS_LLM_PREDICT` so that laptop can finish; origin leaves both unset. `OLLAMA_KEEP_ALIVE=-1` keeps Bonsai resident after the first load.

A job may take hours. The public site does not wait. After the first national backfill, the worker stays up and takes the next pending job as soon as the last one finishes so the resident CPU model is not idle while work remains. Daily URL checks and a monthly overlay refresh share the same lock. Success is the count of `complete` records over months, not documents per hour.

What we do not run: Kubernetes, Swarm, Redis, Postgres, Prometheus, a public chatbot, or a second box "for scale."

## Cloudflare

Both domains use Cloudflare DNS. `aptplans.org` is orange-clouded to the origin. `aptplans.com` (apex and www) 301s to `https://aptplans.org`.

Hashed PDFs under `/files/{sha256}.pdf` get long immutable cache headers. HTML and RSS stay shorter. Cloudflare is a cache, not a second copy of the bytes, and not the place the pipeline runs.

## Legal posture

This is not legal advice. State statutes are generally public domain as government edicts. Airport master plans and Airport Layout Plans are usually public records of a sponsor; consultant copyright lines still appear. We cite the source, keep copies for preservation, access, and diffing, publish a takedown path, never present the site as official, and skip SSI/security-looking appendices.

## Replaceable origin

Rebuild: Debian 13 plus an SSH key for `aptplans`, then GitHub Actions CD (or `scripts/host/remote-deploy.sh`). This GitHub repo is the code and fixture index. The origin disk is the file store. There is no offsite document replica in the first deployment.

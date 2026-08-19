# Architecture

AptPlans is a static document library at [aptplans.org](https://aptplans.org). `aptplans.com` redirects there. The hard problem is discovery, durability, and change over time, not the website chrome.

Official sources remain the citation of record. This service also keeps a preservation copy so files survive when sponsor sites disappear, and so replacements can be diffed.

## What we cover

- Every airport in the current NPIAS (about 3,287 public-use facilities), not only primary hubs.
- Every published master-plan version we can find, plus the airport layout plan (ALP) when it travels with the plan.
- Aviation, airport, and airport land-use statutes in all 50 states. Municipal zoning of every city is out of scope.

Related studies (Part 150, NEPA, minimum standards, CIP standalones) may be named on an airport page later. They are not ingested in the first catalog.

There is no federal repository of master-plan PDFs. FAA recommends master plans (AC 150/5070-6B) and requires an approved ALP for AIP eligibility. Plans live on airport sites, city and county portals, consultant microsites, and state aviation offices.

## System shape

```
GitHub (this repo)          Origin host (KS-6)           Cloudflare
------------------          -------------------           ----------
builder, compose,           hashed PDFs / WARCs          cache in front
systemd unit, crawlers      extracted text               of Caddy
catalog JSON                local CPU parse jobs
statute snapshots           generated HTML/RSS
summaries after review      GGUF weights
```

Visitors hit Cloudflare, then Caddy on the origin. The public site is static HTML, RSS, and PDF downloads. There is no app server with sessions, no accounts, and no public chat.

```
discover -> fetch/hash -> store on disk -> parse -> summary/diff -> review -> publish catalog + HTML + RSS
```

Jobs are a single serial queue. A systemd timer starts `docker compose ... run --rm pipeline`. One document (or one statute snapshot) at a time.

## Repository layout

```
aptplans/
├── site/                 # Jinja templates, CSS, static builder
├── catalog/              # metadata schema and (later) records
├── pipeline/             # fetch / parse / publish job
├── docker/               # Caddy + pipeline images, compose files
├── systemd/              # timer and oneshot service
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

- `complete` — official URL recorded **and** preserved copy stored and hash-verified
- `link_only` — official file found, copy not ingested yet (incomplete)
- `preserved_only` — official URL is dead or replaced; our copy remains
- `missing` — nothing located yet
- `no_plan_known` — sponsor or state confirms none exists (or only an ALP)

`ia_item` is reserved. This project does not upload to the Internet Archive in the first deployment. Wayback CDX is used only as a discovery source for historical official PDFs; any bytes we keep are copied onto origin disk.

Same official URL with a new hash is a new version.

## Discovery

Seed, in order:

1. NPIAS airport list (role, hub, development need)
2. FAA NASR APT data and ADIP 5010 fields
3. [OurAirports](https://ourairports.com/data/) public-domain identifiers (ICAO / IATA / FAA LID / coordinates)

Find documents, in order:

1. State aviation / DOT aeronautics sites
2. Airport, city, and county document centers and master-plan microsites
3. Targeted search for `master plan` / ALP PDFs
4. Wayback CDX for vanished official URLs
5. Community intake via GitHub issues
6. Public-records requests for the remainder

Crawlers send an identifiable User-Agent of `aptplans.org`, honor robots.txt, and request one host at a time with backoff.

## Site

A small Python/Jinja builder (`site/build.py`) writes HTML, CSS, RSS, and a sitemap into `dist/`. Templates stay few. CSS stays thin. The coverage map can use Leaflet later; this is not a JavaScript application.

Intended pages:

- Home: search first, coverage map, recently changed, corpus counts
- Airport: identity, NPIAS role, versions, unofficial summary, Official + Archived, RSS
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

Debian stable on an OVH Eco KS-6 (US East). Docker Compose runs Caddy (static tree) and the pipeline image. One systemd timer runs the pipeline. Unattended-upgrades plus the provider’s DDoS protection is the ops surface.

Disk is the two host spindles in software RAID1. HTML, the catalog checkout, model weights, and PDFs share that mirror. Expected corpus is well under the usable capacity.

Parse and summary jobs run locally on CPU, one at a time, from the preserved copy. Native text is preferred; otherwise layout/OCR on CPU, then a local document pass for TOC, facts, and a one-page unofficial summary. Pairwise change notes run when a prior version exists. Thinking-heavy passes are reserved for hard diffs, not bulk pages. Large PDFs are not fed whole into a vision stack; selected page or ALP sheet images may be captioned after render.

A job may take hours. The public site does not wait. After the first national backfill, steady state is a weekly URL/hash poll. Success is the count of `complete` records over months, not documents per hour.

What we do not run: Kubernetes, Swarm, Redis, Postgres, Prometheus, a public chatbot, or a second box “for scale.”

## Cloudflare

Both domains use Cloudflare DNS. `aptplans.org` is orange-clouded to the origin. `aptplans.com` (apex and www) 301s to `https://aptplans.org`.

Hashed PDFs under `/files/{sha256}.pdf` get long immutable cache headers. HTML and RSS stay shorter. Cloudflare is a cache, not a second copy of the bytes, and not the place the pipeline runs.

## Legal posture

This is not legal advice. State statutes are generally public domain as government edicts. Master plans are usually public records of a sponsor; consultant copyright lines still appear. We cite the source, keep copies for preservation, access, and diffing, publish a takedown path, never present the site as official, and skip SSI/security-looking appendices.

## Replaceable origin

Rebuild: install Debian and Docker, clone this repo, compose up Caddy, enable the timer, place model weights on disk. The catalog in git is the index. The origin disk is the file store. There is no offsite document replica in the first deployment.

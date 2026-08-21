# Security

AptPlans serves public planning documents. It still must not become a dumping ground for secrets, SSI, or a public model endpoint.

## Do not commit

- Source PDFs and WARCs (except hashed fixtures under `catalog/references/files/`)
- Model weights (`.gguf`)
- Extracted full text dumps
- `.env` files and Compose overrides with host secrets
- Anything that looks like SSI, security identification, or non-public engineering drawings

`.gitignore` already covers the common cases. Review `git diff` before you push.

## Public vs origin

| Public (git / HTML) | Origin disk only |
| --- | --- |
| Catalog metadata | Hashed PDFs |
| Statute snapshots | WARCs |
| Reviewed summaries | Extracted text |
| Search snippets via Caddy | Meilisearch volume, master key |
| Builder and Compose files | Model weights |

Cloudflare is a cache. It is not an access-control layer for private files. If a file should not be on the public internet, do not store it under the Caddy docroot. Failed artifacts (including SSI-looking filenames) live in `/var/lib/aptplans/reject` for 90 days, mounted only on the worker and review API, never under Caddy `/files/`. Pull them over `https://aptplans.org/review` with `APTPLANS_REVIEW_TOKEN`. Do not commit those copies.

## Takedown and copyright

Master plans and Airport Layout Plans often carry consultant copyright lines even when they are public records. Cite the official source. Provide a contact (`contact@aptplans.org` and GitHub issues) for takedown requests. Do not present the site as an official FAA or airport publication.

## HTTP

Caddy sets `X-Content-Type-Options`, `Referrer-Policy`, and `X-Frame-Options`. HTML, RSS, and static assets send `Cache-Control: public, max-age=86400`. Hashed `/files/` objects send a one-year immutable header. Visitors use HTTPS at Cloudflare. Origin Caddy also listens on 443 with either a Cloudflare Origin CA cert or a self-signed cert. Use Cloudflare **Full (strict)** once Origin CA material is in GitHub secrets. Cloudflare cache should **Respect Existing Headers** so it does not pick its own TTL.

Hashed file URLs may be cached for a long time; a takedown needs an origin delete plus a Cloudflare purge of that object.

## Origin host

CD keeps the Debian 13 box minimal and reapplies this on every deploy:

- UFW default-deny, 22/80/443 only
- sshd: no passwords, `PermitRootLogin no`, `AllowUsers aptplans`
- fail2ban on sshd (5 tries / 10 minutes → 7 day ban)
- unattended-upgrades for Debian and Docker
- Weekly reboot Monday 12:00 Pacific so kernel updates actually apply
- No public model endpoint. Origin Ollama listens only on the internal Compose `llm` network. Caddy does not proxy it. UFW does not open 11434. Local Compose binds `127.0.0.1:11434` for diagnostics only.
- The review API is HTTPS at `https://aptplans.org/review` (Caddy `:443` only; not on `:80`). Caddy requires `Authorization: Bearer` or `X-Api-Key` except `GET /v1/health`. The app checks the same token. Responses set `Cache-Control: no-store`. Cloudflare must not cache `/review/*` (Cache Rule: Bypass, or respect no-store). Set `APTPLANS_REVIEW_TOKEN` as a GitHub Actions secret so CD writes it to origin `.env.secrets`. Copy the same value locally into `.env` with `APTPLANS_REVIEW_URL=https://aptplans.org/review`. The review container has no published host port; UFW does not open 8787. Do not log `APTPLANS_REVIEW_TOKEN`.

## Pipeline

The document worker is not exposed to the internet. It is a Compose service on the same stack as Caddy, with no published ports, and it calls Ollama by Compose DNS name. Do not publish a prompt box, a host port, or a Cloudflare route in front of the local model.

Caddy may proxy `POST /search/query` to Meilisearch on the Compose network. That path is search-only: no host port, 8 KB body cap, no dumps or settings API. Extracted full text is not under the Caddy docroot. Do not bind 7700 on the host.

Crawlers identify as `aptplans.org`. Fetch egress may use Private Internet Access SOCKS5 via `APTPLANS_FETCH_PROXY` on the worker only. That value is assembled from GitHub Actions secrets and written to `/home/aptplans/.env.secrets`. Do not install a host VPN. GitHub issue comments use `INTAKE_GITHUB_TOKEN` on the origin IP, not through the proxy. Do not scrape authenticated or paywalled portals. Do not store credentials for airport CMS logins in this repository. Do not log `APTPLANS_FETCH_PROXY`, `INTAKE_GITHUB_TOKEN`, `APTPLANS_REVIEW_TOKEN`, `APTPLANS_SEARCH_KEY`, or `APTPLANS_GEMINI_KEY`.

## If something sensitive lands in git

1. Stop serving the object if it was also on origin.
2. Rotate any exposed credentials.
3. Remove the file in a new commit and, if it was pushed, scrub history (`git filter-repo` or a fresh repo).
4. Purge CDN caches for the path.

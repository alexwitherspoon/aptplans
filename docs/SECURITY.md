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
| Builder and Compose files | Model weights |

Cloudflare is a cache. It is not an access-control layer for private files. If a file should not be on the public internet, do not store it under the Caddy docroot.

## Takedown and copyright

Master plans and Airport Layout Plans often carry consultant copyright lines even when they are public records. Cite the official source. Provide a contact (`contact@aptplans.org` and GitHub issues) for takedown requests. Do not present the site as an official FAA or airport publication.

## HTTP

Caddy sets `X-Content-Type-Options`, `Referrer-Policy`, and `X-Frame-Options`. Visitors use HTTPS at Cloudflare. Origin Caddy also listens on 443 with either a Cloudflare Origin CA cert or a self-signed cert. Use Cloudflare **Full (strict)** once Origin CA material is in GitHub secrets.

Hashed file URLs may be cached for a long time; a takedown needs an origin delete plus a Cloudflare purge of that object.

## Origin host

CD keeps the Debian 13 box minimal and reapplies this on every deploy:

- UFW default-deny, 22/80/443 only
- sshd: no passwords, `PermitRootLogin no`, `AllowUsers aptplans`
- fail2ban on sshd (5 tries / 10 minutes → 7 day ban)
- unattended-upgrades for Debian and Docker
- Weekly reboot Monday 12:00 Pacific so kernel updates actually apply
- No public model endpoint. Ollama listens only on the internal Compose `llm` network. Caddy does not proxy it. UFW does not open 11434.

## Pipeline

The document worker is not exposed to the internet. It is a Compose service on the same stack as Caddy, with no published ports, and it calls Ollama by Compose DNS name. Do not publish a prompt box, a host port, or a Cloudflare route in front of the local model.

Crawlers identify as `aptplans.org`. Fetch egress may use Private Internet Access SOCKS5 via `APTPLANS_FETCH_PROXY` on the worker only. That value is assembled from GitHub Actions secrets and written to `/home/aptplans/.env.secrets`. Do not install a host VPN. Do not scrape authenticated or paywalled portals. Do not store credentials for airport CMS logins in this repository. Do not log `APTPLANS_FETCH_PROXY` or `INTAKE_GITHUB_TOKEN`.

## If something sensitive lands in git

1. Stop serving the object if it was also on origin.
2. Rotate any exposed credentials.
3. Remove the file in a new commit and, if it was pushed, scrub history (`git filter-repo` or a fresh repo).
4. Purge CDN caches for the path.

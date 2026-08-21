# GitHub Actions secrets

CD SSHs into the origin from GitHub-hosted runners and converges a bare Debian 13 (trixie) box. Add these under **Settings → Secrets and variables → Actions**.

## Required

| Secret | Value |
| --- | --- |
| `HOST` | Origin IP or hostname (the KS-6) |
| `USER` | SSH user. Must be `aptplans` |
| `SSH_PRIVATE_KEY` | Private key whose public half is already in `/home/aptplans/.ssh/authorized_keys` |

CD SSHs only as `aptplans`. sshd sets `PermitRootLogin no` and `AllowUsers aptplans`, so `root` and the image `debian` account cannot log in remotely. Seed that authorized_keys file from console or the image user before the first deploy.

## Optional

| Secret | Value |
| --- | --- |
| `CLOUDFLARE_ORIGIN_CERT` | PEM for a [Cloudflare Origin CA](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/) certificate covering `aptplans.org` |
| `CLOUDFLARE_ORIGIN_KEY` | Matching private key |
| `PIA_OPENVPN_USER` | Private Internet Access **VPN** username (`p...` login, not the SOCKS `x...` user) |
| `PIA_OPENVPN_PASSWORD` | Matching VPN password |
| `PIA_SERVER_REGIONS` | Gluetun region list. Default `US East` when unset. Example: `US East`, `Netherlands` |
| `INTAKE_GITHUB_TOKEN` | Fine-grained PAT (or GitHub App installation token source) with **Issues: Read and write** on this repo only. Do not name this `GITHUB_TOKEN` |
| `APTPLANS_REVIEW_TOKEN` | Bearer / `X-Api-Key` for `https://aptplans.org/review`. Random 32+ byte hex is enough. Copy the same value into gitignored `.env` (`APTPLANS_REVIEW_URL=https://aptplans.org/review`) |
| `APTPLANS_SEARCH_KEY` | Brave Search API subscription token. Origin discovery searcher. CI must not set this |
| `APTPLANS_GEMINI_KEY` | Optional Gemini API key. Last-resort search packets after Brave stalls. Not a classifier or fetch decider |

Without the Origin CA pair, the host keeps a self-signed origin cert so Caddy can listen on 443. Set Cloudflare SSL to **Full**. After you add Origin CA material, switch the zone to **Full (strict)**.

CD copies PIA VPN, intake, review, and search values to `/home/aptplans/.env.secrets` (mode 600). Bootstrap does not overwrite that file. Each deploy **replaces** the file, so a token that exists only on origin is wiped on the next CD run. Compose interpolates the env-file into **egress**, **worker**, and **review**:

| Origin env | Source |
| --- | --- |
| `PIA_OPENVPN_USER` / `PIA_OPENVPN_PASSWORD` / `PIA_SERVER_REGIONS` | GitHub Actions secrets; consumed by the `egress` (Gluetun) service |
| `APTPLANS_FETCH_PROXY` | Fixed in prod compose as `http://egress:8888` (internal HTTP proxy; not a secret) |
| `INTAKE_GITHUB_TOKEN` | Same name as the Actions secret |
| `INTAKE_GITHUB_REPO` | `owner/name` from the deploying workflow (not a secret) |
| `APTPLANS_REVIEW_TOKEN` | Same name as the Actions secret; interpolated into the review service |
| `APTPLANS_SEARCH_KEY` | Brave Search API token; interpolated into the worker. CD also writes `APTPLANS_SEARCH_PROVIDER=brave` |
| `APTPLANS_GEMINI_KEY` | Optional; interpolated into the worker for one escalate search per stalled airport |

Create `INTAKE_GITHUB_TOKEN` at GitHub → Settings → Developer settings → Personal access tokens → Fine-grained. Resource owner this account, only repository `aptplans`, permission **Issues: Read and write** (Metadata stays read). Leave Contents unset.

Generate `APTPLANS_REVIEW_TOKEN` with `openssl rand -hex 32`. Add it under **Settings → Secrets and variables → Actions**. Copy the same Brave, Gemini, and review values into gitignored `.env` locally (`cp .env.example .env`). Do not commit it. CD logs only `set` or `unset`.

Create `APTPLANS_SEARCH_KEY` at [Brave Search API](https://brave.com/search/api/). Brave is **$5 per 1,000 requests** and includes **$5 credit** each month. AptPlans caps billed spend at **$25/month** (`APTPLANS_BRAVE_MONTHLY_BUDGET_USD=25`): 1,000 credit queries plus 5,000 paid, **6,000 queries**. Set the same $25 limit in the Brave API dashboard. `APTPLANS_SEARCH_MONTHLY_CAP` is an optional tighter query fuse. Live search is limited to **Oregon** (`APTPLANS_SEARCH_STATES=OR` on the worker). Widen with `OR,WA` or `*` when the Oregon pass looks right. Those values are compose config, not GitHub secrets. Create `APTPLANS_GEMINI_KEY` in Google AI Studio only if you want escalate. That path uses Gemini 3.6 Flash with Google Search grounding, **one prompt per airport after Brave did not find a hub or both plan kinds**. Destination URLs become packets and still go through explore, confirm, and gates; bad URLs are dropped. Grounding on Gemini 3 is **5,000 free prompts/month**, then **$14 per 1,000 search queries** (one prompt can fire more than one query). AptPlans caps billed spend at **$25/month** (`APTPLANS_GEMINI_MONTHLY_BUDGET_USD=25`), counting **4 queries per escalate** on the paid slice: **5,446 prompts**. `APTPLANS_GEMINI_MONTHLY_CAP` is an optional tighter prompt fuse. `APTPLANS_GEMINI_MODEL` overrides the default `gemini-3.6-flash`. It extracts destination URLs and drops Google redirect URIs. Model titles and prose are discarded so Gemini cannot label a file as a master plan. Do not use Gemini for verify, kind, or fetch decisions. Local Bonsai stays boxed.

Omit any optional secret to skip that feature. An incomplete PIA VPN pair (user without password) is ignored and the `egress` container will not authenticate. An unset review token leaves `/v1/health` up and every other review path 401. An unset Brave key leaves search on fixtures until you add it.

Production worker fetches are **required** to use the internal egress proxy (`APP_ENV=production`). CI tests the contract with a deterministic mock proxy and does not need live VPN credentials.

The Meilisearch master key is **not** a GitHub secret. Bootstrap writes `/home/aptplans/.env.search` once. CD does not overwrite it.

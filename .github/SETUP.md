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
| `PIA_SOCKS_USER` | Private Internet Access SOCKS5 username (`x…` from the [Client Control Panel](https://www.privateinternetaccess.com), not the `p…` VPN login) |
| `PIA_SOCKS_PASSWORD` | Matching SOCKS5 password |
| `PIA_SOCKS_HOST` | SOCKS host. Default `proxy-nl.privateinternetaccess.com` if unset |
| `PIA_SOCKS_PORT` | SOCKS port. Default `1080` if unset |
| `INTAKE_GITHUB_TOKEN` | Fine-grained PAT (or GitHub App installation token source) with **Issues: Read and write** on this repo only. Do not name this `GITHUB_TOKEN` |

Without the Origin CA pair, the host keeps a self-signed origin cert so Caddy can listen on 443. Set Cloudflare SSL to **Full**. After you add Origin CA material, switch the zone to **Full (strict)**.

CD copies PIA and intake values to `/home/aptplans/.env.secrets` (mode 600). Bootstrap does not overwrite that file. Compose passes it only into interpolation for the **worker** service:

| Origin env | Source |
| --- | --- |
| `APTPLANS_FETCH_PROXY` | Assembled `socks5h://user:pass@host:port` when both PIA user and password are set |
| `INTAKE_GITHUB_TOKEN` | Same name as the Actions secret |
| `INTAKE_GITHUB_REPO` | `owner/name` from the deploying workflow (not a secret) |

Create `INTAKE_GITHUB_TOKEN` at GitHub → Settings → Developer settings → Personal access tokens → Fine-grained. Resource owner this account, only repository `aptplans`, permission **Issues: Read and write** (Metadata stays read). Leave Contents unset.

Omit any optional secret to skip that feature. An incomplete PIA pair (user without password) is ignored.

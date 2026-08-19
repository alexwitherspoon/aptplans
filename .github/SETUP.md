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

Without those two, the host keeps a self-signed origin cert so Caddy can listen on 443. Set Cloudflare SSL to **Full**. After you add Origin CA material, switch the zone to **Full (strict)**.

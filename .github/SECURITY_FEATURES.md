# GitHub repository security features

Audit of [alexwitherspoon/aptplans](https://github.com/alexwitherspoon/aptplans) as of 2026-08-21, plus proposed repo-level hardening. Files in this branch add the workflow and Dependabot config; several toggles still require the GitHub UI or `gh api`.

## Current state

| Feature | Status | Notes |
| --- | --- | --- |
| Secret scanning | Enabled | Catches committed tokens in git history |
| Secret scanning push protection | Enabled | Blocks pushes that add secrets |
| Dependabot alerts | **Disabled** | No vulnerability notifications yet |
| Dependabot security updates | **Disabled** | No auto-fix PRs for known CVEs |
| Dependabot version updates | **Not configured** | No `dependabot.yml` on `main` |
| Code scanning (CodeQL) | **Not configured** | No analysis workflow on `main` |
| Dependency review | **Not configured** | No PR gate on new dependencies |
| Branch protection (`main`) | **None** | Merges are not gated on CI |
| `SECURITY.md` policy | Present | Linked from repo security tab |
| Actions permissions | `allowed_actions: all` | Any third-party action can run |

Existing CI already covers functional regression:

- `Test` — Python 3.12, `pip install -e ".[dev]"`, `make test`
- `Deploy` — runs after successful `Test` on `main`, or manual dispatch
- `Intake ack` — labels-driven issue comment

Dependency surfaces in this repo:

| Surface | Location | Dependabot ecosystem |
| --- | --- | --- |
| Runtime / dev Python | `pyproject.toml` | `pip` |
| GitHub Actions pins | `.github/workflows/*.yml` | `github-actions` |
| Site image base | `docker/Dockerfile` (`caddy:2-alpine`) | `docker` |
| Worker image base | `docker/Dockerfile.worker` (`python:3.14-slim-bookworm`) | `docker` |
| Compose service pins | `docker/docker-compose.yml` (`getmeili/meilisearch:v1.11.3`) | `docker` |
| Ollama image | `${OLLAMA_IMAGE:-ollama/ollama:latest}` | Not pinned in git; set on origin |

## Proposed file changes (this branch)

### `.github/dependabot.yml`

Weekly Monday PRs for:

1. **pip** — `jinja2`, `pypdf`, `pysocks`, `pytest` from `pyproject.toml`
2. **github-actions** — bumps `actions/checkout`, `setup-python`, `codeql-action`, etc.
3. **docker** — base images in `docker/` Dockerfiles and compose `image:` lines

Grouped updates keep PR noise down. Limit of 5 open PRs per ecosystem avoids a flood after the first enable.

### `.github/workflows/codeql.yml`

Static analysis for Python (`catalog/`, `pipeline/`, `site/` builder). Runs on push and PR to `main`, plus a weekly schedule so new CodeQL queries catch latent issues without a code change.

Installs `.[dev,worker]` before analysis so imports resolve the same way as origin.

### `.github/workflows/dependency-review.yml`

On every pull request, flags newly introduced dependencies with known vulnerabilities (default severity gate; no license policy configured). Lightweight; no secrets required.

## Settings to enable in GitHub UI

These cannot be turned on from committed files alone.

### 1. Dependabot alerts (required for alerts and security-update PRs)

**Settings → Code security and analysis → Dependabot alerts → Enable**

Or:

```bash
gh api -X PUT repos/alexwitherspoon/aptplans/vulnerability-alerts
```

Without this, `dependabot.yml` still opens version-update PRs once merged, but you will not get CVE alerts or automatic security-fix PRs.

### 2. Dependabot security updates (recommended)

**Settings → Code security and analysis → Dependabot security updates → Enable**

Opens PRs that bump a dependency when GitHub knows of a fix. Complements version-update PRs from `dependabot.yml`.

### 3. Code scanning default setup (optional alternative)

GitHub offers one-click CodeQL setup in **Settings → Code security → Code scanning**. Prefer the committed workflow in this branch so the config is reviewable and matches the Python install path. Do not enable both the default setup and this workflow without disabling one, or you will duplicate runs.

### 4. Branch protection on `main` (recommended after CI is stable)

**Settings → Branches → Add rule for `main`**

Suggested checks once this branch merges:

| Required status check | Workflow |
| --- | --- |
| `test` | Test |
| `analyze (python)` | CodeQL |
| `dependency-review` | Dependency review |

Also consider:

- Require pull request before merging (direct pushes to `main` currently deploy)
- Do not require stale review dismissal unless you want that friction on a solo repo

Branch protection is optional for a single-maintainer project but prevents merging PRs that skip CI.

### 5. Actions allowlist (optional hardening)

**Settings → Actions → General → Allow *aptplans* actions and reusable workflows**

Current setting is `all`. Tightening to first-party plus an explicit allowlist (`actions/*`, `github/*`, `webfactory/*` for ssh-agent) reduces supply-chain risk from compromised third-party actions. Only do this after listing every action in `.github/workflows/`.

### 6. Private vulnerability reporting

Already available when `SECURITY.md` exists. Confirm **Settings → Code security → Private vulnerability reporting** is enabled so researchers can report issues without a public issue.

## What we are not proposing (yet)

| Feature | Why skip for now |
| --- | --- |
| Dependabot for `ollama/ollama:latest` | Image tag comes from origin env, not a pinned file Dependabot can bump |
| OpenSSF Scorecard workflow | Useful signal but adds another badge and Action; add if you want supply-chain scoring |
| `pip-audit` in CI | Overlaps with Dependabot alerts once enabled; add only if you want fail-the-build on CVEs |
| Required signed commits | High friction for a small public corpus project |
| GitHub Advanced Security | Free for public repos — CodeQL and secret scanning already included |

## Rollout order

1. Merge this branch (or open a PR from `chore/github-security-features`).
2. Enable **Dependabot alerts** and **Dependabot security updates** in repo settings.
3. Watch the first CodeQL run on `main`; fix any findings before requiring the check.
4. Optionally add branch protection requiring `test`, CodeQL, and dependency review.
5. Triage the first batch of Dependabot PRs (expect several Actions bumps and possibly Meilisearch/Caddy base-image updates).

## Local worktree

This branch was prepared in a separate worktree so `main` stays untouched:

```bash
# from the primary clone
git worktree list
# .../aptplans-github-security  chore/github-security-features

cd ../aptplans-github-security
git status
```

Remove the worktree after merge:

```bash
git worktree remove ../aptplans-github-security
git branch -d chore/github-security-features   # after merge to main
```

## Related docs

- [SETUP.md](SETUP.md) — Actions secrets for deploy
- [../docs/SECURITY.md](../docs/SECURITY.md) — what must not be committed, origin hardening

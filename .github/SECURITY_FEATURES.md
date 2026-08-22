# GitHub repository security features

Audit of [alexwitherspoon/aptplans](https://github.com/alexwitherspoon/aptplans). Workflows live in `.github/workflows/`; several toggles still require the GitHub UI.

## Current state

| Feature | Status | Notes |
| --- | --- | --- |
| Secret scanning | Enabled | Push protection on |
| Dependabot alerts | Enabled | No open CVE alerts as of 2026-08-22 |
| Dependabot version updates | Configured | `.github/dependabot.yml` (pip, Actions, docker) |
| Code scanning (CodeQL) | Configured | `.github/workflows/codeql.yml` on push, PR, weekly |
| Dependency review | Configured | `.github/workflows/dependency-review.yml` on PRs |
| Branch protection (`main`) | **None** | Direct pushes deploy after `Test` passes |
| `SECURITY.md` policy | Present | Linked from repo security tab |

Existing CI:

- `Test` — Python 3.12, `make ci`
- `CodeQL` — static analysis for `catalog/`, `pipeline/`, `site/`
- `Deploy` — after successful `Test` on `main`, or manual dispatch
- `Intake ack` — labels-driven issue comment

## Open findings (2026-08-22)

| Source | Count | Action |
| --- | --- | --- |
| Secret scanning | 2 | Purged from git history (`git filter-repo`); resolve stale alerts in the Security tab. |
| CodeQL warnings | 1 open | `py/incomplete-url-substring-sanitization` false positive in `tests/test_search_plan.py` (host set membership, not URL parsing). |

Production code should parse URL hosts with `pipeline/url_hosts.py` instead of substring checks on full URLs.

## Settings still recommended in GitHub UI

1. **Dependabot security updates** — auto PRs when GitHub knows a fix exists.
2. **Branch protection on `main`** — require `test` and CodeQL before merge if you move to PR-only flow.
3. **Actions allowlist** — optional tighten from `all` to `actions/*`, `github/*`, and known third parties.

## Related docs

- [SETUP.md](SETUP.md) — Actions secrets for deploy
- [../docs/SECURITY.md](../docs/SECURITY.md) — what must not be committed, origin hardening

# AptPlans

Discover and engage with airport and aviation planning documents.

**[aptplans.org](https://aptplans.org)** is a public library of US airport master plans, Airport Layout Plans (ALPs), and 50-state aviation, land-use, and airport law (every NPIAS airport, every published version we can find). Official sources stay the citation of record. This project also keeps a preservation copy so files survive when sponsor sites go away, and so we can show what changed.

[![Test](https://github.com/alexwitherspoon/aptplans/actions/workflows/test.yml/badge.svg)](https://github.com/alexwitherspoon/aptplans/actions/workflows/test.yml)

This site is **not** an official FAA, state, or airport publication, and it is not legal advice.

## Quick start

```bash
git clone https://github.com/alexwitherspoon/aptplans.git
cd aptplans
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
make site
make test
make dev
```

The generated site is served at http://127.0.0.1:8080

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — catalog, origin host, pipeline, and site
- [Local setup](docs/LOCAL_SETUP.md)
- [Deployment](docs/DEPLOYMENT.md) — GitHub Actions CD from a bare Debian 13 host
- [Operations](docs/OPERATIONS.md)
- [Security](docs/SECURITY.md)
- [Testing](docs/TESTING.md)
- [Contributing](CONTRIBUTING.md)
- [Code style](CODE_STYLE.md)
- [GitHub secrets](.github/SETUP.md)

## What lives in this repository

Code, catalog metadata, statute snapshots, and generated page summaries. Source PDFs and extracted full text live on the origin host, not in git. See [Architecture](docs/ARCHITECTURE.md).

## License

MIT — see [LICENSE](LICENSE)

---

**Made for pilots, by pilots**

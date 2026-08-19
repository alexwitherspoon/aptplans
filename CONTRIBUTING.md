# Contributing to AptPlans

Thank you for helping build a public library of airport master plans, Airport Layout Plans (ALPs), and state aviation law.

Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) and [CODE_STYLE.md](CODE_STYLE.md) before participating.

## Ways to help

- **Missing documents.** If you know where a master plan, Airport Layout Plan (ALP), or statute lives, [open an issue](https://github.com/alexwitherspoon/aptplans/issues/new?template=missing-document.yml). Official URLs are the most useful thing you can send.
- **Dead or replaced links.** If an official PDF 404s or was quietly swapped, tell us the airport (FAA LID), the old URL, and anything that replaced it.
- **Code and docs.** Fork, branch, and open a pull request. See [Local setup](docs/LOCAL_SETUP.md).

## Local development

```bash
git clone https://github.com/YOUR_USERNAME/aptplans.git
cd aptplans
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
make site
make test
make dev
```

`make dev` serves the generated site at http://localhost:8080

## Pull requests

1. Create a focused branch (`feature/...` or `fix/...`).
2. Follow [CODE_STYLE.md](CODE_STYLE.md).
3. Add tests for new behavior.
4. Update docs when the change is user-facing or operational.
5. Do not commit corpus PDFs, model weights, secrets, or generated `dist/` output. Hashed fixtures under `catalog/references/files/` are the exception.
6. Write a short commit message that explains why the change exists.

This project is an **unofficial document library**. It is not flight-planning weather, not legal advice, and not an FAA or airport publication. Keep that distinction in copy and in code.

## Questions

Open a GitHub issue, or email contact@aptplans.org

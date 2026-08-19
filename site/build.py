"""Build the static AptPlans site into an output directory."""

from __future__ import annotations

import argparse
import shutil
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"


def build(out_dir: Path) -> None:
    out_dir = out_dir.resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
    )
    context = {
        "canonical": "https://aptplans.org",
        "year": date.today().year,
    }
    pages = {
        "index.html": out_dir / "index.html",
        "about.html": out_dir / "about" / "index.html",
    }
    for template_name, dest in pages.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        html = env.get_template(template_name).render(
            **context,
            canonical_path="/" if template_name == "index.html" else "/about/",
        )
        dest.write_text(html, encoding="utf-8")

    css_src = STATIC / "css"
    css_dst = out_dir / "css"
    if css_src.exists():
        shutil.copytree(css_src, css_dst)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the AptPlans static site")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT.parent / "dist",
        help="Output directory (default: dist/ at repo root)",
    )
    args = parser.parse_args()
    build(args.out)


if __name__ == "__main__":
    main()

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_build():
    spec = importlib.util.spec_from_file_location(
        "aptplans_build", ROOT / "site" / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_writes_index_and_css(tmp_path: Path) -> None:
    build = _load_build()
    out = tmp_path / "dist"
    build.build(out)

    index = (out / "index.html").read_text(encoding="utf-8")
    about = (out / "about" / "index.html").read_text(encoding="utf-8")
    css = out / "css" / "styles.css"

    assert "aptplans.org" in index
    assert "Unofficial" in index
    assert "not legal advice" in about.lower()
    assert css.is_file()
    assert "canonical" in index.lower() or 'rel="canonical"' in index

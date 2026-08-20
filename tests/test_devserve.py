from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("devserve", ROOT / "scripts" / "devserve.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_tree_stamp_changes_when_file_appears(tmp_path: Path) -> None:
    dev = _load()
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "page.html").write_text("one", encoding="utf-8")
    first = dev.tree_stamp([folder])
    (folder / "page.html").write_text("two", encoding="utf-8")
    second = dev.tree_stamp([folder])
    assert second != first
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "x.pyc").write_bytes(b"x")
    assert dev.tree_stamp([folder]) == second


def test_watch_roots_include_overlay(tmp_path: Path, monkeypatch) -> None:
    dev = _load()
    monkeypatch.delenv("APTPLANS_CATALOG_OVERLAY", raising=False)
    roots = dev.watch_roots(tmp_path)
    assert tmp_path / "site" in roots
    assert tmp_path / "catalog" in roots
    assert tmp_path / "data" / "catalog" in roots
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    roots = dev.watch_roots(tmp_path)
    assert tmp_path / "overlay" in roots


def test_watch_loop_rebuilds_once_on_stamp_change(tmp_path: Path) -> None:
    dev = _load()
    builds: list[Path] = []
    stamps = [
        (("a", 1, 1),),
        (("a", 1, 1),),
        (("b", 2, 2),),
        (("b", 2, 2),),
    ]
    n = {"i": 0}

    def stamp(_roots):
        value = stamps[min(n["i"], len(stamps) - 1)]
        n["i"] += 1
        return value

    def running():
        return n["i"] < 6

    dev.watch_loop(
        [tmp_path],
        tmp_path / "dist",
        sleep=lambda _s: None,
        stamp=stamp,
        build=builds.append,
        running=running,
    )
    assert builds == [tmp_path / "dist"]

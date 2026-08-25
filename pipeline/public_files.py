"""Project reviewed artifacts into the directory served by Caddy."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

from catalog.models import Document, visible_on_site
from catalog.seed import seed_catalog
from catalog.store import Catalog
from pipeline.refresh import ROOT, overlay_dir_from_env


def public_files_dir() -> Path:
    return Path(
        os.environ.get("APTPLANS_PUBLIC_FILES", ROOT / "data" / "public-files")
    )


def private_files_dir() -> Path:
    return Path(os.environ.get("APTPLANS_FILES", ROOT / "data" / "files"))


def _artifact_name(document: Document) -> str | None:
    if not document.content_sha256 or not document.preserved_url:
        return None
    suffix = Path(urlparse(document.preserved_url).path).suffix.lower()
    if suffix not in {".pdf", ".html"}:
        return None
    return f"{document.content_sha256}{suffix}"


def _publish_file(source: Path, destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size == source.stat().st_size:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def reconcile_public_files(
    catalog: Catalog,
    *,
    private_dir: Path | None = None,
    public_dir: Path | None = None,
) -> dict[str, int]:
    """Publish visible artifacts and remove every stale public projection."""
    private_root = private_dir or private_files_dir()
    public_root = public_dir or public_files_dir()
    public_root.mkdir(parents=True, exist_ok=True)
    expected: set[str] = set()
    published = 0

    for document in catalog.documents:
        if not visible_on_site(document):
            continue
        name = _artifact_name(document)
        if not name:
            continue
        source = private_root / name
        if not source.is_file():
            continue
        expected.add(name)
        destination = public_root / name
        before = destination.is_file()
        _publish_file(source, destination)
        if not before:
            published += 1

    removed = 0
    for path in public_root.rglob("*"):
        if path.is_file() and path.relative_to(public_root).as_posix() not in expected:
            path.unlink()
            removed += 1
    for directory in sorted(
        (path for path in public_root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass

    return {"expected": len(expected), "published": published, "removed": removed}


def main() -> int:
    if os.environ.get("APTPLANS_DOMAIN_STORE") == "1":
        raise RuntimeError("domain mode public files activate through a full release")
    catalog = seed_catalog(ROOT / "catalog", overlay_dir=overlay_dir_from_env())
    result = reconcile_public_files(catalog)
    print(
        "public files "
        f"expected={result['expected']} "
        f"published={result['published']} "
        f"removed={result['removed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

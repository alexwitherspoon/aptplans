"""Cross-system release ordering for static files and Meilisearch."""

from __future__ import annotations

from pathlib import Path

from catalog.models import visible_on_site
from catalog.store import CatalogSnapshot
from pipeline.release_store import BuildRelease, ReleaseStore
from pipeline.search import (
    activate_generation_index,
    configured as search_configured,
    finalize_generation_index,
    remove_revoked_before_release,
    stage_generation_index,
)


class ReleaseCoordinator:
    def __init__(self, ledger_root: Path, releases_root: Path) -> None:
        self.releases = ReleaseStore(ledger_root, releases_root)

    def stage(
        self,
        snapshot: CatalogSnapshot,
        build: BuildRelease,
        *,
        text_dir: Path | None = None,
        metadata: dict | None = None,
    ) -> dict:
        manifest = self.releases.stage(
            snapshot.generation_id,
            build,
            metadata=metadata,
        )
        try:
            public_files = {
                row["path"] for row in manifest.get("public_files") or []
            }
            visible_files = {
                document.preserved_url.removeprefix("/files/")
                for document in snapshot.catalog.documents
                if visible_on_site(document)
                and (document.preserved_url or "").startswith("/files/")
            }
            missing = sorted(visible_files - public_files)
            if missing:
                raise ValueError(
                    f"release omits visible public files: {', '.join(missing[:5])}"
                )
            private_only = {
                document.preserved_url.removeprefix("/files/")
                for document in snapshot.catalog.documents
                if not visible_on_site(document)
                and (document.preserved_url or "").startswith("/files/")
            }
            leaked = sorted((private_only - visible_files) & public_files)
            if leaked:
                raise ValueError(
                    f"release includes private public files: {', '.join(leaked[:5])}"
                )
        except ValueError as exc:
            self.releases.invalidate(snapshot.generation_id, str(exc))
            raise
        staged = stage_generation_index(
            snapshot.catalog,
            snapshot.generation_id,
            dest=text_dir,
        )
        if staged is not None:
            index_uid, document_count = staged
            self.releases.record_search_staged(
                snapshot.generation_id,
                index_uid,
                document_count,
            )
        return manifest

    def activate(
        self,
        upcoming: CatalogSnapshot,
        *,
        previous: CatalogSnapshot | None = None,
    ) -> None:
        release = self.releases.get(upcoming.generation_id)
        if release is None:
            raise ValueError(f"release is not staged: {upcoming.generation_id}")
        if search_configured() and release["search_state"] != "staged":
            raise ValueError(
                f"release search is not staged: {upcoming.generation_id}"
            )
        remove_revoked_before_release(
            previous.catalog if previous else None,
            upcoming.catalog,
        )
        self.releases.activate(upcoming.generation_id)
        if not search_configured():
            return
        index_uid = str(release["search_index_uid"])
        activate_generation_index(index_uid, upcoming.generation_id)
        self.releases.record_search_active(upcoming.generation_id)
        finalize_generation_index(index_uid, upcoming.generation_id)

    def recover(self) -> str | None:
        generation_id = self.releases.recover_activation()
        if generation_id is None or not search_configured():
            return generation_id
        release = self.releases.get(generation_id)
        if release is None or not release["search_index_uid"]:
            return generation_id
        index_uid = str(release["search_index_uid"])
        if release["search_state"] == "staged":
            activate_generation_index(index_uid, generation_id)
            self.releases.record_search_active(generation_id)
            release = self.releases.get(generation_id)
        if release and release["search_state"] == "active":
            finalize_generation_index(index_uid, generation_id)
        return generation_id

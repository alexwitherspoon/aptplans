from pathlib import Path
import json
import sqlite3

import pytest

from catalog.models import Document
from catalog.store import Catalog, CatalogSnapshot
from pipeline.domain_store import DomainStore
from pipeline.release_coordinator import ReleaseCoordinator
from pipeline.release_store import ReleaseStore


def _generation(root: Path, name: str) -> str:
    snapshot = DomainStore(root).commit(
        {("documents", name): {"id": name}},
        reason=f"add {name}",
    )
    return snapshot.generation_id


def _build(label: str):
    def build(site: Path, public_files: Path) -> None:
        (site / "index.html").write_text(f"<h1>{label}</h1>", encoding="utf-8")
        (site / "status.json").write_text("{}", encoding="utf-8")
        (site / "sitemap.xml").write_text("<urlset/>", encoding="utf-8")
        (site / "data").mkdir()
        (site / "data" / "search.json").write_text("[]", encoding="utf-8")
        (public_files / f"{label}.pdf").write_bytes(b"%PDF-" + label.encode())

    return build


def _catalog_snapshot(store: DomainStore, name: str) -> CatalogSnapshot:
    domain = store.commit({}, reason=name)
    return CatalogSnapshot(
        generation_id=domain.generation_id,
        committed_at=domain.committed_at,
        dataset_state={},
        catalog=Catalog.empty(),
    )


def test_validated_release_activates_site_and_files_together(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    releases = tmp_path / "releases"
    generation = _generation(ledger, "one")
    store = ReleaseStore(ledger, releases)
    manifest = store.stage(generation, _build("one"))
    assert manifest["generation_id"] == generation
    assert store.current_generation_id() is None
    store.activate(generation)
    assert store.current_generation_id() == generation
    assert (releases / "current" / "site" / "index.html").read_text() == "<h1>one</h1>"
    assert (releases / "current" / "public-files" / "one.pdf").is_file()
    assert store.events(generation) == [
        "build_started",
        "build_validated",
        "activation_started",
        "activated",
    ]


def test_failed_build_leaves_prior_release_served(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    store = ReleaseStore(ledger, tmp_path / "releases")
    first = _generation(ledger, "one")
    store.stage(first, _build("one"))
    store.activate(first)
    second = _generation(ledger, "two")

    def fail(site: Path, _public_files: Path) -> None:
        (site / "index.html").write_text("partial", encoding="utf-8")
        raise RuntimeError("builder failed")

    with pytest.raises(RuntimeError, match="builder failed"):
        store.stage(second, fail)
    assert store.current_generation_id() == first
    assert store.get(second)["state"] == "failed"
    assert (store.root / "current" / "site" / "index.html").read_text() == "<h1>one</h1>"


def test_invalidated_release_can_be_rebuilt_for_same_generation(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    store = ReleaseStore(ledger, tmp_path / "releases")
    generation = _generation(ledger, "one")
    store.stage(generation, _build("first"))
    store.invalidate(generation, "projection incomplete")
    manifest = store.stage(generation, _build("second"))
    assert {row["path"] for row in manifest["public_files"]} == {"second.pdf"}
    assert store.get(generation)["state"] == "validated"


def test_activation_recovery_follows_served_pointer(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    store = ReleaseStore(ledger, tmp_path / "releases")
    first = _generation(ledger, "one")
    second = _generation(ledger, "two")
    store.stage(first, _build("one"))
    store.activate(first)
    store.stage(second, _build("two"))
    pointer = store.root / "current"
    replacement = store.root / ".interrupted-pointer"
    replacement.symlink_to(second, target_is_directory=True)
    replacement.replace(pointer)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE releases SET state='activating' WHERE generation_id=?",
            (second,),
        )
    assert store.recover_activation() == second
    assert store.get(second)["state"] == "active"
    assert store.get(first)["state"] == "superseded"


def test_tampered_validated_release_cannot_activate(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    store = ReleaseStore(ledger, tmp_path / "releases")
    generation = _generation(ledger, "one")
    store.stage(generation, _build("one"))
    (store.root / generation / "site" / "index.html").write_text(
        "tampered",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not match"):
        store.activate(generation)
    assert store.current_generation_id() is None


def test_coordinator_orders_revocations_site_then_search_additions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = tmp_path / "ledger"
    snapshot = _catalog_snapshot(DomainStore(ledger), "next")
    coordinator = ReleaseCoordinator(ledger, tmp_path / "releases")
    order: list[str] = []
    monkeypatch.setattr(
        "pipeline.release_coordinator.stage_generation_index",
        lambda *_args, **_kwargs: ("staged-index", 3),
    )
    coordinator.stage(snapshot, _build("next"))
    monkeypatch.setattr(
        "pipeline.release_coordinator.search_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "pipeline.release_coordinator.remove_revoked_before_release",
        lambda *_args, **_kwargs: order.append("revoke") or [],
    )
    real_activate = coordinator.releases.activate
    monkeypatch.setattr(
        coordinator.releases,
        "activate",
        lambda generation_id: (order.append("site"), real_activate(generation_id))[1],
    )
    monkeypatch.setattr(
        "pipeline.release_coordinator.activate_generation_index",
        lambda *_args: order.append("search"),
    )
    monkeypatch.setattr(
        "pipeline.release_coordinator.finalize_generation_index",
        lambda *_args: order.append("finalize"),
    )
    coordinator.activate(snapshot)
    assert order == ["revoke", "site", "search", "finalize"]
    assert coordinator.releases.get(snapshot.generation_id)["search_state"] == "active"


def test_coordinator_recovers_search_after_static_pointer_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = tmp_path / "ledger"
    snapshot = _catalog_snapshot(DomainStore(ledger), "next")
    coordinator = ReleaseCoordinator(ledger, tmp_path / "releases")
    monkeypatch.setattr(
        "pipeline.release_coordinator.stage_generation_index",
        lambda *_args, **_kwargs: ("staged-index", 3),
    )
    coordinator.stage(snapshot, _build("next"))
    monkeypatch.setattr(
        "pipeline.release_coordinator.search_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "pipeline.release_coordinator.remove_revoked_before_release",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "pipeline.release_coordinator.activate_generation_index",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("search unavailable")),
    )
    with pytest.raises(RuntimeError, match="search unavailable"):
        coordinator.activate(snapshot)
    assert coordinator.releases.current_generation_id() == snapshot.generation_id
    assert coordinator.releases.get(snapshot.generation_id)["search_state"] == "staged"

    monkeypatch.setattr(
        "pipeline.release_coordinator.activate_generation_index",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "pipeline.release_coordinator.finalize_generation_index",
        lambda *_args: None,
    )
    assert coordinator.recover() == snapshot.generation_id
    assert coordinator.releases.get(snapshot.generation_id)["search_state"] == "active"


def test_coordinator_rejects_missing_visible_public_file(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    domain = DomainStore(ledger).commit({}, reason="next")
    snapshot = CatalogSnapshot(
        generation_id=domain.generation_id,
        committed_at=domain.committed_at,
        dataset_state={},
        catalog=Catalog(
            documents=[
                Document(
                    id="plan",
                    kind="master_plan",
                    source_url="https://example.com/plan.pdf",
                    completeness="complete",
                    review_status="published",
                    preserved_url="/files/expected.pdf",
                )
            ]
        ),
    )
    coordinator = ReleaseCoordinator(ledger, tmp_path / "releases")
    with pytest.raises(ValueError, match="omits visible public files"):
        coordinator.stage(snapshot, _build("other"))
    assert coordinator.releases.get(snapshot.generation_id)["state"] == "failed"


def test_domain_site_build_activates_one_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = tmp_path / "ledger"
    generation = DomainStore(ledger).commit({}, reason="initial")
    monkeypatch.setenv("APTPLANS_DOMAIN_STORE", "1")
    monkeypatch.setenv("APTPLANS_QUEUE", str(ledger))
    monkeypatch.setenv("APTPLANS_SITE", str(tmp_path / "releases/current/site"))
    monkeypatch.setenv("APTPLANS_RELEASES", str(tmp_path / "releases"))
    monkeypatch.setenv("APTPLANS_FILES", str(tmp_path / "private"))
    monkeypatch.setenv("APTPLANS_CATALOG_OVERLAY", str(tmp_path / "overlay"))
    monkeypatch.setenv("APTPLANS_DEV_PREVIEW", "0")
    monkeypatch.setenv("APTPLANS_REFERENCE_SEED", "0")
    monkeypatch.delenv("MEILI_URL", raising=False)

    from pipeline.site_build import run_site_build

    assert run_site_build() == "built"
    current = tmp_path / "releases" / "current"
    assert current.resolve().name == generation.generation_id
    assert (current / "site" / "status.json").is_file()
    assert (current / "site" / "data" / "search.json").is_file()
    manifest = json.loads((current / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["metadata"]["domain_generation_id"] == generation.generation_id
    assert manifest["metadata"]["audit_cutoff"]

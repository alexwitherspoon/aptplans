from __future__ import annotations

from pathlib import Path

from catalog.models import Document
from catalog.store import Catalog
from pipeline.public_files import reconcile_public_files


def _document(review_status: str, sha: str) -> Document:
    return Document.from_dict(
        {
            "id": "plan",
            "kind": "master_plan",
            "source_url": "https://example.com/plan.pdf",
            "preserved_url": f"/files/{sha}.pdf",
            "content_sha256": sha,
            "completeness": "complete",
            "review_status": review_status,
        }
    )


def test_public_projection_follows_visibility_and_revocation(tmp_path: Path) -> None:
    private_dir = tmp_path / "private"
    public_dir = tmp_path / "public"
    private_dir.mkdir()
    sha = "a" * 64
    private_artifact = private_dir / f"{sha}.pdf"
    private_artifact.write_bytes(b"%PDF-private")

    hidden = reconcile_public_files(
        Catalog(documents=[_document("pending", sha)]),
        private_dir=private_dir,
        public_dir=public_dir,
    )
    assert hidden["expected"] == 0
    assert list(public_dir.iterdir()) == []
    assert private_artifact.is_file()

    visible = reconcile_public_files(
        Catalog(documents=[_document("auto_pass", sha)]),
        private_dir=private_dir,
        public_dir=public_dir,
    )
    assert visible["expected"] == 1
    assert (public_dir / f"{sha}.pdf").read_bytes() == b"%PDF-private"

    revoked = reconcile_public_files(
        Catalog(documents=[_document("needs_human", sha)]),
        private_dir=private_dir,
        public_dir=public_dir,
    )
    assert revoked["removed"] == 1
    assert list(public_dir.iterdir()) == []
    assert private_artifact.is_file()

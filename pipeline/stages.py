"""Distinct pipeline stages. A search hit or hub link is never a publish."""

from __future__ import annotations

import re

STAGES = ("signal", "explore", "confirm", "snapshot", "vet", "publish")


_SKIP_LABEL_RE = re.compile(
    r"\bminutes\b|\bagenda\b|privacy statement|economic impact|"
    r"newsletter|meeting packet|board packet|\bpresentations?\b|"
    r"commission agenda|open house",
    re.I,
)
_DATE_LABEL_RE = re.compile(r"^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$")
_PLAN_KIND = frozenset({"master_plan", "alp", "chapter"})
_REVIEW_STATUSES = frozenset({"pending", "auto_pass", "needs_human", "published"})


def review_after_snapshot(previous_review: str | None = None) -> str:
    """Bytes on disk are not a publish. Keep a prior vetted review."""
    if previous_review in {"published", "auto_pass", "needs_human"}:
        return previous_review
    return "pending"


def review_after_vet(*, official_plan: bool, same_airport: bool, kind: str) -> str:
    if kind not in _PLAN_KIND:
        return "pending"
    if official_plan and same_airport:
        return "auto_pass"
    return "pending"


def apply_review_transition(requested: str, *, authority: str) -> str:
    """Validate publication authority before a caller persists a transition."""
    if requested not in _REVIEW_STATUSES:
        raise ValueError(f"unknown review status: {requested}")
    if authority == "machine":
        if requested not in {"pending", "auto_pass"}:
            raise ValueError(f"machine cannot set review status: {requested}")
        return requested
    if authority == "operator":
        return requested
    raise ValueError(f"unknown review authority: {authority}")


def worth_confirm(*, role: str, kind_guess: str, label: str) -> bool:
    """Fetch only plan-shaped links. Meeting minutes stay signals."""
    if role in {"not_plan", "notice", "followup", "hub_page"}:
        return False
    if _SKIP_LABEL_RE.search(label or "") or _DATE_LABEL_RE.match((label or "").strip()):
        return False
    if kind_guess in _PLAN_KIND:
        return True
    return False


def source_family(
    *,
    status: int | None = None,
    error: str | None = None,
    n_artifacts: int = 0,
    n_followups: int = 0,
    hub_kind: str = "other",
    page_url: str = "",
) -> str:
    if status == 403:
        return "bot_wall"
    if error or status in {404, 410}:
        return "dead"
    if n_followups:
        return "sharepoint_list"
    url = (page_url or "").lower()
    if n_artifacts >= 40:
        return "document_dump"
    if "wp-content" in url or "/documents" in url:
        return "wordpress_docs" if n_artifacts else "facility_page"
    if hub_kind == "master_plan":
        return "plan_hub"
    if n_artifacts == 0:
        return "facility_page"
    if n_artifacts == 1 and url.endswith(".pdf"):
        return "bound_pdf"
    return "unknown"

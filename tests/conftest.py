from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _reference_seed_for_tests() -> None:
    """Fixture replay needs git reference rows; production is the runtime default."""
    os.environ.setdefault("APTPLANS_REFERENCE_SEED", "1")

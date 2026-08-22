"""운영 저장 JSON의 공통 자원 상한 회귀."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core import persisted_json


PERSISTED_JSON_WRITER_PATHS = {
    "src/features/storage/reports.py",
    "src/features/storage/cache.py",
    "src/features/admin_dashboard/store.py",
    "src/features/observability/lifecycle.py",
    "src/features/export_pdf/release_store.py",
    "src/features/spanselect/diagnostic_store.py",
}


def test_single_container_boundary_is_shared_and_closed() -> None:
    accepted = json.dumps([None] * persisted_json.MAX_CONTAINER_ITEMS)
    rejected = json.dumps([None] * (persisted_json.MAX_CONTAINER_ITEMS + 1))

    assert persisted_json.validate_persisted_json_text(accepted) is not None
    with pytest.raises(persisted_json.PersistedJsonContractError):
        persisted_json.validate_persisted_json_text(rejected)


def test_non_finite_number_is_not_persisted_as_json() -> None:
    with pytest.raises(persisted_json.PersistedJsonContractError):
        persisted_json.validate_persisted_json_text("[NaN]")


def test_all_persistent_json_writer_modules_use_the_shared_contract() -> None:
    app_root = Path(__file__).resolve().parents[3]
    for relative_path in PERSISTED_JSON_WRITER_PATHS:
        source = (app_root / relative_path).read_text(encoding="utf-8")
        assert "validate_persisted_json_text" in source, relative_path

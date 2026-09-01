"""운영 저장 JSON의 공통 자원 상한 회귀."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core import persisted_json


PERSISTED_JSON_WRITER_PATHS = {
    "src/features/storage/reports.py",
    "src/features/admin_dashboard/store.py",
    "src/features/observability/lifecycle.py",
    "src/features/export_pdf/release_store.py",
    "src/features/spanselect/diagnostic_store.py",
}

#: 한때 layer2 캐시가 JSON을 저장해 위 목록에 있었으나 7b42c9e에서 layer2가
#: 폐기되면서 JSON 쓰기가 사라졌다. 목록에서 빼기만 하면 나중에 JSON 쓰기가
#: 돌아와도 아무도 막지 않으므로, 「JSON을 쓰지 않는다」를 대신 감시한다.
JSON_FREE_STORE_PATHS = {
    "src/features/storage/cache.py",
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


def writes_json_without_shared_contract(source: str) -> bool:
    """모듈 원문이 공통 계약 없이 JSON을 저장하는지 판정한다."""

    return "json.dumps" in source and "validate_persisted_json_text" not in source


def test_unguarded_json_write_is_detected_in_both_directions() -> None:
    """판정 자체가 살아 있는지 본다 — 감시가 늘 참이면 아무것도 못 막는다."""

    assert writes_json_without_shared_contract("payload = json.dumps(row)") is True
    assert (
        writes_json_without_shared_contract(
            "payload = json.dumps(row)\nvalidate_persisted_json_text(payload)"
        )
        is False
    )
    assert writes_json_without_shared_contract("conn.execute(sql, values)") is False


def test_json_free_stores_do_not_regain_unguarded_json_writes() -> None:
    """JSON을 쓰지 않기로 한 저장 모듈이 몰래 JSON 쓰기를 되찾지 않게 한다.

    되찾는 순간 이 시험이 깨지고, 고치는 사람은 공통 계약을 붙여
    ``PERSISTED_JSON_WRITER_PATHS``로 옮기게 된다.
    """

    app_root = Path(__file__).resolve().parents[3]
    for relative_path in JSON_FREE_STORE_PATHS:
        source = (app_root / relative_path).read_text(encoding="utf-8")
        assert not writes_json_without_shared_contract(source), relative_path

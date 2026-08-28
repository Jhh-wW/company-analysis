"""보고서 라우트 시험이 실제 공개 조회 계약을 타게 하는 작은 도우미.

성공 보고서의 공개 GET 정본은 더 이상 ``job_runtime._JOBS``나
``_load_saved_report``가 아니다. 새 보고서는 불변 Delivery, 옛 보고서는 저장 당시
legacy snapshot에서만 읽는다. 화면 모양만 보는 시험도 이 경계를 흉내 내야 제품과
시험이 서로 다른 길을 지키는 일이 생기지 않는다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.features.pipeline.port import Report
from src.features.report_access import constants as report_access_constants
from src.features.report_access import store as report_access_store
from src.features.storage import db as storage_db
from src.web import report_delivery_adapter


def serve_legacy_report_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    report: Report,
    *,
    report_id: str | None = None,
) -> None:
    """성공 보고서를 legacy 영속 snapshot 갈래로만 제공한다.

    Delivery·intent가 없을 때만 legacy를 읽는 실제 라우트 순서를 고정한다. 따라서
    이 도우미를 쓴 시험은 폐기된 메모리 보고서 갈래가 우연히 살아나도 통과하지
    않는다.
    """

    snapshot = report_delivery_adapter.LegacyPublicReport(
        report=report,
        payload_json="{}",
        generated_at=str(report.generated_at or ""),
        stored_at="2026-08-28T00:00:00+09:00",
    )

    monkeypatch.setattr(
        report_delivery_adapter,
        "load_public_delivery",
        lambda _public_id: None,
    )
    monkeypatch.setattr(
        report_delivery_adapter,
        "load_public_delivery_intent",
        lambda _public_id: None,
    )

    def load_snapshot(public_id: str):
        if report_id is not None and public_id != report_id:
            return None
        return snapshot

    monkeypatch.setattr(
        report_delivery_adapter,
        "load_legacy_public_report",
        load_snapshot,
    )


def bind_public_report_access(client: TestClient, report_id: str) -> None:
    """제품의 PUBLIC 발급 API로 현재 시험 브라우저를 run에 결속한다."""

    with storage_db.connect() as conn:
        issued = report_access_store.issue_and_bind(
            conn,
            existing_token=client.cookies.get(
                report_access_constants.PUBLIC_GRANT_COOKIE_NAME
            ),
            run_id=report_id,
        )
    client.cookies.set(
        report_access_constants.PUBLIC_GRANT_COOKIE_NAME,
        issued.token,
    )

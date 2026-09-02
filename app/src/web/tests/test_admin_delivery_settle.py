"""관리자가 스윕이 못 잡은 정체된 delivery 의무를 수동으로 대사하는 경로.

admin_budget_settle의 2026-08-28 선례와 같은 문제: 화면은 「관리자가 대사해야
다시 열립니다」라고 말할 수 있는데 대사할 방법이 코드에 없으면, 재시작해도
DB에서 다시 읽히므로 영원히 안 풀린다. 이 시험은 ``/admin/delivery/settle``이
실제로 존재하고, 진짜 관리자·CSRF 검사를 거치며, 이미 출고된 보고서는
절대 실패로 뒤집지 않는지 본다.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.report_delivery import constants as delivery_constants
from src.features.report_delivery import store as delivery_store
from src.features.report_delivery.cache_identity import CacheNamespace
from src.features.report_delivery.models import (
    ContentSnapshot,
    Delivery,
    DeliveryPolicy,
)
from src.features.report_delivery.source_identity import SourceSnapshot
from src.features.storage import db as storage_db
from src.web import main, runtime


@pytest.fixture
def client():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        yield client


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """관리자로 로그인한 손님. CSRF 시험을 위해 자동 첨부는 하지 않는다."""
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    client._csrf_for_test = auth_logic.csrf_token_for_session(session.token)
    return client


def _seed_required_intent(*, public_id: str, required_at: dt.datetime) -> None:
    with storage_db.connect() as conn:
        delivery_store.mark_delivery_required(
            conn, public_id=public_id, required_at=required_at
        )


def _seed_completed_intent(*, public_id: str, completed_at: dt.datetime) -> None:
    """실제 delivery까지 갖춘 complete 의무를 만든다 — 대사가 절대 건드리면 안 된다."""

    source = SourceSnapshot.capture(
        dart_receipt_nos=("20260828000123",),
        financial_payload={
            "status": "000",
            "list": [{"account_nm": "매출액", "thstrm_amount": "100"}],
        },
        captured_at=completed_at,
        source_as_of=completed_at.date(),
        adapter_versions={"admin-settle-test": "1"},
    )
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version="admin-settle-v1",
        deployment_revision="a" * 40,
        requested_models={"writer": "offline-test"},
        output_settings={"fixture": "admin-settle"},
    )
    content = ContentSnapshot.create(
        payload=f'{{"public_id":"{public_id}"}}'.encode(),
        source_snapshot=source,
        cache_namespace=namespace,
        content_generated_at=completed_at,
        engine_epoch_digest="a" * 64,
        actual_models=("offline-test",),
    )
    with storage_db.connect() as conn:
        delivery_store.save_source_snapshot(conn, source)
        delivery_store.save_cache_namespace(conn, namespace)
        delivery_store.save_content_snapshot(conn, content)
        delivery_store.mark_delivery_required(
            conn, public_id=public_id, required_at=completed_at
        )
        delivery = Delivery.issue(
            public_id=public_id,
            billing_bucket_id="bucket-admin-settle",
            content=content,
            delivered_at=completed_at,
            policy=DeliveryPolicy(
                content_max_age=dt.timedelta(days=60),
                public_link_lifetime=dt.timedelta(days=60),
            ),
            reused_from_cache=False,
        )
        delivery_store.save_delivery(conn, delivery)
        delivery_store.mark_delivery_complete(
            conn, public_id=public_id, completed_at=completed_at
        )


def test_대사_라우트는_확인_없는_POST를_거절한다(admin: TestClient) -> None:
    report_id = "settle-no-csrf-" + "a" * 18
    _seed_required_intent(public_id=report_id, required_at=clock.now_kst())

    missing = admin.request(
        "POST", "/admin/delivery/settle", data={"report_id": report_id}
    )

    assert missing.status_code == 403
    with storage_db.connect() as conn:
        intent = delivery_store.load_delivery_intent(conn, report_id)
    assert intent is not None
    assert intent.state == delivery_store.DELIVERY_INTENT_REQUIRED


def test_대사는_intent를_failed로_바꾸고_감사행을_남긴다(
    admin: TestClient, caplog
) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="security.admin_audit")
    report_id = "settle-happy-path-" + "b" * 14
    _seed_required_intent(public_id=report_id, required_at=clock.now_kst())

    response = admin.request(
        "POST",
        "/admin/delivery/settle",
        data={"report_id": report_id, "csrf_token": admin._csrf_for_test},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with storage_db.connect() as conn:
        intent = delivery_store.load_delivery_intent(conn, report_id)
    assert intent is not None
    assert intent.state == delivery_store.DELIVERY_INTENT_FAILED
    assert intent.failure_code == delivery_constants.MANUAL_SETTLEMENT_FAILURE_CODE

    import json

    events = [
        json.loads(record.getMessage().removeprefix("admin_audit "))
        for record in caplog.records
        if record.name == "security.admin_audit"
    ]
    assert any(
        event["action"] == "admin.delivery.settle" and event["outcome"] == "success"
        for event in events
    )


def test_대사는_delivery가_있는_보고서를_거절한다(admin: TestClient) -> None:
    report_id = "settle-already-delivered-" + "c" * 8
    _seed_completed_intent(public_id=report_id, completed_at=clock.now_kst())

    response = admin.request(
        "POST",
        "/admin/delivery/settle",
        data={"report_id": report_id, "csrf_token": admin._csrf_for_test},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "대사 대상이 아닙니다" in response.text
    with storage_db.connect() as conn:
        intent = delivery_store.load_delivery_intent(conn, report_id)
    assert intent is not None
    assert intent.state == delivery_store.DELIVERY_INTENT_COMPLETE


def test_대사_목록_화면은_정체된_의무만_보여준다(admin: TestClient) -> None:
    pending_id = "settle-list-pending-" + "d" * 12
    delivered_id = "settle-list-delivered-" + "e" * 10
    now = clock.now_kst()
    _seed_required_intent(public_id=pending_id, required_at=now)
    _seed_completed_intent(public_id=delivered_id, completed_at=now)

    response = admin.get("/admin/delivery/settle")

    assert response.status_code == 200
    assert pending_id in response.text
    assert delivered_id not in response.text


def test_관리자_설정_화면에_대사_링크가_있다(admin: TestClient) -> None:
    """admin_home.html은 어느 라우트도 렌더하지 않는 고아 템플릿이라(G-S8 삭제
    예정, 실측 확인) 링크를 둬도 아무도 못 본다. 실제로 렌더되는
    ``/admin/settings``(admin_settings.html) 본문에 대사 화면 링크를 둔다.
    """

    response = admin.get("/admin/settings")

    assert response.status_code == 200
    assert 'href="/admin/delivery/settle"' in response.text

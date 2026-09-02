"""저장은 됐지만 출고가 확정되지 못한 delivery 의무·LINK 이력의 재시작 스윕.

``_save_report`` 성공 직후 ~ 최종 출고 확정(``_finalize_report_delivery``) 사이에
프로세스가 죽으면 ``delivery_intents``는 ``required``인 채, LINK는
``share_link_run_history``도 ``awaiting_release``인 채 영구히 남는다(F1·F2,
36장 계획리빌딩 참고감사 money_lock_02). 이 시험은 그 스윕이 실제로 두 표를
함께 닫고, 정상 진행 중이거나 이미 완료된 행은 절대 건드리지 않는지 본다.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from src.core import clock
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.report_delivery import constants as delivery_constants
from src.features.report_delivery import store as delivery_store
from src.features.report_delivery.cache_identity import CacheNamespace
from src.features.report_delivery.models import (
    ContentSnapshot,
    Delivery,
    DeliveryPolicy,
)
from src.features.report_delivery.source_identity import SourceSnapshot
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.web.main import app


def _seed_required_intent(*, public_id: str, required_at: dt.datetime) -> None:
    with storage_db.connect() as conn:
        delivery_store.mark_delivery_required(
            conn,
            public_id=public_id,
            required_at=required_at,
        )


def _seed_completed_intent(*, public_id: str, completed_at: dt.datetime) -> None:
    """실제 delivery까지 갖춘 complete 의무를 만든다 — 스윕이 절대 건드리면 안 된다."""

    source = SourceSnapshot.capture(
        dart_receipt_nos=("20260828000123",),
        financial_payload={
            "status": "000",
            "list": [{"account_nm": "매출액", "thstrm_amount": "100"}],
        },
        captured_at=completed_at,
        source_as_of=completed_at.date(),
        adapter_versions={"boot-sweep-test": "1"},
    )
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version="boot-sweep-v1",
        deployment_revision="a" * 40,
        requested_models={"writer": "offline-test"},
        output_settings={"fixture": "boot-sweep"},
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
            billing_bucket_id="bucket-boot-sweep",
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


def _seed_awaiting_release_link_run(*, raw_key: str, report_id: str) -> None:
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=raw_key,
            company="카카오",
            job="데이터 분석",
            now_iso="2026-08-21T09:00:00+09:00",
        )
        assert share_store.start_run(
            conn,
            key=raw_key,
            run_id=report_id,
            started_at="2026-08-21T10:00:00+09:00",
            input_company="네이버",
            confirmed_company="네이버(주)",
            company_id="corp-naver",
        )
        assert share_store.finish_run(
            conn,
            run_id=report_id,
            status=share_store.RUN_STATUS_AWAITING_RELEASE,
            finished_at="2026-08-21T10:05:00+09:00",
            report_id=report_id,
            internal_ai_cost_krw=45.0,
        )


def test_startup_marks_stale_required_delivery_intent_as_failed() -> None:
    report_id = "stale-required-" + "a" * 20
    stale_at = clock.now_kst() - dt.timedelta(
        minutes=delivery_constants.STALE_DELIVERY_INTENT_MINUTES + 15
    )
    _seed_required_intent(public_id=report_id, required_at=stale_at)

    # 새 lifespan은 이전 프로세스가 finalize를 부르기 전에 죽은 뒤를 이어받는다.
    # provider·외부 요청은 전혀 필요 없다.
    with TestClient(app):
        with storage_db.connect() as conn:
            intent = delivery_store.load_delivery_intent(conn, report_id)

    assert intent is not None
    assert intent.state == delivery_store.DELIVERY_INTENT_FAILED
    assert intent.failure_code == delivery_constants.STALE_DELIVERY_INTENT_FAILURE_CODE


def test_startup_does_not_touch_completed_or_fresh_intents() -> None:
    fresh_id = "fresh-required-" + "b" * 20
    completed_id = "already-completed-" + "c" * 16
    fresh_at = clock.now_kst() - dt.timedelta(minutes=2)
    completed_at = clock.now_kst() - dt.timedelta(
        minutes=delivery_constants.STALE_DELIVERY_INTENT_MINUTES + 60
    )
    _seed_required_intent(public_id=fresh_id, required_at=fresh_at)
    _seed_completed_intent(public_id=completed_id, completed_at=completed_at)

    with TestClient(app):
        with storage_db.connect() as conn:
            fresh_intent = delivery_store.load_delivery_intent(conn, fresh_id)
            completed_intent = delivery_store.load_delivery_intent(conn, completed_id)

    assert fresh_intent is not None
    assert fresh_intent.state == delivery_store.DELIVERY_INTENT_REQUIRED
    assert completed_intent is not None
    assert completed_intent.state == delivery_store.DELIVERY_INTENT_COMPLETE


def test_startup_stops_stale_awaiting_release_LINK_run() -> None:
    report_id = "stale-link-" + "d" * 20
    raw_key = "boot-sweep-link-secret"
    stale_at = clock.now_kst() - dt.timedelta(
        minutes=delivery_constants.STALE_DELIVERY_INTENT_MINUTES + 15
    )
    _seed_required_intent(public_id=report_id, required_at=stale_at)
    _seed_awaiting_release_link_run(raw_key=raw_key, report_id=report_id)

    with TestClient(app):
        with storage_db.connect() as conn:
            run = share_store.load_run(conn, report_id)

    assert run is not None
    assert run.status == share_store.RUN_STATUS_STOPPED
    assert run.stop_reason == "server_restart_delivery_incomplete"
    assert run.stop_step == "server_restart_recovery"
    # 스윕이 이력을 새로 만들지 않는다 — 기존 내부 비용 기록을 보존한다.
    assert run.internal_ai_cost_krw == 45.0


def test_startup_keeps_completed_LINK_run() -> None:
    report_id = "already-released-link-" + "e" * 12
    raw_key = "boot-sweep-completed-secret"
    completed_at = clock.now_kst() - dt.timedelta(
        minutes=delivery_constants.STALE_DELIVERY_INTENT_MINUTES + 60
    )
    _seed_awaiting_release_link_run(raw_key=raw_key, report_id=report_id)
    _seed_completed_intent(public_id=report_id, completed_at=completed_at)
    with storage_db.connect() as conn:
        assert share_store.mark_released(
            conn,
            report_id=report_id,
            pdf_sha256="a" * 64,
            release_sha256="b" * 64,
            released_at="2026-08-21T10:10:00+09:00",
            customer_charge_krw=0.0,
        )

    with TestClient(app):
        with storage_db.connect() as conn:
            run = share_store.load_run(conn, report_id)
            intent = delivery_store.load_delivery_intent(conn, report_id)

    assert run is not None
    assert run.status == share_store.RUN_STATUS_COMPLETED
    assert intent is not None
    assert intent.state == delivery_store.DELIVERY_INTENT_COMPLETE


def test_고아_URL은_스윕_뒤_재시도_안내로_바뀐다() -> None:
    """스윕이 닫은 report_id URL은 「관리자에게 문의」가 아니라 재시도 안내여야 한다.

    A2b: 스윕만으로는 intent.failure_code가 4개 알려진 출고차단 코드 어디에도
    없어 화면이 그대로 「저장된 보고서를 확인할 수 없습니다 / 관리자에게
    문의해 주세요」(503)로 떨어졌다. reports.py의
    ``_DELIVERY_RETRY_AVAILABLE_FAILURE_CODES`` 분기가 이를 재시도 안내로 바꾼다.
    """

    report_id = "orphan-url-retry-" + "f" * 12
    stale_at = clock.now_kst() - dt.timedelta(
        minutes=delivery_constants.STALE_DELIVERY_INTENT_MINUTES + 15
    )
    with storage_db.connect() as conn:
        delivery_store.mark_delivery_required(
            conn, public_id=report_id, required_at=stale_at
        )

    with TestClient(app) as client:
        session = auth_logic.create_session(
            "admin@example.com", True, subject="google:orphan-retry-admin"
        )
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.get(f"/result/{report_id}", follow_redirects=False)

    assert response.status_code == 409
    assert "관리자에게 문의" not in response.text
    assert "이 조사는 저장 중 중단됐습니다" in response.text
    assert "이용 횟수는 차감되지 않았습니다" in response.text
    assert "같은 회사를 다시 조사할 수 있습니다" in response.text
    # 내부 운영 용어(재시작·스윕·기계 실패 코드)를 화면에 그대로 노출하지 않는다.
    assert delivery_constants.STALE_DELIVERY_INTENT_FAILURE_CODE not in response.text
    assert "재시작" not in response.text

"""hard restart 뒤 LINK 생성 이력이 영구 running으로 남지 않는 계약."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.budget import spend_store
from src.features.budget.constants import SPEND_PHASE_IDENTIFY
from src.features.admin_dashboard import store as dashboard_store
from src.features.report_delivery import artifact as delivery_artifact
from src.features.report_delivery import store as delivery_store
from src.features.report_delivery.cache_identity import CacheNamespace
from src.features.report_delivery.models import (
    ContentSnapshot,
    Delivery,
    DeliveryPolicy,
)
from src.features.report_delivery.source_identity import SourceSnapshot
from src.features.sharelink import store as share_store
from src.features.storage import constants as storage_constants
from src.features.storage import db as storage_db
from src.web import paid_runtime, report_delivery_adapter, runtime
from src.web.main import app


def test_startup_interrupts_stale_LINK_run_with_confirmed_spend() -> None:
    raw_key = "restart-link-secret"
    run_id = "restart-interrupted-run"
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
            run_id=run_id,
            started_at="2026-08-21T10:00:00+09:00",
            input_company="네이버",
            confirmed_company="네이버(주)",
            company_id="corp-naver",
        )
        spend_store.ensure_schema(conn)
        assert spend_store.append_spend(
            conn,
            run_id=run_id,
            phase=SPEND_PHASE_IDENTIFY,
            day=clock.today_kst(),
            bucket=raw_key,
            cost_krw=23.5,
            created_at="2026-08-21T10:00:10+09:00",
        )

    # 새 lifespan은 이전 프로세스의 메모리 작업을 이어갈 수 없으므로 DB의 running을
    # interrupted로 마감한다. 외부 요청이나 provider 호출은 전혀 필요 없다.
    with TestClient(app):
        with storage_db.connect() as conn:
            recovered = share_store.load_run(conn, run_id)

    assert recovered is not None
    assert recovered.status == share_store.RUN_STATUS_INTERRUPTED
    assert recovered.stop_step == "05_생성"
    assert recovered.stop_reason == "server_restart"
    assert recovered.finished_at
    assert recovered.internal_ai_cost_krw == 23.5


def test_attempt원장_전환뒤에도_LINK_재시작비용은_확정액만_복구한다() -> None:
    raw_key = "restart-attempt-link"
    run_id = "restart-attempt-run"
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
            run_id=run_id,
            started_at="2026-08-21T10:00:00+09:00",
            input_company="네이버",
            confirmed_company="네이버(주)",
            company_id="corp-naver",
        )
        spend_store.ensure_schema(conn)
        assert spend_store.append_spend(
            conn,
            run_id=run_id,
            phase=SPEND_PHASE_IDENTIFY,
            day=clock.today_kst(),
            bucket=raw_key,
            cost_krw=23.5,
            created_at="2026-08-21T10:00:10+09:00",
        )

    paid_runtime.prepare_budget_state_machine_cutover()
    paid_runtime._seed_ledger()
    runtime._recover_link_run_history()

    with storage_db.connect() as conn:
        recovered = share_store.load_run(conn, run_id)
    assert recovered is not None
    assert recovered.status == share_store.RUN_STATUS_INTERRUPTED
    assert recovered.internal_ai_cost_krw == 23.5


def test_startup_returns_MEMBER_success_slots_when_crashed_jobs_have_no_delivery() -> None:
    actor = "restart-member@example.com"
    day = clock.today_kst().isoformat()
    with storage_db.connect() as conn:
        for index in range(3):
            assert dashboard_store.reserve_member_run(
                conn,
                run_id=f"restart-member-{index}",
                actor_email=actor,
                day=day,
                now_iso=f"{day}T09:0{index}:00+09:00",
            )
        assert dashboard_store.member_can_start(
            conn,
            actor_email=actor,
            day=day,
        ) is False

    # provider나 외부 요청 없이 lifespan의 재시작 복구만 실행한다.
    with TestClient(app):
        pass

    with storage_db.connect() as conn:
        assert dashboard_store.member_usage_today(
            conn,
            actor_email=actor,
            day=day,
        ) == (0, 0)
        assert dashboard_store.member_can_start(
            conn,
            actor_email=actor,
            day=day,
        ) is True
        assert dashboard_store.list_reserved_member_runs(conn) == ()


def _store_restart_member_delivery(
    *,
    monkeypatch: pytest.MonkeyPatch,
    data_root: Path,
    run_id: str,
    actor: str,
) -> tuple[delivery_artifact.ArtifactMetadata, Path, str]:
    """실제 SQLite·filesystem에 완료 Delivery와 회원 예약을 함께 만든다."""

    # 이 helper는 독립 검증 스크립트에서도 재사용된다. pytest의 autouse fixture에만
    # DB 격리를 맡기면 helper를 직접 부른 순간 실제 ``app/data/storage.db``에
    # 시험 예약·배송 이력이 들어간다. 파일 저장소와 SQLite를 같은 임시 뿌리에
    # 명시적으로 묶어 어느 호출 경로에서도 사용자 데이터를 건드리지 않는다.
    monkeypatch.setenv("APP_DATA_ROOT", str(data_root))
    isolated_db_path = data_root / "storage.db"
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(isolated_db_path))
    assert storage_db.default_db_path() == isolated_db_path
    now = dt.datetime(2026, 8, 31, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    day = clock.today_kst().isoformat()
    source = SourceSnapshot.capture(
        dart_receipt_nos=("20260831000001",),
        financial_payload={
            "status": "000",
            "list": [{"account_nm": "매출액", "thstrm_amount": "100"}],
        },
        captured_at=now,
        source_as_of=now.date(),
        adapter_versions={"restart-test": "1"},
    )
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version="restart-test-v1",
        deployment_revision="a" * 40,
        requested_models={"writer": "offline-test"},
        output_settings={"fixture": "member-restart"},
    )
    content = ContentSnapshot.create(
        payload=(f'{{"run_id":"{run_id}"}}').encode(),
        source_snapshot=source,
        cache_namespace=namespace,
        content_generated_at=now,
        actual_models=("offline-test",),
    )
    backend = report_delivery_adapter.configured_artifact_backend()
    pdf_bytes = f"%PDF-1.4\n% {run_id}\n%%EOF\n".encode()
    with storage_db.connect() as conn:
        assert dashboard_store.reserve_member_run(
            conn,
            run_id=run_id,
            actor_email=actor,
            day=day,
            now_iso=f"{day}T09:00:00+09:00",
        )
        delivery_store.save_source_snapshot(conn, source)
        delivery_store.save_cache_namespace(conn, namespace)
        delivery_store.save_content_snapshot(conn, content)
        delivery_store.mark_delivery_required(
            conn,
            public_id=run_id,
            required_at=now,
        )
        delivery = Delivery.issue(
            public_id=run_id,
            billing_bucket_id=f"member:{actor}",
            content=content,
            delivered_at=now,
            policy=DeliveryPolicy(
                content_max_age=dt.timedelta(days=60),
                public_link_lifetime=dt.timedelta(days=60),
            ),
            reused_from_cache=False,
        )
        delivery_store.save_delivery(conn, delivery)
        blob_intent = delivery_artifact.create_blob_write_intent(
            conn,
            backend,
            pdf_bytes=pdf_bytes,
            created_at=now,
        )
        metadata = delivery_artifact.store_approved_pdf(
            conn,
            backend,
            blob_intent=blob_intent,
            content_snapshot_id=content.content_id,
            pdf_bytes=pdf_bytes,
            version=delivery_artifact.ArtifactVersion(
                renderer_version="restart-test",
                font_bundle_version="restart-fonts",
                checker_version="restart-checker",
            ),
            created_at=now,
            retention=delivery_artifact.ArtifactRetention(
                policy_id="restart-test",
                retain_until=None,
            ),
        )
        delivery_artifact.bind_artifact_to_delivery(
            conn,
            delivery_id=delivery.delivery_id,
            artifact_id=metadata.artifact_id,
        )
        delivery_store.mark_delivery_complete(
            conn,
            public_id=run_id,
            completed_at=now,
        )
    assert metadata.blob_pointer is not None
    return (
        metadata,
        data_root / "report-artifacts" / Path(metadata.blob_pointer.key),
        day,
    )


@pytest.mark.parametrize(
    ("artifact_state", "expected_used"),
    (("available", 1), ("missing", 0), ("corrupt", 0)),
)
def test_MEMBER재시작은_실제로읽히는_PDF만_성공차감한다(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    artifact_state: str,
    expected_used: int,
) -> None:
    actor = f"restart-{artifact_state}@example.com"
    metadata, blob_path, day = _store_restart_member_delivery(
        monkeypatch=monkeypatch,
        data_root=tmp_path / artifact_state,
        run_id=f"restart-{artifact_state}",
        actor=actor,
    )
    assert metadata.blob_pointer is not None and blob_path.is_file()
    if artifact_state == "missing":
        blob_path.unlink()
    elif artifact_state == "corrupt":
        blob_path.write_bytes(b"corrupt")

    runtime._recover_member_run_history()

    with storage_db.connect() as conn:
        assert dashboard_store.member_usage_today(
            conn,
            actor_email=actor,
            day=day,
        ) == (expected_used, 0)
        assert dashboard_store.list_reserved_member_runs(conn) == ()


def test_MEMBER_PDF한건의_검사예외가_다른예약과_서버시작을_막지않는다(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    failed_actor = "restart-io-failed@example.com"
    healthy_actor = "restart-io-healthy@example.com"
    failed, _failed_path, day = _store_restart_member_delivery(
        monkeypatch=monkeypatch,
        data_root=tmp_path / "shared",
        run_id="restart-io-failed",
        actor=failed_actor,
    )
    _healthy, _healthy_path, _ = _store_restart_member_delivery(
        monkeypatch=monkeypatch,
        data_root=tmp_path / "shared",
        run_id="restart-io-healthy",
        actor=healthy_actor,
    )
    real_inspect = delivery_artifact.inspect_artifact

    def fail_one_artifact(conn, backend, artifact_id):
        if artifact_id == failed.artifact_id:
            raise PermissionError("offline fixture permission failure")
        return real_inspect(conn, backend, artifact_id)

    monkeypatch.setattr(delivery_artifact, "inspect_artifact", fail_one_artifact)
    # 이 시험은 MEMBER 복구의 startup 생존만 본다. 앞 단계의 별도 intent
    # reconcile이 같은 monkeypatch를 소비하지 않게 격리한다.
    monkeypatch.setattr(runtime, "_reconcile_artifact_blob_intents", lambda: None)

    with TestClient(app):
        pass

    with storage_db.connect() as conn:
        assert dashboard_store.member_usage_today(
            conn,
            actor_email=failed_actor,
            day=day,
        ) == (0, 0)
        assert dashboard_store.member_usage_today(
            conn,
            actor_email=healthy_actor,
            day=day,
        ) == (1, 0)
        assert dashboard_store.list_reserved_member_runs(conn) == ()

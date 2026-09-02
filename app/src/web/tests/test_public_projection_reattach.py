"""payload 문자열에서 되살린 보고서에도 공개 봉인을 다시 붙이는지 지킨다.

봉인은 보고서 payload가 아니라 별도 표에 있다(root 결정 C, 2026-09-02). 그래서
payload 문자열에서 ``Report``를 다시 만드는 경로는 봉인을 «명시적으로» 붙여야
한다. 안 붙이면 봉인이 실제로 있는데도 화면은 「봉인 없음」으로 그리고, 웹·PDF·
Notion이 다시 각자 문자열을 만든다(I7).

이 파일이 지키는 경로 넷:

  · ``report_delivery_adapter.load_public_delivery`` — 공개 결과 화면의 본문
  · ``routers/reports._approved_report`` — 관리자 승인 snapshot (라우터 갈래)
  · ``job_runtime._load_saved_report`` — 관리자 승인 snapshot (재시작 뒤 조회)
  · ``generation_singleflight`` 재사용 갈래

봉인이 저장본과 어긋나면 **그리지 않는다**(I3 fail-closed). 봉인이 아예 없는
저장본은 예외가 아니라 「봉인 없음」 상태 그대로 지나간다.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest

from src.features.report_delivery import artifact as delivery_artifact
from src.features.report_delivery import singleflight
from src.features.report_delivery import store as delivery_store
from src.features.report_delivery.models import Delivery, DeliveryPolicy
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.features.storage.constants import TABLE_REPORT_PUBLIC_PROJECTIONS
from src.features.admin_dashboard import store as dashboard_store
from src.core import clock
from src.shared import engine_build_identity as build_identity_contract
from src.shared.report_generation.public_projection import (
    build_report_digest,
    public_report_projection_from_dict,
)
from src.web import job_runtime, report_delivery_adapter
from src.web.routers import reports as reports_router
from src.web.tests.test_generation_singleflight_integration import (
    _BUILD_IDENTITY,
    _report as _plain_report,
    _COMMIT,
    _NAMESPACE_ID,
    _persist_shared_content,
    _session,
    _source_digest,
)
from src.core import deployment_identity
from src.web.tests.test_release_authority_full_wiring import (
    _COMPANY_ID,
    _build_full_report,
)


_BUCKET = "public-projection-reattach-bucket"


def _frozen():
    return build_identity_contract.process_engine_build_identity()


def _freeze_build_identity(monkeypatch) -> None:
    """singleflight 시험이 쓰는 고정 배포 신원과 같은 값으로 못 박는다.

    옆 파일의 같은 이름 fixture를 직접 부를 수 없어(pytest 금지) 그 내용만
    같은 상수로 다시 쓴다.
    """

    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", _COMMIT)


def _stored_full_report(report_id: str):
    """FULL 보고서를 봉인과 함께 저장하고 그 보고서를 돌려준다."""

    frozen = _frozen()
    report = _build_full_report(build_identity_sha256=frozen.epoch_digest)
    with storage_db.connect() as conn:
        assert report_store.insert_new(
            conn,
            report_id,
            _COMPANY_ID,
            "분석",
            report,
            engine_epoch_digest=frozen.epoch_digest,
        )
    assert report.public_projection is not None
    return report, frozen


def _swap_stored_seal_for_another_run(report_id: str) -> None:
    """저장된 봉인을 «내용이 다른» 봉인으로 digest까지 통째로 갈아 끼운다.

    ★ 두 digest 열까지 함께 고치는 것이 요점이다. 그러면 봉인 자체의 앞뒤는
      맞아 저장층의 재계산 대조를 통과하고, 오직 생성 증거와의 대조만이 이
      바꿔치기를 잡을 수 있다.

    ★ 왜 보고서를 다시 만들지 않나 — 파이프라인은 결정론이라 같은 입력이면
      «같은» 봉인이 나온다. 그러면 바꿔치기가 바꿔치기가 아니게 된다. 그래서
      저장된 봉인의 표시 필드 하나를 바꿔 실제로 다른 봉인을 만든다.
    """

    with storage_db.connect() as conn:
        row = conn.execute(
            f"""SELECT projection_json FROM {TABLE_REPORT_PUBLIC_PROJECTIONS}
            WHERE report_id = ?""",
            (report_id,),
        ).fetchone()
        assert row is not None, "위조하려면 봉인이 먼저 저장돼 있어야 한다"
        payload = json.loads(str(row["projection_json"]))
        payload["header"]["company"] = "바꿔치기된 회사"
        forged = public_report_projection_from_dict(payload)
        digest = build_report_digest(forged)
        changed = conn.execute(
            f"""UPDATE {TABLE_REPORT_PUBLIC_PROJECTIONS}
            SET projection_json = ?, content_sha256 = ?, display_sha256 = ?
            WHERE report_id = ?""",
            (
                json.dumps(
                    report_store.public_projection_payload(forged),
                    ensure_ascii=False,
                ),
                digest.content_sha256,
                digest.display_sha256,
                report_id,
            ),
        )
        assert changed.rowcount == 1


# ══════════════════════════════════════════════════════════
# ① 공개 결과 화면 — 가장 중요한 경로
# ══════════════════════════════════════════════════════════


def _finalized(report_id: str, monkeypatch, tmp_path):
    report, frozen = _stored_full_report(report_id)
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "art"))
    reports_router.finalize_new_report_delivery(
        report_id=report_id,
        corp_id=_COMPANY_ID,
        billing_bucket_id=_BUCKET,
        report=report,
        actual_models=("deterministic-reattach",),
        reused_from_cache=False,
        engine_build_identity=frozen,
    )
    return report


def test_공개_결과_로드는_봉인을_붙인다(monkeypatch, tmp_path) -> None:
    report_id = uuid.uuid4().hex
    report = _finalized(report_id, monkeypatch, tmp_path)

    loaded = report_delivery_adapter.load_public_delivery(report_id)

    assert loaded is not None
    assert loaded.report.public_projection == report.public_projection


def test_봉인_digest_불일치는_공개_결과를_그리지_않는다(monkeypatch, tmp_path) -> None:
    """봉인 위조가 감지되면 보고서를 내주지 않고 기존 오류 경로로 닫는다."""

    report_id = uuid.uuid4().hex
    _finalized(report_id, monkeypatch, tmp_path)
    _swap_stored_seal_for_another_run(report_id)

    with pytest.raises(report_delivery_adapter.DeliveryAdapterError):
        report_delivery_adapter.load_public_delivery(report_id)


# ══════════════════════════════════════════════════════════
# ② 관리자 승인 snapshot — 라우터 갈래와 재시작 뒤 조회 갈래
# ══════════════════════════════════════════════════════════


def _registered_approved_snapshot(report_id: str):
    report, _frozen_identity = _stored_full_report(report_id)
    with storage_db.connect() as conn:
        dashboard_store.register_report(
            conn,
            report_id=report_id,
            corp_type=report.corp_type,
            payload_json=report_store.report_to_json(report),
            now_iso=clock.iso_now_kst(),
        )
    return report


def test_승인_snapshot_로드는_봉인을_붙인다() -> None:
    report_id = uuid.uuid4().hex
    report = _registered_approved_snapshot(report_id)

    loaded = job_runtime._load_saved_report(report_id)

    assert loaded is not None
    assert loaded.public_projection == report.public_projection


def test_승인_snapshot_라우터_갈래도_봉인을_붙인다() -> None:
    report_id = uuid.uuid4().hex
    report = _registered_approved_snapshot(report_id)

    loaded = reports_router._approved_public_report(report_id, None)

    assert loaded is not None
    assert loaded.public_projection == report.public_projection


def test_승인_snapshot의_봉인이_어긋나면_라우터가_보고서를_내주지_않는다() -> None:
    report_id = uuid.uuid4().hex
    _registered_approved_snapshot(report_id)
    _swap_stored_seal_for_another_run(report_id)

    assert reports_router._approved_public_report(report_id, None) is None


# ══════════════════════════════════════════════════════════
# ③ singleflight 재사용
# ══════════════════════════════════════════════════════════


def test_singleflight_재사용은_봉인을_붙인다(monkeypatch, tmp_path) -> None:
    """waiter가 물려받는 본문에도 봉인이 따라와야 한다.

    ★ 재사용 본문은 payload 문자열(content snapshot)에서 되살아나므로 봉인이
      없다. 원래 발급 Delivery의 공개 ID로 저장된 봉인을 다시 붙인다.
    """

    _freeze_build_identity(monkeypatch)
    report_id = uuid.uuid4().hex
    report = _build_full_report(build_identity_sha256=_BUILD_IDENTITY.epoch_digest)
    assert report.public_projection is not None

    content, artifact = _persist_shared_content(
        report, artifact_root=tmp_path / "reattach-artifacts"
    )
    delivered_at = content.content_generated_at + dt.timedelta(hours=1)
    with storage_db.connect() as conn:
        assert report_store.insert_new(
            conn,
            report_id,
            _COMPANY_ID,
            "분석",
            report,
            engine_epoch_digest=_BUILD_IDENTITY.epoch_digest,
        )
        delivery = Delivery.issue(
            public_id=report_id,
            billing_bucket_id=_BUCKET,
            content=content,
            delivered_at=delivered_at,
            policy=DeliveryPolicy(
                content_max_age=dt.timedelta(days=60),
                public_link_lifetime=dt.timedelta(days=60),
            ),
            reused_from_cache=False,
        )
        delivery_store.save_delivery(conn, delivery)
        delivery_artifact.bind_artifact_to_delivery(
            conn,
            delivery_id=delivery.delivery_id,
            artifact_id=artifact.artifact_id,
        )

    session = _session("reattach-waiter", _BUCKET)
    key = singleflight.LeaseKey(
        billing_bucket_id=_BUCKET,
        corp_id=_COMPANY_ID,
        cache_namespace_id=_NAMESPACE_ID,
        source_identity_digest=_source_digest(),
        engine_epoch_digest=_BUILD_IDENTITY.epoch_digest,
    )
    with storage_db.connect() as conn:
        reused = session._read_completed(
            conn,
            key=key,
            content_id=content.content_id,
            artifact_id=artifact.artifact_id,
        )

    assert reused.report.public_projection == report.public_projection


def test_singleflight_재사용도_봉인이_어긋나면_닫는다(monkeypatch, tmp_path) -> None:
    _freeze_build_identity(monkeypatch)
    report_id = uuid.uuid4().hex
    report = _build_full_report(build_identity_sha256=_BUILD_IDENTITY.epoch_digest)
    content, artifact = _persist_shared_content(
        report, artifact_root=tmp_path / "reattach-bad-artifacts"
    )
    with storage_db.connect() as conn:
        assert report_store.insert_new(
            conn,
            report_id,
            _COMPANY_ID,
            "분석",
            report,
            engine_epoch_digest=_BUILD_IDENTITY.epoch_digest,
        )
        delivery = Delivery.issue(
            public_id=report_id,
            billing_bucket_id=_BUCKET,
            content=content,
            delivered_at=content.content_generated_at + dt.timedelta(hours=1),
            policy=DeliveryPolicy(
                content_max_age=dt.timedelta(days=60),
                public_link_lifetime=dt.timedelta(days=60),
            ),
            reused_from_cache=False,
        )
        delivery_store.save_delivery(conn, delivery)
        delivery_artifact.bind_artifact_to_delivery(
            conn,
            delivery_id=delivery.delivery_id,
            artifact_id=artifact.artifact_id,
        )
    _swap_stored_seal_for_another_run(report_id)

    session = _session("reattach-bad-waiter", _BUCKET)
    key = singleflight.LeaseKey(
        billing_bucket_id=_BUCKET,
        corp_id=_COMPANY_ID,
        cache_namespace_id=_NAMESPACE_ID,
        source_identity_digest=_source_digest(),
        engine_epoch_digest=_BUILD_IDENTITY.epoch_digest,
    )
    with storage_db.connect() as conn:
        with pytest.raises(Exception) as caught:
            session._read_completed(
                conn,
                key=key,
                content_id=content.content_id,
                artifact_id=artifact.artifact_id,
            )
    assert "봉인" in str(caught.value)


# ══════════════════════════════════════════════════════════
# ④ 봉인이 없는 저장본은 예외가 아니다
# ══════════════════════════════════════════════════════════


def test_봉인을_주장하는_보고서의_봉인_행이_사라지면_공개_결과가_닫힌다(
    monkeypatch, tmp_path
) -> None:
    """★ 뒤집힌 시험이다 — S3d에서는 「봉인 없음으로 열린다」였다.

    뒤집은 근거: root 결정 S3f(2026-09-02). 두 규칙은 층이 달라 서로 어긋나지
    않는다.
      · 저장층(``reports.load``)은 봉인 행이 없으면 ``None``을 그대로 돌려준다.
        그건 「봉인 없음」이라는 정의된 상태다(S3b 결정, 그 시험은 지금도 초록).
      · 공개 결과 경로는 다르다. 여기서 그리는 본문의 생성 증거가 「내 봉인의
        지문은 이것」이라고 «말하고» 있는데 그 봉인을 못 찾았다면, 말과 실제가
        다른 것이라 그리지 않고 닫는다(I3).
    """

    report_id = uuid.uuid4().hex
    _finalized(report_id, monkeypatch, tmp_path)
    with storage_db.connect() as conn:
        conn.execute(
            f"DELETE FROM {TABLE_REPORT_PUBLIC_PROJECTIONS} WHERE report_id = ?",
            (report_id,),
        )

    with pytest.raises(report_delivery_adapter.DeliveryAdapterError):
        report_delivery_adapter.load_public_delivery(report_id)


# ══════════════════════════════════════════════════════════
# ⑤ public_id != report_id — 조용한 「봉인 없음」 금지 (S3f)
# ══════════════════════════════════════════════════════════


def _delivery_under_other_public_id(
    report, *, public_id: str, artifact_root
) -> None:
    """봉인이 저장된 report_id와 «다른» 공개 ID로 Delivery를 하나 만든다.

    운영 발급 경로 두 곳은 언제나 ``public_id=report_id``로 넣는다. 이 모양은
    그 관례가 깨진 상태를 흉내 낸 것이다.
    """

    content, artifact = _persist_shared_content(report, artifact_root=artifact_root)
    with storage_db.connect() as conn:
        delivery = Delivery.issue(
            public_id=public_id,
            billing_bucket_id=_BUCKET,
            content=content,
            delivered_at=content.content_generated_at + dt.timedelta(hours=1),
            policy=DeliveryPolicy(
                content_max_age=dt.timedelta(days=60),
                public_link_lifetime=dt.timedelta(days=60),
            ),
            reused_from_cache=False,
        )
        delivery_store.save_delivery(conn, delivery)
        delivery_artifact.bind_artifact_to_delivery(
            conn,
            delivery_id=delivery.delivery_id,
            artifact_id=artifact.artifact_id,
        )


def test_공개ID가_보고서ID와_다르면_봉인_없음으로_조용히_넘어가지_않는다(
    monkeypatch, tmp_path
) -> None:
    """★ 조용한 실패가 가장 나쁘다.

    봉인은 ``report_id``로 저장된다. Delivery의 공개 ID가 그것과 다르면 조회가
    빈손으로 돌아오고, 그 결과는 「이 보고서에는 봉인이 없다」와 **구별되지
    않는다**. 그런데 본문에 실린 생성 증거는 「내 봉인의 지문은 이것」이라고
    말하고 있다. 말과 실제가 다르면 그리지 않고 닫는다(I3).

    발급 경로 두 곳(``routers/reports.py``의 ``persist_reused_delivery``·
    ``persist_approved_delivery`` 호출, 2026-09-02 기준 1464·1546행)은 언제나
    ``public_id=report_id``로 넣는다. 이 시험은 그 관례가 깨졌을 때 조용히
    지나가지 않는다는 약속이다.
    """

    _freeze_build_identity(monkeypatch)
    report_id = uuid.uuid4().hex
    other_public_id = uuid.uuid4().hex
    report = _build_full_report(build_identity_sha256=_BUILD_IDENTITY.epoch_digest)
    assert report.public_projection is not None
    with storage_db.connect() as conn:
        assert report_store.insert_new(
            conn,
            report_id,
            _COMPANY_ID,
            "분석",
            report,
            engine_epoch_digest=_BUILD_IDENTITY.epoch_digest,
        )
    _delivery_under_other_public_id(
        report,
        public_id=other_public_id,
        artifact_root=tmp_path / "mismatched-artifacts",
    )

    with pytest.raises(report_delivery_adapter.DeliveryAdapterError):
        report_delivery_adapter.load_public_delivery(other_public_id)


def test_증거가_봉인을_말하지_않는_보고서는_봉인_없이도_열린다(
    monkeypatch, tmp_path
) -> None:
    """음성 대조 — 위 규칙이 봉인을 «주장하지 않는» 보고서를 막지 않는다.

    v1·SHADOW 저장본은 생성 증거 자체가 없어 「봉인이 있다」고 말하지 않는다.
    그런 보고서는 봉인 행이 없어도 그대로 열려야 한다. 이게 깨지면 옛 보고서가
    화면에서 통째로 안 열린다.
    """

    _freeze_build_identity(monkeypatch)
    public_id = uuid.uuid4().hex
    plain = _plain_report()
    assert plain.generation_evidence is None
    _delivery_under_other_public_id(
        plain,
        public_id=public_id,
        artifact_root=tmp_path / "plain-artifacts",
    )

    loaded = report_delivery_adapter.load_public_delivery(public_id)

    assert loaded is not None
    assert loaded.report.public_projection is None

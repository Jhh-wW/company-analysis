"""raw 보고서 staging이 Delivery 성공 전에는 정상 보고서가 되지 않는다.

이 시험은 저장 함수 하나가 아니라 실제 worker의
``의무 표식 -> raw 저장 -> 출고 확정`` 순서와 관리자 조회/링크 발급 경계를
함께 고정한다. 출고 중 죽거나 마지막 transaction이 rollback돼도 raw 본문이
관리자 화면을 통해 정상 보고서로 승격되는 회귀를 막는다.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.admin_dashboard import store as dashboard_store
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget.sharing import REPORT_LINK_MAX_AGE_DAYS
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.features.report_access import store as report_access_store
from src.features.report_access.models import ReportAudience
from src.features.report_delivery import constants as delivery_constants
from src.features.report_delivery import store as delivery_store
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.shared import engine_build_identity as build_identity_contract
from src.shared.report_evidence.constants import ReleaseMode
from src.web import job_runtime, report_publication, runtime
from src.web.main import app
from src.web.routers import admin as admin_router
from src.web.routers import analysis as analysis_router
from src.web.routers import dashboard as dashboard_router
from src.web.routers import reports as reports_router


# web 공통 fixture가 값싼 가짜 완료 함수로 바꾸기 전, 생산 함수를 보관한다.
_REAL_REQUIRE_REPORT_DELIVERY = job_runtime._require_report_delivery
_REAL_FINALIZE_REPORT_DELIVERY = job_runtime._finalize_report_delivery


def _demo_report(*, full: bool = False):
    pipeline = DemoPipeline()
    sample = next(item for item in available_companies() if item["is_report"])
    user_input = UserInput(
        company=sample["company"],
        job=sample["job"],
        region="",
        posting_text="",
    )
    result = pipeline.run(user_input, pipeline.find_company(user_input))
    assert result.outcome is Outcome.REPORT and result.report is not None
    return dataclasses.replace(
        result.report,
        generated_at=clock.iso_now_kst(),
        release_mode=(ReleaseMode.FULL.value if full else result.report.release_mode),
    )


def _admin_job(report, *, report_id: str, with_result: bool) -> job_runtime.Job:
    return job_runtime.Job(
        job_id=report_id,
        user_input=UserInput(company=report.company, job=report.job, region=""),
        card=CompanyCard(
            legal_name=report.company,
            typed_name=report.company,
            address="",
            ceo="",
            founded="",
            ref="staged-admin-corp",
        ),
        result=(RunResult(outcome=Outcome.REPORT, report=report) if with_result else None),
        report_audience=ReportAudience.ADMIN,
        engine_build_identity=build_identity_contract.process_engine_build_identity(),
    )


def _stage_required_report(report, *, report_id: str) -> job_runtime.Job:
    job = _admin_job(report, report_id=report_id, with_result=True)
    issued_at = clock.now_kst()
    job.delivery_issued_at = issued_at
    job.delivery_expires_at = issued_at + dt.timedelta(days=REPORT_LINK_MAX_AGE_DAYS)
    assert _REAL_REQUIRE_REPORT_DELIVERY(job)
    assert job_runtime._save_report(job)
    return job


def _login_admin(client: TestClient) -> str:
    session = auth_logic.create_session(
        "admin@example.com",
        True,
        subject=f"test:staged-admin:{uuid.uuid4().hex}",
    )
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    return auth_logic.csrf_token_for_session(session.token)


def _assert_admin_rejects_temporary_report(
    client: TestClient,
    *,
    report_id: str,
    company: str,
    csrf_token: str,
) -> None:
    detail = client.get(f"/admin/reports/{report_id}", follow_redirects=False)
    assert detail.status_code == 409
    assert detail.text == (
        "출고가 완료되지 않은 임시 보고서는 정상 보고서로 검토할 수 없습니다."
    )
    # raw 본문을 응답에 섞지 않는다.
    assert company not in detail.text
    snapshot = client.get(
        f"/admin/reports/{report_id}/versions/1", follow_redirects=False
    )
    assert snapshot.status_code == 409
    assert snapshot.text == (
        "출고가 완료되지 않은 임시 보고서 스냅샷은 검토할 수 없습니다."
    )
    assert company not in snapshot.text

    with storage_db.connect() as conn:
        before = tuple(link.key_hash for link in share_store.list_all(conn))
    issued = client.post(
        "/admin/links/new",
        data={
            "company": company,
            "job": "개발",
            "report_reference": report_id,
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )
    assert issued.status_code == 400
    assert "자동출고가 완료되지 않은 임시 보고서는 연결할 수 없습니다." in issued.text
    with storage_db.connect() as conn:
        after = tuple(link.key_hash for link in share_store.list_all(conn))
    assert after == before


def _assert_staged_and_blocked(report_id: str) -> None:
    with storage_db.connect() as conn:
        assert report_store.load(conn, report_id) is not None
        state = dashboard_store.get_report_state(conn, report_id)
        assert (
            dashboard_router._dashboard_stored_report_state(conn, report_id)
            == "unavailable"
        )
        assert state.status == dashboard_store.REPORT_STATUS_PENDING
        assert state.blocked is True
        assert dashboard_store.approved_report_payload(
            conn, report_id=report_id
        ) == ""


def test_실제worker_출고예외는_raw를_관리자정상보고서나_LINK로_승격하지않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # demo/non-FULL은 기존 호환 계약상 finalize 실패 뒤 메모리 preview가 남는다.
    # 바로 그 까다로운 경우에도 재시작 뒤 DB raw가 정상 보고서로 보이면 안 된다.
    report = _demo_report()
    report_id = uuid.uuid4().hex
    job = _admin_job(report, report_id=report_id, with_result=False)

    class _ReportPipeline:
        @staticmethod
        def run(*_args, **_kwargs):
            return RunResult(outcome=Outcome.REPORT, report=report)

    def _raise_at_finalize(_job):
        raise RuntimeError("시험: raw 저장 뒤 자동출고 확정 실패")

    monkeypatch.setattr(runtime, "_PIPELINE", _ReportPipeline())
    monkeypatch.setattr(
        job_runtime, "_require_report_delivery", _REAL_REQUIRE_REPORT_DELIVERY
    )
    monkeypatch.setattr(job_runtime, "_finalize_report_delivery", _raise_at_finalize)
    monkeypatch.setattr(job_runtime, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    asyncio.run(job_runtime._run_job(job))

    assert job.report_persisted is True
    assert job.delivery_persisted is False
    assert job.result is not None
    assert job.result.outcome is Outcome.REPORT
    assert job.result.report is report
    _assert_staged_and_blocked(report_id)
    with storage_db.connect() as conn:
        intent = delivery_store.load_delivery_intent(conn, report_id)
    assert intent is not None
    assert intent.state == delivery_store.DELIVERY_INTENT_FAILED
    assert intent.failure_code == "artifact_finalization_failed"

    # 실제 서버에서는 worker가 끝난 뒤 이 Job이 메모리에 남는다. raw보다
    # 메모리를 먼저 읽던 옛 경로가 결과·PDF·Notion을 우회하지 못해야 한다.
    job_runtime._JOBS[report_id] = job

    def _forbidden_raw_export(*_args, **_kwargs):
        raise AssertionError("실패 Delivery의 메모리/raw 보고서를 출력했습니다")

    monkeypatch.setattr(reports_router, "_release_state", _forbidden_raw_export)
    monkeypatch.setattr(
        reports_router, "send_report_to_notion", _forbidden_raw_export
    )
    with TestClient(app) as client:
        csrf = _login_admin(client)
        progress = client.get(f"/api/progress/{report_id}", follow_redirects=False)
        result = client.get(f"/result/{report_id}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{report_id}", follow_redirects=False)
        notion = client.post(
            f"/notion/{report_id}",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert progress.status_code == 409
        assert progress.json() == {
            "error": (
                "보고서 저장의 마지막 확인이 끝나지 않아 결과를 열지 않습니다. "
                "이용 횟수는 차감되지 않았습니다."
            ),
            "code": "report_not_published",
            "retry_url": "/",
        }
        assert "finished" not in progress.json()
        assert "next_url" not in progress.json()
        # FAILED intent의 저장된 실패 종류가 일반 STAGED(409 「확인 중」)보다
        # 먼저다. artifact 최종화 실패는 재시도로 복구할 수 있는지 모르는 저장
        # 실패이므로 세 채널 모두 503으로 닫고 raw는 결코 보여주지 않는다.
        assert result.status_code == pdf.status_code == notion.status_code == 503
        assert "저장된 보고서를 확인할 수 없습니다" in result.text
        assert "보고서를 잠시 확인 중입니다" not in result.text
        assert report.company not in result.text
        assert report.company not in pdf.text
        assert report.company not in notion.text

    # 재시작 뒤 메모리 Job이 사라져도 raw DB 행만으로 정상 화면을 만들 수 없다.
    job_runtime._JOBS.clear()
    with TestClient(app) as client:
        csrf = _login_admin(client)
        _assert_admin_rejects_temporary_report(
            client,
            report_id=report_id,
            company=report.company,
            csrf_token=csrf,
        )


def test_REQUIRED에서_죽은보고서는_재시작스윕뒤에도_pending차단을_유지한다() -> None:
    report = _demo_report()
    report_id = uuid.uuid4().hex
    _stage_required_report(report, report_id=report_id)
    _assert_staged_and_blocked(report_id)

    # 아직 30분이 안 지난 REQUIRED도 관리자 상세·LINK 발급 권위가 아니다.
    with TestClient(app) as client:
        csrf = _login_admin(client)
        _assert_admin_rejects_temporary_report(
            client,
            report_id=report_id,
            company=report.company,
            csrf_token=csrf,
        )

    # 프로세스가 finalize 전에 죽고 30분이 지난 모양을 만든다.
    stale_at = clock.now_kst() - dt.timedelta(
        minutes=delivery_constants.STALE_DELIVERY_INTENT_MINUTES + 1
    )
    with storage_db.connect() as conn:
        conn.execute(
            f"UPDATE {delivery_store.TABLE_DELIVERY_INTENTS} "
            "SET required_at=?, updated_at=? WHERE public_id=?",
            (
                stale_at.astimezone(dt.timezone.utc).isoformat(),
                stale_at.astimezone(dt.timezone.utc).isoformat(),
                report_id,
            ),
        )

    # 새 TestClient lifespan이 실제 서버 시작 스윕을 실행한다.
    job_runtime._JOBS.clear()
    with TestClient(app) as client:
        csrf = _login_admin(client)
        with storage_db.connect() as conn:
            intent = delivery_store.load_delivery_intent(conn, report_id)
        assert intent is not None
        assert intent.state == delivery_store.DELIVERY_INTENT_FAILED
        assert (
            intent.failure_code
            == delivery_constants.STALE_DELIVERY_INTENT_FAILURE_CODE
        )
        _assert_admin_rejects_temporary_report(
            client,
            report_id=report_id,
            company=report.company,
            csrf_token=csrf,
        )
    _assert_staged_and_blocked(report_id)


def test_intent행없이_죽은_staging도_재시작뒤_legacy로_열지않는다() -> None:
    """intent=None은 legacy의 충분조건이 아니다 — staging 사건도 함께 본다."""

    report = _demo_report()
    report_id = uuid.uuid4().hex
    job = _admin_job(report, report_id=report_id, with_result=True)
    # 의무 표식 함수를 일부러 부르지 않아 raw commit 직후 crash/행 손실을 재현한다.
    assert job_runtime._save_report(job)
    with storage_db.connect() as conn:
        assert delivery_store.load_delivery_intent(conn, report_id) is None
        assert dashboard_store.report_is_unpublished_staging(conn, report_id)

    # 시작 스윕은 intent 없는 행을 만들거나 legacy로 승격하지 않는다.
    job_runtime._JOBS.clear()
    with TestClient(app) as client:
        csrf = _login_admin(client)
        pending = client.get(f"/result/{report_id}", follow_redirects=False)
        assert pending.status_code == 409
        assert "보고서를 잠시 확인 중입니다" in pending.text
        assert 'href=""' in pending.text
        assert report.company not in pending.text
        _assert_admin_rejects_temporary_report(
            client,
            report_id=report_id,
            company=report.company,
            csrf_token=csrf,
        )
    with storage_db.connect() as conn:
        assert delivery_store.load_delivery_intent(conn, report_id) is None
        assert dashboard_store.report_is_unpublished_staging(conn, report_id)
        assert not report_publication.report_is_published_or_legacy(conn, report_id)
    _assert_staged_and_blocked(report_id)


def test_staging소유자도_직접POST로_설문이나_오류를_기록하지못한다() -> None:
    """화면 차단 외에 MEMBER 쓰기 경계도 같은 publication 판정을 쓴다."""

    report = _demo_report()
    report_id = uuid.uuid4().hex
    email = "staged-member@example.com"
    subject = "google:staged-member-owner"
    other_email = "other-staged-member@example.com"
    other_subject = "google:other-staged-member"
    job = _admin_job(report, report_id=report_id, with_result=True)
    job.report_audience = ReportAudience.MEMBER
    job.member_email = email
    with storage_db.connect() as conn:
        assert share_allow.invite(
            conn,
            email=email,
            note="",
            now_iso=clock.iso_now_kst(),
        )
        assert share_allow.invite(
            conn,
            email=other_email,
            note="",
            now_iso=clock.iso_now_kst(),
        )
        assert dashboard_store.reserve_member_run(
            conn,
            run_id=report_id,
            actor_email=email,
            day=clock.today_kst().isoformat(),
            now_iso=clock.iso_now_kst(),
        )
        assert report_access_store.bind_member_run(
            conn,
            run_id=report_id,
            identity_subject=subject,
        )
    assert job_runtime._save_report(job)

    # dashboard blocked 한 겹에만 우연히 기대지 않는다. projection이 잘못
    # normal로 바뀌어도 마지막 staging 사건이 publication helper를 닫아야 한다.
    with storage_db.connect() as conn:
        conn.execute(
            f"UPDATE {dashboard_store.TABLE_REPORT_STATES} "
            "SET status=?, blocked=0 WHERE report_id=?",
            (dashboard_store.REPORT_STATUS_NORMAL, report_id),
        )
        assert not report_publication.report_is_published_or_legacy(conn, report_id)

    with TestClient(app) as client:
        session = auth_logic.create_session(email, False, subject=subject)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        csrf = auth_logic.csrf_token_for_session(session.token)
        survey = client.post(
            f"/reports/{report_id}/survey",
            data={
                "rating": "5",
                "overall_feedback": "유용합니다",
                "business_distinction": "구분이 잘 보입니다",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        error = client.post(
            f"/reports/{report_id}/errors",
            data={
                "area": "매출 표",
                "reason": "원문과 다릅니다",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
    # 공개 상태 우선순위를 고르기 전에 소유권이 먼저다. 초대된 다른 회원도
    # 이 실행의 STAGED/intent 여부나 회사명을 관찰할 수 없어야 한다.
    with TestClient(app) as other_client:
        other_session = auth_logic.create_session(
            other_email,
            False,
            subject=other_subject,
        )
        other_client.cookies.set(
            auth_constants.SESSION_COOKIE_NAME, other_session.token
        )
        crossed = other_client.get(
            f"/result/{report_id}", follow_redirects=False
        )
    assert crossed.status_code == 404
    assert report.company not in crossed.text
    assert survey.status_code == 409
    assert survey.text == (
        "출고가 완료되지 않은 임시 보고서에는 설문을 남길 수 없습니다."
    )
    assert error.status_code == 409
    assert error.text == (
        "출고가 완료되지 않은 임시 보고서에는 오류를 신고할 수 없습니다."
    )
    with storage_db.connect() as conn:
        assert conn.execute(
            f"SELECT COUNT(*) FROM {dashboard_store.TABLE_SURVEYS} WHERE report_id=?",
            (report_id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            f"SELECT COUNT(*) FROM {dashboard_store.TABLE_ERRORS} "
            "WHERE report_id=?",
            (report_id,),
        ).fetchone()[0] == 0


def test_보고서행조차없으면_legacy가아니며_실제legacy행만허용한다() -> None:
    """부재를 옛 보고서로 오인하지 않고, 실제 과거 payload만 호환한다."""

    report_id = uuid.uuid4().hex
    with storage_db.connect() as conn:
        assert not report_store.exists(conn, report_id)
        assert not report_publication.report_is_published_or_legacy(
            conn, report_id
        )

        report = _demo_report()
        assert report_store.insert_new(
            conn,
            report_id=report_id,
            corp_id="legacy-corp",
            job=report.job,
            report=report,
            engine_epoch_digest=(
                build_identity_contract.process_engine_build_identity().epoch_digest
            ),
        )
        assert report_publication.report_is_published_or_legacy(conn, report_id)


def test_intent없는_기존보고서는_소급차단하지않고_관리자LINK를_허용한다() -> None:
    report = _demo_report()
    report_id = uuid.uuid4().hex
    with storage_db.connect() as conn:
        assert report_store.insert_new(
            conn,
            report_id=report_id,
            corp_id="legacy-corp",
            job=report.job,
            report=report,
            engine_epoch_digest=(
                build_identity_contract.process_engine_build_identity().epoch_digest
            ),
        )
        dashboard_store.register_report(
            conn,
            report_id=report_id,
            corp_type=report.corp_type,
            now_iso=clock.iso_now_kst(),
            payload_json=report_store.report_to_json(report),
        )
        assert delivery_store.load_delivery_intent(conn, report_id) is None
        assert report_publication.report_is_published_or_legacy(conn, report_id)

    with TestClient(app) as client:
        csrf = _login_admin(client)
        progress = client.get(f"/api/progress/{report_id}")
        assert progress.status_code == 200
        assert progress.json()["finished"] is True
        assert progress.json()["recovered"] is True
        assert progress.json()["next_url"] == f"/result/{report_id}"
        detail = client.get(f"/admin/reports/{report_id}", follow_redirects=False)
        assert detail.status_code == 200
        assert report.company in detail.text
        issued = client.post(
            "/admin/links/new",
            data={
                "company": report.company,
                "job": report.job,
                "report_reference": report_id,
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        assert issued.status_code == 200
        key_hash = issued.headers["X-Link-Identifier"]
    with storage_db.connect() as conn:
        link = share_store.load_by_hash(conn, key_hash)
        assert link is not None
        assert link.report_id == report_id
        assert admin_router._linked_report_state(conn, link) == "active"
    assert analysis_router._bound_report_view(link)[0] == f"/result/{report_id}"


@pytest.mark.parametrize("intent_state", ("complete", "missing"))
def test_publish사건만꾸며도_실제delivery없는raw는_완료로보지않는다(
    intent_state: str,
) -> None:
    """상태 열/사건은 Delivery의 대체물이 아니다.

    생산 코드는 한 transaction에서 둘을 쓰지만, 관리자 도구·부분 복구·손상으로
    사건만 남아도 공통 읽기 경계가 raw를 정상 보고서로 열어서는 안 된다.
    """

    report = _demo_report()
    report_id = uuid.uuid4().hex
    job = _stage_required_report(report, report_id=report_id)
    with storage_db.connect() as conn:
        assert dashboard_store.publish_staged_report(
            conn,
            report_id=report_id,
            now_iso=clock.iso_now_kst(),
        )
        if intent_state == "complete":
            # 정상 API는 실제 Delivery 없이는 COMPLETE를 거절한다. 바로 그
            # 불변식이 외부 복구/손상으로 깨진 저장 모양을 재현한다.
            conn.execute(
                f"UPDATE {delivery_store.TABLE_DELIVERY_INTENTS} "
                "SET state=?, failure_code='' WHERE public_id=?",
                (delivery_store.DELIVERY_INTENT_COMPLETE, report_id),
            )
        else:
            conn.execute(
                f"DELETE FROM {delivery_store.TABLE_DELIVERY_INTENTS} "
                "WHERE public_id=?",
                (report_id,),
            )
        assert dashboard_store.report_publication_lifecycle(conn, report_id) == (
            dashboard_store.REPORT_EVENT_PUBLISHED
        )
        assert delivery_store.load_delivery_by_public_id(conn, report_id) is None
        if intent_state == "complete":
            with pytest.raises(delivery_store.LifecycleStoreCorrupt):
                report_publication.report_is_published_or_legacy(conn, report_id)
            with pytest.raises(delivery_store.LifecycleStoreCorrupt):
                dashboard_router._dashboard_stored_report_state(conn, report_id)
        else:
            assert not report_publication.report_is_published_or_legacy(
                conn, report_id
            )
            assert (
                dashboard_router._dashboard_stored_report_state(conn, report_id)
                == "unavailable"
            )

    job_runtime._JOBS.clear()
    with TestClient(app) as client:
        _login_admin(client)
        progress = client.get(f"/api/progress/{report_id}")
        detail = client.get(f"/admin/reports/{report_id}", follow_redirects=False)
    assert progress.status_code == (503 if intent_state == "complete" else 410)
    assert progress.json()["code"] == (
        "progress_store_unavailable"
        if intent_state == "complete"
        else "job_unavailable"
    )
    assert "finished" not in progress.json()
    assert detail.status_code == (503 if intent_state == "complete" else 409)
    assert report.company not in detail.text


def test_실제성공transaction만_normal로_승격하고_COMPLETE재시도는_멱등이다(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _demo_report()
    report_id = uuid.uuid4().hex
    job = _admin_job(report, report_id=report_id, with_result=False)

    class _ReportPipeline:
        @staticmethod
        def run(*_args, **_kwargs):
            return RunResult(outcome=Outcome.REPORT, report=report)

    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setattr(runtime, "_PIPELINE", _ReportPipeline())
    monkeypatch.setattr(
        job_runtime, "_require_report_delivery", _REAL_REQUIRE_REPORT_DELIVERY
    )
    monkeypatch.setattr(
        job_runtime, "_finalize_report_delivery", _REAL_FINALIZE_REPORT_DELIVERY
    )
    monkeypatch.setattr(job_runtime, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    asyncio.run(job_runtime._run_job(job))

    assert job.report_persisted is True
    assert job.delivery_persisted is True
    assert job.result is not None and job.result.outcome is Outcome.REPORT
    with storage_db.connect() as conn:
        state = dashboard_store.get_report_state(conn, report_id)
        intent = delivery_store.load_delivery_intent(conn, report_id)
        published_before = conn.execute(
            f"SELECT COUNT(*) FROM {dashboard_store.TABLE_REPORT_EVENTS} "
            "WHERE report_id=? AND action=?",
            (report_id, dashboard_store.REPORT_EVENT_PUBLISHED),
        ).fetchone()[0]
    assert state.status == dashboard_store.REPORT_STATUS_NORMAL
    assert state.blocked is False
    assert intent is not None
    assert intent.state == delivery_store.DELIVERY_INTENT_COMPLETE
    assert published_before == 1

    # 같은 process에 성공 Job이 남아 있는 동안에도 DB publication을 확인한 뒤
    # 정상 결과 주소를 준다. 미출고 REPORT만 막고 완료 보고서를 같이 막지 않는다.
    job_runtime._JOBS[report_id] = job
    with TestClient(app) as client:
        _login_admin(client)
        live_progress = client.get(f"/api/progress/{report_id}")
    assert live_progress.status_code == 200
    assert live_progress.json()["finished"] is True
    assert live_progress.json()["next_url"] == f"/result/{report_id}"

    job_runtime._JOBS.clear()
    with TestClient(app) as client:
        _login_admin(client)
        progress = client.get(f"/api/progress/{report_id}")
        published_result = client.get(
            f"/result/{report_id}", follow_redirects=False
        )
    assert progress.status_code == 200
    assert progress.json()["finished"] is True
    assert progress.json()["recovered"] is True
    assert progress.json()["next_url"] == f"/result/{report_id}"
    assert published_result.status_code == 200
    assert report.company in published_result.text

    # COMPLETE 응답만 잃은 재시도는 새 delivery나 상태 사건을 만들지 않는다.
    assert _REAL_FINALIZE_REPORT_DELIVERY(job)
    with storage_db.connect() as conn:
        published_after = conn.execute(
            f"SELECT COUNT(*) FROM {dashboard_store.TABLE_REPORT_EVENTS} "
            "WHERE report_id=? AND action=?",
            (report_id, dashboard_store.REPORT_EVENT_PUBLISHED),
        ).fetchone()[0]
        state_after = dashboard_store.get_report_state(conn, report_id)
    assert published_after == published_before
    assert state_after == state


class _CommitFailingConnection:
    """최종 명시 commit만 실패시키고 나머지 sqlite 계약은 그대로 위임한다."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def commit(self) -> None:
        raise sqlite3.OperationalError("시험: delivery 최종 commit 실패")


class _CommitFailingStorage:
    def __init__(self, real_storage) -> None:
        self._real_storage = real_storage

    def __getattr__(self, name: str):
        return getattr(self._real_storage, name)

    @contextmanager
    def connect(self, *args, **kwargs):
        with self._real_storage.connect(*args, **kwargs) as conn:
            yield _CommitFailingConnection(conn)


def test_delivery최종commit실패는_publish도rollback해_pending차단을_보존한다(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _demo_report()
    report_id = uuid.uuid4().hex
    job = _stage_required_report(report, report_id=report_id)
    with storage_db.connect() as conn:
        # 실제 worker가 finalize 전에 남기는 원가 원장도 같은 모양으로 준비한다.
        job_runtime.cost_store.record_run_costs(
            conn,
            run_id=report_id,
            outcome=Outcome.REPORT,
            internal_ai_cost_krw=0.0,
        )

    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "commit-fail-artifacts"))
    monkeypatch.setattr(
        reports_router,
        "storage_db",
        _CommitFailingStorage(storage_db),
    )
    with pytest.raises(sqlite3.OperationalError, match="최종 commit 실패"):
        _REAL_FINALIZE_REPORT_DELIVERY(job)

    _assert_staged_and_blocked(report_id)
    with storage_db.connect() as conn:
        intent = delivery_store.load_delivery_intent(conn, report_id)
        assert intent is not None
        assert intent.state == delivery_store.DELIVERY_INTENT_FAILED
        assert intent.failure_code == "artifact_finalization_failed"
        assert delivery_store.load_delivery_by_public_id(conn, report_id) is None
        assert conn.execute(
            f"SELECT COUNT(*) FROM {dashboard_store.TABLE_REPORT_EVENTS} "
            "WHERE report_id=? AND action=?",
            (report_id, dashboard_store.REPORT_EVENT_PUBLISHED),
        ).fetchone()[0] == 0

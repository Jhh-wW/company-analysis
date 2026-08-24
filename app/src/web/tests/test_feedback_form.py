"""오류 신고 사용자 폼 — 렌더·접수·검증·CSRF·escape와 화면별 진입점 계약.

★ 여기서 지키는 것
  ① GET은 신고 유형 select와 개인정보 안내문을 보여주고, 닫힌 목록 밖 stage는
     400이 아니라 빈 값으로 접는다
  ② POST 성공·검증 실패·하루 상한 초과가 각각 올바른 화면·상태 코드를 돌려주고
     신고 원문은 저장 그대로, 화면 재표시는 escape된다
  ③ CSRF 없는 접수 요청은 거절되고 아무것도 저장되지 않는다
  ④ 신고자 식별자는 원문 이메일·열쇠가 아니라 해시 지문이다
  ⑤ 후보·검색없음·중단·결과 화면에 정확한 stage로 신고 진입점이 걸려 있다
  ⑥ 기존 회원 전용 /reports/{id}/errors(신고 즉시 차단) 라우트는 그대로 남는다
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget import logic as budget_logic
from src.features.budget import spend_store
from src.features.business_candidate import logic as candidate_logic
from src.features.business_candidate.logic import RawBusinessCandidate
from src.features.feedback_report import constants as feedback_constants
from src.features.feedback_report import logic as feedback_logic
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import (
    CompanyCard,
    CompanyLookupResult,
    Outcome,
    RunResult,
    UserInput,
)
from src.features.sharelink import allowlist
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import job_runtime, main, runtime


def _session_client(
    *, email: str = "admin@example.com", is_admin: bool = True
) -> tuple[TestClient, str]:
    """CSRF와 Origin이 맞는 로그인 손님 클라이언트.

    ★ 관리자 여부는 로그인 스냅샷이 아니라 매 요청 conftest의
      ``ENV_ADMIN_EMAILS`` 목록으로 다시 계산된다(auth.logic.get_session).
      기본 이메일을 그 목록에 있는 admin@example.com으로 둬야 PUBLIC 갈래로
      떨어져 후보 검색·조사 예산이 0원으로 막히는 일을 피한다.
    """
    client = TestClient(
        main.app,
        base_url="http://127.0.0.1:8000",
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    session = auth_logic.create_session(email, is_admin)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    return client, auth_logic.csrf_token_for_session(session.token)


# ══════════════════════════════════════════════════════════
# GET 폼 렌더
# ══════════════════════════════════════════════════════════


def test_get_렌더는_신고유형select와_안내문을_보여준다():
    client, _csrf = _session_client()

    response = client.get(
        "/feedback",
        params={"stage": feedback_constants.STAGE_COMPANY_SELECT, "company": "카카오"},
    )

    assert response.status_code == 200
    for category in feedback_constants.REPORT_CATEGORIES:
        assert f'<option value="{category}"' in response.text
    assert feedback_constants.FORM_GUIDE_NOTICE in response.text
    assert "카카오" in response.text
    assert "기업 선택" in response.text


def test_get는_닫힌목록밖_stage를_400아니라_빈값으로_받는다():
    client, _csrf = _session_client()

    response = client.get("/feedback", params={"stage": "없는단계"})

    assert response.status_code == 200
    assert 'name="stage" value=""' in response.text


def test_get는_company_쿼리를_escape해서_보여준다():
    client, _csrf = _session_client()
    hostile = '<script>alert(1)</script>'

    response = client.get(
        "/feedback",
        params={"stage": feedback_constants.STAGE_NO_SEARCH, "company": hostile},
    )

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


# ══════════════════════════════════════════════════════════
# POST 접수
# ══════════════════════════════════════════════════════════


def test_post_성공하면_접수확인화면을_보여주고_원문그대로_저장한다():
    client, csrf = _session_client()
    hostile_body = "매출액이 <b>실제보다</b> & 부풀려졌습니다"

    response = client.post(
        "/feedback",
        data={
            "stage": feedback_constants.STAGE_REPORT,
            "company": "삼성전자",
            "report": "job-123",
            "category": feedback_constants.CATEGORY_WRONG_INFO,
            "item_label": "매출액",
            "body": hostile_body,
            "ref_url": "https://example.com/disclosure",
            "csrf_token": csrf,
        },
    )

    assert response.status_code == 200
    assert "신고가 접수되었습니다" in response.text
    assert "관리자 검토 후 조치됩니다" in response.text
    assert 'href="/result/job-123"' in response.text

    with storage_db.connect() as conn:
        page = feedback_logic.list_reports(conn, stage=feedback_constants.STAGE_REPORT)
    assert page.total == 1
    stored = page.items[0]
    # 저장은 원문 그대로 — escape는 렌더 계층의 몫이다.
    assert stored.body == hostile_body
    assert stored.company_name == "삼성전자"
    # 신고자 식별자는 원문 이메일이 아니라 SHA-256 지문이다.
    assert len(stored.reporter_key) == 64
    assert all(ch in "0123456789abcdef" for ch in stored.reporter_key)
    assert "admin@example.com" not in stored.reporter_key


def test_post_검증실패는_메시지와_입력을_보존하며_escape한다():
    client, csrf = _session_client()
    hostile_item_label = '<script>alert(1)</script>'

    response = client.post(
        "/feedback",
        data={
            "stage": feedback_constants.STAGE_COMPANY_SELECT,
            "company": "카카오",
            "category": "이상한유형",
            "item_label": hostile_item_label,
            "body": "정상 본문",
            "csrf_token": csrf,
        },
    )

    assert response.status_code == 400
    assert "신고 유형이 올바르지 않습니다" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    with storage_db.connect() as conn:
        page = feedback_logic.list_reports(conn)
    assert page.total == 0


def test_post_하루상한_초과는_429와_상한메시지를_보여준다():
    client, csrf = _session_client()
    reporter_key = spend_store.bucket_id("user:admin@example.com")
    with storage_db.connect() as conn:
        for _ in range(feedback_constants.DAILY_CREATE_LIMIT_PER_REPORTER):
            feedback_logic.create_report(
                conn,
                stage=feedback_constants.STAGE_COMPANY_SELECT,
                category=feedback_constants.CATEGORY_OTHER,
                body="이미 접수된 신고",
                reporter_key=reporter_key,
            )

    response = client.post(
        "/feedback",
        data={
            "stage": feedback_constants.STAGE_COMPANY_SELECT,
            "category": feedback_constants.CATEGORY_OTHER,
            "body": "상한을 넘는 신고",
            "csrf_token": csrf,
        },
    )

    assert response.status_code == 429
    assert feedback_constants.DAILY_LIMIT_MESSAGE in response.text
    with storage_db.connect() as conn:
        page = feedback_logic.list_reports(conn, stage=feedback_constants.STAGE_COMPANY_SELECT)
    assert page.total == feedback_constants.DAILY_CREATE_LIMIT_PER_REPORTER


def test_post_csrf가_틀리면_거절되고_아무것도_저장되지_않는다():
    client, _csrf = _session_client()

    response = client.post(
        "/feedback",
        data={
            "stage": feedback_constants.STAGE_COMPANY_SELECT,
            "category": feedback_constants.CATEGORY_OTHER,
            "body": "본문",
            "csrf_token": "wrong-token",
        },
    )

    assert response.status_code == 403
    with storage_db.connect() as conn:
        page = feedback_logic.list_reports(conn)
    assert page.total == 0


# ══════════════════════════════════════════════════════════
# 화면별 진입점
# ══════════════════════════════════════════════════════════


class _CandidateEntryPointPipeline:
    """후보 목록 렌더만 보는 무과금 fixture — search_business_candidates만 쓴다."""

    business_candidate_provider_costs_money = False

    def __init__(self, candidates):
        self._candidates = list(candidates)

    def search_business_candidates(self, **_kwargs):
        return list(self._candidates)

    def find_company_metered(self, user_input):
        raise AssertionError("후보가 있으면 이름 재조회를 부르면 안 됩니다")

    def find_company_by_ref_metered(self, user_input, candidate_ref):
        raise AssertionError("후보 선택 전에는 DART 재조회가 없어야 합니다")


def test_후보화면에_기업선택_단계_신고링크가_있다(monkeypatch):
    monkeypatch.setattr(candidate_logic, "_RATE_HISTORY", budget_logic.RateHistory())
    monkeypatch.setattr(
        runtime,
        "_PIPELINE",
        _CandidateEntryPointPipeline(
            [
                RawBusinessCandidate(
                    candidate_name="(주)제이와이피엔터테인먼트",
                    provider_name="DART",
                    candidate_ref="00258689",
                    name_match_kind="exact_name",
                    name_similarity=1.0,
                )
            ]
        ),
    )
    client, csrf = _session_client()

    response = client.post(
        "/confirm",
        data={"company": "JYP", "region": "서울 강동구", "retry": "0", "csrf_token": csrf},
    )

    assert response.status_code == 200
    assert "원하는 기업이 없으신가요?" in response.text
    assert "오류 신고" in response.text
    assert "/feedback?stage=기업선택&amp;company=JYP" in response.text


class _RealNotFoundPipeline:
    """실제 조사 모드처럼 보이되, 후보도 이름도 못 찾는 최소 fixture."""

    business_candidate_provider_costs_money = False

    def search_business_candidates(self, **_kwargs):
        return []

    def find_company_metered(self, user_input):
        return CompanyLookupResult(card=None, model="fake-not-found")


def test_검색결과없음_화면에_검색없음_단계_신고링크가_있다(monkeypatch):
    monkeypatch.setattr(candidate_logic, "_RATE_HISTORY", budget_logic.RateHistory())
    monkeypatch.setattr(runtime, "_PIPELINE", _RealNotFoundPipeline())
    client, csrf = _session_client()

    response = client.post(
        "/confirm",
        data={"company": "없는회사", "region": "", "retry": "0", "csrf_token": csrf},
    )

    assert response.status_code == 200
    assert "찾지 못했습니다" in response.text
    assert "피드백 보내기" in response.text
    assert "/feedback?stage=검색없음&amp;company=" in response.text


def _stopped_job(job_id: str) -> job_runtime.Job:
    # 회사명은 순수 ASCII로 둔다 — urlencode 결과를 손으로 다시 계산하지 않아도
    # 링크 조립이 맞는지 문자열 그대로 검사할 수 있다.
    return job_runtime.Job(
        job_id=job_id,
        user_input=UserInput(company="Woorien", job="", region="서울 중구"),
        card=CompanyCard(
            legal_name="주식회사 우리엔", typed_name="Woorien",
            address="서울특별시 중구", ceo="", founded="",
        ),
        finished=True,
        result=RunResult(
            outcome=Outcome.GATE_STOPPED,
            message="양사 공식 원문을 같은 지표·기간·연결범위로 비교할 수 없습니다.",
        ),
    )


def test_중단화면에_생성중_단계_신고링크와_보고서식별자가_있다():
    job_id = uuid.uuid4().hex
    job_runtime._JOBS[job_id] = _stopped_job(job_id)
    try:
        with TestClient(main.app) as client:
            response = client.get(f"/result/{job_id}")
    finally:
        job_runtime._JOBS.pop(job_id, None)

    assert response.status_code == 200
    assert "이 판정이 틀렸다고 생각되면" in response.text
    assert (
        f"/feedback?stage=생성중&amp;company=Woorien&amp;report={job_id}"
        in response.text
    )


def test_결과화면은_기존신고폼_대신_새_신고링크를_보여준다():
    report_id = f"feedback-entry-{uuid.uuid4().hex}"
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "CORP-001", "", build_demo_report())
        assert allowlist.invite(
            conn, email="member@example.com", note="", now_iso="2026-08-24T10:00:00+09:00"
        )

    client, _csrf = _session_client(email="member@example.com", is_admin=False)
    response = client.get(f"/result/{report_id}")

    assert response.status_code == 200
    assert f'action="/reports/{report_id}/errors"' not in response.text
    assert "신고를 접수해도 이 보고서의 열람·다운로드·공유는 멈추지 않습니다" in response.text
    assert f"/feedback?stage=보고서&amp;company=" in response.text
    assert f"&amp;report={report_id}" in response.text


def test_기존_reports_errors_라우트는_삭제되지_않고_그대로_동작한다():
    """화면 진입만 새 시스템으로 바뀌었을 뿐, 회원 전용 차단형 신고 경로는 그대로다."""
    report_id = f"legacy-errors-{uuid.uuid4().hex}"
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "CORP-001", "", build_demo_report())
        assert allowlist.invite(
            conn, email="legacy-member@example.com", note="",
            now_iso="2026-08-24T10:00:00+09:00",
        )
    client, csrf = _session_client(email="legacy-member@example.com", is_admin=False)

    reported = client.post(
        f"/reports/{report_id}/errors",
        data={"area": "표", "reason": "원출처와 다릅니다", "csrf_token": csrf},
        follow_redirects=False,
    )
    blocked_result = client.get(f"/result/{report_id}", follow_redirects=False)

    assert reported.status_code == 303
    assert blocked_result.status_code == 409
    assert "오류 신고가 접수되어" in blocked_result.text


def test_narrow_beta_공유경로목록에_feedback이_포함된다():
    """시험공개(BETA_ADMIN_ONLY) 중에도 LINK 손님이 신고 화면에 들어갈 수 있어야 한다."""
    assert "/feedback" in auth_constants.BETA_SHARE_PATHS

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

from fastapi import Request
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.admin_dashboard import store as dashboard_store
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
from src.features.report_access import constants as report_access_constants
from src.features.report_access import store as report_access_store
from src.features.sharelink import allowlist
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import PUBLIC_BUCKET
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import job_runtime, main, runtime
from src.web.routers import feedback


def _session_client(
    *,
    email: str = "admin@example.com",
    is_admin: bool = True,
    subject: str | None = None,
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
    session = auth_logic.create_session(email, is_admin, subject=subject)
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


def test_get는_닫힌목록밖_stage를_400아니라_고를_수_있는_select로_보여준다():
    """옛 동작(hidden 빈 값)은 사용자가 고칠 수 없어 POST가 늘 400으로 막다른
    폼이었다(실측 결함). 이제는 닫힌 목록 4개짜리 select로 렌더해 고를 수 있다."""
    client, _csrf = _session_client()

    response = client.get("/feedback", params={"stage": "없는단계"})

    assert response.status_code == 200
    assert 'name="stage" value=""' not in response.text
    assert '<select id="feedback-stage" name="stage"' in response.text
    for value in feedback_constants.REPORT_STAGES:
        assert f'<option value="{value}">' in response.text


def test_get는_유효한_stage면_지금처럼_hidden으로_그대로_넣는다():
    """정상 진입(쿼리에 유효 stage)은 select가 아니라 종전처럼 hidden이다."""
    client, _csrf = _session_client()

    response = client.get(
        "/feedback", params={"stage": feedback_constants.STAGE_COMPANY_SELECT}
    )

    assert response.status_code == 200
    assert (
        f'name="stage" value="{feedback_constants.STAGE_COMPANY_SELECT}"'
        in response.text
    )
    assert "feedback-stage" not in response.text


def test_stage_없이_들어와도_select에서_골라_제출할_수_있다():
    """예전엔 stage가 hidden 빈 값이라 폼을 다 채워도 항상 400이었다(막다른
    폼, stage를 사용자가 고칠 방법이 없었다). 이제는 select에서 골라 제출하면
    정상 접수된다."""
    client, csrf = _session_client()

    form = client.get("/feedback", params={"stage": "없는단계"})
    assert '<select id="feedback-stage" name="stage"' in form.text

    response = client.post(
        "/feedback",
        data={
            "stage": feedback_constants.STAGE_NO_SEARCH,
            "category": feedback_constants.CATEGORY_OTHER,
            "body": "select로 직접 고른 단계로 제출한 신고",
            "csrf_token": csrf,
        },
    )

    assert response.status_code == 200
    assert "신고가 접수되었습니다" in response.text


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
    # 신고자 식별자는 «갈래 라벨:SHA-256 지문»이다 — 원문 이메일은 아니다.
    # ★ 갈래 라벨(admin/member/link/public)이 reporter_key 앞에 붙는다
    #   (2026-08-25 추가) — 관리자가 «회원 신고인지 링크 손님 신고인지»조차
    #   구분 못 하던 실측 결함을 고치면서 바뀐 계약. 지문 부분의 길이·문자
    #   구성·원문 비저장 보장은 그대로 지킨다.
    assert stored.reporter_key.startswith("admin:")
    digest = stored.reporter_key.split(":", 1)[1]
    assert len(digest) == 64
    assert all(ch in "0123456789abcdef" for ch in digest)
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
    # ★ 실제 POST가 계산할 reporter_key와 같은 모양(admin: 접두)으로 미리
    #   채워야 상한 판정이 같은 신고자로 묶인다 — 접두가 다르면 다른
    #   신고자로 보여 상한에 걸리지 않는다(2026-08-25 계약 변경 반영).
    reporter_key = f"admin:{spend_store.bucket_id('user:admin@example.com')}"
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


def test_PUBLIC이라도_초대안된_계정_하나가_상한을_채워도_다른_계정은_막히지_않는다():
    """옛 동작: PUBLIC 손님 전원이 고정 공용 버킷(``PUBLIC_BUCKET``) 하나를
    공유해, 초대 안 된 구글 계정 하나가 하루 상한(20건)을 채우면 같은 계층
    전체의 신고가 그날 막혔다(실측 결함, 계정 하나로 신고 채널 전체를 잠그는
    DoS). 이제는 세션(로그인)이 있는 손님이면 계정별로 다른 통장을 쓴다."""
    stage = feedback_constants.STAGE_COMPANY_SELECT
    # ★ 초대 안 된 손님은 track_of()가 여전히 PUBLIC을 돌려준다(초대 명단
    #   여부가 기준 — decide_track). 통장만 계정별로 MEMBER 모양을 빌려
    #   쓸 뿐 갈래 라벨은 "public:"이다(2026-08-25 계약 변경 반영).
    reporter_key_a = "public:" + spend_store.bucket_id(
        share_tracks.bucket_of(
            share_tracks.Track.MEMBER, email="stranger-a@example.com", share_key=""
        )
    )
    with storage_db.connect() as conn:
        for _ in range(feedback_constants.DAILY_CREATE_LIMIT_PER_REPORTER):
            feedback_logic.create_report(
                conn,
                stage=stage,
                category=feedback_constants.CATEGORY_OTHER,
                body="A 계정이 이미 채운 상한",
                reporter_key=reporter_key_a,
            )

    client_a, csrf_a = _session_client(email="stranger-a@example.com", is_admin=False)
    blocked = client_a.post(
        "/feedback",
        data={
            "stage": stage,
            "category": feedback_constants.CATEGORY_OTHER,
            "body": "A 계정의 21번째 신고",
            "csrf_token": csrf_a,
        },
    )
    assert blocked.status_code == 429  # A 계정 본인은 자기 상한에 걸린다

    client_b, csrf_b = _session_client(email="stranger-b@example.com", is_admin=False)
    allowed = client_b.post(
        "/feedback",
        data={
            "stage": stage,
            "category": feedback_constants.CATEGORY_OTHER,
            "body": "B 계정의 첫 신고 — A 계정 때문에 막히면 안 된다",
            "csrf_token": csrf_b,
        },
    )
    assert allowed.status_code == 200
    assert "신고가 접수되었습니다" in allowed.text


def test_reporter_key는_PUBLIC_세션이_있으면_계정마다_다르다():
    session_a = auth_logic.create_session("session-a@example.com", False)
    session_b = auth_logic.create_session("session-b@example.com", False)
    request_a = Request(
        {
            "type": "http",
            "headers": [
                (b"cookie", f"{auth_constants.SESSION_COOKIE_NAME}={session_a.token}".encode())
            ],
        }
    )
    request_b = Request(
        {
            "type": "http",
            "headers": [
                (b"cookie", f"{auth_constants.SESSION_COOKIE_NAME}={session_b.token}".encode())
            ],
        }
    )

    key_a = feedback._reporter_key(request_a)
    key_b = feedback._reporter_key(request_b)

    assert key_a != key_b
    # ★ 로그인은 했지만 초대 명단에 없는 세션이라 갈래는 "public:"이다.
    #   지문 부분(콜론 뒤)은 여전히 원문 이메일이 아니라 SHA-256이다
    #   (원문 식별자 저장 금지 원칙 유지, 2026-08-25 계약 변경 반영).
    assert key_a.startswith("public:") and key_b.startswith("public:")
    digest_a = key_a.split(":", 1)[1]
    assert len(digest_a) == 64 and all(ch in "0123456789abcdef" for ch in digest_a)
    assert "session-a@example.com" not in key_a


def test_reporter_key는_세션조차_없는_진짜_익명만_공용_버킷을_공유한다():
    """정말 아무 식별자도 없을 때만 고정 공용 버킷(PUBLIC_BUCKET)을 쓴다."""
    request = Request({"type": "http", "headers": []})

    key = feedback._reporter_key(request)

    # ★ 진짜 익명도 갈래 라벨("public:")은 붙는다 — 지문(통장)만 공용이다
    #   (2026-08-25 계약 변경 반영).
    assert key == f"public:{spend_store.bucket_id(PUBLIC_BUCKET)}"


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
    try:
        with TestClient(main.app) as client:
            # lifespan이 Job 메모리를 초기화한 뒤, 실제 시작 응답이 남겼을
            # grant와 in-memory Job을 같은 순서로 준비한다.
            job_runtime._JOBS[job_id] = _stopped_job(job_id)
            with storage_db.connect() as conn:
                grant = report_access_store.issue_and_bind(
                    conn, existing_token="", run_id=job_id
                )
            client.cookies.set(
                report_access_constants.PUBLIC_GRANT_COOKIE_NAME, grant.token
            )
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
    report_id = uuid.uuid4().hex
    subject = "google:feedback-entry-owner"
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "CORP-001", "", build_demo_report())
        assert allowlist.invite(
            conn, email="member@example.com", note="", now_iso="2026-08-24T10:00:00+09:00"
        )
        assert dashboard_store.reserve_member_run(
            conn,
            run_id=report_id,
            actor_email="member@example.com",
            day="2026-08-24",
            now_iso="2026-08-24T10:00:00+09:00",
        )
        assert report_access_store.bind_member_run(
            conn, run_id=report_id, identity_subject=subject
        )
        assert report_access_store.bind_report(
            conn,
            run_id=report_id,
            report_id=report_id,
            delivery_expires_at=None,
        )
        assert dashboard_store.settle_member_run(
            conn,
            run_id=report_id,
            succeeded=True,
            report_id=report_id,
            now_iso="2026-08-24T10:01:00+09:00",
        )

    client, _csrf = _session_client(
        email="member@example.com", is_admin=False, subject=subject
    )
    response = client.get(f"/result/{report_id}")

    assert response.status_code == 200
    assert f'action="/reports/{report_id}/errors"' not in response.text
    assert "신고를 접수해도 이 보고서의 열람·다운로드·공유는 멈추지 않습니다" in response.text
    assert f"/feedback?stage=보고서&amp;company=" in response.text
    assert f"&amp;report={report_id}" in response.text


def test_기존_reports_errors_라우트는_삭제되지_않고_그대로_동작한다():
    """화면 진입만 새 시스템으로 바뀌었을 뿐, 회원 전용 차단형 신고 경로는 그대로다."""
    report_id = uuid.uuid4().hex
    subject = "google:legacy-errors-owner"
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "CORP-001", "", build_demo_report())
        assert allowlist.invite(
            conn, email="legacy-member@example.com", note="",
            now_iso="2026-08-24T10:00:00+09:00",
        )
        assert dashboard_store.reserve_member_run(
            conn,
            run_id=report_id,
            actor_email="legacy-member@example.com",
            day="2026-08-24",
            now_iso="2026-08-24T10:00:00+09:00",
        )
        assert report_access_store.bind_member_run(
            conn, run_id=report_id, identity_subject=subject
        )
        assert report_access_store.bind_report(
            conn,
            run_id=report_id,
            report_id=report_id,
            delivery_expires_at=None,
        )
        assert dashboard_store.settle_member_run(
            conn,
            run_id=report_id,
            succeeded=True,
            report_id=report_id,
            now_iso="2026-08-24T10:01:00+09:00",
        )
    client, csrf = _session_client(
        email="legacy-member@example.com", is_admin=False, subject=subject
    )

    reported = client.post(
        f"/reports/{report_id}/errors",
        data={"area": "표", "reason": "원출처와 다릅니다", "csrf_token": csrf},
        follow_redirects=False,
    )
    blocked_result = client.get(f"/result/{report_id}", follow_redirects=False)

    assert reported.status_code == 303
    assert blocked_result.status_code == 409
    assert "보고서를 열 수 없습니다" in blocked_result.text


def test_narrow_beta_공유경로목록에_feedback이_포함된다():
    """시험공개(BETA_ADMIN_ONLY) 중에도 LINK 손님이 신고 화면에 들어갈 수 있어야 한다."""
    assert "/feedback" in auth_constants.BETA_SHARE_PATHS

"""링크 손님도 오류 신고를 낼 수 있어야 하지만, 완전 익명은 여전히 막혀야
한다는 사용자 확정 사항을 못 박는 시험.

★ 무엇이 문제였나
  1. 결과 화면의 «오류 신고» 카드가 MEMBER 전용 조건(``member_feedback_allowed``
     = 초대 명단에 있는 로그인 회원)에 묶여 있어, 세션 없이 LINK로만 들어온
     손님에게는 아예 안 보였다.
  2. ``POST /feedback``이 타는 ``require_analysis_action_csrf``는 원래
     회사 확인·거절·조사 시작(confirm·run) 화면을 위해 설계됐다 — 그 화면들은
     DemoPipeline 손님이면 완전 익명이어도 same-origin만 맞으면 통과시킨다
     (무료 미리보기라 안전해서다). 오류 신고 라우터가 이 함수를 그대로
     재사용하면서, «완전 익명은 대상이 아니다»라는 이 기능만의 요구를
     같이 물려받지 못했다 — 세션도 LINK 쿠키도 없는 손님이 DemoPipeline
     환경(웹 시험 기본값)에서 신고를 낼 수 있었다(실측 결함).

★ 무엇을 지키는가
  - 로그인 회원(MEMBER)과 LINK 손님은 신고를 낼 수 있다.
  - 완전 익명(세션도 유효한 LINK 쿠키도 없음)은 여전히 403으로 막힌다.
  - 신고 기록에 «회원인지 링크 손님인지»가 구분되어 남는다(원문 이메일·
    열쇠는 여전히 저장하지 않는다 — track 라벨만 덧붙인다).
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.feedback_report import constants as feedback_constants
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import main, runtime
from src.web.routers import feedback as feedback_router

_카카오열쇠 = "a1b2c3d4e5f60718a1b2c3d4e5f60718"


@pytest.fixture
def client():
    """★ 반드시 `with` — 아니면 뒤에서 도는 조사가 취소된다 (P-92 교훈)."""
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app, base_url="https://testserver") as client:
        yield client


def _저장된_보고서로_링크를_발급한다(key: str) -> str:
    report_id = "hash-drift-불필요-link-guest-report"
    report = build_demo_report()
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "demo-corp", report.job, report)
        share_store.insert_new(
            conn, key=key, company=report.company, job="마케팅",
            report_id=report_id, now_iso="2026-08-16T10:00:00",
        )
    return report_id


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]*)"', html)
    assert match is not None, "오류 신고 폼에 csrf_token이 없습니다"
    return match.group(1)


def _신고_폼값(*, report_id: str, company: str, **overrides) -> dict:
    base = {
        "stage": feedback_constants.STAGE_REPORT,
        "company": company,
        "report": report_id,
        "category": feedback_constants.CATEGORY_OTHER,
        "item_label": "표에 숫자가 이상합니다",
        "body": "3장 표의 매출 숫자가 본문 설명과 다릅니다.",
    }
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════
# ① 링크 손님에게 «오류 신고»가 보인다 (설문은 여전히 회원 전용)
# ══════════════════════════════════════════════════════════


def test_LINK_손님도_결과화면에서_오류신고_버튼이_보인다(client: TestClient):
    report_id = _저장된_보고서로_링크를_발급한다(_카카오열쇠)

    opened = client.get(f"/k/{_카카오열쇠}", follow_redirects=False)
    result = client.get(opened.headers["location"])

    assert result.status_code == 200
    assert 'href="/feedback?stage=보고서&amp;company=' in result.text
    assert "오류 신고" in result.text
    # ★ MEMBER 전용 설문 입력은 그대로 안 보여야 한다 — 링크 손님은 초대
    #   명단에 없으므로 설문 대상이 아니다.
    assert 'id="member-survey-form"' not in result.text


def test_완전_익명은_결과화면에서_오류신고_버튼이_안_보인다(client: TestClient):
    """★ 화면에 숨기는 것은 배려일 뿐 진짜 차단이 아니다 — 진짜 차단은
    POST /feedback이 매 요청마다 CSRF+신원으로 다시 한다(아래 시험).
    이 시험은 «불필요한 유도조차 안 한다»만 확인한다.
    """
    report_id = "anon-visible-report"
    report = build_demo_report()
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "demo-corp", report.job, report)

    result = client.get(f"/result/{report_id}")

    assert result.status_code == 200
    assert "오류 신고" not in result.text


# ══════════════════════════════════════════════════════════
# ② 링크 손님의 POST /feedback이 통과한다
# ══════════════════════════════════════════════════════════


def test_LINK_손님의_오류신고_POST가_통과하고_링크로_구분되어_남는다(
    client: TestClient,
):
    report_id = _저장된_보고서로_링크를_발급한다(_카카오열쇠)
    client.get(f"/k/{_카카오열쇠}")  # LINK 쿠키를 받는다

    form_page = client.get(
        f"/feedback?stage=보고서&company=demo-corp&report={report_id}"
    )
    token = _csrf_token(form_page.text)

    response = client.post(
        "/feedback",
        data=_신고_폼값(report_id=report_id, company="demo-corp", csrf_token=token),
    )

    assert response.status_code == 200
    assert "접수" in response.text
    with storage_db.connect() as conn:
        row = conn.execute(
            "SELECT reporter_key FROM feedback_reports WHERE report_ref = ?",
            (report_id,),
        ).fetchone()
    assert row is not None
    # ★ 원문 열쇠는 안 남지만, «링크 손님이 냈다»는 갈래는 남아야 한다.
    assert row[0].startswith("link:")
    assert _카카오열쇠 not in row[0]


def test_MEMBER의_오류신고_POST도_통과하고_회원으로_구분되어_남는다(
    client: TestClient,
):
    report_id = "member-feedback-report"
    report = build_demo_report()
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "demo-corp", report.job, report)
        share_allow.invite(
            conn, email="friend@example.com", note="시험",
            now_iso="2026-08-16T10:00:00",
        )
    session = auth_logic.create_session("friend@example.com", False)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)

    result = client.get(f"/result/{report_id}")
    assert 'id="member-survey-form"' in result.text
    assert "오류 신고" in result.text

    form_page = client.get(
        f"/feedback?stage=보고서&company=demo-corp&report={report_id}"
    )
    token = _csrf_token(form_page.text)
    response = client.post(
        "/feedback",
        data=_신고_폼값(report_id=report_id, company="demo-corp", csrf_token=token),
    )

    assert response.status_code == 200
    assert "접수" in response.text
    with storage_db.connect() as conn:
        row = conn.execute(
            "SELECT reporter_key FROM feedback_reports WHERE report_ref = ?",
            (report_id,),
        ).fetchone()
    assert row is not None
    assert row[0].startswith("member:")
    assert "friend@example.com" not in row[0]


# ══════════════════════════════════════════════════════════
# ③ ★★ 완전 익명은 여전히 막힌다 (사용자 확정 — 보안 구멍 금지)
# ══════════════════════════════════════════════════════════


def test_완전_익명의_오류신고_POST는_403으로_막힌다(client: TestClient):
    """★ 세션도 유효한 LINK 쿠키도 없는 손님은 GET/feedback으로 화면은
    보되(폼 자체는 누구나 열람 가능), 실제 접수(POST)는 통과하면 안 된다.
    """
    form_page = client.get("/feedback?stage=보고서&company=demo-corp&report=x")
    token = _csrf_token(form_page.text)

    response = client.post(
        "/feedback",
        data=_신고_폼값(report_id="x", company="demo-corp", csrf_token=token),
    )

    assert response.status_code == 403
    with storage_db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM feedback_reports WHERE report_ref = 'x'"
        ).fetchone()[0]
    assert count == 0


def test_만료된_LINK_쿠키를_들고와도_오류신고_POST는_막힌다(client: TestClient):
    """★ 쿠키에 그럴듯한 32자리 열쇠가 있어도, DB에 없거나 만료됐으면
    ``_request_csrf_secret``이 빈 문자열을 돌려줘 익명과 같은 취급을 받는다
    (request_helpers._raw_share_key가 이미 하는 일 — 여기서는 그 계약이
    오류 신고 경로에도 그대로 적용되는지만 확인한다).
    """
    client.cookies.set(KEY_COOKIE_NAME, _카카오열쇠)  # DB에 발급된 적 없는 열쇠

    form_page = client.get("/feedback?stage=보고서&company=demo-corp&report=x")
    token = _csrf_token(form_page.text)

    response = client.post(
        "/feedback",
        data=_신고_폼값(report_id="x", company="demo-corp", csrf_token=token),
    )

    assert response.status_code == 403


def test_reporter_key_생성_직접_확인(monkeypatch):
    """★ 위 통합 시험이 실제로 무엇을 지키는지 단위 수준에서도 못 박는다 —
    track 라벨이 reporter_key 앞에 콜론으로 붙는 정확한 모양을 고정한다.
    """
    from fastapi import Request
    from src.features.sharelink import tracks as share_tracks

    def _member_track(_request):
        return share_tracks.Track.MEMBER, "member-bucket", 1000.0

    monkeypatch.setattr(feedback_router.request_helpers, "_track_of", _member_track)
    request = Request({"type": "http", "headers": [], "method": "GET"})

    key = feedback_router._reporter_key(request)

    assert key.startswith("member:")
    assert key.count(":") == 1
    digest = key.split(":", 1)[1]
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)

"""관리자 신고 관리 화면 — 접근 제어·목록·필터·상태 전이·escape 계약.

★ 여기서 지키는 것
  ① 관리자가 아니면 목록·상세·상태 변경 어디에도 못 들어온다
  ② 상태별 집계는 0건도 숨기지 않고, 필터·페이지네이션이 실제로 동작한다
  ③ 허용된 전이(미처리→검토중→처리완료|반려)만 실행되고 위반 메시지는 그대로 보인다
  ④ 신고 원문은 저장 그대로, 화면에서는 escape되어 script가 글자로만 보인다
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.feedback_report import constants as feedback_constants
from src.features.feedback_report import logic as feedback_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.storage import db as storage_db
from src.web import main, runtime


@pytest.fixture
def client():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        yield client


def _login(client: TestClient, *, email: str = "admin@example.com", is_admin: bool = True) -> str:
    session = auth_logic.create_session(email, is_admin)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    return auth_logic.csrf_token_for_session(session.token)


def _seed(**overrides) -> str:
    values = dict(
        stage=feedback_constants.STAGE_REPORT,
        category=feedback_constants.CATEGORY_WRONG_INFO,
        body="매출 수치가 실제 공시와 다릅니다",
        company_name="삼성전자",
        report_ref="",
        item_label="재무 지표",
        ref_url="",
        reporter_key="tester",
        now_iso="2026-08-20T10:00:00+09:00",
    )
    values.update(overrides)
    with storage_db.connect() as conn:
        created = feedback_logic.create_report(conn, **values)
    return created.report_id


def _status_of(report_id: str) -> str:
    with storage_db.connect() as conn:
        found = feedback_logic.get_report(conn, report_id)
    assert found is not None
    return found.status


def test_feedback_admin_pages_require_an_admin_session(client: TestClient):
    report_id = _seed()

    anonymous_list = client.get("/admin/feedback-reports", follow_redirects=False)
    anonymous_detail = client.get(
        f"/admin/feedback-reports/{report_id}", follow_redirects=False
    )
    anonymous_change = client.post(
        f"/admin/feedback-reports/{report_id}/status",
        data={"to_status": feedback_constants.STATUS_REVIEWING},
        follow_redirects=False,
    )
    _login(client, email="member@example.com", is_admin=False)
    member_list = client.get("/admin/feedback-reports", follow_redirects=False)
    member_change = client.post(
        f"/admin/feedback-reports/{report_id}/status",
        data={"to_status": feedback_constants.STATUS_REVIEWING},
        follow_redirects=False,
    )

    for blocked in (
        anonymous_list, anonymous_detail, anonymous_change, member_list, member_change
    ):
        assert blocked.status_code in (302, 303, 307)
    # 비관리자의 시도로 상태가 바뀌지 않았다.
    assert _status_of(report_id) == feedback_constants.STATUS_OPEN


def test_list_renders_zero_inclusive_counts_rows_and_nav_entry(client: TestClient):
    open_id = _seed(company_name="삼성전자")
    reviewing_id = _seed(
        company_name="카카오",
        category=feedback_constants.CATEGORY_SOURCE_ERROR,
        now_iso="2026-08-21T09:00:00+09:00",
    )
    with storage_db.connect() as conn:
        feedback_logic.change_status(
            conn,
            report_id=reviewing_id,
            to_status=feedback_constants.STATUS_REVIEWING,
            actor="a" * 16,
            now_iso="2026-08-21T10:00:00+09:00",
        )
    _login(client)

    response = client.get("/admin/feedback-reports")
    today = client.get("/admin")

    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert "전체 신고 2" in response.text
    assert "미처리 1" in response.text and "검토중 1" in response.text
    # 0건 상태도 숨기지 않는다.
    assert "처리완료 0" in response.text and "반려 0" in response.text
    assert open_id in response.text and reviewing_id in response.text
    assert "상세 보기" in response.text
    assert f'href="/admin/feedback-reports/{open_id}"' in response.text
    # 관리자에게 신고 관리 진입점이 있다.
    # ★ 기대값 이전 — 여섯 묶음 정보 구조에서 신고 관리는
    #   「보고서」 묶음에 든다. 그래서 메뉴에는 자기 이름이 아니라
    #   「보고서」가 현재 위치로 표시되고, 진입점은 오늘 화면의 바로가기에 있다.
    #   「들어갈 길이 있다」는 보장 자체는 그대로 지킨다.
    menu = response.text.split('<nav class="frame-menu"', 1)[1].split("</nav>", 1)[0]
    assert ">보고서</a>" in menu
    assert menu.count('aria-current="page"') == 1
    assert 'href="/admin/issues" aria-current="page"' in menu
    assert today.status_code == 200
    assert 'href="/admin/feedback-reports"' in today.text
    assert "신고 관리" in today.text


def test_list_filters_narrow_rows_and_bad_filter_shows_message(client: TestClient):
    open_id = _seed(company_name="삼성전자")
    reviewing_id = _seed(company_name="카카오", now_iso="2026-08-21T09:00:00+09:00")
    with storage_db.connect() as conn:
        feedback_logic.change_status(
            conn,
            report_id=reviewing_id,
            to_status=feedback_constants.STATUS_REVIEWING,
            actor="a" * 16,
            now_iso="2026-08-21T10:00:00+09:00",
        )
    _login(client)

    by_status = client.get(
        "/admin/feedback-reports",
        params={"status": feedback_constants.STATUS_REVIEWING},
    )
    by_keyword = client.get("/admin/feedback-reports", params={"keyword": "카카오"})
    by_period = client.get(
        "/admin/feedback-reports",
        params={"date_from": "2026-08-21", "date_to": "2026-08-21"},
    )
    bad_filter = client.get(
        "/admin/feedback-reports", params={"status": "이상한값"}
    )

    assert by_status.status_code == 200
    assert reviewing_id in by_status.text and open_id not in by_status.text
    assert reviewing_id in by_keyword.text and open_id not in by_keyword.text
    assert reviewing_id in by_period.text and open_id not in by_period.text
    # 잘못된 필터는 계약 메시지를 그대로 보여주고 400으로 답한다.
    assert bad_filter.status_code == 400
    assert "처리 상태 필터가 올바르지 않습니다" in bad_filter.text


def test_detail_shows_fields_and_only_allowed_transition_buttons(client: TestClient):
    report_id = _seed(
        report_ref="RESULT-0001",
        item_label="재무 지표",
        ref_url="https://example.com/news",
    )
    _login(client)

    response = client.get(f"/admin/feedback-reports/{report_id}")
    missing = client.get("/admin/feedback-reports/RPT-99999999-999")

    assert response.status_code == 200
    for expected in (
        report_id, "삼성전자", "RESULT-0001", "재무 지표",
        "https://example.com/news", "매출 수치가 실제 공시와 다릅니다",
        feedback_constants.STAGE_REPORT, feedback_constants.CATEGORY_WRONG_INFO,
    ):
        assert expected in response.text
    assert 'href="https://example.com/news"' not in response.text
    assert "신고자가 쓴 주소는 자동으로 열지 않습니다" in response.text
    # 미처리 상태에서는 검토중 버튼만 노출된다.
    assert f'value="{feedback_constants.STATUS_REVIEWING}"' in response.text
    assert f'value="{feedback_constants.STATUS_RESOLVED}"' not in response.text
    assert f'value="{feedback_constants.STATUS_REJECTED}"' not in response.text
    assert missing.status_code == 404


def test_status_transitions_follow_the_allowed_path_only(client: TestClient):
    report_id = _seed()
    csrf = _login(client)

    skip_review = client.post(
        f"/admin/feedback-reports/{report_id}/status",
        data={
            "to_status": feedback_constants.STATUS_RESOLVED,
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    start_review = client.post(
        f"/admin/feedback-reports/{report_id}/status",
        data={
            "to_status": feedback_constants.STATUS_REVIEWING,
            "admin_note": "원출처 확인을 시작합니다",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    detail = client.get(f"/admin/feedback-reports/{report_id}")
    resolve = client.post(
        f"/admin/feedback-reports/{report_id}/status",
        data={
            "to_status": feedback_constants.STATUS_RESOLVED,
            "admin_note": "수치를 수정했습니다",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    closed_detail = client.get(f"/admin/feedback-reports/{report_id}")

    # 검토를 건너뛴 종결은 위반 메시지를 그대로 화면에 보여준다.
    assert skip_review.status_code == 400
    assert "바꿀 수 없습니다" in skip_review.text
    assert start_review.status_code == 303
    assert start_review.headers["location"] == f"/admin/feedback-reports/{report_id}"
    assert feedback_constants.STATUS_REVIEWING in detail.text
    assert "원출처 확인을 시작합니다" in detail.text
    assert resolve.status_code == 303
    assert _status_of(report_id) == feedback_constants.STATUS_RESOLVED
    # 종결 상태에서는 상태 변경 버튼이 사라진다.
    assert 'name="to_status"' not in closed_detail.text
    assert "종결 상태입니다" in closed_detail.text


def test_status_change_requires_a_valid_csrf_token(client: TestClient):
    report_id = _seed()
    _login(client)

    response = client.post(
        f"/admin/feedback-reports/{report_id}/status",
        data={
            "to_status": feedback_constants.STATUS_REVIEWING,
            "csrf_token": "wrong-token",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert _status_of(report_id) == feedback_constants.STATUS_OPEN


def test_report_body_and_ref_url_are_escaped_on_render(client: TestClient):
    hostile_body = '<script>alert("x")</script> 본문'
    hostile_url = 'https://example.com/x?q="<x>'
    report_id = _seed(body=hostile_body, ref_url=hostile_url)
    # 저장은 원문 그대로다 — escape는 렌더 계층의 몫이다.
    with storage_db.connect() as conn:
        stored = feedback_logic.get_report(conn, report_id)
    assert stored is not None and stored.body == hostile_body
    assert stored.ref_url == hostile_url
    _login(client)

    listing = client.get("/admin/feedback-reports")
    detail = client.get(f"/admin/feedback-reports/{report_id}")

    for response in (listing, detail):
        assert response.status_code == 200
        assert "<script>alert(" not in response.text
        assert "&lt;script&gt;" in response.text
    assert '"<x>' not in detail.text
    assert "&lt;x&gt;" in detail.text


def test_pagination_splits_pages_and_keeps_order(client: TestClient):
    page_size = feedback_constants.DEFAULT_PAGE_SIZE
    seeded = [
        _seed(
            reporter_key=f"tester-{index}",
            now_iso=f"2026-08-20T10:{index:02d}:00+09:00",
        )
        for index in range(page_size + 1)
    ]
    _login(client)

    first = client.get("/admin/feedback-reports")
    second = client.get("/admin/feedback-reports", params={"page": "2"})

    assert first.status_code == 200
    # 최신 접수가 1페이지에 먼저 나오고, 가장 오래된 1건은 2페이지로 밀린다.
    assert seeded[-1] in first.text and seeded[0] not in first.text
    assert 'href="/admin/feedback-reports?page=2"' in first.text
    assert second.status_code == 200
    assert seeded[0] in second.text and seeded[-1] not in second.text
    assert "이전" in second.text
    assert f"총 {page_size + 1}건" in second.text

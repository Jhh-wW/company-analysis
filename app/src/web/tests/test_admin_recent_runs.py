"""「오늘 상태」 화면이 링크와 무관하게 최근 실행 진단을 찾아 주는지 본다.

★ 왜 필요한가(실측 2026-09-07) — 실행 진단 화면으로 가는 링크가 «초대 링크
  상세»에만 있었다. 어느 링크로 만들었는지 모르거나 링크 밖에서 만든 보고서는
  진단을 찾을 길이 없어, 관리자가 방금 만든 보고서의 진단을 못 열었다.

★ 진단 기록은 실행마다 링크와 무관하게 쌓인다. 그래서 목록의 정본은 링크
  실행 이력이 아니라 진단 표다.
"""

from __future__ import annotations

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.observability import run_steps_store
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.web import main, runtime
from src.web.routers import dashboard


#: 링크 실행 이력이 있는 실행과 없는 실행. 실행 번호 모양은 진단 화면 주소 규칙과 같다.
_LINKED_RUN_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
_ORPHAN_RUN_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
_REPORT_ID = "11" * 16
_LINK_KEY = "2" * 32
_COMPANY = "가나다전자"


@pytest.fixture
def client():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    return client


@pytest.fixture
def member(client: TestClient) -> TestClient:
    session = auth_logic.create_session("member@example.com", False)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    return client


def _seed_two_runs() -> None:
    """링크 이력이 있는 실행 1건과 링크 밖 실행 1건을 심는다."""

    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=_LINK_KEY,
            company=_COMPANY,
            job="데이터 분석",
            now_iso="2026-09-06T09:00:00+09:00",
        )
        assert share_store.start_run(
            conn,
            key=_LINK_KEY,
            run_id=_LINKED_RUN_ID,
            started_at="2026-09-06T10:00:00+09:00",
            input_company="가나다",
            confirmed_company=_COMPANY,
            company_id="corp-ganada",
        )
        assert share_store.finish_run(
            conn,
            run_id=_LINKED_RUN_ID,
            status=share_store.RUN_STATUS_AWAITING_RELEASE,
            finished_at="2026-09-06T10:05:00+09:00",
            report_id=_REPORT_ID,
            internal_ai_cost_krw=120.0,
        )
        run_steps_store.record_once(
            conn,
            run_id=_LINKED_RUN_ID,
            steps=[{"step": "5b_뉴스_수집", "검색": 12}],
            recorded_at="2026-09-06T10:05:00+09:00",
        )
        run_steps_store.record_once(
            conn,
            run_id=_ORPHAN_RUN_ID,
            steps=[{"step": "7_이름후보", "후보": 9}],
            recorded_at="2026-09-07T11:30:00+09:00",
        )


def _recent_card(body: str) -> str:
    """「최근 실행 진단」 카드만 잘라 낸다. 화면 다른 곳의 같은 낱말과 섞이지 않게."""

    표지 = '<p class="frame-eyebrow">최근 실행 진단</p>'
    assert 표지 in body, "「오늘 상태」 화면에 최근 실행 진단 카드가 없다"
    시작 = body.index(표지)
    return body[시작 : body.index("</article>", 시작)]


def test_오늘_화면이_링크_안팎의_최근_실행을_모두_보여준다(admin: TestClient):
    _seed_two_runs()

    응답 = admin.get("/admin")

    assert 응답.status_code == 200
    카드 = _recent_card(응답.text)
    # 링크 이력이 있는 실행 — 회사와 상태를 링크 상세와 같은 말로 보여 준다.
    assert _COMPANY in 카드
    assert "자동출고 검사 대기" in 카드
    assert f'href="/admin/runs/{_LINKED_RUN_ID}/diagnostics"' in 카드
    # 링크 밖에서 만든 실행 — 이게 이 화면을 만든 이유다.
    assert "링크 밖 실행" in 카드
    assert f'href="/admin/runs/{_ORPHAN_RUN_ID}/diagnostics"' in 카드
    # 최신 기록이 위에 온다.
    assert 카드.index(_ORPHAN_RUN_ID) < 카드.index(_LINKED_RUN_ID)
    assert "2026-09-07 11:30 (한국시간)" in 카드
    assert _ORPHAN_RUN_ID[:12] in 카드


def test_목록의_링크를_그대로_누르면_그_실행의_진단이_열린다(admin: TestClient):
    """★ 배선 시험 — 주소를 시험 안에서 다시 만들면 화면의 오타를 못 잡는다."""

    _seed_two_runs()
    카드 = _recent_card(admin.get("/admin").text)
    주소 = re.findall(r'href="(/admin/runs/[^"]+/diagnostics)"', 카드)
    assert len(주소) == 2, 주소

    진단 = admin.get([항목 for 항목 in 주소 if _ORPHAN_RUN_ID in 항목][0])

    assert 진단.status_code == 200
    assert _ORPHAN_RUN_ID in 진단.text
    assert "7_이름후보" in 진단.text


def test_조각_새로고침도_같은_목록을_그린다(admin: TestClient):
    """새로고침에서 목록이 사라지면 관리자는 「없어졌다」고 읽는다."""

    _seed_two_runs()

    조각 = admin.get("/admin/refresh/today")

    assert 조각.status_code == 200
    카드 = _recent_card(조각.text)
    assert _COMPANY in 카드
    assert "링크 밖 실행" in 카드
    assert f'href="/admin/runs/{_ORPHAN_RUN_ID}/diagnostics"' in 카드


def test_기록이_없으면_없다고_말한다(admin: TestClient):
    """반대 경우 시험 — 늘 목록이 있다고 적으면 위 시험이 초록이 되지 않게 한다."""

    카드 = _recent_card(admin.get("/admin").text)

    assert "아직 기록된 실행이 없습니다" in 카드
    assert "/diagnostics" not in 카드


def test_관리자가_아닌_회원에게는_보이지_않는다(member: TestClient):
    _seed_two_runs()

    응답 = member.get("/admin", follow_redirects=False)

    assert 응답.status_code == 303
    assert 응답.headers["location"] == "/auth/not-admin"
    assert _COMPANY not in 응답.text
    assert _ORPHAN_RUN_ID not in 응답.text


def test_진단_저장소를_못_읽으면_확인_불가라고_말하고_화면은_열린다(
    admin: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """★ 한 조각의 실패가 「오늘 상태」 전체를 닫으면 안 된다.
    ⚠️ 조용히 빈 목록으로 두면 「실행이 없다」로 읽힌다."""

    _seed_two_runs()

    def 못_읽는다(*_args, **_kwargs):
        raise sqlite3.OperationalError("run steps unavailable")

    monkeypatch.setattr(run_steps_store, "list_recent", 못_읽는다)

    응답 = admin.get("/admin")

    assert 응답.status_code == 200
    카드 = _recent_card(응답.text)
    assert "확인 불가" in 카드
    assert "아직 기록된 실행이 없습니다" not in 카드
    assert "/diagnostics" not in 카드
    # 저장소 속사정은 화면으로 새지 않는다.
    assert "run steps unavailable" not in 응답.text
    assert "OperationalError" not in 응답.text


def test_목록은_실행에_묶인_보고서_번호도_함께_들고_있다():
    """화면 표에는 없지만 맥락에는 있다 — 보고서로 되짚을 때 쓴다."""

    _seed_two_runs()

    맥락 = dashboard._dashboard_context(_가짜요청())

    assert 맥락["dashboard_recent_runs_available"] is True
    묶음 = {항목["run_id"]: 항목 for 항목 in 맥락["dashboard_recent_runs"]}
    assert 묶음[_LINKED_RUN_ID]["report_id"] == _REPORT_ID
    assert 묶음[_ORPHAN_RUN_ID]["report_id"] == ""
    assert 묶음[_ORPHAN_RUN_ID]["company"] == ""


def _가짜요청():
    """`_dashboard_context`가 쓰는 최소한의 Request 흉내."""
    from starlette.requests import Request  # noqa: PLC0415

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
        }
    )

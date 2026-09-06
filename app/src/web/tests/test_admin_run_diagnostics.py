"""실행 진단 기록을 관리자만, 그리고 «전부» 볼 수 있는지 본다.

★ 이 시험이 지키는 것 — 진단 원본에는 수집 주소·문서 ID가 섞일 수 있다.
  화면이 열리는 것보다 「누가 여는가」가 먼저다.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.observability import run_steps_store
from src.features.pipeline.demo import DemoPipeline
from src.features.storage import db as storage_db
from src.web import main, runtime


_RUN_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
_STEPS = [
    {"step": "5b_뉴스_수집", "검색": 12, "선별": 5, "제외": {"BODY_FETCH_FAILED": 2}},
    {"step": "7_이름후보", "후보": 9, "조각": 4, "종류별": {"product": 6}},
    {"step": "6_수집_홈페이지", "주소": "https://corp.example.com/about"},
]


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


def _seed(run_id: str = _RUN_ID, steps: list[dict] | None = None) -> None:
    with storage_db.connect() as conn:
        run_steps_store.record_once(
            conn,
            run_id=run_id,
            steps=_STEPS if steps is None else steps,
            recorded_at="2026-09-06T10:00:00+09:00",
        )


def test_로그인하지_않은_사람은_진단을_못_본다(client: TestClient):
    _seed()

    response = client.get(
        f"/admin/runs/{_RUN_ID}/diagnostics", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/not-admin"


def test_관리자가_아닌_회원도_진단을_못_본다(member: TestClient):
    _seed()

    response = member.get(
        f"/admin/runs/{_RUN_ID}/diagnostics", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/not-admin"
    assert "5b_뉴스_수집" not in response.text


def test_관리자는_저장된_단계기록_전체를_본다(admin: TestClient):
    _seed()

    response = admin.get(f"/admin/runs/{_RUN_ID}/diagnostics")

    assert response.status_code == 200
    body = response.text
    # 요약 로그가 고르지 «않는» 단계까지 원본 그대로 보여야 원인 추적이 된다.
    for name in ("5b_뉴스_수집", "7_이름후보", "6_수집_홈페이지"):
        assert name in body
    assert "BODY_FETCH_FAILED" in body
    assert "단계 3개" in body


def test_기록이_없는_실행은_없다고_말한다(admin: TestClient):
    response = admin.get("/admin/runs/00000000000000000000000000000000/diagnostics")

    assert response.status_code == 200
    assert "저장된 진단 기록이 없습니다" in response.text


def test_모양이_틀린_실행번호는_찾지_않고_거절한다(admin: TestClient):
    response = admin.get("/admin/runs/..%2Fetc%2Fpasswd/diagnostics")

    assert response.status_code == 404


def test_저장한_기록과_화면의_JSON이_같다(admin: TestClient):
    _seed()

    body = admin.get(f"/admin/runs/{_RUN_ID}/diagnostics").text

    start = body.index("<pre")
    start = body.index(">", start) + 1
    shown = body[start : body.index("</pre>", start)]
    # 화면은 HTML 이스케이프를 거치므로 따옴표만 되돌려 비교한다.
    restored = shown.replace("&#34;", '"').replace("&quot;", '"').replace("&amp;", "&")
    assert json.loads(restored) == _STEPS

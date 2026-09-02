"""관리자 화면이 회원별 한도를 «그 친구의 값»으로 보여준다 (G-S5, D-G4 (a)).

★ 이 시험이 막는 것 — 저장은 회원별로 되는데 화면만 「/ 3건」으로 박혀 있어
  관리자가 「7건으로 올렸는데 왜 3건이라고 쓰여 있지」를 겪는 것. 화면이 거짓말을
  하면 관리자는 한도를 또 올린다.

★ 친구 본인 화면에는 다른 친구 정보도, 내부 열 이름도 나오면 안 된다.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import allowlist as share_allow
from src.features.storage import db as storage_db
from src.web import main, runtime

_친구 = "friend@example.com"
_다른친구 = "other@example.com"
_시각 = "2026-09-02T10:00:00+09:00"


def _주체(email: str) -> str:
    digest = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:24]
    return f"google:test-{digest}"


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


def _초대한다(email: str, *, 이름: str = "") -> None:
    with storage_db.connect() as conn:
        assert share_allow.invite(
            conn, email=email, display_name=이름, note="", now_iso=_시각
        )


def _한도를_정한다(email: str, *, 건수: int | None, 금액: float | None) -> None:
    with storage_db.connect() as conn:
        assert share_allow.set_limits(
            conn, email=email, daily_success_limit=건수, daily_budget_krw=금액,
            reason="면접 준비", now_iso=_시각,
        )


# ══════════════════════════════════════════════════════════
# ① 친구 목록 — 「/ 3건」 하드코딩이 그 친구의 값으로 바뀐다
# ══════════════════════════════════════════════════════════


def test_화면의_3건_하드코딩이_회원값으로_바뀐다(admin: TestClient):
    _초대한다(_친구, 이름="김민지")
    _초대한다(_다른친구, 이름="이서준")
    _한도를_정한다(_친구, 건수=7, 금액=4500.0)

    화면 = admin.get("/admin/members")

    assert 화면.status_code == 200
    # 한도를 올린 친구는 7건으로, 안 정한 친구는 기존 3건으로 보인다.
    assert "오늘 성공 0 / 7건" in 화면.text
    assert "오늘 성공 0 / 3건" in 화면.text
    # 안내 문장도 「전원 3건」이라고 단정하지 않는다.
    assert "성공한 보고서 3건을 이용합니다" not in 화면.text


def test_화면에_회원별_하루_비용_한도도_같이_보인다(admin: TestClient):
    _초대한다(_친구, 이름="김민지")
    _초대한다(_다른친구, 이름="이서준")
    _한도를_정한다(_친구, 건수=7, 금액=4500.0)

    화면 = admin.get("/admin/members")

    assert "4,500원" in 화면.text
    assert "3,000원" in 화면.text
    assert "면접 준비" in 화면.text  # 왜 올렸는지도 같이 남아 보인다


def test_회원_화면에_그_친구의_한도_변경_폼이_있다(admin: TestClient):
    _초대한다(_친구, 이름="김민지")

    화면 = admin.get("/admin/members")

    assert f'action="/admin/members/{_친구}/limit"' in 화면.text
    assert 'name="daily_success_limit"' in 화면.text
    assert 'name="daily_budget_krw"' in 화면.text
    assert 'name="reason"' in 화면.text
    assert 'name="csrf_token"' in 화면.text


def test_초대한_친구가_없으면_한도_폼도_안_보인다(admin: TestClient):
    화면 = admin.get("/admin/members")

    assert 화면.status_code == 200
    assert "/limit" not in 화면.text


# ══════════════════════════════════════════════════════════
# ② 오늘 화면 — 합계가 회원별 값의 합이다
# ══════════════════════════════════════════════════════════


def test_오늘_합계는_회원별_값의_합이다(admin: TestClient):
    """★ 「인원 × 3,000원」이면 한 명만 올려도 합계가 틀린다."""
    _초대한다(_친구, 이름="김민지")
    _초대한다(_다른친구, 이름="이서준")
    _한도를_정한다(_친구, 건수=7, 금액=4500.0)

    화면 = admin.get("/admin")

    assert 화면.status_code == 200
    # 4,500 + 3,000 = 7,500원 · 7 + 3 = 10건
    assert "7,500원" in 화면.text
    assert "10건" in 화면.text
    assert "친구 2명" in 화면.text


def test_한도를_아무도_안_바꿨으면_합계는_인원_곱하기_기본값이다(admin: TestClient):
    """★ 음성 대조 — 회원별 값을 넣었다고 기존 숫자가 흔들리면 안 된다."""
    _초대한다(_친구, 이름="김민지")
    _초대한다(_다른친구, 이름="이서준")

    화면 = admin.get("/admin")

    assert "6,000원" in 화면.text
    assert "6건" in 화면.text


def test_친구가_없으면_오늘_합계는_0이다(admin: TestClient):
    화면 = admin.get("/admin")

    assert 화면.status_code == 200
    assert "친구 0명" in 화면.text


def test_오늘_화면은_기본값을_전원_규칙이_아니라_기본값이라고_말한다(
    admin: TestClient,
):
    """★ 「전원 3건」이라고 단정하면 한 명을 올린 뒤 화면이 거짓말이 된다.

    기존 회귀시험(`test_admin_access.py`)이 이 문장을 지켜 왔다. 문장을 지우는
    대신 「따로 정하지 않은 친구는」이라는 조건을 앞에 붙여 사실로 만든다.
    """
    _초대한다(_친구, 이름="김민지")
    _한도를_정한다(_친구, 건수=7, 금액=4500.0)

    화면 = admin.get("/admin")

    assert (
        "따로 정하지 않은 친구는 하루 3,000원 입장 기준 + 성공 3건입니다"
        in 화면.text
    )
    assert "친구 1명 하루 합계 4,500원 · 성공 7건" in 화면.text


# ══════════════════════════════════════════════════════════
# ②-2 접근 화면 — 「인원 × 기본값」이 아니라 회원별 값의 합
# ══════════════════════════════════════════════════════════


def test_접근_화면_합계는_회원별_한도의_합이다():
    """★ 한 명만 올려도 「N명 × 3,000원」은 실제 노출보다 작아진다.

    비용 노출 규모를 읽으려고 보는 숫자라, 작게 보이면 관리자가 링크·친구를
    더 늘려도 된다고 판단한다.
    """
    _초대한다(_친구, 이름="김민지")
    _초대한다(_다른친구, 이름="이서준")
    _한도를_정한다(_친구, 건수=3, 금액=6000.0)

    with TestClient(main.app) as client:
        runtime._PIPELINE = DemoPipeline()
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        화면 = client.get("/admin/access")

    assert 화면.status_code == 200
    # 6,000 + 3,000 = 9,000원. 여기에 관리자 5,000원을 더해 14,000원.
    assert "초대한 친구 2명 합계 9,000원" in 화면.text
    assert "14,000원" in 화면.text
    # 한 명이라도 다르면 「N명 × 3,000원」은 거짓이므로 쓰지 않는다.
    assert "명 × 3,000원" not in 화면.text
    assert "11,000원" not in 화면.text


def test_아무도_한도를_안_바꿨으면_접근_화면_문구가_그대로다():
    """★ 음성 대조 — 기존 문장을 지우지 않고 조건부로만 만든다.

    `test_admin_access.py`의 기존 회귀시험이 「친구 N명 × 3,000원」과
    「성공 보고서 3건」을 지켜 왔고, 그 파일은 이 작업의 소유 밖이다.
    """
    _초대한다(_친구, 이름="김민지")

    with TestClient(main.app) as client:
        runtime._PIPELINE = DemoPipeline()
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        화면 = client.get("/admin/access")

    assert "초대한 친구 1명 × 3,000원" in 화면.text
    assert "성공 보고서 3건" in 화면.text
    assert "비용 입장 상한 3,000원" in 화면.text
    # 링크 0개 + 친구 3,000 + 관리자 5,000 = 8,000원
    assert "8,000원" in 화면.text
    assert "합계" in 화면.text  # 「차단 기준 합계」 문장 자체는 그대로 있다


# ══════════════════════════════════════════════════════════
# ③ 친구 본인 화면 — 남의 정보도, 내부 열 이름도 안 보인다
# ══════════════════════════════════════════════════════════


def test_친구_본인_화면에는_다른_회원_정보와_내부_용어가_안_보인다(
    client: TestClient,
):
    _초대한다(_친구, 이름="김민지")
    _초대한다(_다른친구, 이름="이서준")
    _한도를_정한다(_친구, 건수=7, 금액=4500.0)
    session = auth_logic.create_session(_친구, False, subject=_주체(_친구))
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)

    본인화면 = client.get("/")

    assert 본인화면.status_code == 200
    assert _다른친구 not in 본인화면.text
    assert "이서준" not in 본인화면.text
    for 내부용어 in (
        "daily_success_limit",
        "daily_budget_krw",
        "limit_reason",
        "allowed_users",
        "admin.member.limit",
    ):
        assert 내부용어 not in 본인화면.text


def test_친구는_관리자_친구_화면을_열_수_없다(client: TestClient):
    _초대한다(_친구, 이름="김민지")
    _초대한다(_다른친구, 이름="이서준")
    session = auth_logic.create_session(_친구, False, subject=_주체(_친구))
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)

    막힌화면 = client.get("/admin/members", follow_redirects=False)

    assert 막힌화면.status_code != 200
    assert _다른친구 not in 막힌화면.text

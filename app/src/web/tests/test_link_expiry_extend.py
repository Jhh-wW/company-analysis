"""링크 만료일을 «관리 화면에서 실제로» 미룰 수 있는지 못 박는다 (티켓 G-S4).

★ 이 시험이 지키는 것 네 가지
  ① 판단이 맞아도 **라우트에 안 걸려 있으면 소용없다** — 폼과 POST를 실제로 탄다
  ② 미룬 사실이 **이력행과 감사행 둘 다**에 남는다. 하나만 남으면 「무엇이
     바뀌었나」와 「누가 승인했나」 중 하나를 영영 모른다
  ③ **이유가 없으면 안 바뀐다** — 기록에 이유가 빠지면 흔적이지 기록이 아니다
  ④ 옛 운영 계약(`render-admin-real-no-forwarded-v1`)에서는 아예 없는 길이다

⚠️ 날짜 수는 **리터럴**이다. 생산 상수를 import해 같은 상수와 비교하면 값이
  몰래 바뀌어도 시험이 그대로 통과한다.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.observability import admin_audit_store
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.web import deployment_mode, main, runtime

_열쇠 = "abcdef0123456789abcdef0123456789"
_발급 = "2026-09-02T09:00:00+09:00"

#: 한 번에 미룰 수 있는 최대 날 수(리터럴). 상수 자체는 sharelink 시험이 본다.
_최대연장일 = 90
#: 위험 동작의 이유는 20자 이상이어야 한다(G-S9 · 설계 05장 §4). 길이 규칙
#: 자체는 `test_admin_dangerous_actions.py`가 보고, 여기서는 규칙에 맞는 글로
#: «미루기가 실제로 되는지»만 본다.
_연장이유 = "서류 발표가 2주 밀려 링크를 더 열어 두기로 했습니다"


@pytest.fixture
def client():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as test_client:
        yield test_client



# ══════════════════════════════════════════════════════════
# 위험 동작 확인 단계(G-S9) — 시험이 실제 확인 화면을 거치게 한다
# ══════════════════════════════════════════════════════════


def _확인화면_경로(url: str, data: dict) -> str:
    """이 POST 앞에 서 있는 확인 화면의 주소. 확인이 필요 없으면 빈 글자."""

    if url == "/admin/links/revoke":
        return f"/admin/links/{data.get('key', '')}/revoke"
    if url == "/admin/revoke":
        return f"/admin/members/{data.get('email', '')}/remove"
    if url.endswith("/extend") or url.endswith("/limit"):
        return url
    return ""


def _확인표(client: TestClient, url: str, data: dict) -> str:
    """확인 화면을 «실제로 열어» 1회용 표를 받아 온다.

    ★ 표를 지어내지 않는다 — 화면이 안 주면 빈 글자이고, 그 요청은 서버가
      그대로 거절한다. 확인 단계 자체가 지켜지는지는 전용 시험
      `test_admin_dangerous_actions.py`가 본다.
    """

    경로 = _확인화면_경로(url, data)
    if not 경로:
        return ""
    화면 = client.get(경로)
    찾음 = re.search(r'name="confirm_token" value="([0-9a-f]+)"', 화면.text)
    return 찾음.group(1) if 찾음 else ""


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    csrf = auth_logic.csrf_token_for_session(session.token)
    original_post = client.post

    def post_with_csrf(url, *args, **kwargs):
        data = dict(kwargs.pop("data", {}) or {})
        data.setdefault("csrf_token", csrf)
        # 위험 동작은 확인 화면을 거쳐야 실행된다(G-S9). 브라우저가 하는 것과
        # 같은 두 단계를 여기서 그대로 밟는다.
        if "confirm_token" not in data:
            표 = _확인표(client, url, data)
            if 표:
                data["confirm_token"] = 표
        return original_post(url, *args, data=data, **kwargs)

    client.post = post_with_csrf
    return client


def _링크발급(*, key: str = _열쇠, company: str = "하이브") -> str:
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn, key=key, company=company, job="인사", now_iso=_발급
        )
    return share_store.key_hash_of(key)


def _만료일(key: str = _열쇠) -> str:
    with storage_db.connect() as conn:
        link = share_store.load(conn, key)
    assert link is not None
    return link.expires_at


def _이력(key_hash: str) -> list[share_store.ShareLinkAdjustment]:
    with storage_db.connect() as conn:
        return share_store.list_link_adjustments(conn, key_hash=key_hash)


def _감사행(action: str) -> list[tuple]:
    with storage_db.connect() as conn:
        return conn.execute(
            f"SELECT action, outcome, reason_code FROM "
            f"{admin_audit_store.TABLE_ADMIN_AUDIT_EVENTS} WHERE action = ?",
            (action,),
        ).fetchall()


def _내일부터(days: int) -> str:
    return (clock.today_kst() + dt.timedelta(days=days)).isoformat()


def _현재만료를(days: int, *, key_hash: str) -> str:
    """지금 만료일을 «오늘 + days»로 못 박는다.

    ★ 경계를 세려면 시작점이 고정돼야 한다. 발급 기본값(90일)에 기대면
      상한 경계와 현재 만료일이 겹쳐 무엇을 재는 시험인지 흐려진다.
    """

    value = (clock.today_kst() + dt.timedelta(days=days)).isoformat()
    with storage_db.connect() as conn:
        assert share_store.set_expires_at(
            conn, key_hash=key_hash, expires_at=value
        )
        conn.commit()
    return value


# ══════════════════════════════════════════════════════════
# ① 연장은 이력행 + 감사행을 함께 남긴다
# ══════════════════════════════════════════════════════════


def test_연장은_확인화면과_이력행과_감사행을_함께_남긴다(admin: TestClient):
    """★ 확인 «화면»(2단계 확인)은 G-S9 몫이라 여기서는 안 본다.

    여기서 보는 것은 그 앞 단계다 — 이유를 반드시 받고, 바뀐 값이 이력표에,
    승인 사실이 감사 원장에 «같은 요청으로» 함께 남는가.
    """

    key_hash = _링크발급()
    이전 = _현재만료를(10, key_hash=key_hash)
    화면 = admin.get(f"/admin/link/{key_hash}/extend")
    assert 화면.status_code == 200
    assert f'action="/admin/link/{key_hash}/extend"' in 화면.text
    assert "미루는 이유" in 화면.text

    새날 = _내일부터(40)
    응답 = admin.post(
        f"/admin/link/{key_hash}/extend",
        data={"expires_on": 새날, "reason": _연장이유},
        follow_redirects=False,
    )

    assert 응답.status_code == 303
    assert 응답.headers["location"] == f"/admin/link/{key_hash}/extend"
    assert _만료일() == 새날

    이력 = _이력(key_hash)
    assert len(이력) == 1
    assert 이력[0].kind == "expires"
    assert 이력[0].new_value == 새날
    assert 이력[0].old_value == 이전
    assert 이력[0].reason == _연장이유
    assert 이력[0].actor_id

    성공행 = [행 for 행 in _감사행("admin.link.extend") if 행[1] == "success"]
    assert [행[2] for 행 in 성공행] == ["expiry_extended"]

    다시 = admin.get(f"/admin/link/{key_hash}/extend")
    assert _연장이유 in 다시.text
    assert 새날 in 다시.text


def test_이유가_없으면_만료일도_이력도_안_바뀐다(admin: TestClient):
    key_hash = _링크발급()
    이전 = _현재만료를(10, key_hash=key_hash)

    응답 = admin.post(
        f"/admin/link/{key_hash}/extend",
        data={"expires_on": _내일부터(40), "reason": "   "},
        follow_redirects=False,
    )

    assert 응답.status_code == 400
    assert "이유를 적어주세요" in 응답.text
    assert _만료일() == 이전
    assert _이력(key_hash) == []


# ══════════════════════════════════════════════════════════
# ② 경계 — 과거 불가, 최대 90일, 줄이기 불가
# ══════════════════════════════════════════════════════════


#: 경계 시험의 기준이 되는 «지금 만료일» — 오늘 + 이 날 수.
_기준만료 = 10


@pytest.mark.parametrize(
    ("days", "조각"),
    [
        (0, "오늘보다 뒤"),
        (-1, "오늘보다 뒤"),
        (_기준만료, "뒤의 날짜만"),
        (_기준만료 + _최대연장일 + 1, f"{_최대연장일}일까지만"),
    ],
)
def test_연장은_최대_90일까지만_과거로는_불가(admin: TestClient, days, 조각):
    key_hash = _링크발급()
    이전 = _현재만료를(_기준만료, key_hash=key_hash)

    응답 = admin.post(
        f"/admin/link/{key_hash}/extend",
        data={"expires_on": _내일부터(days), "reason": "일정 변경"},
        follow_redirects=False,
    )

    assert 응답.status_code == 400
    assert 조각 in 응답.text
    assert _만료일() == 이전
    assert _이력(key_hash) == []


def test_정확히_90일째는_받는다(admin: TestClient):
    """★ 경계 바로 안쪽도 함께 본다 — 상한이 하루 어긋나면 못 잡는다.

    상한은 «오늘»이 아니라 «지금 만료일»에서 잰다. 그래야 방금 발급한 링크도
    한 번은 미룰 수 있다.
    """

    key_hash = _링크발급()
    _현재만료를(_기준만료, key_hash=key_hash)
    새날 = _내일부터(_기준만료 + _최대연장일)

    응답 = admin.post(
        f"/admin/link/{key_hash}/extend",
        data={"expires_on": 새날, "reason": "채용 일정이 한 달 뒤로 연기되었습니다"},
        follow_redirects=False,
    )

    assert 응답.status_code == 303
    assert _만료일() == 새날


def test_방금_발급한_링크도_한_번은_미룰_수_있다(admin: TestClient):
    """★ 상한을 오늘에서 재면 새 링크의 상한과 현재 만료일이 같은 날이 되어
    「미루기」가 처음부터 불가능해진다. 그 회귀를 여기서 막는다.
    """

    key_hash = _링크발급()
    이전 = _만료일()
    새날 = (dt.date.fromisoformat(이전) + dt.timedelta(days=1)).isoformat()

    응답 = admin.post(
        f"/admin/link/{key_hash}/extend",
        data={"expires_on": 새날, "reason": "마감 직전이라 하루만 더 열어 둡니다"},
        follow_redirects=False,
    )

    assert 응답.status_code == 303
    assert _만료일() == 새날


def test_지금_만료일보다_앞당기는_값은_거절한다(admin: TestClient):
    """줄이는 것은 «철회»의 일이다. 한 폼에 두면 실수로 조기에 닫는다."""

    key_hash = _링크발급()
    이전 = _현재만료를(_기준만료, key_hash=key_hash)

    응답 = admin.post(
        f"/admin/link/{key_hash}/extend",
        data={"expires_on": _내일부터(_기준만료 - 5), "reason": "짧게 바꾸기"},
        follow_redirects=False,
    )

    assert 응답.status_code == 400
    assert "뒤의 날짜만" in 응답.text
    assert _만료일() == 이전


def test_모양이_아닌_날짜는_거절한다(admin: TestClient):
    key_hash = _링크발급()
    이전 = _현재만료를(_기준만료, key_hash=key_hash)

    응답 = admin.post(
        f"/admin/link/{key_hash}/extend",
        data={"expires_on": "20261231", "reason": "일정 변경"},
        follow_redirects=False,
    )

    assert 응답.status_code == 400
    assert _만료일() == 이전


# ══════════════════════════════════════════════════════════
# ③ 권한·계약
# ══════════════════════════════════════════════════════════


def test_옛_계약에서는_연장이_404다(admin: TestClient, monkeypatch):
    key_hash = _링크발급()
    이전 = _현재만료를(_기준만료, key_hash=key_hash)
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        "render-admin-real-no-forwarded-v1",
    )
    # 이 계약은 forwarded 헤더를 안 믿고 고정 출처 하나만 신뢰한다.
    # Host를 안 맞추면 라우트 앞의 공용 검사가 400으로 먼저 끊어 404를 못 본다.
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, "https://demo.example")

    응답 = admin.post(
        f"/admin/link/{key_hash}/extend",
        data={"expires_on": _내일부터(40), "reason": "일정 변경"},
        headers={"Host": "demo.example"},
        follow_redirects=False,
    )

    assert 응답.status_code == 404
    assert 응답.text == "찾을 수 없습니다."
    assert _만료일() == 이전
    assert _이력(key_hash) == []


def test_관리자가_아니면_연장하지_못한다(client: TestClient):
    """★ 로그인 벽은 303으로 «다른 곳»에 보낸다. 상태 코드만 보면 성공과
    구별되지 않으므로 도착지와 저장된 값을 함께 본다.
    """

    key_hash = _링크발급()
    이전 = _현재만료를(_기준만료, key_hash=key_hash)

    응답 = client.post(
        f"/admin/link/{key_hash}/extend",
        data={"expires_on": _내일부터(40), "reason": "일정 변경"},
        follow_redirects=False,
    )

    assert 응답.headers.get("location") != f"/admin/link/{key_hash}/extend"
    assert _만료일() == 이전
    assert _이력(key_hash) == []


def test_CSRF_없는_연장은_거절된다(client: TestClient):
    key_hash = _링크발급()
    이전 = _현재만료를(_기준만료, key_hash=key_hash)
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)

    응답 = client.post(
        f"/admin/link/{key_hash}/extend",
        data={"expires_on": _내일부터(40), "reason": "일정 변경"},
        follow_redirects=False,
    )

    assert 응답.headers.get("location") != f"/admin/link/{key_hash}/extend"
    assert _만료일() == 이전
    assert _이력(key_hash) == []


# ══════════════════════════════════════════════════════════
# ④ 미룬 만료일이 실제 문에도 걸린다
# ══════════════════════════════════════════════════════════


def test_미룬_만료일은_링크_입구에서도_통한다(admin: TestClient, monkeypatch):
    """★ 화면 숫자만 바뀌고 문이 그대로면 「연장」은 거짓말이다."""

    key_hash = _링크발급()
    # 이미 닫힌 링크로 만든다 — 어제까지였던 것으로.
    with storage_db.connect() as conn:
        assert share_store.set_expires_at(
            conn,
            key_hash=key_hash,
            expires_at=(clock.today_kst() - dt.timedelta(days=1)).isoformat(),
        )
        conn.commit()
    닫힘 = admin.get(f"/k/{_열쇠}", follow_redirects=False)
    assert 닫힘.headers["location"] == "/?share_status=expired"

    admin.post(
        f"/admin/link/{key_hash}/extend",
        data={"expires_on": _내일부터(30), "reason": "면접 일정이 잡혀 링크를 다시 엽니다"},
        follow_redirects=False,
    )

    열림 = admin.get(f"/k/{_열쇠}", follow_redirects=False)
    assert 열림.headers["location"] == "/"

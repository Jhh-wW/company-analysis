"""로그인 벽이 켜진 배포에서 닫힌 초대 링크 손님이 구글 로그인으로 튕기지 않는다.

QR로 받은 초대 링크는 회수할 수 없어 기본 수명이 지나면 반드시 닫힌다. 그때
`/k/<열쇠>`가 첫 화면으로 되돌려보내면, 첫 화면 자체가 로그인 뒤에 있는 배포에서는
손님이 구글 계정 선택 화면에 도착한다. 로그인해도 들어올 수 없는 계정이라 손님은
이유도 모른 채 막히고, 연락처 안내에도 도달하지 못한다.

★ 지키는 것
  ① 사용 중단·기간 만료·발급된 적 없음 세 갈래 모두 되돌려보내지 않고(Location 없음)
     연락 안내가 있는 화면을 직접 그린다.
  ② 조사가 도는 중에 링크가 닫히면 진행 화면이 읽는 진행 상태 응답이 JSON 안내다.
     로그인 화면 HTML을 받으면 진행 화면은 해석에 실패해 「네트워크 문제」로 보인다.
  ③ 그 손님의 첫 화면·신고 화면도 로그인으로 튕기지 않는다.
  ④ 다른 조사의 번호는 이 링크 손님에게 안내를 주지 않는다 — 있고 없음을 알리지 않는다.

★ 실제 AI·네트워크는 한 번도 부르지 않는다. 저장소와 화면 응답만 본다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as storage_db
from src.web import main, runtime
from src.web.tests._visible_text import visible_text

_LINK = "a1b2c3d4e5f60718a1b2c3d4e5f60718"
_MISSING_LINK = "0f1e2d3c4b5a69780f1e2d3c4b5a6978"
_JOB_ID = "b7c8d9e0f1a23456b7c8d9e0f1a23456"
_OTHER_JOB_ID = "c8d9e0f1a23456b7c8d9e0f1a23456b7"

#: 손님 화면에 절대 나오면 안 되는 내부 용어. 소문자로 낮춰 비교한다.
_금지어 = ("link", "member", "capability", "revoke", "share_key", "철회")

#: 이 화면의 존재 이유. 손님이 여기서 갈 수 있는 유일한 길이다.
_연락안내 = "포트폴리오에 적힌 연락처"

#: 첫 화면으로 가는 버튼의 글자. 로그인 벽 너머라 손님에게는 열리지 않는다.
_첫화면_버튼 = "첫 화면으로 돌아가기"


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    """운영과 같은 관리자 전용 로그인 벽을 켠 손님 브라우저."""

    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as test_client:
        yield test_client


def _링크를_만든다(key: str = _LINK) -> None:
    with storage_db.connect() as conn:
        assert (
            share_store.insert_new(
                conn,
                key=key,
                company="가나다전자",
                job="영업",
                now_iso="2026-08-17T10:00:00",
            )
            is True
        )


def _링크를_닫는다(key: str = _LINK) -> None:
    with storage_db.connect() as conn:
        assert share_store.delete(conn, key) is True


def _링크_기간을_넘긴다(key: str = _LINK) -> None:
    with storage_db.connect() as conn:
        assert (
            share_store.set_expires_at(
                conn,
                key_hash=share_store.key_hash_of(key),
                expires_at=clock.today_kst().isoformat(),
            )
            is True
        )


def _조사를_시작한_것으로_기록한다(key: str = _LINK, run_id: str = _JOB_ID) -> None:
    """이 링크로 시작한 조사 한 건을 이력에 남긴다(유료 호출 없음)."""

    with storage_db.connect() as conn:
        assert (
            share_store.start_run(
                conn,
                key=key,
                run_id=run_id,
                started_at=clock.iso_now_kst(),
                input_company="가나다전자",
                confirmed_company="가나다전자 주식회사",
                company_id="corp-001",
            )
            is True
        )


def _안내_화면인지_본다(응답) -> None:
    """되돌려보내지 않고, 연락 안내가 보이고, 내부 용어가 없는 화면인지."""

    assert 응답.status_code in {403, 410}, 응답.text
    assert "location" not in 응답.headers, "닫힌 링크 손님을 다른 주소로 보냈다"
    assert "no-store" in 응답.headers.get("cache-control", "")
    본문 = visible_text(응답.text)
    assert _연락안내 in 본문, 본문
    for 용어 in _금지어:
        assert 용어 not in 본문.lower(), 용어


def test_사용이_중단된_링크는_로그인_대신_안내_화면을_연다(client: TestClient) -> None:
    _링크를_만든다()
    _링크를_닫는다()

    열림 = client.get(f"/k/{_LINK}", follow_redirects=False)

    _안내_화면인지_본다(열림)


def test_기간이_지난_링크도_같은_안내_화면을_연다(client: TestClient) -> None:
    _링크를_만든다()
    _링크_기간을_넘긴다()

    열림 = client.get(f"/k/{_LINK}", follow_redirects=False)

    _안내_화면인지_본다(열림)


def test_발급된_적_없는_링크도_안내_화면을_연다(client: TestClient) -> None:
    열림 = client.get(f"/k/{_MISSING_LINK}", follow_redirects=False)

    _안내_화면인지_본다(열림)


def test_링크가_닫힌_손님의_첫_화면도_안내_화면이다(client: TestClient) -> None:
    _링크를_만든다()
    client.cookies.set(KEY_COOKIE_NAME, _LINK)
    _링크를_닫는다()

    첫화면 = client.get("/", follow_redirects=False)

    _안내_화면인지_본다(첫화면)


def test_안내_화면을_새로고침해도_로그인으로_가지_않는다(client: TestClient) -> None:
    """두 번째 요청도 같은 안내다 — 안내를 한 번만 보여 주면 막다른 길은 그대로다."""

    _링크를_만든다()
    client.cookies.set(KEY_COOKIE_NAME, _LINK)
    _링크를_닫는다()

    client.get("/", follow_redirects=False)
    새로고침 = client.get("/", follow_redirects=False)

    _안내_화면인지_본다(새로고침)


def test_링크가_닫힌_손님의_신고_화면도_안내_화면이다(client: TestClient) -> None:
    _링크를_만든다()
    client.cookies.set(KEY_COOKIE_NAME, _LINK)
    _링크를_닫는다()

    신고 = client.get("/feedback", follow_redirects=False)

    _안내_화면인지_본다(신고)


def test_진행중_조사의_링크가_닫히면_진행_상태가_안내_JSON이다(
    client: TestClient,
) -> None:
    _링크를_만든다()
    _조사를_시작한_것으로_기록한다()
    client.cookies.set(KEY_COOKIE_NAME, _LINK)
    _링크를_닫는다()

    진행 = client.get(f"/api/progress/{_JOB_ID}", follow_redirects=False)

    assert 진행.status_code == 410, 진행.text
    assert "location" not in 진행.headers
    assert 진행.headers["content-type"].startswith("application/json")
    본문 = 진행.json()
    # 진행 화면 JS(`progress.html`의 poll)가 읽는 이름 그대로여야 안내가 보인다.
    assert _연락안내 in 본문["error"], 본문
    assert 본문["retryable"] is False
    assert 본문["retry_url"] == "/"
    for 용어 in _금지어:
        assert 용어 not in 본문["error"].lower(), 용어


def test_다른_조사_번호에는_링크_상태를_알려주지_않는다(client: TestClient) -> None:
    _링크를_만든다()
    _조사를_시작한_것으로_기록한다()
    client.cookies.set(KEY_COOKIE_NAME, _LINK)
    _링크를_닫는다()

    남의조사 = client.get(f"/api/progress/{_OTHER_JOB_ID}", follow_redirects=False)

    assert 남의조사.status_code == 303
    assert 남의조사.headers["location"] == "/auth/login"


def test_안내_화면에_로그인으로_튕기는_첫_화면_버튼이_없다(client: TestClient) -> None:
    """★ 이 손님에게 첫 화면은 로그인 뒤에 있다 — 눌러도 들어가지 못한다.

    열리지 않는 버튼을 그리면 손님은 안내를 읽는 대신 그 버튼을 눌러 구글 계정
    선택 화면으로 가고, 이 화면이 없애려던 막다른 길을 그대로 다시 만난다.
    """

    _링크를_만든다()
    _링크를_닫는다()

    열림 = client.get(f"/k/{_LINK}", follow_redirects=False)

    _안내_화면인지_본다(열림)
    assert '<a class="btn" href="/">' not in 열림.text, 열림.text
    assert _첫화면_버튼 not in visible_text(열림.text)


def test_첫_화면에_들어갈_수_있는_관리자에게는_그_버튼을_남긴다(
    client: TestClient,
) -> None:
    """★ 반대 경우 — 실제로 열리는 사람에게서까지 길을 뺏지 않았는지 본다."""

    _링크를_만든다()
    _링크를_닫는다()
    관리자 = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, 관리자.token)

    열림 = client.get(f"/k/{_LINK}", follow_redirects=False)

    assert 열림.status_code == 410, 열림.text
    assert '<a class="btn" href="/">' in 열림.text
    assert _첫화면_버튼 in visible_text(열림.text)

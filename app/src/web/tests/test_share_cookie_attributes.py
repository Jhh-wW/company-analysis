"""초대 링크 쿠키에 붙는 «보호 속성»을 글자 그대로 못 박는다.

★ 왜 필요한가
  초대 링크를 열면 브라우저에 열쇠 쿠키가 하나 심긴다. 이 쿠키는 로그인 없이
  보고서를 여는 통행증이라, 아래 넷 중 하나만 빠져도 남이 가로채거나 다른
  사이트가 대신 쓰게 된다.

    - ``HttpOnly``  — 페이지 안 스크립트가 쿠키 값을 읽지 못하게 막는다
    - ``SameSite=lax`` — 다른 사이트가 건 요청에 쿠키가 따라가지 않게 막는다
    - ``Secure``    — 암호화되지 않은 연결로는 보내지 않는다
    - ``Max-Age``   — 초대 링크 수명과 같은 값. 영구 통행증이 되지 않게 한다

  현재 값은 넷 다 맞다. 그런데 이 넷을 «단정하는 시험이 하나도 없었다» —
  설정 한 줄이 지워지거나 틀의 기본값이 바뀌어 조용히 빠져도 모두 초록이었다.
  값을 고치는 게 아니라, 지금 맞는 값이 소리 없이 사라지지 않게 못 박는다.

★ 쿠키 상자가 아니라 «응답 머리글 글자»를 읽는다
  요청 도구가 쿠키를 해석해 담아 주는 상자에는 이 속성들이 남지 않는다. 실제
  브라우저가 받는 ``set-cookie`` 한 줄을 그대로 읽어야 속성을 볼 수 있다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import (
    KEY_COOKIE_MAX_AGE_SEC,
    KEY_COOKIE_NAME,
    KEY_PATH_PREFIX,
)
from src.features.storage import db as storage_db
from src.web import main, runtime

_열쇠 = "a1b2c3d4e5f60718a1b2c3d4e5f60718"


@pytest.fixture()
def client():
    """★ 반드시 ``with`` — 아니면 뒤에서 도는 조사가 취소된다.

    배포 기본값대로 https로 왕복시킨다. ``Secure``는 암호화된 연결에서만
    확인할 수 있는 속성이라, 평문 주소로 열면 이 시험이 볼 것이 없어진다.
    """

    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app, base_url="https://testserver") as started:
        yield started


def _링크발급(key: str) -> None:
    with storage_db.connect() as conn:
        share_store.insert_new(
            conn,
            key=key,
            company="가나다전자",
            job="마케팅",
            report_id="",
            now_iso="2026-08-16T10:00:00",
        )


def _열쇠쿠키_머리글(response) -> str:
    """응답이 실제로 내려보낸 열쇠 쿠키 한 줄을 그대로 꺼낸다."""

    lines = [
        line
        for line in response.headers.get_list("set-cookie")
        if line.startswith(f"{KEY_COOKIE_NAME}=")
    ]
    assert len(lines) == 1, f"열쇠 쿠키가 한 줄이 아니다: {lines}"
    return lines[0]


def test_초대링크를_열면_열쇠쿠키에_보호속성_넷이_모두_붙는다(client: TestClient):
    _링크발급(_열쇠)

    response = client.get(f"{KEY_PATH_PREFIX}/{_열쇠}", follow_redirects=False)

    assert response.status_code == 303
    cookie = _열쇠쿠키_머리글(response)
    assert f"{KEY_COOKIE_NAME}={_열쇠}" in cookie
    # ★ 글자로 단정한다. 「설정 함수를 불렀는가」가 아니라 「브라우저가 받는
    #   줄에 있는가」를 봐야, 틀의 기본값이 바뀌어 빠지는 경우까지 잡힌다.
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" in cookie
    # 수명은 초대 링크 상수와 «같은 값»이어야 한다. 시험이 숫자를 따로 적으면
    # 상수만 바뀌었을 때 둘이 갈라진 채로 통과한다.
    assert f"Max-Age={KEY_COOKIE_MAX_AGE_SEC}" in cookie


def test_열쇠쿠키_수명은_초대링크_수명인_90일이다():
    """상수 자체가 조용히 줄거나 늘지 않게 값을 한 번 못 박는다."""

    assert KEY_COOKIE_MAX_AGE_SEC == 60 * 60 * 24 * 90

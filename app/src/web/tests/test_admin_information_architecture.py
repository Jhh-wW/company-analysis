# -*- coding: utf-8 -*-
"""관리자 화면의 정보 구조(G-S8) 계약.

★ 무엇을 지키나 — 결정 D-G6 (a)에 따른 「6묶음 재배치」의 사람이 보는 결과다.
  ① 옛 통합 화면 주소는 지우지 않고 링크 화면으로 보낸다.
  ② 배포 계약이 끈 기능은 «눌리지 않는 회색 버튼»이 아니라 안내 한 줄로만 보인다.
  ③ 메뉴는 여섯이고, PC와 폰이 같은 여섯을 본다.
  ④ 메뉴 이름에 내부 용어가 없다.

★ 여기서 보지 않는 것 — 각 화면 «본문»의 문구다. 그건 옮겨 온 그대로이고
  `test_admin_access.py`·`test_member_limit_screen.py`가 이미 지킨다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.storage import constants as storage_constants
from src.web import deployment_mode, main, runtime


TEMPLATES = Path(__file__).parents[1] / "templates"

#: 메뉴에 있으면 안 되는 내부 용어와 그 이유.
#: 「이름은 사람 말로」 — 설계 05장 §3, 티켓 G-S8 요구 1.
MENU_BANNED_TERMS: dict[str, str] = {
    "LINK": "내부 자료 이름 — 사용자에게는 「초대 링크」다",
    "MEMBER": "내부 갈래 이름 — 사용자에게는 「회원」이다",
    "capability": "내부 권한 모델 이름",
    "bucket": "내부 비용 통장 이름",
    "우리": "우리 사정 — 화면은 사용자가 할 수 있는 일만 말한다",
    "admin": "내부 경로 조각이 메뉴 «글자»로 새면 안 된다",
}

#: 6묶음 정보 구조(설계 05장 §3). (메뉴 글자, 주소).
EXPECTED_MENU: tuple[tuple[str, str], ...] = (
    ("오늘 상태", "/admin"),
    ("초대 링크", "/admin/links"),
    ("회원", "/admin/members"),
    ("보고서", "/admin/issues"),
    ("비용", "/admin/costs"),
    ("운영", "/admin/settings"),
)

#: 메뉴가 붙는 모든 관리자 화면. 한 화면만 보고 「전수」라고 말하지 않는다.
MENU_BEARING_PAGES: tuple[str, ...] = (
    "/admin",
    "/admin/links",
    "/admin/members",
    "/admin/issues",
    "/admin/costs",
    "/admin/settings",
    "/admin/feedback-reports",
)


@pytest.fixture
def admin_client():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        yield client


def _menu_markup(page_text: str) -> str:
    assert '<nav class="frame-menu"' in page_text, "메뉴가 없는 화면이다"
    return page_text.split('<nav class="frame-menu"', 1)[1].split("</nav>", 1)[0]


# ══════════════════════════════════════════════════════════
# ① 옛 통합 화면 주소
# ══════════════════════════════════════════════════════════


def test_admin_access는_links로_303한다(admin_client: TestClient):
    """이미 뿌린 주소를 지우지 않는다 — 결정 D-G6 (a)의 「35 URL 회귀 0」."""

    response = admin_client.get("/admin/access", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/links"
    assert "no-store" in response.headers["cache-control"]


def test_admin_access는_로그인_없이는_링크화면을_알려주지_않는다():
    """리다이렉트라고 권한 판정을 건너뛰면 안 된다 — 거절도 기록에 남아야 한다."""

    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        response = client.get("/admin/access", follow_redirects=False)

    assert response.status_code in (302, 303, 307)
    assert response.headers.get("location") != "/admin/links"


# ══════════════════════════════════════════════════════════
# ② 꺼진 기능
# ══════════════════════════════════════════════════════════


def test_꺼진_기능은_버튼이_아니라_안내문으로_보인다(monkeypatch, tmp_path):
    """설계 05장 §2 원칙 1.

    회색 「발급 불가」 버튼은 「되는데 왜 안 눌리지」라는 오해만 남긴다. 지금 이
    운영판에서 무엇을 할 수 있는지 한 줄로 말하고, 버튼 자체를 없앤다.
    """

    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        deployment_mode.RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, "https://pilot.example")
    monkeypatch.delenv(auth_constants.ENV_BETA_ADMIN_ONLY, raising=False)
    runtime._PIPELINE = DemoPipeline()

    with TestClient(main.app, base_url="https://pilot.example") as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        links = client.get("/admin/links")
        members = client.get("/admin/members")

    assert links.status_code == 200 and members.status_code == 200
    for 화면이름, 응답 in (("초대 링크", links), ("회원", members)):
        assert "disabled" not in 응답.text, f"{화면이름} 화면에 꺼진 버튼이 남았다"
        assert "불가</button>" not in 응답.text, f"{화면이름} 화면에 꺼진 버튼이 남았다"
    assert 'role="note"' in links.text and 'role="note"' in members.text
    assert "이 운영판에서는 초대 링크를 발급할 수 없습니다." in links.text
    assert "이 운영판에서는 친구를 초대할 수 없습니다." in members.text
    # 안내문의 값어치는 «다음에 뭘 할 수 있는지»에 있다.
    assert "철회할 수 있습니다" in links.text
    assert "뺄 수 있습니다" in members.text


def test_기능이_켜져_있으면_안내문_대신_실제_폼이_보인다(admin_client: TestClient):
    """음성 대조 — 위 시험이 「폼이 없으니 늘 초록」이 되지 않게 한다."""

    links = admin_client.get("/admin/links")
    members = admin_client.get("/admin/members")

    assert 'action="/admin/links/new"' in links.text
    assert 'action="/admin/invite"' in members.text
    assert "이 운영판에서는 초대 링크를 발급할 수 없습니다." not in links.text
    assert "이 운영판에서는 친구를 초대할 수 없습니다." not in members.text

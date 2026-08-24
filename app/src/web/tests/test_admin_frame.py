"""승인된 운영 대시보드로 프레임 주소를 안전하게 수렴시키는 계약."""

from pathlib import Path

from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.web import main, runtime


def test_admin_frame_requires_an_admin_session():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        response = client.get("/admin/frame", follow_redirects=False)

    assert response.status_code in (302, 303, 307)


def test_approved_frame_url_redirects_to_the_single_admin_dashboard():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.get("/admin/frame", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert "no-store" in response.headers["cache-control"]


def test_approved_dashboard_keeps_the_mobile_scope_in_existing_styles():
    css = (Path(__file__).parents[1] / "static" / "style.css").read_text(
        encoding="utf-8"
    )

    assert ".admin-frame .frame-mobile-notice" in css
    assert ".admin-frame .frame-menu .frame-desktop-link" in css
    assert ".admin-frame form { display: none; }" in css
    assert "@media (max-width: 700px)" in css
    assert ".frame-mobile-emergency" in css
    assert ".frame-emergency-stop { display: block; }" in css


def test_mobile_emergency_stop_is_present_but_other_mobile_forms_stay_hidden():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.get("/admin")

    assert response.status_code == 200
    assert 'class="frame-emergency-stop"' in response.text
    assert 'name="status" value="maintenance"' in response.text
    assert "새 생성 긴급 중단" in response.text


def test_pc_dashboard_has_exactly_six_menus_and_contextual_access_actions():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.get("/admin")
        destination = client.get("/admin/access")
        members = client.get("/admin/members")
        links = client.get("/admin/links")
        settings = client.get("/admin/settings")

    assert response.status_code == 200
    menu = response.text.split('<nav class="frame-menu"', 1)[1].split("</nav>", 1)[0]
    assert menu.count("<a ") == 6
    assert ">오늘</a>" in menu and ">문제·보고서</a>" in menu
    assert ">친구</a>" in menu and ">지원 LINK</a>" in menu and "⚙" in menu
    assert ">신고 관리</a>" in menu
    assert 'href="/admin/access"' not in menu
    assert destination.status_code == 200
    assert "초대·LINK 관리" in destination.text
    assert "LINK 발급" in destination.text
    assert "친구 초대" in destination.text
    assert 'href="/admin/access#invited-members-title"' in members.text
    assert 'href="/admin/access#company-links-title"' in links.text
    assert 'href="/admin/access"' in settings.text

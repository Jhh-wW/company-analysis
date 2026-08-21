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

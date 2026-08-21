"""구형 품질 대시보드 주소가 통합 운영 대시보드로 합쳐지는 계약."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.web import main, runtime


def test_legacy_quality_dashboard_redirects_to_the_single_admin_dashboard(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.get("/admin/dashboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert "no-store" in response.headers["cache-control"]

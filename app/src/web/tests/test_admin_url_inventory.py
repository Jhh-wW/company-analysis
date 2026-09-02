# -*- coding: utf-8 -*-
"""관리자 화면 URL 전수 목록이 정보 구조 재배치(G-S8) 뒤에도 살아 있는지 못 박는다.

★ 왜 필요한가 — 결정 D-G6 (a)는 「동작하는 35개 URL은 버리지 않고 정보 구조만
  재배치」다. 화면을 나누고 메뉴 이름을 바꾸는 동안 라우트 하나가 조용히 사라지면
  이미 뿌린 주소가 죽는다. 이 시험은 **재배치 전에 이미 초록**이어야 하고(회귀
  방지 기준선), 재배치 뒤에도 초록이어야 한다.

★ 이 시험이 지키는 것과 못 지키는 것
  - 지킨다: 목록의 (메서드, 경로 틀)이 앱 라우트 표에 **등록돼 있다**.
  - 지킨다: GET 화면이 200 또는 303으로 열린다(라우팅 404·405·5xx가 0).
  - 지킨다: POST가 404·405·5xx가 아니다(도달 가능하고, 서버 오류가 아니다).
  - 못 지킨다: POST가 «끝까지 옳게» 도는지. 그것은 각 기능의 전용 시험이 본다.
    여기서는 일부러 CSRF 토큰을 빼서 부작용 없이 도달 여부만 확인한다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.admin_dashboard import store as dashboard_store
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.feedback_report import constants as feedback_constants
from src.features.feedback_report import logic as feedback_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.web import main, runtime


# 감사 문서(`참고_감사/admin_dashboard.md`, HEAD ddc4682)가 전수 대조한 35개.
# 형식: (메서드, 라우트에 등록된 경로 틀, 실제로 두드릴 경로 틀).
# 두드릴 경로의 `{...}`는 아래 fixture가 실제로 만든 자료로 채운다.
_ADMIN_URL_INVENTORY: tuple[tuple[str, str, str], ...] = (
    ("GET", "/admin", "/admin"),
    ("GET", "/admin/dashboard", "/admin/dashboard"),
    ("GET", "/admin/frame", "/admin/frame"),
    ("GET", "/admin/access", "/admin/access"),
    ("POST", "/admin/links/new", "/admin/links/new"),
    ("GET", "/admin/links/{key_hash}", "/admin/links/{link_hash}"),
    ("GET", "/admin/link/{key}", "/admin/link/{link_hash}"),
    ("GET", "/admin/link/report/{report_id}", "/admin/link/report/{report_id}"),
    ("POST", "/admin/links/report", "/admin/links/report"),
    ("POST", "/admin/links/revoke", "/admin/links/revoke"),
    ("POST", "/admin/budget/recheck", "/admin/budget/recheck"),
    ("POST", "/admin/budget/settle", "/admin/budget/settle"),
    ("POST", "/admin/invite", "/admin/invite"),
    ("POST", "/admin/revoke", "/admin/revoke"),
    ("GET", "/admin/feedback-reports", "/admin/feedback-reports"),
    (
        "GET",
        "/admin/feedback-reports/{report_id}",
        "/admin/feedback-reports/{feedback_id}",
    ),
    (
        "POST",
        "/admin/feedback-reports/{report_id}/status",
        "/admin/feedback-reports/{feedback_id}/status",
    ),
    ("GET", "/admin/refresh/today", "/admin/refresh/today"),
    ("GET", "/admin/issues", "/admin/issues"),
    ("GET", "/admin/refresh/issues", "/admin/refresh/issues"),
    ("GET", "/admin/reports/{report_id}", "/admin/reports/{report_id}"),
    (
        "GET",
        "/admin/reports/{report_id}/versions/{version}",
        "/admin/reports/{report_id}/versions/1",
    ),
    ("POST", "/admin/reports/{report_id}/state", "/admin/reports/{report_id}/state"),
    (
        "POST",
        "/admin/reports/{report_id}/corrected-payload",
        "/admin/reports/{report_id}/corrected-payload",
    ),
    ("POST", "/admin/reports/{report_id}/trash", "/admin/reports/{report_id}/trash"),
    (
        "POST",
        "/admin/reports/{report_id}/restore",
        "/admin/reports/{report_id}/restore",
    ),
    ("GET", "/admin/members", "/admin/members"),
    ("GET", "/admin/links", "/admin/links"),
    (
        "POST",
        "/admin/links/{key_hash}/opens/confirm",
        "/admin/links/{link_hash}/opens/confirm",
    ),
    ("GET", "/admin/settings", "/admin/settings"),
    ("GET", "/admin/settings/change", "/admin/settings/change"),
    (
        "GET",
        "/admin/settings/weekly-reports/{week_start}/download",
        "/admin/settings/weekly-reports/{week_start}/download",
    ),
    (
        "POST",
        "/admin/settings/weekly-reports/run",
        "/admin/settings/weekly-reports/run",
    ),
    ("POST", "/admin/settings/trash-cleanup/run", "/admin/settings/trash-cleanup/run"),
    ("POST", "/admin/settings/service", "/admin/settings/service"),
)

_INVENTORY_SIZE = 35

# 자료가 없어서 화면이 «스스로» 404를 돌려주는 자리. 라우트가 지워졌을 때 나오는
# FastAPI 기본 404(`{"detail":"Not Found"}`)와 구별하려고 화면 문구까지 본다.
# 스냅샷은 보고서 본문 전체와 SHA-256 결속이 있어야 만들어져서 여기서 안 심는다.
_HANDLER_OWN_404: dict[str, str] = {
    "/admin/reports/{report_id}/versions/{version}": (
        "설문 당시 보고서 스냅샷을 찾을 수 없습니다."
    ),
}

_LINK_KEY = "a" * 32
_REPORT_ID = "c" * 32
_WEEK_START = "2026-08-24"


@pytest.fixture
def admin_client():
    """관리자 세션 + 각 화면이 「자료 없음」으로 빠지지 않을 최소 자료."""

    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        with storage_db.connect() as conn:
            share_store.insert_new(
                conn,
                key=_LINK_KEY,
                company="카카오",
                job="마케팅",
                now_iso="2026-08-18T10:00:00+09:00",
            )
            feedback = feedback_logic.create_report(
                conn,
                stage=feedback_constants.STAGE_REPORT,
                category=feedback_constants.CATEGORY_WRONG_INFO,
                body="매출 수치가 실제 공시와 다릅니다",
                company_name="카카오",
                report_ref="",
                item_label="재무 지표",
                ref_url="",
                reporter_key="url-inventory",
                now_iso="2026-08-20T10:00:00+09:00",
            )
            dashboard_store.save_weekly_report(
                conn,
                week_start=_WEEK_START,
                workbook_blob=b"PK\x03\x04 url-inventory placeholder",
                actor_email="admin@example.com",
                now_iso=clock.iso_now_kst(),
            )
        client._url_inventory_substitutions = {
            "link_hash": share_store.key_hash_of(_LINK_KEY),
            "report_id": _REPORT_ID,
            "feedback_id": feedback.report_id,
            "week_start": _WEEK_START,
        }
        yield client


def _iter_app_routes(routes):
    """FastAPI가 `include_router`로 감싼 라우터 안까지 들어가 본다."""

    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _iter_app_routes(included.routes)
            continue
        nested = getattr(route, "routes", None)
        if nested:
            yield from _iter_app_routes(nested)
            continue
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", None) or ():
            yield method, path


def _registered_routes() -> set[tuple[str, str]]:
    return set(_iter_app_routes(main.app.routes))


def test_감사목록은_35개다():
    """목록을 슬쩍 줄여서 초록을 만드는 길을 막는다."""

    assert len(_ADMIN_URL_INVENTORY) == _INVENTORY_SIZE
    assert len({(method, path) for method, path, _ in _ADMIN_URL_INVENTORY}) == (
        _INVENTORY_SIZE
    )


@pytest.mark.parametrize(
    ("method", "route_path"),
    [(method, route_path) for method, route_path, _ in _ADMIN_URL_INVENTORY],
)
def test_35개_관리자_URL은_라우트표에_등록돼_있다(method: str, route_path: str):
    assert (method, route_path) in _registered_routes()


@pytest.mark.parametrize(
    ("method", "route_path", "probe_template"),
    list(_ADMIN_URL_INVENTORY),
)
def test_35개_관리자_URL은_전부_200_또는_303이다(
    admin_client: TestClient, method: str, route_path: str, probe_template: str
):
    """로그인한 관리자에게 «사라진» 화면이 하나도 없어야 한다.

    POST는 부작용을 만들지 않으려고 CSRF 토큰 없이 두드린다. 그래서 성공(303)이
    아니라 «거절»이 정상이며, 여기서 보는 것은 **라우트가 아직 있는가**다.
    라우트가 지워지면 404·405가 되고, 안에서 터지면 5xx가 된다.
    """

    probe_path = probe_template.format(**admin_client._url_inventory_substitutions)
    if method == "GET":
        response = admin_client.get(probe_path, follow_redirects=False)
        own_404 = _HANDLER_OWN_404.get(route_path, "")
        if own_404 and response.status_code == 404:
            # 화면이 스스로 돌려준 「자료 없음」이다 — 라우팅 404가 아니다.
            assert own_404 in response.text, probe_path
            return
        assert response.status_code in (200, 303), (probe_path, response.status_code)
        return

    response = admin_client.request(method, probe_path, follow_redirects=False)
    assert response.status_code not in (404, 405), (probe_path, response.status_code)
    assert response.status_code < 500, (probe_path, response.status_code)

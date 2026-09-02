# -*- coding: utf-8 -*-
"""관리자 «첫 화면»이 유료 조사 차단을 알리는지 못 박는다.

★ 왜 이 파일이 생겼나
  ─────────────────────────────────────────────────────────
  사용자가 현대카드를 돌렸더니 이렇게 떴다:
    「비용 기록을 확인할 수 없어 새 조사를 잠시 멈췄습니다」

  **그런데 관리자 화면에는 아무 문제도 안 떠 있었다.**
  원인: 이 판정을 하는 곳이 `/admin/access` 배너뿐이라, 관리자가 그 화면을
  «직접 열어야만» 보였다. 관리자 첫 화면(`/admin`)은 이 상태를 아예 안 읽어서
  모든 유료 조사가 멈춘 날에도 「먼저 확인할 일」이 비어 있었다.

  안 보이는 고장은 안 고쳐진다. 첫 화면이 말해야 한다.
"""

from __future__ import annotations

import pytest

from src.web import paid_runtime
from src.web.routers import dashboard


@pytest.fixture(autouse=True)
def _원래대로():
    """모듈 전역이라 새면 다음 시험이 틀린다."""
    원래_healthy = paid_runtime._BUDGET_STORE_HEALTHY
    원래_미확정 = set(paid_runtime._UNRESOLVED_BUCKETS)
    yield
    paid_runtime._BUDGET_STORE_HEALTHY = 원래_healthy
    paid_runtime._UNRESOLVED_BUCKETS = 원래_미확정


# ══════════════════════════════════════════════════════════
# 두 화면이 «같은» 판정을 쓰는가
# ══════════════════════════════════════════════════════════


def test_원장이_막히면_차단으로_판정한다() -> None:
    paid_runtime._BUDGET_STORE_HEALTHY = False
    paid_runtime._UNRESOLVED_BUCKETS = set()

    막혔나, 사유 = paid_runtime.paid_research_block()

    assert 막혔나 is True
    assert "비용 기록 파일을 읽지 못해" in 사유


def test_미확정_통장만_있어도_차단으로_판정한다() -> None:
    paid_runtime._BUDGET_STORE_HEALTHY = True
    paid_runtime._UNRESOLVED_BUCKETS = {("2026-08-28", "bucket")}

    막혔나, 사유 = paid_runtime.paid_research_block()

    assert 막혔나 is True
    assert "확인되지 않은 건이 남아" in 사유


def test_멀쩡하면_차단_아님() -> None:
    paid_runtime._BUDGET_STORE_HEALTHY = True
    paid_runtime._UNRESOLVED_BUCKETS = set()

    assert paid_runtime.paid_research_block() == (False, "")


def test_관리자_접근화면과_첫화면이_같은_판정을_쓴다() -> None:
    """★ 두 화면이 어긋나면 관리자가 「어느 쪽이 맞나」를 먼저 의심하게 된다."""
    from src.web.routers import admin as admin_router

    paid_runtime._BUDGET_STORE_HEALTHY = False
    paid_runtime._UNRESOLVED_BUCKETS = set()

    assert admin_router._paid_research_status() == paid_runtime.paid_research_block()


# ══════════════════════════════════════════════════════════
# 첫 화면이 실제로 그것을 최우선으로 띄우는가
# ══════════════════════════════════════════════════════════


def test_첫화면이_유료_차단을_최우선으로_띄운다(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 이 시험이 수정의 «이유»다.

    되돌리면 유료 조사가 전부 멈춘 날에도 첫 화면이 「문제 없음」이 된다.
    """
    paid_runtime._BUDGET_STORE_HEALTHY = False
    paid_runtime._UNRESOLVED_BUCKETS = set()

    # 저장소를 안 건드리려고 읽기 함수를 「정상·빈 목록」으로 고정한다.
    monkeypatch.setattr(
        dashboard, "_dashboard_read", lambda label, fallback, reader: (fallback, True)
    )

    맥락 = dashboard._dashboard_context(_가짜요청())
    이슈 = 맥락["dashboard_primary_issue"]

    assert 이슈 is not None, "★ 첫 화면이 유료 조사 차단을 안 알린다"
    assert 이슈["kind"] == "paid_research"
    assert 이슈["href"] == "/admin/access", "누를 곳을 알려줘야 한다"
    assert "비용 기록 파일을 읽지 못해" in 이슈["detail"]


def test_멀쩡하면_첫화면이_유료_차단을_안_띄운다(monkeypatch: pytest.MonkeyPatch) -> None:
    """없는 문제를 만들어 내면 진짜 문제가 묻힌다."""
    paid_runtime._BUDGET_STORE_HEALTHY = True
    paid_runtime._UNRESOLVED_BUCKETS = set()
    monkeypatch.setattr(
        dashboard, "_dashboard_read", lambda label, fallback, reader: (fallback, True)
    )

    이슈 = dashboard._dashboard_context(_가짜요청())["dashboard_primary_issue"]

    assert 이슈 is None or 이슈["kind"] != "paid_research"


def _가짜요청():
    """`_dashboard_context` 가 쓰는 최소한의 Request 흉내."""
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
        }
    )

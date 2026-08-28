# -*- coding: utf-8 -*-
"""미확정 유료 단계를 «보고 마감하는» 길을 못 박는다.

★ 왜 이 파일이 생겼나 (2026-08-28)
  ─────────────────────────────────────────────────────────
  사용자 화면이 이렇게 말한다:
    「provider 과금 여부를 확정하지 못한 통장의 유료 조사를 닫았습니다.
      **관리자가 미확정 비용을 대사해야** 해당 통장이 다시 열립니다.」

  그런데 **대사할 방법이 코드에 없었다.** `finish_inflight` 는 내부 정산에서만
  불렸고 관리자 경로가 0개였다. 그래서:
    ① 무엇이 걸려 있는지 볼 화면이 없었고
    ② 「원장 다시 읽기」를 눌러도 미확정은 그대로 남아 계속 막혔고
    ③ 재시작해도 DB에서 다시 읽히니 **영영 안 풀렸다.**

  사용자가 실제로 겪은 일 — 버튼을 눌렀는데 아무 일도 안 일어나는 것처럼 보였다.
  (「대시보드 자체가 작동하는 거 맞아? 그냥 버튼만 있는 거 아니고?」)

★ 이 시험이 지키는 세 가지
  ① **못 읽은 것을 「없다」로 보이게 하지 않는다.** 있는데 없다고 하면 손을 뗀다.
  ② **돌고 있는 단계는 마감하지 않는다.** 끝날 때 두 번 적히거나 표식을 못 찾는다.
  ③ **예약액만큼 썼다고 확정한다.** 모를 때 적게 잡으면 하루 상한이 헐거워진다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.core import clock
from src.features.budget import spend_store
from src.features.budget.constants import SPEND_PHASE_PIPELINE
from src.features.storage import db as storage_db
from src.web import paid_runtime


_통장 = "user:0123456789abcdef0123"


@pytest.fixture(autouse=True)
def _원래대로():
    원래_healthy = paid_runtime._BUDGET_STORE_HEALTHY
    원래_미확정 = set(paid_runtime._UNRESOLVED_BUCKETS)
    원래_활성 = set(paid_runtime._ACTIVE_PAID_PHASES)
    yield
    paid_runtime._BUDGET_STORE_HEALTHY = 원래_healthy
    paid_runtime._UNRESOLVED_BUCKETS = 원래_미확정
    paid_runtime._ACTIVE_PAID_PHASES = 원래_활성


def _미확정을_심는다(run_id: str = "run-abc", 예약: float = 900.0) -> dt.date:
    """진짜 DB에 마감 안 된 유료 단계 한 줄을 만든다."""
    오늘 = clock.today_kst()
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        spend_store.begin_inflight(
            conn,
            run_id=run_id,
            phase=SPEND_PHASE_PIPELINE,
            day=오늘,
            bucket=_통장,
            started_at=clock.iso_now_kst(),
            requested_cost_krw=예약,
        )
    return 오늘


# ══════════════════════════════════════════════════════════
# ① 무엇이 걸렸는지 보인다
# ══════════════════════════════════════════════════════════


def test_걸려_있는_것을_보여_준다() -> None:
    """★ 대사하라면서 대사할 대상을 안 보여 주면 관리자는 아무것도 못 한다."""
    _미확정을_심는다()

    항목들, 읽었나 = paid_runtime.list_unresolved_spend()

    assert 읽었나 is True
    assert len(항목들) == 1
    assert 항목들[0].run_id == "run-abc"
    assert 항목들[0].phase == SPEND_PHASE_PIPELINE


def test_못_읽으면_없다가_아니라_못_읽었다로_알린다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 있는데 없다고 하면 관리자가 손을 뗀다. 둘은 다른 사실이다."""

    def 터진다(*_a, **_k):
        raise RuntimeError("일부러 낸 오류")

    monkeypatch.setattr(storage_db, "connect", 터진다)

    항목들, 읽었나 = paid_runtime.list_unresolved_spend()

    assert 항목들 == ()
    assert 읽었나 is False


# ══════════════════════════════════════════════════════════
# ② 마감이 실제로 통장을 연다
# ══════════════════════════════════════════════════════════


def test_마감하면_그_통장이_다시_열린다() -> None:
    """★ 이 시험이 2026-08-28 수정의 «이유»다. 되돌리면 영영 안 풀린다."""
    _미확정을_심는다()
    paid_runtime._seed_ledger()
    assert paid_runtime._UNRESOLVED_BUCKETS, "준비 실패 — 막힌 상태를 못 만들었다"

    마감했나, 알림 = paid_runtime.settle_unresolved_spend("run-abc", SPEND_PHASE_PIPELINE)

    assert 마감했나 is True
    assert paid_runtime._UNRESOLVED_BUCKETS == set()
    assert "다시 열었습니다" in 알림
    assert paid_runtime.paid_research_block() == (False, "")


def test_예약액만큼_썼다고_확정한다() -> None:
    """★ 모를 때 적게 잡으면 하루 상한이 실제보다 헐거워진다."""
    오늘 = _미확정을_심는다(예약=900.0)

    paid_runtime.settle_unresolved_spend("run-abc", SPEND_PHASE_PIPELINE)

    with storage_db.connect() as conn:
        기록 = spend_store.load_day(conn, 오늘)
    # 원장에는 원문이 아니라 «지문»이 저장된다 (`bucket_id`).
    assert 기록.by_bucket.get(spend_store.bucket_id(_통장)) == pytest.approx(900.0)
    assert 기록.total_krw == pytest.approx(900.0)


# ══════════════════════════════════════════════════════════
# ③ 안전선 — 돌고 있는 것은 건드리지 않는다
# ══════════════════════════════════════════════════════════


def test_돌고_있는_단계는_마감하지_않는다() -> None:
    """★ 마감해 버리면 그 조사가 끝날 때 두 번 적히거나 표식을 못 찾는다."""
    _미확정을_심는다()
    paid_runtime._ACTIVE_PAID_PHASES = {
        ("run-abc", SPEND_PHASE_PIPELINE, clock.today_kst().isoformat(), _통장)
    }

    마감했나, 알림 = paid_runtime.settle_unresolved_spend("run-abc", SPEND_PHASE_PIPELINE)

    assert 마감했나 is False
    assert "돌고 있는" in 알림


def test_없는_항목을_마감하려_하면_그렇게_알린다() -> None:
    마감했나, 알림 = paid_runtime.settle_unresolved_spend("없는-요청", SPEND_PHASE_PIPELINE)

    assert 마감했나 is False
    assert "찾지 못했습니다" in 알림


def test_빈_값이면_아무것도_안_한다() -> None:
    assert paid_runtime.settle_unresolved_spend("", "")[0] is False
    assert paid_runtime.settle_unresolved_spend("run-abc", "")[0] is False


# ══════════════════════════════════════════════════════════
# ④ 관리자에게 실제 길이 있는가
# ══════════════════════════════════════════════════════════


def test_마감_경로가_등록돼_있다() -> None:
    from src.web.routers import admin as admin_router

    경로들 = {
        (route.path, method)
        for route in admin_router.router.routes
        for method in getattr(route, "methods", set()) or set()
    }

    assert ("/admin/budget/settle", "POST") in 경로들


def test_화면에_마감_버튼과_경고가_있다() -> None:
    """무엇을 하는 버튼인지 옆에 적어야 한다 — 돈을 썼다고 확정하는 일이다."""
    from src.core import paths

    화면 = (
        paths.APP_ROOT / "src" / "web" / "templates" / "admin_access.html"
    ).read_text(encoding="utf-8")

    assert 'action="/admin/budget/settle"' in 화면
    assert "예약액만큼 썼다고 확정" in 화면
    assert "unresolved_spend" in 화면


# ══════════════════════════════════════════════════════════
# ⑤ 버튼이 «아무 일도 안 한 것처럼» 보이지 않는가
# ══════════════════════════════════════════════════════════


@pytest.fixture
def _관리자():
    """관리자로 로그인한 손님 (test_admin_access.py 의 픽스처와 같은 방식)."""
    from fastapi.testclient import TestClient

    from src.features.auth import constants as auth_constants
    from src.features.auth import logic as auth_logic
    from src.features.pipeline.demo import DemoPipeline
    from src.web import main, runtime

    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        client._csrf = auth_logic.csrf_token_for_session(session.token)
        yield client


def test_미확정이_남으면_다시읽기가_말없이_돌아가지_않는다(_관리자) -> None:
    """★ 2026-08-28 사용자 신고: 「그냥 버튼만 있는 거 아니냐」.

    「원장 다시 읽기」는 원장을 살려도 **미확정 통장은 못 푼다.** 그런데 그때도
    그냥 리다이렉트해서, 관리자 눈에는 **버튼이 아무 일도 안 한 것처럼** 보였다.
    아직 막혀 있다는 사실과 이유를 화면에 말해야 한다.
    """
    _미확정을_심는다()
    paid_runtime._seed_ledger()
    assert paid_runtime._UNRESOLVED_BUCKETS, "준비 실패 — 막힌 상태를 못 만들었다"

    응답 = _관리자.post(
        "/admin/budget/recheck",
        data={"csrf_token": _관리자._csrf},
        follow_redirects=False,
    )

    assert 응답.status_code == 503, "★ 말없이 303 으로 돌아가면 안 된다"
    assert "아직 유료 조사가 닫혀 있습니다" in 응답.text
    assert "마감되지 않은 통장" in 응답.text


def test_화면에_걸린_항목이_실제로_그려진다(_관리자) -> None:
    """무엇이 걸렸는지 못 보면 대사할 수 없다."""
    _미확정을_심는다(run_id="run-보이나")
    paid_runtime._seed_ledger()

    화면 = _관리자.get("/admin/access").text

    assert "마감되지 않은 유료 단계" in 화면
    assert "run-보이나"[:12] in 화면
    assert 'action="/admin/budget/settle"' in 화면


def test_마감_버튼을_누르면_실제로_열린다(_관리자) -> None:
    """★ 끝까지 동작하는지 «화면을 거쳐» 확인한다 — 이게 사용자가 하는 일이다."""
    _미확정을_심는다()
    paid_runtime._seed_ledger()
    assert paid_runtime.paid_research_block()[0] is True

    응답 = _관리자.post(
        "/admin/budget/settle",
        data={
            "csrf_token": _관리자._csrf,
            "run_id": "run-abc",
            "phase": SPEND_PHASE_PIPELINE,
        },
        follow_redirects=False,
    )

    assert 응답.status_code == 303, f"열렸어야 한다: {응답.status_code}"
    assert 응답.headers["location"] == "/admin/access"
    assert paid_runtime.paid_research_block() == (False, "")


def test_원장이_깨진_축소화면에도_걸린_항목이_보인다(_관리자, monkeypatch) -> None:
    """★ 원장을 못 읽는 순간이야말로 관리자가 «무엇이 걸렸는지» 알아야 할 때다.

    이때 화면은 503 축소본으로 떨어지는데, 거기서 목록이 빠지면
    「대사하라」는 말만 있고 대상은 안 보이는 상태가 그대로 남는다.
    """
    _미확정을_심는다(run_id="run-축소본")
    # 원장 검사만 실패시킨다 — 미확정 목록 읽기는 살아 있어야 한다.
    monkeypatch.setattr(paid_runtime, "_BUDGET_STORE_HEALTHY", False)

    응답 = _관리자.get("/admin/access")

    assert 응답.status_code == 503
    assert "확인 불가" in 응답.text, "축소 화면이 맞는지 확인"
    assert "마감되지 않은 유료 단계" in 응답.text
    assert "run-축소본"[:12] in 응답.text
    assert 'action="/admin/budget/settle"' in 응답.text

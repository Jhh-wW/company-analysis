# -*- coding: utf-8 -*-
"""비용 원장을 «다시 읽어» 유료 조사를 여는 경로를 못 박는다.

★ 왜 이 파일이 생겼나 (2026-08-28)
  ─────────────────────────────────────────────────────────
  사용자 화면이 이렇게 말한다:
    「비용 기록을 확인할 수 없어 새 조사를 잠시 멈췄습니다.
      **관리자 확인이 끝나야 다시 열립니다.**」

  그런데 **관리자가 「확인」을 실행할 방법이 코드에 없었다.**
  `_BUDGET_STORE_HEALTHY` 를 True 로 되돌리는 곳이 기동 시 `_seed_ledger()` 한 곳뿐이라,
  운영 중 한 번 꺼지면 **서버를 재시작하기 전까지 모든 유료 조사가 막혔다.**
  실제로 2026-08-28 에 그 일이 났다 — 조사 한 건이 성공한 «뒤» 다음 조사가 막혔고,
  관리자 화면에는 풀 수단이 없었다.

★ 이 시험이 지키는 두 가지
  ① **강제로 열지 않는다.** 자료가 여전히 나쁘면 닫힌 채로 남아야 한다.
     돈이 걸린 문을 사람 말 한마디로 여는 길을 만들면 안 된다.
  ② **진행 중인 유료 단계가 있으면 다시 검사하지 않는다.**
     `_seed_ledger()` 는 진행중 표식을 전부 「결과를 모르는 것」으로 다시 분류한다.
     돌아가는 조사를 미확정으로 만들면 **오히려 더 막힌다.**
"""

from __future__ import annotations

import pytest

from src.web import paid_runtime


@pytest.fixture(autouse=True)
def _원래대로(monkeypatch: pytest.MonkeyPatch):
    """전역 상태를 시험마다 되돌린다 — 모듈 전역이라 새면 다음 시험이 틀린다."""
    원래_healthy = paid_runtime._BUDGET_STORE_HEALTHY
    원래_미확정 = set(paid_runtime._UNRESOLVED_BUCKETS)
    원래_활성 = set(paid_runtime._ACTIVE_PAID_PHASES)
    yield
    paid_runtime._BUDGET_STORE_HEALTHY = 원래_healthy
    paid_runtime._UNRESOLVED_BUCKETS = 원래_미확정
    paid_runtime._ACTIVE_PAID_PHASES = 원래_활성


def _seed_를_가짜로(monkeypatch: pytest.MonkeyPatch, *, 결과: bool, 미확정: int = 0) -> list[int]:
    """`_seed_ledger` 를 «불렸는지 세는» 가짜로 바꾼다."""
    부른횟수: list[int] = []

    def 가짜_seed() -> None:
        부른횟수.append(1)
        paid_runtime._BUDGET_STORE_HEALTHY = 결과
        paid_runtime._UNRESOLVED_BUCKETS = {("2026-08-28", f"b{i}") for i in range(미확정)}

    monkeypatch.setattr(paid_runtime, "_seed_ledger", 가짜_seed)
    return 부른횟수


# ══════════════════════════════════════════════════════════
# ① 강제로 열지 않는다
# ══════════════════════════════════════════════════════════


def test_자료가_여전히_나쁘면_닫힌_채로_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 이게 이 기능의 «안전선»이다. 되돌리면 돈이 새는 문이 열린다."""
    paid_runtime._BUDGET_STORE_HEALTHY = False
    paid_runtime._ACTIVE_PAID_PHASES = set()
    _seed_를_가짜로(monkeypatch, 결과=False)

    열렸나, 알림 = paid_runtime.recheck_budget_store()

    assert 열렸나 is False
    assert paid_runtime._BUDGET_STORE_HEALTHY is False
    assert "여전히" in 알림


def test_자료가_멀쩡해지면_다시_열린다(monkeypatch: pytest.MonkeyPatch) -> None:
    paid_runtime._BUDGET_STORE_HEALTHY = False
    paid_runtime._ACTIVE_PAID_PHASES = set()
    _seed_를_가짜로(monkeypatch, 결과=True)

    열렸나, 알림 = paid_runtime.recheck_budget_store()

    assert 열렸나 is True
    assert paid_runtime._BUDGET_STORE_HEALTHY is True
    assert "다시 열었습니다" in 알림


def test_원장은_복원됐어도_미확정_통장이_남으면_그렇게_알린다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """전역은 열려도 «그 통장»은 계속 막힌다 — 사람이 그 사실을 알아야 한다."""
    paid_runtime._BUDGET_STORE_HEALTHY = False
    paid_runtime._ACTIVE_PAID_PHASES = set()
    _seed_를_가짜로(monkeypatch, 결과=True, 미확정=2)

    열렸나, 알림 = paid_runtime.recheck_budget_store()

    assert 열렸나 is True
    assert "2건" in 알림
    assert "막혀" in 알림


# ══════════════════════════════════════════════════════════
# ② 돌아가는 조사를 망가뜨리지 않는다
# ══════════════════════════════════════════════════════════


def test_진행_중인_유료_단계가_있으면_다시_검사하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ `_seed_ledger()` 는 진행중 표식을 전부 「결과 모름」으로 다시 분류한다.

    돌아가는 조사에 대고 부르면 그 조사가 미확정이 되어 **오히려 더 막힌다.**
    """
    paid_runtime._BUDGET_STORE_HEALTHY = False
    paid_runtime._ACTIVE_PAID_PHASES = {("run-1", "본조사", "2026-08-28", "bucket")}
    부른횟수 = _seed_를_가짜로(monkeypatch, 결과=True)

    열렸나, 알림 = paid_runtime.recheck_budget_store()

    assert 부른횟수 == [], "★ 진행 중인데 원장을 다시 읽었다 — 조사가 미확정이 된다"
    assert 열렸나 is False, "원래 상태를 그대로 돌려줘야 한다"
    assert "1건" in 알림
    assert "끝난 뒤에" in 알림


def test_진행_중이면_열려_있던_상태도_그대로_돌려준다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """진행 중이라 «안 했다»는 것이지 「닫혔다」가 아니다. 상태를 바꾸지 않는다."""
    paid_runtime._BUDGET_STORE_HEALTHY = True
    paid_runtime._ACTIVE_PAID_PHASES = {("run-1", "본조사", "2026-08-28", "bucket")}
    부른횟수 = _seed_를_가짜로(monkeypatch, 결과=False)

    열렸나, _ = paid_runtime.recheck_budget_store()

    assert 부른횟수 == []
    assert 열렸나 is True
    assert paid_runtime._BUDGET_STORE_HEALTHY is True


# ══════════════════════════════════════════════════════════
# ③ 관리자 경로가 실제로 있는가
# ══════════════════════════════════════════════════════════


def test_관리자_경로가_등록돼_있다() -> None:
    """★ 「관리자 확인이 끝나야 열립니다」라고 말하려면 그 경로가 실제로 있어야 한다.

    이 시험이 빨간불이면 사용자에게 **없는 것을 가리키는 안내**를 하고 있는 것이다.
    """
    from src.web.routers import admin as admin_router

    # ⚠️ `app.routes` 를 보면 안 된다 — 이 앱은 라우터를 `_IncludedRouter` 로 감싸
    #   붙이므로 개별 경로가 거기 안 드러난다(실측 2026-08-28). 라우터를 직접 본다.
    경로들 = {
        (route.path, method)
        for route in admin_router.router.routes
        for method in getattr(route, "methods", set()) or set()
    }

    assert ("/admin/budget/recheck", "POST") in 경로들


def test_화면에_다시_읽는_버튼이_있다() -> None:
    """안내 문구만 있고 버튼이 없으면 관리자도 막다른 길에 선다."""
    from src.core import paths

    화면 = (
        # ★ 2026-09-02 G-S8 — 차단 배너가 비용 화면과 축소 화면이 함께 쓰는
        #   조각으로 빠져나갔다. 버튼이 있는 곳은 이제 이 파일이다.
        paths.APP_ROOT / "src" / "web" / "templates" / "_admin_spend_banners.html"
    ).read_text(encoding="utf-8")

    assert 'action="/admin/budget/recheck"' in 화면
    assert "강제로 열지 않습니다" in 화면, "무엇을 하는 버튼인지 옆에 적어야 한다"

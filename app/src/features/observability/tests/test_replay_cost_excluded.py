"""대시보드가 «쓰지 않은 돈»을 세지 않는지 못 박는다 (문제로그 P-84).

★ 이 시험이 잡는 것 — **「이번 달 AI 비용」이 실제 지출의 45배로 나오는 것.**
  데모는 저장된 결과를 되돌려 줄 뿐 AI를 안 부른다. **0원이다.**
  그런데 데모 기록에는 이전 조사 때 쓴 돈이 들어 있었다.
  실측 — 이력 **813건 34,223원**이 쌓였는데 **진짜 지출은 0원**이었다
  (그 813건이 전부 데모였다. 웹 화면으로는 아직 한 번도 돈을 안 썼다).

★ **막는 곳이 두 군데다. 둘 다 필요하다.**
  ① `pipeline/demo.py` — 앞으로 쌓일 기록에 0원을 적는다
  ② 여기(`metrics.py`) — **이미 쌓인 옛 기록**을 집계에서 뺀다
  ①만 있으면 옛 813건이 영영 부풀어 있고, ②만 있으면 새 기록도 계속 틀린 값을 남긴다.

⚠️ **건수는 빼지 않는다** — 데모도 「실행」은 실행이다.
  틀린 것은 「얼마 썼나」뿐이므로 고치는 것도 그 하나뿐이다.
"""

from __future__ import annotations

import datetime as dt

from src.core.constants import REPLAY_MODEL_MARK
from src.features.observability.metrics import build_dashboard
from src.features.observability.tests.test_metrics import _record

_오늘 = dt.date(2026, 8, 16)
_이번달 = "2026-08-16T10:00:00"
_데모모델 = f"claude-haiku-4-5 {REPLAY_MODEL_MARK}"


# ══════════════════════════════════════════════════════════
# ① 데모 비용은 안 센다
# ══════════════════════════════════════════════════════════


def test_데모_기록의_비용은_안_센다():
    """★ P-84 그 자체."""
    기록 = [
        _record(run_id="d1", at=_이번달, cost_krw=45.67, model=_데모모델),
        _record(run_id="d2", at=_이번달, cost_krw=31.65, model=_데모모델),
    ]

    d = build_dashboard(기록, today=_오늘, model="x")

    assert d.cost_month_krw == 0.0


def test_진짜_조사_비용은_그대로_센다():
    """★ 반대 방향 — 다 빼버리면 비용을 «영영 못 재는» 지표가 된다."""
    기록 = [
        _record(run_id="r1", at=_이번달, cost_krw=88.2, model="claude-sonnet-4-6"),
        _record(run_id="r2", at=_이번달, cost_krw=81.7, model="claude-sonnet-4-6"),
    ]

    d = build_dashboard(기록, today=_오늘, model="x")

    assert d.cost_month_krw == 169.9


def test_섞여_있으면_진짜_것만_센다():
    """실제 상황은 이 모양이다 — 데모가 대부분이고 진짜가 몇 건."""
    기록 = [
        _record(run_id="d1", at=_이번달, cost_krw=45.67, model=_데모모델),
        _record(run_id="d2", at=_이번달, cost_krw=999.0, model=_데모모델),
        _record(run_id="r1", at=_이번달, cost_krw=88.2, model="claude-sonnet-4-6"),
    ]

    d = build_dashboard(기록, today=_오늘, model="x")

    assert d.cost_month_krw == 88.2


# ══════════════════════════════════════════════════════════
# ② 건수는 그대로 센다 (과잉 제외 방지)
# ══════════════════════════════════════════════════════════


def test_데모도_건수에는_들어간다():
    """★ 여기가 깨지면 데모를 «없던 일»로 만든 것이다 — 그건 다른 거짓말이다."""
    기록 = [
        _record(run_id="d1", at=_이번달, cost_krw=45.67, model=_데모모델),
        _record(run_id="d2", at=_이번달, cost_krw=31.65, model=_데모모델),
    ]

    d = build_dashboard(기록, today=_오늘, model="x")

    assert d.total == 2
    assert d.today == 2


# ══════════════════════════════════════════════════════════
# ③ 0원인 «이유»를 화면이 말할 수 있어야 한다
# ══════════════════════════════════════════════════════════


def test_당월_데모_건수를_따로_알려준다():
    """0원만 보여주면 관리자가 「비용 집계가 고장 났다」고 오해한다."""
    기록 = [
        _record(run_id="d1", at=_이번달, cost_krw=45.67, model=_데모모델),
        _record(run_id="d2", at=_이번달, cost_krw=31.65, model=_데모모델),
        _record(run_id="r1", at=_이번달, cost_krw=88.2, model="claude-sonnet-4-6"),
    ]

    d = build_dashboard(기록, today=_오늘, model="x")

    assert d.replay_month == 2


def test_지난달_데모는_당월_건수에_안_들어간다():
    """「이번 달」이라고 적혀 있으면 이번 달만 세야 한다."""
    기록 = [
        _record(run_id="d1", at="2026-07-20T10:00:00", cost_krw=45.67, model=_데모모델),
        _record(run_id="d2", at=_이번달, cost_krw=31.65, model=_데모모델),
    ]

    d = build_dashboard(기록, today=_오늘, model="x")

    assert d.replay_month == 1


# ══════════════════════════════════════════════════════════
# ④ 꼬리표가 «한 곳»에서만 정의된다
# ══════════════════════════════════════════════════════════


def test_데모가_붙이는_꼬리표와_집계가_보는_꼬리표가_같다():
    """★ 글자를 양쪽에 적어 두면 한쪽만 바뀌는 순간 조용히 틀린다 (P-83 함정)."""
    from src.features.pipeline import demo

    assert REPLAY_MODEL_MARK in demo._PILOT_MODEL

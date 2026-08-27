# -*- coding: utf-8 -*-
"""원문으로 쓸 공시를 «어떤 순서로» 찾는지 못 박는다 (`run_pilot.latest_report_rcept`).

★ 왜 이 파일이 생겼나 (2026-08-28)
  ─────────────────────────────────────────────────────────
  현대카드로 뽑은 보고서가 **거의 비었다.** 아홉 장이 각각 한 문장이고,
  출처 목록에 「전자공시 재무」 하나뿐이었다 — 즉 **공시 원문 조각이 0개**였다.

  원인: 비상장이면 「감사보고서」만 찾고 있었다.
  실측 (2026-08-28, DART 직접 조회) —
    현대카드 · `pblntf_ty="F"`(외부감사관련) → **0건** (status 013)
    현대카드 · `pblntf_ty="A"`(정기공시)     → **사업보고서 3건**
  대형 비상장사는 감사보고서를 «따로 내지 않고» 사업보고서에 첨부해 낸다
  (외부감사법 23조① 단서 — 첨부해 내면 감사인이 제출한 것으로 «본다»).

  못 찾으면 이 함수가 None 을 돌려주고 → 원문이 «빈 문자열» → 조각 0개 →
  **재무 API 숫자만으로** 보고서가 쓰인다. 그것이 껍데기 보고서의 정체였다.

  ⚠️ 이 함수의 «비상장» 경로를 지키는 시험이 **저장소에 0건**이었다.
     있던 것은 `test_real_cache.py` 의 「상장사」 두 줄뿐이다.

★ 고친 뒤 실측 (같은 날, 진짜 DART) —
  현대카드   원문 0자·조각 0개 → **369,310자 · 1판 조각 6 + 추가 절 3**
  토스       0 → **394,474자 · 8 + 6**
  야놀자     0 → **265,462자 · 8 + 4**

★ 감사보고서 폴백을 «지운 것이 아니다». 사업보고서를 안 내는 비상장 중소기업은
  감사보고서가 유일한 원문이다. 순서만 바꿨다 — 아래 폴백 시험이 그것을 지킨다.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.features.pipeline import real


class _계수기:
    """UsageCounter 흉내 — 호출 수만 센다."""

    def __init__(self) -> None:
        self.ticks = 0

    def tick(self, today: str | None = None) -> int:
        self.ticks += 1
        return self.ticks


def _목록(*이름들: str) -> dict[str, Any]:
    """`list.json` 정상 응답 흉내. 접수번호는 이름 순서대로 커진다."""
    return {
        "status": "000",
        "list": [
            {"rcept_no": f"2026010100{i:04d}", "report_nm": nm}
            for i, nm in enumerate(이름들, start=1)
        ],
    }


_없음: dict[str, Any] = {"status": "013", "list": []}


def _엔진(monkeypatch: pytest.MonkeyPatch, 응답: dict[str, dict[str, Any]]) -> Any:
    """`pblntf_ty` 별로 정해진 응답을 주는 엔진을 만든다. 부른 순서를 기록한다."""
    engine = real._engine()
    부른순서: list[str] = []

    def 가짜_get_json(endpoint: str, params: dict[str, Any], counter: Any) -> dict[str, Any]:
        assert endpoint == "list.json", f"뜻밖의 endpoint: {endpoint}"
        counter.tick()
        ty = params["pblntf_ty"]
        부른순서.append(ty)
        return 응답.get(ty, _없음)

    monkeypatch.setattr(engine, "get_json", 가짜_get_json)
    engine.__부른순서 = 부른순서  # 시험이 꺼내 볼 수 있게
    return engine


# ══════════════════════════════════════════════════════════
# 순서 자체 — 이걸 뒤집으면 현대카드가 다시 껍데기가 된다
# ══════════════════════════════════════════════════════════


def test_현대카드_모양_감사보고서가_없어도_사업보고서를_찾는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 이 시험이 2026-08-28 수정의 «이유»다. 되돌리면 여기가 빨간불이 된다.

    실측한 현대카드 응답을 그대로 흉내 냈다 — F 는 013(없음), A 에 사업보고서 3건.
    """
    engine = _엔진(
        monkeypatch,
        {
            "F": _없음,
            "A": _목록(
                "사업보고서 (2023.12)",
                "사업보고서 (2024.12)",
                "사업보고서 (2025.12)",
            ),
        },
    )

    고른것 = engine.latest_report_rcept("00222374", "비상장 외감", _계수기())

    assert 고른것 is not None, "★ 못 찾으면 원문이 비고 조각이 0개가 된다"
    assert "사업보고서" in 고른것["report_nm"]
    assert 고른것["report_nm"] == "사업보고서 (2025.12)", "최신본을 골라야 한다"


def test_비상장은_사업보고서를_먼저_본다(monkeypatch: pytest.MonkeyPatch) -> None:
    """둘 다 있으면 사업보고서가 이긴다 — 본문이 훨씬 두껍기 때문이다."""
    engine = _엔진(
        monkeypatch,
        {"A": _목록("사업보고서 (2025.12)"), "F": _목록("감사보고서 (2025.12)")},
    )

    고른것 = engine.latest_report_rcept("00222374", "비상장 외감", _계수기())

    assert "사업보고서" in 고른것["report_nm"]
    assert engine.__부른순서[0] == "A", "A 를 먼저 물어야 한다"


def test_사업보고서가_없으면_감사보고서로_넘어간다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 폴백을 지운 것이 아니다.

    사업보고서를 안 내는 비상장 중소기업은 감사보고서가 «유일한» 원문이다.
    이 시험이 빨간불이면 그 회사들이 통째로 죽는다.
    """
    engine = _엔진(monkeypatch, {"A": _없음, "F": _목록("감사보고서 (2025.12)")})

    고른것 = engine.latest_report_rcept("00999999", "비상장 외감", _계수기())

    assert 고른것 is not None
    assert "감사보고서" in 고른것["report_nm"]
    assert engine.__부른순서 == ["A", "F"], "A 를 먼저 보고 없을 때만 F 로 간다"


def test_상장사는_감사보고서로_넘어가지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """상장사 동작은 «바꾸지 않았다». 쓸데없는 조회로 일일 한도를 깎지 않는다."""
    계수기 = _계수기()
    engine = _엔진(monkeypatch, {"A": _없음, "F": _목록("감사보고서 (2025.12)")})

    고른것 = engine.latest_report_rcept("00126380", "상장사", 계수기)

    assert 고른것 is None
    assert engine.__부른순서 == ["A"], "상장사는 A 한 번만 물어야 한다"
    assert 계수기.ticks == 1


def test_둘_다_없으면_None(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _엔진(monkeypatch, {"A": _없음, "F": _없음})

    assert engine.latest_report_rcept("00999999", "비상장 외감", _계수기()) is None


# ══════════════════════════════════════════════════════════
# 고르는 규칙 — 원래 있던 것을 안 깨뜨렸는가
# ══════════════════════════════════════════════════════════


def test_연결_보고서는_빼고_고른다(monkeypatch: pytest.MonkeyPatch) -> None:
    """「연결감사보고서」는 별도 본문이 아니라 다른 문서다."""
    engine = _엔진(
        monkeypatch,
        {"A": _없음, "F": _목록("감사보고서 (2025.12)", "연결감사보고서 (2025.12)")},
    )

    고른것 = engine.latest_report_rcept("00999999", "비상장 외감", _계수기())

    assert 고른것["report_nm"] == "감사보고서 (2025.12)"


def test_첨부정정보다_본문_있는_공시를_고른다(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 「[첨부정정]」 공시의 zip 엔 본문이 없고 고친 첨부만 있다 (로보스타 실측)."""
    engine = _엔진(
        monkeypatch,
        {"A": _목록("사업보고서 (2025.12)", "[첨부정정]사업보고서 (2025.12)")},
    )

    고른것 = engine.latest_report_rcept("00222374", "비상장 외감", _계수기())

    assert "첨부정정" not in 고른것["report_nm"]


def test_이름이_안_맞으면_그_유형은_건너뛴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """목록에 뭔가 있어도 «원하는 이름»이 없으면 다음 유형으로 넘어가야 한다."""
    engine = _엔진(
        monkeypatch,
        {"A": _목록("분기보고서 (2025.09)"), "F": _목록("감사보고서 (2025.12)")},
    )

    고른것 = engine.latest_report_rcept("00999999", "비상장 외감", _계수기())

    assert "감사보고서" in 고른것["report_nm"]


# ══════════════════════════════════════════════════════════
# 순서표 자체 — 값을 바꾸면 여기서 걸린다
# ══════════════════════════════════════════════════════════


def test_찾는_순서표가_그대로다() -> None:
    """★ 순서를 바꾸려면 이 시험을 먼저 고쳐라 — 껍데기 보고서로 돌아가는 길이다."""
    engine = real._engine()

    assert engine.FILING_LOOKUP_ORDER["상장사"] == (("A", "사업보고서"),)
    assert engine.FILING_LOOKUP_DEFAULT == (
        ("A", "사업보고서"),
        ("F", "감사보고서"),
    )

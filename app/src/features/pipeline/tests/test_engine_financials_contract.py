# -*- coding: utf-8 -*-
"""재무 조회가 「못 물어봄」을 「자료 없음」으로 둔갑시키지 않는지 지킨다.

★ 왜 이 파일이 생겼나 (2026-08-27 · 적대 검수가 찾은 결함 D8)
  ─────────────────────────────────────────────────────────
  판정에 조건 2-b(「공개된 재무제표가 있나」)를 넣으면서 재무 조회 결과가
  «거부 여부»를 가르게 됐다. 그런데 `fetch_financials` 는 DART 상태값이
  000 이 아니면 **전부 조용히 건너뛰고** 빈 결과를 돌려주고 있었다.

  그래서 DART 시스템 점검(800)·정의되지 않은 오류(900)·오류(901) 같은
  **기술 실패가 「이 회사는 재무 자료가 없다」로 둔갑**했고, 곧바로
  「분석할 근거를 모을 수 없습니다」 화면이 됐다. 멀쩡한 회사가 거부된다.

  공시 목록 쪽은 이미 같은 원칙을 지키고 있었다(`real.py` 의 013 처리 —
  「오류 응답에는 목록이 없지만 그것은 감사보고서가 없음의 증거가 아니다」).
  재무 쪽만 안 지키고 있었다.

★ 이 시험이 못 박는 것 — **「빈 결과」와 「못 물어봄」은 다르다.**
  · 013(조회 범위에 자료 없음) → 빈 결과. 거부해도 «정직한» 거부다.
  · 그 밖의 비정상 상태       → 터진다. 거부가 아니라 실패로 다뤄야 한다.

★ 왜 여기(app/)에 두나 — CI 와 로컬이 `analysis_engine/src` 만 거두므로
  `analysis_engine/tools/` 에 두면 **아무도 안 돌린다.**
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


def _엔진_with_status(monkeypatch: pytest.MonkeyPatch, 상태들: list[str]) -> Any:
    """`get_json` 이 정해진 상태값을 차례로 돌려주는 엔진을 만든다."""
    engine = real._engine()
    남은 = list(상태들)

    def 가짜_get_json(endpoint: str, params: dict[str, Any], counter: Any) -> dict[str, Any]:
        assert endpoint == "fnlttSinglAcnt.json", f"뜻밖의 endpoint: {endpoint}"
        counter.tick()
        상태 = 남은.pop(0) if 남은 else "013"
        본문 = {"status": 상태}
        if 상태 == "000":
            본문["list"] = [{"account_nm": "자산총계", "thstrm_amount": "1"}]
        return 본문

    monkeypatch.setattr(engine, "get_json", 가짜_get_json)
    return engine


@pytest.mark.parametrize("오류상태", ["800", "900", "901", "100", "101", "021"])
def test_DART_오류는_자료없음이_아니라_실패로_터진다(
    monkeypatch: pytest.MonkeyPatch, 오류상태: str
) -> None:
    """★ 이 시험이 D8 수정의 «이유»다. 되돌리면 여기가 빨간불이 된다."""
    engine = _엔진_with_status(monkeypatch, [오류상태])

    with pytest.raises(RuntimeError):
        engine.fetch_financials("00222374", _계수기())


def test_013은_터지지_않고_빈_결과다(monkeypatch: pytest.MonkeyPatch) -> None:
    """「조회 범위에 자료 없음」은 오류가 아니다 — 이걸 터뜨리면 정상 거부가 막힌다."""
    engine = _엔진_with_status(monkeypatch, ["013", "013", "013"])

    payload, years = engine.fetch_financials("00000000", _계수기())

    assert payload is None
    assert years == []


def test_정상이면_연도가_모인다(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _엔진_with_status(monkeypatch, ["000", "000", "013"])

    payload, years = engine.fetch_financials("00222374", _계수기())

    assert payload is not None
    assert len(years) == 2


def test_한_해가_비어도_다른_해가_있으면_찾아낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    """최근 연도 사업보고서가 아직 안 나온 시기를 위해 3년을 훑는다.

    ★ 첫 해가 013 이라고 멈추면 1~3월에 조사한 회사가 통째로 거부된다.
    """
    engine = _엔진_with_status(monkeypatch, ["013", "000", "013"])

    payload, years = engine.fetch_financials("00222374", _계수기())

    assert payload is not None
    assert years == [_올해() - 2]


def test_세_해를_다_물어본다(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 실측 정정 — 「한 번 더 나간다」가 아니라 «3번» 나간다.

    무료이지만 DART 일일 호출 한도는 그만큼 줄어든다. 판정 «전»으로 옮겨
    거부될 회사도 3번을 쓰게 됐으므로 개수를 못 박아 둔다.
    """
    engine = _엔진_with_status(monkeypatch, ["013", "013", "013"])
    counter = _계수기()

    engine.fetch_financials("00000000", counter)

    assert counter.ticks == 3


def _올해() -> int:
    """엔진이 쓰는 「올해」와 같은 값 (엔진은 `dt.date.today()` 를 쓴다)."""
    import datetime as dt

    return dt.date.today().year

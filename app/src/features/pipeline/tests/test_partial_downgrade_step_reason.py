# -*- coding: utf-8 -*-
"""부분 보고서 전환 step 의 사유가 사전검사의 닫힌 코드를 그대로 싣는지 못 박는다.

★ 왜 이 시험이 있나 (2026-09-05)
  `real.py` 의 «6_수집_DART부분보고서전환» step 이 사유를 «회사 웹 경로 일시
  장애»로 박아 두고 있었다. 사전검사에 «자료 일부 부족»·«정식 문서 하한 도달
  불가» 갈래가 늘면서 그 문구는 우리은행처럼 웹 장애가 아닌 전환까지 웹 장애로
  기록하게 됐다. 사유는 사전검사(`official_evidence_preflight`)가 고른
  `dart_partial_reason` 하나만 실어야 갈래가 늘어도 운영 기록이 맞는다.

★ 왜 소스 검사인가
  이 step 은 `_run_metered` 깊숙이 있어 단위로 떼기 어렵다. 대신 «하드코딩
  문구가 다시 들어오지 않는다»와 «사전검사 필드를 읽는다»를 소스에서 확인한다
  — 저장소의 다른 계약 시험(`core/tests/test_cost_contract.py`)과 같은 방식이다.
"""

from __future__ import annotations

from pathlib import Path

from src.features.pipeline.official_evidence_preflight import (
    DART_PARTIAL_REASON_INSUFFICIENT_WITH_READY_SECTIONS,
    DART_PARTIAL_REASON_TOO_FEW_DOCUMENTS_FOR_FULL,
    DART_PARTIAL_REASON_TRANSIENT_WEB_FAILURE,
)

_REAL_PY = Path(__file__).resolve().parents[1] / "real.py"
_STEP_NAME = "6_수집_DART부분보고서전환"
_LEGACY_HARDCODED_REASON = "회사 웹 경로 일시 장애"


def _step_block() -> str:
    source = _REAL_PY.read_text(encoding="utf-8")
    start = source.index(_STEP_NAME)
    # step 딕셔너리 한 덩어리만 본다 — 닫는 중괄호까지.
    end = source.index("}", start)
    return source[start:end]


def test_전환_step은_사전검사의_닫힌_사유코드를_싣는다():
    block = _step_block()
    assert "official_preflight.dart_partial_reason" in block
    assert _LEGACY_HARDCODED_REASON not in block


def test_하드코딩_사유_문구는_real_py_어디에도_없다():
    source = _REAL_PY.read_text(encoding="utf-8")
    assert _LEGACY_HARDCODED_REASON not in source


def test_사전검사_사유코드는_세_갈래_전부_닫힌_문자열이다():
    reasons = {
        DART_PARTIAL_REASON_TRANSIENT_WEB_FAILURE,
        DART_PARTIAL_REASON_INSUFFICIENT_WITH_READY_SECTIONS,
        DART_PARTIAL_REASON_TOO_FEW_DOCUMENTS_FOR_FULL,
    }
    assert len(reasons) == 3
    assert all(reason.isascii() and " " not in reason for reason in reasons)

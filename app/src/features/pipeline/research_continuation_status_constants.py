"""보완조사 연속 상태를 공개 source status로 바꿀 때의 닫힌 어휘."""

from typing import Final

from src.features.pipeline.supplementary_research_runtime_constants import (
    SUPPLEMENTARY_RESEARCH_CONTINUE_STEP,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP,
    FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT,
)


RESEARCH_CONTINUATION_STEP: Final[str] = SUPPLEMENTARY_RESEARCH_CONTINUE_STEP
RESEARCH_CONTINUATION_REASON_KEY: Final[str] = "사유코드"

CLASSIFIER_COVERAGE_GAP_CODE: Final[str] = (
    FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP
)
OFFICIAL_EVIDENCE_INSUFFICIENT_CODE: Final[str] = (
    FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT
)

CLASSIFIER_STATUS_NAME: Final[str] = "공식 자료 사전 분류"
CLASSIFIER_STATUS_STATE: Final[str] = "failed"
CLASSIFIER_STATUS_DETAIL: Final[str] = (
    "공식 자료는 확인했지만 장별 분류가 충분하지 않아 추가 조사를 진행했습니다"
)

READINESS_STATUS_NAME: Final[str] = "공식 자료 사전 준비"
READINESS_STATUS_STATE: Final[str] = "none"
READINESS_STATUS_DETAIL: Final[str] = (
    "공식 자료 사전 준비가 장별 분석에 충분하지 않아 추가 조사를 진행했습니다"
)

"""보완조사 연속 관측을 안전한 ``SourceStatus`` 한 줄로 덧붙인다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.features.pipeline.port import SourceStatus
from src.features.pipeline.research_continuation_status_constants import (
    CLASSIFIER_COVERAGE_GAP_CODE,
    CLASSIFIER_STATUS_DETAIL,
    CLASSIFIER_STATUS_NAME,
    CLASSIFIER_STATUS_STATE,
    OFFICIAL_EVIDENCE_INSUFFICIENT_CODE,
    READINESS_STATUS_DETAIL,
    READINESS_STATUS_NAME,
    READINESS_STATUS_STATE,
    RESEARCH_CONTINUATION_REASON_KEY,
    RESEARCH_CONTINUATION_STEP,
)


def add_research_continuation_source_status(
    sources: list[SourceStatus], steps: Sequence[Mapping[str, object]],
) -> list[SourceStatus]:
    """알려진 보완조사 사유만 기존 source 목록 뒤에 안전하게 붙인다.

    보완조사 단계가 없거나 사유가 계약 밖이면 원 목록 객체를 그대로 돌린다.
    따라서 임의 오류문·URL·원문과 새 미정 코드는 외부 source status에
    직렬화되지 않는다.
    """

    continuation = next(
        (
            step
            for step in steps
            if step.get("step") == RESEARCH_CONTINUATION_STEP
        ),
        None,
    )
    if continuation is None:
        return sources

    reason_code = continuation.get(RESEARCH_CONTINUATION_REASON_KEY)
    if reason_code == CLASSIFIER_COVERAGE_GAP_CODE:
        return [
            *sources,
            SourceStatus(
                CLASSIFIER_STATUS_NAME,
                CLASSIFIER_STATUS_STATE,
                CLASSIFIER_STATUS_DETAIL,
            ),
        ]
    if reason_code == OFFICIAL_EVIDENCE_INSUFFICIENT_CODE:
        return [
            *sources,
            SourceStatus(
                READINESS_STATUS_NAME,
                READINESS_STATUS_STATE,
                READINESS_STATUS_DETAIL,
            ),
        ]
    return sources

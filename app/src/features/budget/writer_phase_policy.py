"""실제 요청이 고정한 작성 선택을 본조사 예약액으로 투영한다."""

from src.features.budget.constants import (
    PAID_PHASE_PROVIDER_BUDGET_KRW, PIPELINE_WRITER_HAIKU_MODEL,
    PIPELINE_WRITER_SONNET_MODEL, SONNET_WRITER_PIPELINE_BUDGET_KRW,
    SPEND_PHASE_PIPELINE,
)


def writer_pipeline_reservation_krw(writer_model: str, *, is_v2: bool) -> float:
    """환경은 읽지 않는다. 기본·Haiku·v1은 기존 2,000원 예약을 유지한다."""
    if type(is_v2) is not bool or type(writer_model) is not str:
        raise ValueError("작성 선택은 문자열 모델과 명시 v2 여부여야 합니다")
    if writer_model not in ("", PIPELINE_WRITER_HAIKU_MODEL, PIPELINE_WRITER_SONNET_MODEL):
        raise ValueError("본조사 예약에 허용되지 않은 작성 모델입니다")
    if is_v2 and writer_model == PIPELINE_WRITER_SONNET_MODEL:
        return SONNET_WRITER_PIPELINE_BUDGET_KRW
    return PAID_PHASE_PROVIDER_BUDGET_KRW[SPEND_PHASE_PIPELINE]

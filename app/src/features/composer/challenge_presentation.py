"""검증된 대응 설명만 있을 때 공개 범위를 정확히 표시한다."""

from src.features.composer.constants import (
    CHALLENGE_FLOW_SECTION_ID,
    CHALLENGE_RESPONSE_CLAIM_SLOT,
    GRADE_CONFIRMED,
    NOTICE_CHALLENGE_RESPONSE_ONLY,
)
from src.features.composer.port import ComposedReport


def challenge_response_only_notice(
    report: ComposedReport, *, has_industry_context: bool,
) -> str:
    """최종 본문이 확인된 대응뿐일 때만 안내한다. 사실·출처는 바꾸지 않는다."""
    if has_industry_context:
        return ""
    for section in report.sections:
        if section.section_id != CHALLENGE_FLOW_SECTION_ID:
            continue
        # 도식·보도표나 옛 의미칸 없는 문장의 문제 관계를 추정하지 않는다.
        if not section.sentences or section.flow_rows or section.news_rows:
            return ""
        if all(
            sentence.planned_claim_slot == CHALLENGE_RESPONSE_CLAIM_SLOT
            and sentence.verification_state == "verified"
            and sentence.grade == GRADE_CONFIRMED
            for sentence in section.sentences
        ):
            return NOTICE_CHALLENGE_RESPONSE_ONLY
    return ""

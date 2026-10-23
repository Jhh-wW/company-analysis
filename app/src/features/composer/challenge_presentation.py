"""검증된 대응 의미칸의 설명 범위를 표시한다."""

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
    """확인된 대응 의미칸을 안내한다. 문제의 부재나 출처 종류는 추정하지 않는다."""
    if has_industry_context:
        return ""
    for section in report.sections:
        if section.section_id != CHALLENGE_FLOW_SECTION_ID:
            continue
        # 도식·보도표나 옛 의미칸 없는 문장의 문제 관계를 추정하지 않는다.
        if not section.sentences or section.flow_rows or section.news_rows:
            return ""
        # 대응 칸도 사건과 대응을 함께 인용할 수 있다. 이 분류만으로
        # 문제가 없거나 출처가 공식 자료뿐이라고 안내해서는 안 된다.
        if all(
            sentence.planned_claim_slot == CHALLENGE_RESPONSE_CLAIM_SLOT
            and sentence.verification_state == "verified"
            and sentence.grade == GRADE_CONFIRMED
            for sentence in section.sentences
        ):
            return NOTICE_CHALLENGE_RESPONSE_ONLY
    return ""

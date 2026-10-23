"""검수한 계획 발언에 현재 상태 확인을 덧씌우지 않도록 범위를 표시한다."""

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.future_presentation_constants import (
    FUTURE_SECTION_ID, PLAN_SCOPE_MARKERS, PLAN_SCOPE_NOTICE, STATED_PLAN_SLOT,
)
from src.features.composer.port import ComposedSection


def section_guidance_lines(section: ComposedSection) -> list[str]:
    """사실·인용·승인은 그대로 두고 발표 계획의 확인 범위를 별도로 안내한다.

    명시적으로 검수된 stated_plan만 대상으로 한다. 옛 의미칸 없는 문장이나
    보도표를 보고 계획의 존재를 추측하지 않는다. 안내는 회사가 실행하지
    않았다는 주장이 아니며, 회사의 발언 안에 추가하지 않는다.
    """
    guidance = [section.notice] if section.notice else []
    if section.section_id != FUTURE_SECTION_ID:
        return guidance
    scope_markers = tuple("".join(marker.split()) for marker in PLAN_SCOPE_MARKERS)

    def has_scope(text: str) -> bool:
        return any(marker in "".join(text.split()) for marker in scope_markers)

    if any(has_scope(line) for line in guidance):
        return guidance
    if any(
        sentence.planned_claim_slot == STATED_PLAN_SLOT
        and sentence.verification_state == "verified"
        and sentence.grade == GRADE_CONFIRMED
        and sentence.citations
        and not has_scope(sentence.text)
        for sentence in section.sentences
    ):
        guidance.append(PLAN_SCOPE_NOTICE)
    return guidance

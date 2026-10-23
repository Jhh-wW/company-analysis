"""정확한 Fact에 결속된 현재 사업 설명 한 문장만 맨앞으로 배치한다."""

from collections.abc import Sequence
from typing import Protocol, TypeVar

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.identity_content import administrative_identity_only
from src.features.composer.identity_content_constants import (
    IDENTITY_SECTION_ID, IDENTITY_SENTENCE_SPLIT_RE,
)
from src.features.composer.identity_opening_constants import (
    IDENTITY_ACTUAL_FACT_STATUS,
    IDENTITY_BUSINESS_DEFINITION_SLOT,
    IDENTITY_CURRENT_TIME_STATE,
    IDENTITY_DEPENDENT_OPENING_RE,
    IDENTITY_ENTITY_DEFINITION_END_RE,
    IDENTITY_PURPOSE_RE,
    IDENTITY_VERIFIED_STATE,
)
from src.features.composer.port import ComposedSentence
from src.shared.report_quality.constants import VERIFIED_PROSE_CLAIM_TYPE


class IdentityOpeningFact(Protocol):
    """배치 판단에 필요한 기존 사실의 메타정보만 읽는다."""

    section_owner: str
    claim_slot: str
    claim_type: str
    time_state: str
    fact_status: str
    plan_status: str


FactValue = TypeVar("FactValue", bound=IdentityOpeningFact)


def identity_opening_definition_form(text: str) -> bool:
    """승인 여부와 분리된 정의문 형식 검사. 새 승인이나 현재성을 만들지 않는다."""

    return (
        bool(IDENTITY_ENTITY_DEFINITION_END_RE.search(text.strip()))
        and not IDENTITY_SENTENCE_SPLIT_RE.search(text.strip())
        and not administrative_identity_only(text)
        and not IDENTITY_PURPOSE_RE.search(text)
        and not IDENTITY_DEPENDENT_OPENING_RE.search(text.lstrip())
    )


def identity_opening_sentence_eligible(sentence: ComposedSentence) -> bool:
    """첫 문장으로 독립해서 읽을 수 있는 승인된 사업 설명인지 확인한다.

    현재성이나 Fact 결속 판정은 포함하지 않는다. 공개 순서 선택에는 반드시
    ``identity_opening_candidate``의 Fact 조건도 함께 적용한다.
    """

    return (
        sentence.grade == GRADE_CONFIRMED
        and sentence.verification_state == IDENTITY_VERIFIED_STATE
        and sentence.planned_claim_slot == IDENTITY_BUSINESS_DEFINITION_SLOT
        and identity_opening_definition_form(sentence.text)
    )


def identity_opening_candidate(sentence: ComposedSentence, fact: IdentityOpeningFact) -> bool:
    """공개 결속 후의 문장·Fact가 현재 사업 첫 문장 배치 조건을 만족하는가."""

    return (
        identity_opening_sentence_eligible(sentence)
        and fact.section_owner == IDENTITY_SECTION_ID
        and fact.claim_slot == IDENTITY_BUSINESS_DEFINITION_SLOT
        and fact.claim_type == VERIFIED_PROSE_CLAIM_TYPE
        and fact.time_state == IDENTITY_CURRENT_TIME_STATE
        and fact.fact_status == IDENTITY_ACTUAL_FACT_STATUS
        and not fact.plan_status
    )


def prioritize_identity_opening(
    pairs: Sequence[tuple[ComposedSentence, FactValue]],
) -> tuple[tuple[ComposedSentence, FactValue], ...]:
    """승인된 객체를 그대로 이동한다. 적합한 문장이 없으면 원래 순서를 지킨다.

    호출자는 공개 결속 검사를 끝낸 문장과 그 검사가 확정한 Fact만 전달한다.
    여기서는 신규 문장·승인·Fact를 만들거나 누락된 현재 사업을 추정하지 않는다.
    """

    original = tuple(pairs)
    for index, (sentence, fact) in enumerate(original):
        if not identity_opening_candidate(sentence, fact):
            continue
        if index:
            return (original[index], *original[:index], *original[index + 1:])
        break
    return original

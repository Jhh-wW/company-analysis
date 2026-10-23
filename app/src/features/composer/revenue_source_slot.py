"""검수 전, 실제 수익원 선언을 자기 근거가 지원하는 수익모델 칸에 둔다."""
from collections.abc import Mapping
from dataclasses import replace

from src.features.composer.port import CollectedFragment, ComposedSentence
from src.shared.report_evidence.business_slot_scope import (
    business_slot_scope_problem, revenue_source_declared,
)
from src.shared.report_evidence.business_slot_scope_constants import REVENUE_SLOT, VALUE_EXCHANGE_SLOT


def normalize_revenue_source_slot(
    sentence: ComposedSentence, fragments: Mapping[str, CollectedFragment],
) -> ComposedSentence:
    """본문·인용을 바꾸거나 검수 후 승인을 다른 슬롯에 재사용하지 않는다."""
    if (sentence.verification_state != "unverified" or sentence.structured_claim is not None
            or sentence.planned_claim_slot != VALUE_EXCHANGE_SLOT
            or not revenue_source_declared(sentence.text)
            or not business_slot_scope_problem(sentence.text, VALUE_EXCHANGE_SLOT)
            or not sentence.citations or any(citation not in fragments for citation in sentence.citations)):
        return sentence
    if not any(
        REVENUE_SLOT in fragments[citation].supported_claim_slots
        and revenue_source_declared(fragments[citation].text)
        for citation in sentence.citations
    ):
        return sentence
    return replace(sentence, planned_claim_slot=REVENUE_SLOT)

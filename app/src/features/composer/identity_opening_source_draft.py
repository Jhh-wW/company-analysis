"""공식 현재 정산 관계를 첫 소개의 미검증 후보 한 문장으로만 옮긴다."""

from collections.abc import Sequence

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.empty_section_recovery import recovery_evidence
from src.features.composer.identity_content_constants import IDENTITY_SECTION_ID
from src.features.composer.identity_opening_constants import IDENTITY_BUSINESS_DEFINITION_SLOT
from src.features.composer.identity_opening_source_draft_constants import (
    IDENTITY_SETTLEMENT_DRAFT_TEMPLATE,
    IDENTITY_SETTLEMENT_SOURCE_RE,
    IDENTITY_SOURCE_UNCERTAIN_RE,
)
from src.features.composer.port import CollectedFragment, ComposedSentence
from src.features.composer.settlement_candidate_constants import SETTLEMENT_SEGMENT_RE
from src.features.composer.settlement_role_constants import SETTLEMENT_NONACTUAL_RE
from src.shared.report_evidence.practice_context_constants import HYPOTHETICAL_MARKER_RE


def draft_identity_opening_from_sources(
    fragments: Sequence[CollectedFragment],
) -> ComposedSentence | None:
    """복구 자격과 현재 완결 정산 문형을 모두 만족하는 첫 근거만 인용한다.

    원문·문맥·기존 승인 객체는 바꾸지 않는다. 반환값은 새 Fact나 승인이 아니며,
    호출자가 기존 독립 검수와 공개 결속 검사를 그대로 수행해야 한다.
    """

    eligible = recovery_evidence(fragments).get(IDENTITY_SECTION_ID, ())
    for fragment in eligible:
        if (IDENTITY_BUSINESS_DEFINITION_SLOT not in fragment.supported_claim_slots
                or fragment.practice_context_json):
            continue
        context = "".join((fragment.text, fragment.section_context_json,
                           fragment.source_context_json))
        compact_context = "".join(context.split())
        if (IDENTITY_SOURCE_UNCERTAIN_RE.search(compact_context)
                or SETTLEMENT_NONACTUAL_RE.search(compact_context)
                or HYPOTHETICAL_MARKER_RE.search(context)):
            continue
        for segment in SETTLEMENT_SEGMENT_RE.finditer(fragment.text):
            match = IDENTITY_SETTLEMENT_SOURCE_RE.fullmatch("".join(segment[0].split()))
            if match is None or not match["business"]:
                continue
            return ComposedSentence(
                IDENTITY_SETTLEMENT_DRAFT_TEMPLATE.format(business=match["business"]),
                (fragment.fragment_id,), GRADE_CONFIRMED,
                planned_claim_slot=IDENTITY_BUSINESS_DEFINITION_SLOT,
            )
    return None

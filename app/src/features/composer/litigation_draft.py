"""자료 부재 논평에 섞인 기말 소송 사실을 새 검수 후보로만 분리한다."""
from dataclasses import replace
import hashlib
import logging

from src.features.composer import litigation_draft_constants as c
from src.features.composer.absence_claim_guard import absence_claim_problem
from src.features.composer.grounding_constants import (
    LITIGATION_CURRENT_COMPANY_RE, LITIGATION_ROLE_COUNT_RE,
)
from src.features.composer.litigation_timing import litigation_period_context, litigation_snapshot_date
from src.features.composer.litigation_timing_constants import AS_OF_RE, CURRENT_LITIGATION_CLAUSE_RE
from src.shared.company_identity import exact_company_names_equivalent

logger = logging.getLogger(__name__)


def prepare_litigation_draft(sentence, fragments_by_id, *, company_name=""):
    """원문과 대조한 선행 사실만 남긴다. 검수 상태·근거·슬롯은 바꾸지 않는다."""
    if (sentence.planned_claim_slot != c.LITIGATION_DRAFT_SLOT
            or sentence.verification_state != "unverified"
            or sentence.structured_claim is not None or sentence.verified_fact_id
            or sentence.news_translation_receipt
            or len(sentence.citations) != c.LITIGATION_DRAFT_CITATION_COUNT
            or not absence_claim_problem(sentence.text)
            or c.LITIGATION_DRAFT_NONACTUAL_RE.search(sentence.text)):
        return sentence
    fragment = fragments_by_id.get(sentence.citations[0])
    if (fragment is None or not litigation_period_context(fragment)
            or not fragment.source_document_id or not fragment.source_publisher
            or not exact_company_names_equivalent(company_name, fragment.source_publisher)
            or c.LITIGATION_DRAFT_NONACTUAL_RE.search(fragment.text)):
        return sentence
    dated = AS_OF_RE.match(sentence.text)
    match = c.LITIGATION_DRAFT_FACT_RE.fullmatch(sentence.text, dated.end()) if dated else None
    if not match or not absence_claim_problem(match['comment']):
        return sentence
    text = sentence.text[:match.end('fact')] + "이다."
    sources = ((fragment.text, fragment.reporting_period, fragment.source_publisher),)
    if (not litigation_snapshot_date(text, sources)
            or not litigation_snapshot_date(sentence.text, sources)):
        return sentence
    clauses = tuple(CURRENT_LITIGATION_CLAUSE_RE.finditer(fragment.text))
    if len(clauses) != c.LITIGATION_DRAFT_CITATION_COUNT:
        return sentence
    clause = clauses[0].group()
    source_count = LITIGATION_ROLE_COUNT_RE.search(clause)
    if (not LITIGATION_CURRENT_COMPANY_RE.match(clause) or not source_count
            or source_count['role'] != match['role'] or source_count['count'] != match['count']):
        return sentence
    source_end = c.LITIGATION_DRAFT_SOURCE_END_RE.fullmatch(clause, source_count.end())
    if not source_end:
        return sentence
    prepared = replace(sentence, text=text)
    # 기존 날짜·수치 대조를 재사용할 뿐, 이 검사는 의미 검수의 참 판정이 아니다.
    from src.features.composer.verify import _numeric_disposal, NUMERIC_PASS
    if _numeric_disposal(prepared, fragments_by_id, ()) != NUMERIC_PASS:
        return sentence
    if match['amount']:
        # 다른 표의 같은 숫자를 소송금액으로 빌리지 않는다.
        if not source_end['amount']:
            return sentence
        from src.features.composer.verify import _extract_numbers, _evidence_number_pools, _number_matches_by_math
        candidate_numbers = _extract_numbers(match['amount'])
        source_values = _evidence_number_pools((source_end['amount'],))[1]
        if len(candidate_numbers) != c.LITIGATION_DRAFT_CITATION_COUNT or not _number_matches_by_math(candidate_numbers[0], source_values):
            return sentence
    logger.info("검수 전 소송 후보 분리: 사유 %s, 조각 %s, 원후보지문 %s, 새후보지문 %s",
                c.LITIGATION_DRAFT_REASON, fragment.fragment_id,
                hashlib.sha256(sentence.text.encode()).hexdigest(),
                hashlib.sha256(text.encode()).hexdigest())
    return prepared

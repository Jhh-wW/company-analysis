"""포트폴리오의 수익 연결은 실제 서비스 선언으로만 보완하고 다시 검수한다."""
from collections.abc import Sequence
import hashlib
import logging

from src.features.composer.accounting_policy_guard import accounting_policy_problem
from src.features.composer.port import CollectedFragment, ComposedSentence
from src.features.composer import portfolio_revenue_content_constants as c
from src.shared.report_evidence.constants import FORMAL_DOCUMENT_SOURCE_KINDS
from src.features.composer.service_revenue_declaration import service_revenue_declaration_spans
from src.shared.report_evidence.practice_context_constants import HYPOTHETICAL_MARKER_RE

logger = logging.getLogger(__name__)


def portfolio_accounting_only_problem(text: str) -> str:
    """명시한 순수 정책 문법만 제외하며 모르는 추가 사업 절은 보존한다."""
    if accounting_policy_problem(text, section_id="portfolio"):
        return c.PORTFOLIO_ACCOUNTING_ONLY
    surface = "".join(text.split())
    for pattern in c.PORTFOLIO_ACCOUNTING_PATTERNS:
        if pattern.fullmatch(surface):
            prefix = surface.split("서비스", 1)[0] if "서비스" in surface else ""
            if not c.PORTFOLIO_REAL_ACTIVITY_PREFIX_RE.search(prefix):
                return c.PORTFOLIO_ACCOUNTING_ONLY
    return ""


def _declaration_key(text: str) -> str:
    return c.PORTFOLIO_DECLARATION_ENDING_RE.sub("인식", "".join(text.split()))


def _canonical_metadata_ready(fragment: CollectedFragment) -> bool:
    # 승인이 아니다. 정본을 생산할 수 있는 typed 원문만 후보로 고른다.
    # 실제 Source 도장·회사 신원·원문 결속은 기존 최종 검증기가 그대로 검사한다.
    return bool(
        fragment.formal_source_kind in FORMAL_DOCUMENT_SOURCE_KINDS
        and fragment.document_identity and fragment.document_content_sha256
        and fragment.identity_binding and fragment.source_document_id
        and fragment.source_url and fragment.document_date
        and c.PORTFOLIO_REVENUE_SLOT in fragment.supported_claim_slots
    )


def supplement_portfolio_revenue_source(
    sentences: Sequence[ComposedSentence], fragments: Sequence[CollectedFragment],
) -> tuple[ComposedSentence, ...]:
    """원문 후보를 검수 전에만 최대 한 개 추가한다. 기존 승인은 재사용하지 않는다."""
    result = tuple(sentences)
    if any(sentence.verification_state != "unverified" or sentence.structured_claim is not None
           or sentence.verified_fact_id for sentence in result):
        return result
    existing = {
        _declaration_key(sentence.text[start:end])
        for sentence in result if sentence.planned_claim_slot == c.PORTFOLIO_REVENUE_SLOT
        for start, end in service_revenue_declaration_spans(sentence.text)
    }
    added = 0
    for fragment in sorted(fragments, key=lambda value: value.document_date, reverse=True):
        if not _canonical_metadata_ready(fragment):
            continue
        # 보조 후보에 한해 예시 문맥이 조금이라도 남으면 추가하지 않는다.
        # 기존 작성 후보·교육 문맥의 다른 실제 사실을 삭제하는 판정은 아니다.
        if fragment.practice_context_json or HYPOTHETICAL_MARKER_RE.search(fragment.text):
            continue
        for start, end in service_revenue_declaration_spans(fragment.text):
            exact_text = fragment.text[start:end]
            if _declaration_key(exact_text) in existing:
                return result
            result += (ComposedSentence(
                exact_text, (fragment.fragment_id,), "확인",
                planned_claim_slot=c.PORTFOLIO_REVENUE_SLOT,
            ),)
            logger.info("검수 전 원문 후보 보완: 사유 %s, 조각 %s, 후보지문 %s",
                        c.PORTFOLIO_SOURCE_CANDIDATE_REASON, fragment.fragment_id,
                        hashlib.sha256(exact_text.encode("utf-8")).hexdigest())
            added += 1
            if added >= c.PORTFOLIO_SOURCE_CANDIDATE_LIMIT:
                return result
    return result

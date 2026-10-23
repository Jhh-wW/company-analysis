"""현재 공시의 완결 정산 관계를 미검수 후보로만 보완한다."""
from collections.abc import Sequence
import hashlib
import logging
import re

from src.features.composer import settlement_candidate_constants as c
from src.features.composer.constants import OPERATIONS_FLOW_MAX_ROWS
from src.features.composer.port import CollectedFragment, ComposedSentence, FlowRow
from src.features.composer.settlement_role_constants import (
    SETTLEMENT_CANDIDATE_TEMPLATE, SETTLEMENT_NONACTUAL_RE,
    SETTLEMENT_SOURCE_TEMPLATE, SETTLEMENT_STATEMENT_RE,
)
from src.shared.report_evidence.constants import FORMAL_DOCUMENT_SOURCE_KINDS
from src.shared.report_evidence.practice_context_constants import HYPOTHETICAL_MARKER_RE

logger = logging.getLogger(__name__)


def _surface(text: str) -> str:
    return "".join(text.split())


def _source_statement(text: str) -> tuple[str, str] | None:
    """닫힌 공시 절의 제목만 빼며, 반환 산문은 원문 바이트 그대로다."""
    if SETTLEMENT_NONACTUAL_RE.search(text) or HYPOTHETICAL_MARKER_RE.search(text):
        return None
    pattern = re.compile(SETTLEMENT_SOURCE_TEMPLATE.format(target="가맹점"))
    for segment in c.SETTLEMENT_SEGMENT_RE.finditer(text):
        original = segment[0]
        positions = tuple(i for i, char in enumerate(original) if not char.isspace())
        compact = "".join(original[i] for i in positions)
        if not pattern.fullmatch(compact):
            continue
        heading = c.SETTLEMENT_HEADING_RE.match(compact)
        start = heading.end() if heading else 0
        statement = original[positions[start]:].strip()
        match = SETTLEMENT_STATEMENT_RE.fullmatch(_surface(statement))
        if match:
            return statement, match["business"]
    return None


def supplement_settlement_candidates(
    sentences: Sequence[ComposedSentence], rows: Sequence[FlowRow],
    fragments: Sequence[CollectedFragment],
) -> tuple[tuple[ComposedSentence, ...], tuple[FlowRow, ...]]:
    """정본 메타·원문·자기 슬롯이 있는 최대 한 관계를 검수 전에만 추가한다."""
    result, flow = tuple(sentences), tuple(rows)
    if (any(s.verification_state != "unverified" or s.structured_claim is not None
            or s.verified_fact_id for s in result)
            or any(row.review_binding is not None for row in flow)):
        return result, flow
    compact_flow = re.compile(SETTLEMENT_CANDIDATE_TEMPLATE.format(target="가맹점"))
    added = 0
    for fragment in sorted(fragments, key=lambda item: item.document_date, reverse=True):
        if not (fragment.formal_source_kind in FORMAL_DOCUMENT_SOURCE_KINDS
                and fragment.document_identity and fragment.document_content_sha256
                and fragment.identity_binding and fragment.source_document_id
                and fragment.source_url and fragment.document_date
                and c.SETTLEMENT_SLOT in fragment.supported_claim_slots
                and not fragment.practice_context_json):
            continue
        found = _source_statement(fragment.text)
        if found is None:
            continue
        statement, business = found
        prose_present = any(fragment.fragment_id in s.citations
                            and SETTLEMENT_STATEMENT_RE.fullmatch(_surface(s.text))
                            for s in result)
        flow_present = any(
            fragment.fragment_id in row.citations and not any(row.cells[-1:])
            and (compact_flow.fullmatch(_surface(cell)) or SETTLEMENT_STATEMENT_RE.fullmatch(_surface(cell)))
            for row in flow for cell in row.cells
        )
        if not prose_present:
            result += (ComposedSentence(statement, (fragment.fragment_id,), "확인",
                                        planned_claim_slot=c.SETTLEMENT_SLOT),)
        if not flow_present and len(flow) < OPERATIONS_FLOW_MAX_ROWS:
            flow += (FlowRow(("", business, c.SETTLEMENT_FLOW_ACTION, ""), (fragment.fragment_id,)),)
        if len(result) != len(sentences) or len(flow) != len(rows):
            logger.info("검수 전 원문 후보 보완: 사유 %s, 조각 %s, 원문지문 %s",
                        c.SETTLEMENT_CANDIDATE_REASON, fragment.fragment_id,
                        hashlib.sha256(statement.encode("utf-8")).hexdigest())
        added += 1
        if added >= c.SETTLEMENT_CANDIDATE_LIMIT:
            break
    return result, flow

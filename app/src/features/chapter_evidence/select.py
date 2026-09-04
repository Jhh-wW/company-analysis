"""한 장에 넣을 근거 조각을 고르고 문자·토큰 예산 안으로 자른다.

우선순위는 두 단계다.
1) 슬롯 커버리지 — 정책이 정한 순서로 각 수집 슬롯의 최고점 조각을 먼저 담는다.
   슬롯 하나가 예산 때문에 통째로 비지 않도록 하기 위함이다.
2) 남는 예산 — 아직 못 담은 조각을 점수(score_millis) 내림차순으로 채운다.

⚠️ 설계상 한계 — 슬롯 대표 조각들의 합이 그 자체로 예산을 넘을 만큼 크면
(예: 슬롯마다 몇천자짜리 대표 조각), 이 구현은 «예산을 어겨서라도 대표를
지키는» 대신 정책 순서상 뒤에 오는 슬롯의 대표를 budget_truncated로 남기고
포기한다 — ChapterEvidenceCandidates 계약 자체가 문자 합계 상한을 어기면
생성 즉시 거부하기 때문에 이 규칙은 어길 수 없다. DEFAULT_MAX_CHARS_PER_SECTION
은 이 경우가 실무에서 드물도록 여유 있게 잡았다(constants.py 주석 참조).

정책: 자격 없는 조각은 조용히 필터링하고(예외로 죽이지 않고) 전용 사유
코드를 남긴다(fail-closed, quiet-filter) — produce.py의 attempt 필터링과
같은 정책이다. 이유: 수집기 한 건의 실수(엉뚱한 company_id·해시)가 그
회사의 아홉 장 생산 전체를 죽이면 안 된다. 조각의 자격은 두 겹으로 확인
한다 — 1층 company_id 결속(generation=8), 2층 exact_evidence_hashes
결속(generation=7). 한쪽 방어가 뚫려도(예: company_id는 맞는데 문서
충돌) 다른 쪽이 혼자 잡을 수 있도록 층을 분리해 순서대로 확인한다.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace

from src.features.chapter_evidence.constants import (
    CHARS_PER_ESTIMATED_TOKEN,
    DEFAULT_MAX_CHARS_PER_SECTION,
    DEFAULT_MAX_ESTIMATED_TOKENS_PER_SECTION,
)
from src.shared.report_evidence.models import CollectedEvidenceDocument, EvidenceFragment
from src.shared.report_evidence.constants import (
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    SOURCE_KIND_OFFICIAL_IR_PDF,
)
from src.shared.report_evidence.policy import collector_slots_for
from src.shared.report_evidence.source_kind_policy import (
    formal_document_is_writer_eligible,
)


@dataclass(frozen=True)
class SectionFragmentSelection:
    """한 장에 담기로 확정한 근거 조각과 그 예산 사용량."""

    fragments: tuple[EvidenceFragment, ...]
    estimated_tokens: int
    reason_codes: tuple[str, ...]


def _estimated_tokens(char_count: int) -> int:
    return math.ceil(char_count / CHARS_PER_ESTIMATED_TOKEN)


def _dedupe_by_evidence_range(
    fragments: Iterable[EvidenceFragment],
) -> tuple[list[EvidenceFragment], int]:
    """같은 문서·원문 위치를 ID 하나로 합치고 슬롯 커버리지만 합집합한다.

    같은 문단을 revenue_model·customer_type 슬롯마다 복제하면 AI 입력과
    토큰 예산이 같은 원문을 여러 번 센다. 문서+location+원문 해시가 같은
    조각은 최고점 ID 하나로 합치되 ``covered_slot_ids``를 보존한다.
    """

    by_evidence_range: dict[tuple[str, str, str], list[EvidenceFragment]] = defaultdict(list)
    for fragment in fragments:
        by_evidence_range[
            (fragment.document_id, fragment.location, fragment.text_sha256)
        ].append(fragment)

    kept: list[EvidenceFragment] = []
    duplicate_count = 0
    for items in by_evidence_range.values():
        items.sort(key=lambda fragment: (-fragment.score_millis, fragment.fragment_id))
        primary = items[0]
        covered_slot_ids = tuple(
            dict.fromkeys(
                slot_id
                for item in items
                for slot_id in item.covered_slot_ids
            )
        )
        reason_codes = tuple(
            dict.fromkeys(
                reason_code
                for item in items
                for reason_code in item.reason_codes
            )
        )
        kept.append(
            replace(
                primary,
                covered_slot_ids=covered_slot_ids,
                reason_codes=reason_codes,
            )
        )
        duplicate_count += len(items) - 1
    return kept, duplicate_count


def select_section_fragments(
    *,
    section_id: str,
    company_id: str,
    documents: tuple[CollectedEvidenceDocument, ...],
    fragments: tuple[EvidenceFragment, ...],
    max_chars: int = DEFAULT_MAX_CHARS_PER_SECTION,
    max_estimated_tokens: int = DEFAULT_MAX_ESTIMATED_TOKENS_PER_SECTION,
) -> SectionFragmentSelection:
    """이 장·이 회사에 쓸 근거 조각을 골라 예산 안으로 자른다."""

    own_documents_by_id = {
        document.document_id: document
        for document in documents
        if document.company_id == company_id
    }
    collector_slot_order = collector_slots_for(section_id)
    collector_slot_set = set(collector_slot_order)

    eligible: list[EvidenceFragment] = []
    company_mismatch_count = 0
    missing_document_count = 0
    unbound_count = 0
    low_trust_ir_count = 0
    low_trust_external_page_count = 0
    for fragment in fragments:
        if fragment.section_id != section_id:
            continue
        eligible_slot_ids = tuple(
            slot_id
            for slot_id in fragment.covered_slot_ids
            if slot_id in collector_slot_set
        )
        if not eligible_slot_ids:
            continue
        # generation=8 결속 방어(fail-closed), 1층 — 조각 자신의 company_id가
        # 대상 회사와 다르면 document_id가 우연히 겹쳐도(수집기 버그) 여기서
        # 먼저 걸러낸다. exact_evidence_hashes 결속(2층)과 방어 층을 분리해,
        # 한쪽이 뚫려도 다른 쪽이 혼자 잡을 수 있게 한다.
        if fragment.company_id != company_id:
            company_mismatch_count += 1
            continue
        document = own_documents_by_id.get(fragment.document_id)
        if document is None:
            # 자료가 실제로 없는 것과 수집기가 조각의 원본 문서를 전달하지
            # 않은 내부 배선 오류는 다르다. 조각을 fail-closed로 제외하되
            # 후단 preflight가 내부 오류로 분류할 수 있게 흔적을 남긴다.
            missing_document_count += 1
            continue
        if not formal_document_is_writer_eligible(document):
            # 공식 HTML exact-link 외부 첨부는 provenance 후보일 뿐이다.
            # CDN 자료가 필수 슬롯을 채우는 근거로 승격되지 않게 통합
            # 경계에서도 한 번 더 fail-closed 한다.
            if document.source_kind == SOURCE_KIND_OFFICIAL_IR_PDF:
                low_trust_ir_count += 1
            else:
                low_trust_external_page_count += 1
            continue
        # generation=7 결속 방어(fail-closed), 2층 — 조각의 text_sha256이
        # 원본 문서의 exact_evidence_hashes 허용 목록에 없으면 여기서
        # 걸러낸다. 안 그러면 ChapterEvidenceCandidates 생성 시 계약이
        # 예외를 던져 이 회사 전체 생산이 죽는다.
        if fragment.text_sha256 not in document.exact_evidence_hashes:
            unbound_count += 1
            continue
        eligible.append(
            replace(
                fragment,
                slot_id=(
                    fragment.slot_id
                    if fragment.slot_id in eligible_slot_ids
                    else eligible_slot_ids[0]
                ),
                covered_slot_ids=eligible_slot_ids,
            )
        )

    deduped, duplicate_count = _dedupe_by_evidence_range(eligible)

    by_slot: dict[str, list[EvidenceFragment]] = defaultdict(list)
    for fragment in deduped:
        for slot_id in fragment.covered_slot_ids:
            if slot_id in collector_slot_set:
                by_slot[slot_id].append(fragment)
    for items in by_slot.values():
        items.sort(key=lambda fragment: (-fragment.score_millis, fragment.fragment_id))

    included: list[EvidenceFragment] = []
    included_ids: set[str] = set()
    # 대표 선정에서 이미 «오버사이즈»로 사유를 남긴 조각은 2단계에서 다시 보지
    # 않는다 — 안 그러면 fragment_exceeds_budget과 budget_truncated_fragments가
    # 같은 조각을 두 번 세게 된다.
    excluded_ids: set[str] = set()
    char_total = 0
    token_total = 0
    oversized_slots: list[str] = []
    dropped_for_budget = 0

    # 1단계: 슬롯 커버리지 — 정책 순서로 슬롯마다 최고점 대표 조각을 먼저 담는다.
    for slot_id in collector_slot_order:
        candidates = by_slot.get(slot_id, [])
        if not candidates:
            continue
        representative = candidates[0]
        if representative.fragment_id in included_ids:
            # 이미 앞 슬롯에서 같은 원문 ID를 넣었다. 현재 슬롯 커버리지는
            # covered_slot_ids로 보존되므로 입력·예산에는 다시 더하지 않는다.
            continue
        cost_chars = len(representative.text)
        cost_tokens = _estimated_tokens(cost_chars)
        if cost_chars > max_chars or cost_tokens > max_estimated_tokens:
            oversized_slots.append(slot_id)
            excluded_ids.add(representative.fragment_id)
            continue
        if (
            char_total + cost_chars > max_chars
            or token_total + cost_tokens > max_estimated_tokens
        ):
            dropped_for_budget += 1
            excluded_ids.add(representative.fragment_id)
            continue
        included.append(representative)
        included_ids.add(representative.fragment_id)
        char_total += cost_chars
        token_total += cost_tokens

    # 2단계: 남는 예산을 점수 내림차순으로 채운다.
    remaining = sorted(
        (
            fragment
            for fragment in deduped
            if fragment.fragment_id not in included_ids
            and fragment.fragment_id not in excluded_ids
        ),
        key=lambda fragment: (-fragment.score_millis, fragment.fragment_id),
    )
    for fragment in remaining:
        cost_chars = len(fragment.text)
        cost_tokens = _estimated_tokens(cost_chars)
        if cost_chars > max_chars or cost_tokens > max_estimated_tokens:
            # 대표가 아니었던(2등 이후) 조각이 단독으로 예산을 넘는 경우다.
            # 슬롯 자체는 이미 대표가 있거나 대표 부재를 별도로 기록했으므로,
            # 여기서는 일반 예산 절단 건수로 흡수한다.
            dropped_for_budget += 1
            continue
        if (
            char_total + cost_chars > max_chars
            or token_total + cost_tokens > max_estimated_tokens
        ):
            dropped_for_budget += 1
            continue
        included.append(fragment)
        included_ids.add(fragment.fragment_id)
        char_total += cost_chars
        token_total += cost_tokens

    reason_codes: list[str] = []
    if duplicate_count:
        reason_codes.append(f"duplicate_fragments_removed:{duplicate_count}")
    if company_mismatch_count:
        reason_codes.append(f"fragment_company_mismatch:{company_mismatch_count}")
    if missing_document_count:
        reason_codes.append(f"fragment_document_missing:{missing_document_count}")
    if unbound_count:
        reason_codes.append(f"fragment_not_bound_to_document:{unbound_count}")
    if low_trust_ir_count:
        reason_codes.append(f"low_trust_ir_fragment_ignored:{low_trust_ir_count}")
    if low_trust_external_page_count:
        reason_codes.append(
            "low_trust_external_page_fragment_ignored:"
            f"{low_trust_external_page_count}"
        )
    for slot_id in oversized_slots:
        reason_codes.append(f"fragment_exceeds_budget:{slot_id}")
    if dropped_for_budget:
        reason_codes.append(f"budget_truncated_fragments:{dropped_for_budget}")

    included.sort(key=lambda fragment: fragment.fragment_id)
    return SectionFragmentSelection(
        fragments=tuple(included),
        estimated_tokens=token_total,
        reason_codes=tuple(reason_codes),
    )

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
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from src.features.chapter_evidence.constants import (
    CHARS_PER_ESTIMATED_TOKEN,
    DEFAULT_MAX_CHARS_PER_SECTION,
    DEFAULT_MAX_ESTIMATED_TOKENS_PER_SECTION,
)
from src.shared.report_evidence.models import CollectedEvidenceDocument, EvidenceFragment
from src.shared.report_evidence.policy import collector_slots_for


@dataclass(frozen=True)
class SectionFragmentSelection:
    """한 장에 담기로 확정한 근거 조각과 그 예산 사용량."""

    fragments: tuple[EvidenceFragment, ...]
    estimated_tokens: int
    reason_codes: tuple[str, ...]


def _estimated_tokens(char_count: int) -> int:
    return math.ceil(char_count / CHARS_PER_ESTIMATED_TOKEN)


def _dedupe_by_text_hash(
    fragments: Iterable[EvidenceFragment],
) -> tuple[list[EvidenceFragment], int]:
    """같은 원문(text_sha256)을 가진 조각 중 최고점 하나만 남긴다."""

    by_hash: dict[str, list[EvidenceFragment]] = defaultdict(list)
    for fragment in fragments:
        by_hash[fragment.text_sha256].append(fragment)

    kept: list[EvidenceFragment] = []
    duplicate_count = 0
    for items in by_hash.values():
        items.sort(key=lambda fragment: (-fragment.score_millis, fragment.fragment_id))
        kept.append(items[0])
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

    own_document_ids = {
        document.document_id
        for document in documents
        if document.company_id == company_id
    }
    collector_slot_order = collector_slots_for(section_id)
    collector_slot_set = set(collector_slot_order)

    eligible = [
        fragment
        for fragment in fragments
        if fragment.section_id == section_id
        and fragment.slot_id in collector_slot_set
        and fragment.document_id in own_document_ids
    ]

    deduped, duplicate_count = _dedupe_by_text_hash(eligible)

    by_slot: dict[str, list[EvidenceFragment]] = defaultdict(list)
    for fragment in deduped:
        by_slot[fragment.slot_id].append(fragment)
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

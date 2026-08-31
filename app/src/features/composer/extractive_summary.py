"""검증된 본문 사실만 글자 그대로 재사용하는 핵심 요약 선택기.

요약을 다시 AI에게 쓰게 하면 본문에 없던 원인·전망이 새로 생기고, 작성과
검수 호출도 각각 한 번 더 든다. 이 모듈은 이미 정확한 원문에 결속된 본문
문장만 골라 0장 요약으로 재사용한다. 문장 본문·인용·등급은 바꾸지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final, Sequence

from src.features.composer.constants import GRADE_CONFIRMED, GRADE_INTERPRETED
from src.features.composer.port import ComposedReport, ComposedSentence
from src.features.pipeline.port import FactRecord
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_quality.evidence_support import prose_evidence_support_ready
from src.shared.report_quality.fact_binding import fact_evidence_binding


SUMMARY_MIN_FACTS: Final[int] = 3
SUMMARY_MAX_FACTS: Final[int] = 5

# 지원동기를 쓰는 독자에게 먼저 필요한 순서다. 회사가 무엇으로 돈을 벌고,
# 무엇을 팔며, 지금 무엇을 풀고, 다음에 무엇을 하려는지, 무엇이 다른지를
# 우선한다. 자료가 없을 때만 나머지 장으로 넓힌다.
SUMMARY_SECTION_PRIORITY: Final[tuple[str, ...]] = (
    "business_model",
    "portfolio",
    "current_challenges",
    "future_strategy",
    "competitive_position",
    "identity",
    "past_changes",
    "operations_partners",
    "culture",
)


@dataclass(frozen=True)
class ExtractiveSummaryItem:
    """본문 문장 하나와 그 문장을 이미 잠근 원자 사실 ID."""

    section_id: str
    sentence: ComposedSentence
    fact_id: str


@dataclass(frozen=True)
class ExtractiveSummary:
    """추가 생성 없이 고른 요약과 엄격 출고 가능 여부."""

    items: tuple[ExtractiveSummaryItem, ...]

    @property
    def sentences(self) -> tuple[ComposedSentence, ...]:
        return tuple(item.sentence for item in self.items)

    @property
    def fact_ids(self) -> tuple[str, ...]:
        return tuple(item.fact_id for item in self.items)

    @property
    def bound_sentences(self) -> tuple[ComposedSentence, ...]:
        """공개 글자는 그대로 두고 검증 사실 ID만 프로그램이 덧붙인다."""

        return tuple(
            replace(item.sentence, verified_fact_id=item.fact_id)
            for item in self.items
        )

    @property
    def section_ids(self) -> tuple[str, ...]:
        return tuple(item.section_id for item in self.items)

    @property
    def release_ready(self) -> bool:
        return (
            SUMMARY_MIN_FACTS <= len(self.items) <= SUMMARY_MAX_FACTS
            and len(set(self.section_ids)) >= SUMMARY_MIN_FACTS
        )


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _fact_key(
    *, section_id: str, claim: str, claim_slot: str
) -> tuple[str, str, str]:
    return (section_id, _normalized_text(claim), claim_slot.strip())


def _verified_fact_registry(
    facts: Sequence[FactRecord],
) -> tuple[dict[str, FactRecord], dict[tuple[str, str, str], FactRecord]]:
    """손상·중복 사실을 요약 재료에서 fail-closed로 제외한다."""

    by_id: dict[str, FactRecord] = {}
    duplicate_ids: set[str] = set()
    by_key: dict[tuple[str, str, str], FactRecord] = {}
    duplicate_keys: set[tuple[str, str, str]] = set()
    for fact in facts:
        fact_id = fact.fact_id.strip()
        section_id = fact.section_owner.strip()
        claim_slot = fact.claim_slot.strip()
        key = _fact_key(
            section_id=section_id,
            claim=fact.claim,
            claim_slot=claim_slot,
        )
        if (
            not fact_id
            or section_id not in CLAIM_SLOTS_BY_SECTION
            or claim_slot not in CLAIM_SLOTS_BY_SECTION[section_id]
            or not key[1]
            or (fact.verification_status or fact.status) != "verified"
            or not prose_evidence_support_ready(
                fact.claim_type, fact.evidence_support_terms
            )
            or not fact.evidence_binding
            or fact.evidence_binding != fact_evidence_binding(fact)
        ):
            continue
        if fact_id in by_id:
            duplicate_ids.add(fact_id)
        else:
            by_id[fact_id] = fact
        if key in by_key:
            duplicate_keys.add(key)
        else:
            by_key[key] = fact
    for fact_id in duplicate_ids:
        by_id.pop(fact_id, None)
    for key in duplicate_keys:
        by_key.pop(key, None)
    return by_id, by_key


def _bound_fact_for_sentence(
    section_id: str,
    sentence: ComposedSentence,
    *,
    by_id: dict[str, FactRecord],
    by_key: dict[tuple[str, str, str], FactRecord],
) -> FactRecord | None:
    claim_slot = sentence.planned_claim_slot.strip()
    if (
        sentence.verification_state != "verified"
        or sentence.grade not in (GRADE_CONFIRMED, GRADE_INTERPRETED)
        or not sentence.citations
        or claim_slot not in CLAIM_SLOTS_BY_SECTION.get(section_id, ())
    ):
        return None
    key = _fact_key(
        section_id=section_id,
        claim=sentence.text,
        claim_slot=claim_slot,
    )
    structured = sentence.structured_claim
    fact = (
        by_id.get(structured.fact_id.strip())
        if structured is not None
        else by_key.get(key)
    )
    if fact is None or _fact_key(
        section_id=fact.section_owner,
        claim=fact.claim,
        claim_slot=fact.claim_slot,
    ) != key:
        return None
    return fact


def select_extractive_summary(
    report: ComposedReport,
    facts: Sequence[FactRecord],
) -> ExtractiveSummary:
    """장별로 검증 사실을 고르되 어떤 공개 글자도 새로 만들지 않는다.

    첫 바퀴는 서로 다른 장에서 한 문장씩 고른다. 다섯 장을 채우지 못했지만
    적어도 세 장이 준비됐다면 다음 바퀴에서 같은 장의 다른 사실을 보충한다.
    세 장에도 못 미치면 억지로 한 장의 문장을 복제하지 않고 ``release_ready``를
    거짓으로 남겨 상위 품질 게이트가 정직하게 중단할 수 있게 한다.
    """

    by_id, by_key = _verified_fact_registry(facts)
    sections = {section.section_id: section for section in report.sections}
    pools: dict[str, list[ExtractiveSummaryItem]] = {}
    for section_id in SUMMARY_SECTION_PRIORITY:
        section = sections.get(section_id)
        if section is None:
            continue
        candidates: list[ExtractiveSummaryItem] = []
        # 같은 장에서는 확인 문장을 해석 문장보다 먼저 고르되 원래 문장 순서는
        # 각 등급 안에서 유지한다.
        for grade in (GRADE_CONFIRMED, GRADE_INTERPRETED):
            for sentence in section.sentences:
                if sentence.grade != grade:
                    continue
                fact = _bound_fact_for_sentence(
                    section_id,
                    sentence,
                    by_id=by_id,
                    by_key=by_key,
                )
                if fact is not None:
                    candidates.append(
                        ExtractiveSummaryItem(section_id, sentence, fact.fact_id)
                    )
        if candidates:
            pools[section_id] = candidates

    selected: list[ExtractiveSummaryItem] = []
    seen_claims: set[str] = set()
    deepest = max((len(pool) for pool in pools.values()), default=0)
    for round_index in range(deepest):
        for section_id in SUMMARY_SECTION_PRIORITY:
            pool = pools.get(section_id, ())
            if round_index >= len(pool):
                continue
            candidate = pool[round_index]
            claim_key = _normalized_text(candidate.sentence.text).casefold()
            if not claim_key or claim_key in seen_claims:
                continue
            selected.append(candidate)
            seen_claims.add(claim_key)
            if len(selected) >= SUMMARY_MAX_FACTS:
                return ExtractiveSummary(tuple(selected))
        # 한 바퀴가 끝났는데 서로 다른 세 장조차 없으면 같은 장 문장을 더
        # 뽑아 길이만 맞추지 않는다.
        if round_index == 0 and len(selected) < SUMMARY_MIN_FACTS:
            return ExtractiveSummary(tuple(selected))
    return ExtractiveSummary(tuple(selected))

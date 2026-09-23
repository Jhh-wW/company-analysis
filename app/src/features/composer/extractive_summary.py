"""검증된 본문 사실만 글자 그대로 재사용하는 핵심 요약 선택기.

요약을 다시 AI에게 쓰게 하면 본문에 없던 원인·전망이 새로 생기고, 작성과
검수 호출도 각각 한 번 더 든다. 이 모듈은 이미 정확한 원문에 결속된 본문
문장만 골라 0장 요약으로 재사용한다. 문장 본문·인용·등급은 바꾸지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Final, Mapping, Sequence

from src.features.composer.constants import GRADE_CONFIRMED, GRADE_INTERPRETED
from src.features.composer.extractive_summary_constants import (
    SUMMARY_FORMAL_ENDING_PATTERN,
    SUMMARY_FORMAL_ENDING_SCORE,
    SUMMARY_INTERPRETED_SCORE,
    SUMMARY_LOW_VALUE_PATTERNS,
    SUMMARY_LOW_VALUE_SCORE,
    SUMMARY_NAMED_ENTITY_PATTERN,
    SUMMARY_NAMED_ENTITY_SCORE,
    SUMMARY_NUMERIC_PATTERN,
    SUMMARY_NUMERIC_SCORE,
    SUMMARY_REVENUE_COMPOSITION_PATTERN,
    SUMMARY_REVENUE_ROUTE_SUFFIX,
    SUMMARY_REVENUE_ROUTE_COUNTS,
    SUMMARY_REVENUE_STREAM_SEPARATOR,
    SUMMARY_REVENUE_MIN_STREAMS,
)
from src.features.composer.logic import SummaryCandidate
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


def _revenue_composition_key(text: str, company_name: str) -> tuple[str, ...] | None:
    """열거만 하는 수익 구성의 항목을 비교하며 추가 절·시점·수치는 지우지 않는다."""

    match = re.fullmatch(SUMMARY_REVENUE_COMPOSITION_PATTERN, _normalized_text(text))
    company = _normalized_text(company_name)
    if match is None or not company:
        return None
    aliases = {"회사", company, company.removeprefix("주식회사 ")}
    if match["subject"] not in aliases:
        return None
    route_count = re.search(SUMMARY_REVENUE_ROUTE_SUFFIX, match["streams"])
    streams_text = re.sub(SUMMARY_REVENUE_ROUTE_SUFFIX, "", match["streams"]).strip()
    streams_text = re.sub(r"과 관련된(?=\s+용역(?:\s|과|와|및|$))", "", streams_text)
    if any(marker in streams_text for marker in (",", ";", "이며", "하고", "하지만")):
        return None
    streams = re.split(SUMMARY_REVENUE_STREAM_SEPARATOR, streams_text)
    if len(streams) < SUMMARY_REVENUE_MIN_STREAMS:
        return None
    if route_count is not None and len(streams) != SUMMARY_REVENUE_ROUTE_COUNTS[route_count["count"]]:
        return None
    keys = []
    for stream in streams:
        value = re.sub(r"\s*(?:매출|수익)$", "", stream).strip()
        value = re.sub(r"과 관련된(?=\s+용역$)", "", value)
        value = _normalized_text(value)
        if not value:
            return None
        keys.append(value)
    return tuple(sorted(keys))


def distinct_summary_candidates(
    candidates: Sequence[SummaryCandidate], *, company_name: str,
    facts_by_sentence_id: Mapping[int, FactRecord] | None = None,
) -> tuple[SummaryCandidate, ...]:
    """같은 원문을 인용한 수익 구성 바꿔쓰기를 후보 단계에서 한 번만 둔다.

    확인을 먼저 남기고, 동급이면 다른 사실이 없는 장의 문장을 남겨 대체
    후보가 있는 장에서 다른 사실을 고를 수 있게 한다. 일반 유사도나 공통
    단어 수로 의미를 추정하지 않으며 검증 상태·인용·본문은 그대로 둔다.
    """

    keys = [
        _revenue_composition_key(candidate.sentence.text, company_name)
        if candidate.sentence.verification_state == "verified" else None
        for candidate in candidates
    ]
    if not any(key is not None for key in keys):
        return tuple(candidates)

    def repeats(left: int, right: int) -> bool:
        if facts_by_sentence_id is not None:
            left_fact = facts_by_sentence_id[id(candidates[left].sentence)]
            right_fact = facts_by_sentence_id[id(candidates[right].sentence)]
            if any(
                getattr(left_fact, field) != getattr(right_fact, field)
                for field in (
                    "legal_entity", "subject_scope", "time_state", "as_of",
                    "fiscal_year", "event_date", "period_start", "period_end",
                )
            ):
                return False
        return bool(
            keys[left] is not None and keys[left] == keys[right]
            and set(candidates[left].sentence.citations)
            & set(candidates[right].sentence.citations)
        )

    # 다른 사실의 수만 비교한다. 같은 구성 문장의 바꿔쓰기 수는 대안이 아니다.
    alternative_counts = [
        sum(
            other.section_id == candidate.section_id and keys[j] != keys[i]
            for j, other in enumerate(candidates)
        )
        for i, candidate in enumerate(candidates)
    ]
    kept: list[int] = []
    for index in sorted(
        range(len(candidates)),
        key=lambda i: (
            candidates[i].sentence.grade != GRADE_CONFIRMED, alternative_counts[i],
        ),
    ):
        if not any(repeats(index, previous) for previous in kept):
            kept.append(index)
    return tuple(candidates[index] for index in sorted(kept))


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


def _summary_score(sentence: ComposedSentence) -> int:
    """회계 상용구보다 회사의 구체적 사업 사실을 먼저 읽게 한다."""

    text = sentence.text
    return sum((
        SUMMARY_NUMERIC_SCORE if re.search(SUMMARY_NUMERIC_PATTERN, text) else 0,
        SUMMARY_NAMED_ENTITY_SCORE if re.search(SUMMARY_NAMED_ENTITY_PATTERN, text) else 0,
        SUMMARY_LOW_VALUE_SCORE if any(pattern in text for pattern in SUMMARY_LOW_VALUE_PATTERNS) else 0,
        SUMMARY_INTERPRETED_SCORE if sentence.grade == GRADE_INTERPRETED else 0,
        SUMMARY_FORMAL_ENDING_SCORE if re.search(SUMMARY_FORMAL_ENDING_PATTERN, text) else 0,
    ))


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
    facts_by_sentence_id: dict[int, FactRecord] = {}
    for section_id in SUMMARY_SECTION_PRIORITY:
        section = sections.get(section_id)
        if section is None:
            continue
        candidates: list[ExtractiveSummaryItem] = []
        for sentence in section.sentences:
            fact = _bound_fact_for_sentence(
                section_id,
                sentence,
                by_id=by_id,
                by_key=by_key,
            )
            if fact is not None:
                facts_by_sentence_id[id(sentence)] = fact
                candidates.append(
                    ExtractiveSummaryItem(section_id, sentence, fact.fact_id)
                )
        if candidates:
            # 숫자 가점이 확인 우선을 뒤집지 않게 등급부터 비교한다.
            # 등급과 점수가 같으면 안정 정렬로 본문 순서를 지킨다.
            pools[section_id] = sorted(
                candidates,
                key=lambda item: (
                    item.sentence.grade != GRADE_INTERPRETED,
                    _summary_score(item.sentence),
                ),
                reverse=True,
            )

    # 엄격 경로도 동일한 후보 중복 규칙을 쓰되 FactRecord의 회사·시점이
    # 다르면 같은 문구라도 서로 다른 사실로 보존한다.
    all_items = [item for pool in pools.values() for item in pool]
    companies = {fact.legal_entity for fact in facts_by_sentence_id.values()}
    distinct = distinct_summary_candidates(
        tuple(SummaryCandidate(item.section_id, item.sentence) for item in all_items),
        company_name=next(iter(companies)) if len(companies) == 1 else "",
        facts_by_sentence_id=facts_by_sentence_id,
    )
    distinct_ids = {id(candidate.sentence) for candidate in distinct}
    pools = {
        section_id: [item for item in pool if id(item.sentence) in distinct_ids]
        for section_id, pool in pools.items()
    }

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

"""이미 검증된 동일 원문·동일 정산을 요약에 한 번만 선택한다."""

from collections.abc import Mapping, Sequence
import re
from typing import Protocol

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.extractive_summary_constants import (
    SUMMARY_FACT_SCOPE_FIELDS, SUMMARY_SETTLEMENT_BUSINESS_PATTERN,
    SUMMARY_SETTLEMENT_PATTERN,
)
from src.features.composer.logic import SummaryCandidate


class _SummaryEvidence(Protocol):
    supporting_source_ids: list[str]
    supporting_source_identities: list[str]
    supporting_evidence_hashes: list[str]


def _evidence_keys(fact: _SummaryEvidence) -> frozenset[tuple[str, str]]:
    identities, hashes = fact.supporting_source_identities, fact.supporting_evidence_hashes
    if (not identities or len(identities) != len(hashes)
            or len(identities) != len(fact.supporting_source_ids)
            or any(not value for value in identities)
            or any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in hashes)):
        return frozenset()
    return frozenset(zip(identities, hashes))


def distinct_settlement_summaries(
    candidates: Sequence[SummaryCandidate], *, company_name: str,
    facts_by_sentence_id: Mapping[int, _SummaryEvidence] | None,
) -> tuple[SummaryCandidate, ...]:
    """본문·Fact는 보존하고 같은 지급 관계의 요약 선택만 줄인다.

    서로 다른 조각 번호도 독립 문서 신원과 정확한 원문 지문이 같으면 비교한다.
    같은 원문이라는 이유만으로 다른 주장을 합치지 않도록 완결 정산 문법과
    회사·시점 범위까지 일치해야 한다. 근거가 없는 legacy 후보는 그대로 둔다.
    """
    if not facts_by_sentence_id or not company_name:
        return tuple(candidates)
    subject = "|".join(("회사", "당사", re.escape("".join(company_name.split()))))
    pattern = re.compile(SUMMARY_SETTLEMENT_PATTERN.format(subject=subject))
    eligible: dict[int, tuple[_SummaryEvidence, frozenset[tuple[str, str]]]] = {}
    for index, candidate in enumerate(candidates):
        sentence = candidate.sentence
        fact = facts_by_sentence_id.get(id(sentence))
        compact = "".join(sentence.text.split())
        if (fact is None or sentence.verification_state != "verified"
                or candidate.section_id not in {"identity", "business_model"}
                or not re.search(SUMMARY_SETTLEMENT_BUSINESS_PATTERN, compact)
                or not pattern.fullmatch(compact)):
            continue
        keys = _evidence_keys(fact)
        if keys:
            eligible[index] = fact, keys
    kept: list[int] = []
    removed: set[int] = set()
    for index in sorted(eligible, key=lambda i: (
        candidates[i].sentence.grade != GRADE_CONFIRMED,
        candidates[i].section_id != "business_model", i,
    )):
        fact, keys = eligible[index]
        if any(
            keys & eligible[previous][1]
            and all(getattr(fact, field) == getattr(eligible[previous][0], field)
                    for field in SUMMARY_FACT_SCOPE_FIELDS)
            for previous in kept
        ):
            removed.add(index)
        else:
            kept.append(index)
    return tuple(candidate for i, candidate in enumerate(candidates) if i not in removed)

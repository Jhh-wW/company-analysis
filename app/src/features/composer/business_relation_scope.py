"""구체 사업 관계가 자기 인용에서 확인되는지 좁게 대조한다.

닫힌 관계·고객 유형만 검사한다. 일반 사실의 의미, 회사 소유, 수치와 시점은
기존 역할 결속·법인·의미 검수가 계속 맡는다. 빈 결과는 전체 사실의 승인이 아니다.
"""
from collections.abc import Mapping
import re
import unicodedata

from src.features.composer.business_relation_scope_constants import (
    ACTOR_CLAUSE_BOUNDARY_RE, ACTUAL_ACTION_RE, BUSINESS_RELATION_PROBLEM,
    BUSINESS_RELATION_SECTIONS, CLAUSE_BOUNDARY_RE, CUSTOMER_CLAIM_RE,
    CUSTOMER_FAMILIES, CUSTOMER_SOURCE_RELATION_RE, GENERIC_SUBJECT_RE,
    NEGATED_ACTION_RE, OTHER_ACTOR_RE, PENDING_ACTION_RE, RELATION_FAMILIES,
    STATE_CLAUSE_BOUNDARY_RE, SUBJECT_RE, SUBJECT_SUFFIX_RE,
)


def _surface(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _units(text: str) -> tuple[str, ...]:
    # 공시의 줄바꿈은 행갈이가 섞여 있으므로 문장 경계로 쓰지 않는다.
    # 표의 명시적 행 경계는 유지하여 다른 행의 고객·거래를 빌리지 못하게 한다.
    if "|" in text:
        text = re.sub(r"\n(?=[^\n]*\|)", "; ", text)
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()
    return tuple(unit.strip() for sentence in CLAUSE_BOUNDARY_RE.split(normalized)
                 for unit in ACTOR_CLAUSE_BOUNDARY_RE.split(sentence) if unit.strip())


def _subject(unit: str) -> str:
    if "|" in unit:
        subject = _surface(unit.split("|", 1)[0])
        return "" if GENERIC_SUBJECT_RE.fullmatch(subject) else subject
    match = SUBJECT_RE.search(unit)
    if not match:
        return ""
    subject = _surface(match[1]).split("의")[-1]
    subject = SUBJECT_SUFFIX_RE.sub("", subject)
    return "" if GENERIC_SUBJECT_RE.fullmatch(subject) else subject


def _compatible_subject(claim: str, source: str) -> bool:
    candidate_subject, source_subject = _subject(claim), _subject(source)
    # 문장 주어가 잘린 원문은 새 사실로 승격하지 않는다. 주어 밖 관계는 기존 검수가 맡는다.
    if not candidate_subject:
        return not bool(OTHER_ACTOR_RE.search(source_subject))
    if not source_subject:
        return True
    return candidate_subject in source_subject or source_subject in candidate_subject


def _state_supported(claim: str, source: str, source_pattern: re.Pattern) -> bool:
    candidate = _surface(claim)
    # 같은 문장의 다른 행동에 붙은 계획·부정으로 실제 관계까지 지우지 않는다.
    for segment in STATE_CLAUSE_BOUNDARY_RE.split(source):
        original = _surface(segment)
        if not source_pattern.search(original):
            continue
        if NEGATED_ACTION_RE.search(original) and not NEGATED_ACTION_RE.search(candidate):
            continue
        if PENDING_ACTION_RE.search(original) and not PENDING_ACTION_RE.search(candidate):
            continue
        return True
    return False


def business_relation_scope_problem(
    text: str, own_sources: Mapping[str, str], *, section_id: str,
) -> str:
    """2·7장 산문의 명시적 거래·고객 관계만 자기 인용의 절/표행과 대조한다."""
    if section_id not in BUSINESS_RELATION_SECTIONS or not own_sources:
        return ""
    source_units = tuple(unit for value in own_sources.values() for unit in _units(value))
    for claim in _units(text):
        candidate = _surface(claim)
        # 부정·계획을 있는 그대로 알리는 문장은 관계를 실제 수행했다고 단정하지 않는다.
        # 같은 원문을 긍정·현재 사실로 바꾸는 것은 아래 원문 상태 대조가 막는다.
        for _, claim_pattern, source_pattern in RELATION_FAMILIES:
            if not claim_pattern.search(candidate):
                continue
            if not any(source_pattern.search(_surface(unit))
                       and _compatible_subject(claim, unit)
                       and _state_supported(claim, unit, source_pattern) for unit in source_units):
                return BUSINESS_RELATION_PROBLEM
        if CUSTOMER_CLAIM_RE.search(candidate):
            for _, family in CUSTOMER_FAMILIES:
                if not family.search(candidate):
                    continue
                if not any(family.search(_surface(unit))
                           and CUSTOMER_SOURCE_RELATION_RE.search(_surface(unit))
                           and ACTUAL_ACTION_RE.search(_surface(unit))
                           and _compatible_subject(claim, unit)
                           and _state_supported(claim, unit, family) for unit in source_units):
                    return BUSINESS_RELATION_PROBLEM
    return ""

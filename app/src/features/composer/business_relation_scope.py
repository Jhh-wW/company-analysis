"""구체 사업 관계가 자기 인용에서 확인되는지 좁게 대조한다.

닫힌 관계·고객 유형만 검사한다. 일반 사실의 의미, 회사 소유, 수치와 시점은
기존 역할 결속·법인·의미 검수가 계속 맡는다. 빈 결과는 전체 사실의 승인이 아니다.
"""
from collections.abc import Mapping
from src.shared.report_evidence.partnership_scope import research_partnership_claim_problem
from src.shared.report_evidence.business_slot_scope import business_slot_scope_problem
from src.shared.report_evidence.business_slot_scope_constants import CUSTOMER_SLOT
import re
import unicodedata

from src.features.composer.business_relation_scope_constants import (
    ACTOR_CLAUSE_BOUNDARY_RE, ACTUAL_ACTION_RE, BUSINESS_RELATION_PROBLEM,
    BUSINESS_RELATION_SECTIONS, CLAUSE_BOUNDARY_RE, CUSTOMER_CLAIM_RE,
    CUSTOMER_FAMILIES, CUSTOMER_SOURCE_RELATION_RE, GENERIC_SUBJECT_RE,
    NEGATED_ACTION_RE, OTHER_ACTOR_RE, PENDING_ACTION_RE, RELATION_FAMILIES,
    STATE_CLAUSE_BOUNDARY_RE, SUBJECT_RE, SUBJECT_SUFFIX_RE,
    ACCOUNTING_INSPECTION_RE, PRODUCT_QUALITY_RE, QUALITY_PURPOSE_LINK_RE, QUALITY_PURPOSE_DENIAL_RE,
    PROVISION_ACTION_RE, PROVISION_ITEM_FAMILIES, PROVISION_GENERIC_ITEMS,
    PROVISION_COMPANY_SUBJECT_RE,
    PROVISION_UNCONFIRMED_TAIL_RE,
    RECEIVABLE_COLLECTION_RE, RECEIVABLE_ACTION_RE, RECEIVABLE_COUNTERPARTY_RE,
    RECEIVABLE_OBJECT_QUALIFIER,
    RECOGNITION_CLAIM_RE, RECOGNITION_SOURCE_RE, RECOGNITION_BASES, RECOGNITION_METRIC_HEAD_RE,
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


def _accounting_quality_problem(claim: str, source_units: tuple[str, ...]) -> bool:
    candidate = _surface(claim)
    if not (ACCOUNTING_INSPECTION_RE.search(candidate)
            and PRODUCT_QUALITY_RE.search(candidate)
            and QUALITY_PURPOSE_LINK_RE.search(candidate)) or QUALITY_PURPOSE_DENIAL_RE.search(candidate):
        return False
    accounting_units = [unit for unit in source_units
                        if ACCOUNTING_INSPECTION_RE.search(_surface(unit))
                        and _compatible_subject(claim, unit)]
    # 자기 인용에 회계 실사 자체가 없는 경우는 기존 의미 검수가 판단한다.
    return bool(accounting_units) and not any(
        PRODUCT_QUALITY_RE.search(_surface(unit))
        and QUALITY_PURPOSE_LINK_RE.search(_surface(unit))
        and not QUALITY_PURPOSE_DENIAL_RE.search(_surface(unit)) for unit in accounting_units
    )


def _provision_relations(unit: str) -> tuple[tuple[bool, frozenset[str], str, bool], ...]:
    """한 절 안에서도 각 제공 동작의 바로 앞 항목을 따로 묶는다."""
    relations = []
    surface = _surface(unit)
    previous_end = 0
    for action in PROVISION_ACTION_RE.finditer(surface):
        prefix = surface[previous_end:action.start()]
        items = frozenset(name for name, pattern in PROVISION_ITEM_FAMILIES
                          if pattern.search(prefix))
        if items - PROVISION_GENERIC_ITEMS:
            items -= PROVISION_GENERIC_ITEMS
        if items:
            relations.append((bool(action.group("receive")) or action[0] == "수령", items, prefix,
                              bool(PROVISION_UNCONFIRMED_TAIL_RE.search(surface[action.end():]))))
        previous_end = action.end()
    return tuple(relations)


def _raw_subject(unit: str) -> str:
    if "|" in unit:
        return _surface(unit.split("|", 1)[0])
    match = SUBJECT_RE.search(unit)
    return _surface(match[1]) if match else ""


def _reciprocal_parties(claim: str, claim_prefix: str, source: str, source_prefix: str) -> bool:
    """공급자가 제공하고 수혜자가 제공받았다는 같은 거래의 관점 전환은 남긴다."""
    claim_subject, source_subject = _raw_subject(claim), _raw_subject(source)
    if not claim_subject or not source_subject:
        return False
    return (
        any(source_subject + suffix in claim_prefix for suffix in ("로부터", "으로부터", "에게", "한테"))
        and any(claim_subject + suffix in source_prefix for suffix in ("로부터", "으로부터", "에게", "한테"))
    )


def _same_provision_actor(claim: str, source: str) -> bool:
    candidate_subject, source_subject = _raw_subject(claim), _raw_subject(source)
    if candidate_subject and source_subject:
        if PROVISION_COMPANY_SUBJECT_RE.fullmatch(candidate_subject):
            return bool(PROVISION_COMPANY_SUBJECT_RE.fullmatch(source_subject))
        if PROVISION_COMPANY_SUBJECT_RE.fullmatch(source_subject):
            return False
    return _compatible_subject(claim, source)


def _provision_direction_problem(claim: str, source_units: tuple[str, ...]) -> bool:
    for received, items, prefix, unconfirmed in _provision_relations(claim):
        if unconfirmed:
            continue
        matching = []
        for unit in source_units:
            for source_received, source_items, source_prefix, source_unconfirmed in _provision_relations(unit):
                if not items.issubset(source_items):
                    continue
                reciprocal = _reciprocal_parties(claim, prefix, unit, source_prefix)
                if reciprocal and source_received != received:
                    matching.append(not source_unconfirmed)
                elif _same_provision_actor(claim, unit):
                    if source_received == received:
                        matching.append(not source_unconfirmed)
                    elif not source_unconfirmed:
                        matching.append(False)
        # 방향이 원문에 명백히 반대로 쓰인 경우만 제한한다. 불명확한 항목은 추정하지 않는다.
        if matching and not any(matching):
            return True
    return False


def business_relation_scope_problem(
    text: str, own_sources: Mapping[str, str], *, section_id: str, claim_slot: str = "",
) -> str:
    """2·7장 산문의 명시적 거래·고객 관계만 자기 인용의 절/표행과 대조한다."""
    if section_id not in BUSINESS_RELATION_SECTIONS or not own_sources:
        return ""
    # 원문 사실이 참이어도 회수관리만으로 고객유형 칸을 충족하지 않는다.
    # 혼합 원문은 유지하고 실제 후보의 해당 절만 같은 수집 계약으로 검사한다.
    if (section_id == "business_model" and claim_slot == CUSTOMER_SLOT
            and business_slot_scope_problem(text, CUSTOMER_SLOT)):
        return BUSINESS_RELATION_PROBLEM
    if section_id == "operations_partners":
        problem = research_partnership_claim_problem(text, own_sources, claim_slot=claim_slot)
        if problem:
            return problem
    source_units = tuple(unit for value in own_sources.values() for unit in _units(value))
    for claim in _units(text):
        candidate = _surface(claim)
        if section_id == "business_model":
            for match in RECEIVABLE_COLLECTION_RE.finditer(candidate):
                item = match["object"]
                action = re.compile(re.escape(item) + RECEIVABLE_OBJECT_QUALIFIER + RECEIVABLE_ACTION_RE.pattern)
                relation = re.compile(re.escape(item) + r"[^.!?。;\n]{0,20}회수")
                supported = [unit for unit in source_units
                             if item in _surface(unit) and _compatible_subject(claim, unit)
                             and (not RECEIVABLE_COUNTERPARTY_RE.match(_surface(unit))
                                  or _raw_subject(claim) == _raw_subject(unit))
                             and action.search(_surface(unit))
                             and _state_supported(claim, unit, relation)]
                # 계정 잔액·누적액만 있는 원문에서 행동을 만들어 내지 않는다.
                if not supported:
                    return BUSINESS_RELATION_PROBLEM
            if RECOGNITION_CLAIM_RE.search(candidate):
                supported = [unit for unit in source_units
                             if RECOGNITION_SOURCE_RE.search(_surface(unit))
                             and (_compatible_subject(claim, unit)
                                  or RECOGNITION_METRIC_HEAD_RE.match(_surface(unit)))]
                if not supported:
                    return BUSINESS_RELATION_PROBLEM
                for claim_basis, source_basis in RECOGNITION_BASES:
                    if claim_basis.search(candidate) and not any(source_basis.search(_surface(unit)) for unit in supported):
                        return BUSINESS_RELATION_PROBLEM
        if section_id == "operations_partners" and (
            _accounting_quality_problem(claim, source_units)
            or _provision_direction_problem(claim, source_units)
        ):
            return BUSINESS_RELATION_PROBLEM
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

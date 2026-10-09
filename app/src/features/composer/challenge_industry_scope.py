"""산업 원문 설명을 직접 회사 문제로 승인하는 부분 작성 우회를 제한한다."""
from __future__ import annotations

import unicodedata

from src.features.composer.challenge_industry_scope_constants import (
    COMPANY_SCOPE_RE, DIRECT_CHALLENGE_CLAIM_SLOTS, INDUSTRY_SUBJECT_RE,
    QUALIFIED_SUBJECT_RE, ROUTINE_COMPANY_ACTIVITY_RE, COMPANY_STATE_JOIN_RE,
    MARKET_BACKGROUND_RE, MARKET_OBSERVATION_ONLY_RE,
    INDUSTRY_REPORT_PREFIX_RE, INDUSTRY_REPORT_SCOPE_RE, INDUSTRY_REPORT_FUTURE_RE,
    OTHER_COMPANY_SCOPE_RE, GENERAL_BUSINESS_POPULATION_RE,
    COMPANY_PROBLEM_LINK_ONLY, COMPANY_EXPLICIT_CONSTRAINT_RE,
    INDUSTRY_ENVIRONMENT_PREFIX_RE, INDUSTRY_GROUP_SUBJECT_RE,
    INDUSTRY_SCOPE_DIRECT_ISSUE_SLOTS,
    INDUSTRY_CLAUSE_SUBJECT_RE,
)
from src.shared.report_evidence.challenge_eligibility import challenge_issue_problem
from src.shared.report_evidence.challenge_eligibility_constants import (
    ADMINISTRATIVE_EVENT_ONLY, UNIT_RE, PROBLEM_RE, POLICY_BUSINESS_PROBLEM_RE,
    ISSUE_RELATION_VETO_RE, ROUTINE_DUTY_OTHER_CLAUSE_RE,
)


def _has_company_problem(text: str) -> bool:
    """주장 안 자기 관계 뒤의 문제 술어만 읽고 산업·다른 회사의 주어는 빌리지 않는다."""
    other = (*OTHER_COMPANY_SCOPE_RE.finditer(text), *GENERAL_BUSINESS_POPULATION_RE.finditer(text))
    for match in COMPANY_SCOPE_RE.finditer(text):
        if any(start.start() <= match.start() < start.end() for start in other):
            continue
        end = min((boundary.start() for boundary in other if boundary.start() >= match.end()), default=len(text))
        tail = "".join(text[match.end():end].split())
        if (PROBLEM_RE.search(tail) or POLICY_BUSINESS_PROBLEM_RE.search(tail)
                or COMPANY_EXPLICIT_CONSTRAINT_RE.search(tail)
                or any(value.group() not in COMPANY_PROBLEM_LINK_ONLY
                       for value in ISSUE_RELATION_VETO_RE.finditer(tail))):
            return True
    return False


def _has_non_group_clause_problem(text: str) -> bool:
    """명시한 다른 주어의 제약은 일반 업계 주어에 흡수하지 않는다.

    목표 회사의 직접 피해로 승인하는 함수가 아니다. 새 배경 분기의 거절을
    면제하고 기존 회사 귀속·자기 원문·의미 검수로 돌려보내는 데만 쓴다.
    """
    subjects = tuple(INDUSTRY_CLAUSE_SUBJECT_RE.finditer(text))
    for index, match in enumerate(subjects):
        if INDUSTRY_GROUP_SUBJECT_RE.fullmatch(match.group("subject")):
            continue
        end = subjects[index + 1].start() if index + 1 < len(subjects) else len(text)
        tail = "".join(text[match.end():end].split())
        if (PROBLEM_RE.search(tail) or POLICY_BUSINESS_PROBLEM_RE.search(tail)
                or COMPANY_EXPLICIT_CONSTRAINT_RE.search(tail)
                or any(value.group() not in COMPANY_PROBLEM_LINK_ONLY
                       for value in ISSUE_RELATION_VETO_RE.finditer(tail))):
            return True
    return False


def industry_only_challenge_problem(text: str, *, claim_slot: str = "") -> str:
    """회사 관계 없는 산업 주어 설명을 직접 과제 칸으로 만들지 않는다.

    산업 원문은 폐기하지 않는다. 사업 앵커·적용 관계가 검증된 별도 산업 해석
    블록은 이 함수에 들어오지 않는다. 빈 사유는 나머지 의미의 승인이 아니다.
    """
    if claim_slot not in DIRECT_CHALLENGE_CLAIM_SLOTS:
        return ""
    units = tuple(
        unit.strip() for sentence in UNIT_RE.split(text)
        for unit in COMPANY_STATE_JOIN_RE.split(sentence) if unit.strip()
    )
    found_industry_subject = False
    for unit in units:
        normalized = unicodedata.normalize("NFKC", unit)
        reporting = INDUSTRY_REPORT_PREFIX_RE.fullmatch(normalized)
        reported = reporting.group("body") if reporting else normalized
        background = MARKET_BACKGROUND_RE.fullmatch(normalized)
        surface = "".join(normalized.split())
        environment = INDUSTRY_ENVIRONMENT_PREFIX_RE.fullmatch(normalized)
        # 산업 배경 뒤 일반 업계 집단의 변화·전망을 회사 직접 과제로 세지 않는다.
        # 같은 주장에 명시 회사 제약이 있으면 기존 의미 검수에 남긴다. 해석 칸과
        # 별도 산업 맥락의 원문 검증·등록은 이 분기로 승인하거나 바꾸지 않는다.
        if claim_slot in INDUSTRY_SCOPE_DIRECT_ISSUE_SLOTS:
            scoped_body = environment.group("body") if environment else (
                background.group("body") if background else ""
            )
            group_subject = (INDUSTRY_GROUP_SUBJECT_RE.match(scoped_body) if environment
                             else INDUSTRY_GROUP_SUBJECT_RE.search(scoped_body))
            if (scoped_body and group_subject
                    and not _has_company_problem(normalized)
                    and not _has_non_group_clause_problem(scoped_body)):
                found_industry_subject = True
                continue
        if GENERAL_BUSINESS_POPULATION_RE.search(reported) and not _has_company_problem(reported):
            found_industry_subject = True
            continue
        if (background
                and MARKET_OBSERVATION_ONLY_RE.fullmatch("".join(background.group("body").split()))
                and not PROBLEM_RE.search(surface)
                and not POLICY_BUSINESS_PROBLEM_RE.search(surface)
                and not ISSUE_RELATION_VETO_RE.search(surface)):
            # 자기 원문의 다른 절이나 회사 일상업무에서 직접 피해 관계를 빌리지 않는다.
            # 다른 술어·제약이 섞인 시장 배경 문장은 이 닫힌 분기에 들어오지 않는다.
            found_industry_subject = True
            continue
        subject = INDUSTRY_SUBJECT_RE.match(reported)
        if subject and not QUALIFIED_SUBJECT_RE.search(subject.group("subject")):
            if _has_company_problem(normalized):
                return ""
            found_industry_subject = True
            continue
        if (reporting and INDUSTRY_REPORT_SCOPE_RE.search(normalized)
                and INDUSTRY_REPORT_FUTURE_RE.search(reported)
                and not _has_company_problem(reported)):
            found_industry_subject = True
            continue
        ordinary = ROUTINE_COMPANY_ACTIVITY_RE.fullmatch(normalized)
        if (ordinary and not ROUTINE_DUTY_OTHER_CLAUSE_RE.search(ordinary.group("object"))
                and not PROBLEM_RE.search(surface)
                and not POLICY_BUSINESS_PROBLEM_RE.search(surface)
                and not ISSUE_RELATION_VETO_RE.search(surface)):
            continue
        # 산업 설명 뒤 일반 업무 목록을 덧붙여 직접 회사 문제로 만들지 않는다.
        # 다른 술어·피해·대응이 있는 혼합 후보는 기존 의미 검수에 남긴다.
        if not challenge_issue_problem(unit):
            return ""
    return ADMINISTRATIVE_EVENT_ONLY if found_industry_subject else ""

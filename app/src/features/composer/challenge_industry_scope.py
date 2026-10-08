"""산업 원문 설명을 직접 회사 문제로 승인하는 부분 작성 우회를 제한한다."""
from __future__ import annotations

import unicodedata

from src.features.composer.challenge_industry_scope_constants import (
    COMPANY_SCOPE_RE, DIRECT_CHALLENGE_CLAIM_SLOTS, INDUSTRY_SUBJECT_RE,
    QUALIFIED_SUBJECT_RE, ROUTINE_COMPANY_ACTIVITY_RE, COMPANY_STATE_JOIN_RE,
)
from src.shared.report_evidence.challenge_eligibility import challenge_issue_problem
from src.shared.report_evidence.challenge_eligibility_constants import (
    ADMINISTRATIVE_EVENT_ONLY, UNIT_RE, PROBLEM_RE, POLICY_BUSINESS_PROBLEM_RE,
    ISSUE_RELATION_VETO_RE, ROUTINE_DUTY_OTHER_CLAUSE_RE,
)


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
        subject = INDUSTRY_SUBJECT_RE.match(normalized)
        if subject and not QUALIFIED_SUBJECT_RE.search(subject.group("subject")):
            if COMPANY_SCOPE_RE.search(normalized):
                return ""
            found_industry_subject = True
            continue
        ordinary = ROUTINE_COMPANY_ACTIVITY_RE.fullmatch(normalized)
        surface = "".join(normalized.split())
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

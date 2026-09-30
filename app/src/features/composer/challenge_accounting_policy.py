"""회사 실제 사건과 평상시 회계·반품 조건을 5장에서 구별한다."""
from __future__ import annotations

import unicodedata
from src.features.composer import challenge_accounting_constants as c


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _has_policy_context(text: str) -> bool:
    surface = _surface(text)
    return bool((c.POLICY_SUBJECT_RE.search(surface) and c.POLICY_TREATMENT_RE.search(surface))
                or (c.FINANCIAL_SUBJECT_RE.search(surface)
                    and c.FINANCIAL_TREATMENT_RE.search(surface))
                or c.FINANCIAL_RISK_SUBJECT_RE.search(surface)
                or c.FINANCIAL_ADMINISTRATION_RE.search(surface)
                or c.PLANNED_BUSINESS_SERVICE_RE.search(surface))


def _has_business_fact(text: str) -> bool:
    surface = _surface(text)
    return bool(c.BUSINESS_SERVICE_RE.search(surface) or (
        not c.HYPOTHETICAL_EVENT_RE.search(surface)
        and (c.ACTUAL_EVENT_RE.search(surface) or c.BUSINESS_OPERATION_RE.search(surface)
             or c.BUSINESS_RESPONSE_RE.search(surface))
    ))


def _policy_units(text: str) -> tuple[tuple[str, bool], ...]:
    """쉼표 앞 회계 주어를 같은 문장의 처리절에 전달한다. 실제 사건절은 보존한다."""
    result = []
    for sentence in c.POLICY_SENTENCE_RE.split(text):
        context = _has_policy_context(sentence)
        for unit in c.POLICY_SUBCLAUSE_RE.split(sentence):
            if unit.strip():
                policy = (_has_policy_context(unit) or (
                    context and (c.POLICY_CONTINUATION_RE.search(_surface(unit))
                                 or c.POLICY_TREATMENT_RE.search(_surface(unit)))
                ))
                result.append((unit, bool(policy) and not _has_business_fact(unit)))
    return tuple(result)


def is_challenge_accounting_policy(clause: str) -> bool:
    units = _policy_units(clause)
    return bool(units) and all(excluded for _, excluded in units)

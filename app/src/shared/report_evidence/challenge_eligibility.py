"""원문을 유지하고 당면 과제의 지원칸·배치에만 닫힌 부정 경계를 적용한다."""
from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from src.shared.report_evidence import challenge_eligibility_constants as c


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


@dataclass(frozen=True)
class ChallengeEligibilityScope:
    score_text: str
    excluded_spans: tuple[tuple[int, int, str], ...] = ()

    @property
    def excluded_clauses(self) -> int:
        return len(self.excluded_spans)


def _reason(text: str, *, table_record: bool = False, positive_context: bool = False,
            policy_units: tuple = (), credit_context: bool = False) -> str:
    surface = _surface(text)
    credit_business = bool(any(not c.CREDIT_HYPOTHETICAL_TAIL_RE.match(surface[match.end():])
                               for match in c.CREDIT_ACTUAL_BUSINESS_RE.finditer(surface))
                           or c.CUSTOMER_CREDIT_SERVICE_RE.search(surface)
                           or c.CUSTOMER_LEGAL_FINANCIAL_SERVICE_RE.search(surface))
    if (c.CREDIT_POLICY_RE.search(surface) or (
            credit_context and c.CREDIT_MEASUREMENT_UNIT_RE.search(surface))) and not credit_business:
        return c.ADMINISTRATIVE_EVENT_ONLY
    business_exception = bool(
        c.POLICY_BUSINESS_PROBLEM_RE.search(c.POLICY_REDUCTION_PURPOSE_RE.sub("", surface))
        or c.CUSTOMER_LEGAL_FINANCIAL_SERVICE_RE.search(surface)
        or c.PRODUCT_REGULATION_RESPONSE_RE.search(surface)
        or c.LITIGATION_PAYMENT_ACTION_RE.search(surface)
        or credit_business
    )
    if (c.FINANCIAL_EXPOSURE_RE.search(surface)
            or c.GENERAL_LEGAL_MANAGEMENT_RE.search(surface)
            or c.CONDITIONAL_SANCTION_RULE_RE.search(surface)
            or c.ACCOUNTING_MEASUREMENT_RE.search(surface)
            or c.LITIGATION_ACCOUNTING_ASSESSMENT_RE.search(surface)
            or c.INTERNAL_LEGAL_ACTIVITY_RE.search(surface)
            or any(pattern.search(surface) for pattern in policy_units)) and not business_exception:
        return c.ADMINISTRATIVE_EVENT_ONLY
    if c.ADMINISTRATIVE_RE.search(surface) and not c.BUSINESS_PROBLEM_RE.search(surface):
        return c.ADMINISTRATIVE_EVENT_ONLY
    if (table_record and c.DATE_RE.search(surface) and c.ACCIDENT_RE.search(surface)
            and c.REMEDIAL_RECORD_RE.search(surface)
            and not c.CONTINUING_PROBLEM_RE.search(surface)
            and not c.ACTIVE_IMPACT_RE.search(surface)
            and not c.OPERATING_RESPONSE_RE.search(surface)):
        # 조치 목록은 완료 판정이 아니다. 같은 행의 현재 영향이 미확인된 이력이다.
        return c.HISTORICAL_EVENT_ONLY
    if (table_record and c.DATE_RE.search(surface) and c.INCIDENT_RE.search(surface)
            and c.COMPLETED_ACTION_RE.search(surface)
            and not c.CONTINUING_PROBLEM_RE.search(surface)
            and not c.ACTIVE_IMPACT_RE.search(surface)
            and not c.OPERATING_RESPONSE_RE.search(surface)):
        return c.HISTORICAL_EVENT_ONLY
    if (c.POSITIVE_RE.search(surface) or (
            positive_context and c.POSITIVE_FOLLOWUP_RE.search(surface))) and not c.PROBLEM_RE.search(surface):
        return c.POSITIVE_RESPONSE_ONLY
    return ""



def challenge_issue_problem(text: str) -> str:
    """닫힌 일반 설명만 제한한다. 나머지 문장의 문제성을 승인하지 않는다."""
    units = tuple(unit for unit in c.UNIT_RE.split(text) if unit.strip())
    if not units:
        return ""
    def ordinary(unit: str) -> bool:
        surface = _surface(unit)
        if (c.PROBLEM_RE.search(surface) or c.POLICY_BUSINESS_PROBLEM_RE.search(surface)
                or c.ISSUE_RELATION_VETO_RE.search(surface)):
            return False
        raw_unit = unicodedata.normalize("NFKC", unit)
        duty_subject = c.ROUTINE_DUTY_SUBJECT_RE.match(raw_unit)
        duty_list = (len(surface) <= c.ROUTINE_DUTY_MAX_CHARS
                     and duty_subject
                     and not c.ROUTINE_DUTY_OTHER_CLAUSE_RE.search(raw_unit[duty_subject.end():])
                     and c.ROUTINE_DUTY_ACTIVITY_RE.search(surface)
                     and c.ROUTINE_DUTY_END_RE.search(surface))
        return bool(duty_list or c.ROUTINE_OPERATION_ISSUE_RE.fullmatch(surface)
                    or c.INDUSTRY_TREND_ISSUE_RE.fullmatch(surface))
    return c.ADMINISTRATIVE_EVENT_ONLY if all(ordinary(unit) for unit in units) else ""


def challenge_eligibility_scope(text: str, slot_id: str = "") -> ChallengeEligibilityScope:
    """섞인 원문은 적격 절만 채점한다. 원문 자체와 다른 장의 근거는 바꾸지 않는다.

    임의 자연어의 사업 관련성을 증명하지 않는다. 닫힌 모순이 없는 나머지는
    기존 자기 인용·의미 검수로 판단한다. 교육·납부의 진행은 문제 지속의 증거가 아니다.
    """
    kept = []
    excluded = []
    start = 0
    table_record = False
    pending_header = None
    saw_header = False
    positive_context = False
    full_surface = _surface(text)
    credit_context = bool(c.CREDIT_MEASUREMENT_CONTEXT_RE.search(full_surface))
    policy_units = tuple(unit for first, second, unit in c.POLICY_CONTEXT_RULES
                         if first.search(full_surface) and second.search(full_surface))
    # 표의 재해 내용 끝 마침표가 같은 행의 조치 열을 분리하지 않게 한다.
    bounds = []
    record_start = 0
    records = [(match.start(), match.end()) for match in c.RECORD_BOUNDARY_RE.finditer(text)]
    for record_end, next_record in (*records, (len(text), len(text))):
        record = text[record_start:record_end]
        if "|" not in record:
            bounds.extend((record_start + match.start(), record_start + match.end())
                          for match in c.UNIT_RE.finditer(record))
        if record_end != len(text):
            bounds.append((record_end, next_record))
        record_start = next_record
    cursor = 0
    extra_bounds = []
    for end, next_start in (*bounds, (len(text), len(text))):
        unit = text[cursor:end]
        # 긍정 서술 뒤의 독립 문제 절을 남긴다. 사건 표의 같은 행은 나누지 않는다.
        if "|" not in unit:
            for match in c.POSITIVE_CLAUSE_BOUNDARY_RE.finditer(unit):
                if c.POSITIVE_RE.search(_surface(unit[:match.start()])):
                    extra_bounds.append((cursor + match.start(), cursor + match.end()))
        cursor = next_start
    bounds = sorted((*bounds, *extra_bounds))
    bounds = sorted((*bounds, *((match.start(), match.start())
                               for match in c.LITIGATION_ASSESSMENT_START_RE.finditer(text))))
    for end, next_start in (*bounds, (len(text), len(text))):
        unit = text[start:end]
        if unit.strip():
            header = "|" in unit and bool(c.TABLE_HEADER_RE.search(_surface(unit)))
            if header:
                table_record = True
                saw_header = True
                pending_header = (start, next_start)
            elif "|" not in unit:
                table_record = False
                pending_header = None
            reason = _reason(unit, credit_context=credit_context, table_record=table_record and not header,
                             positive_context=positive_context, policy_units=policy_units)
            if not reason and slot_id == "current_challenges:issue":
                reason = challenge_issue_problem(unit)
            positive_context = reason == c.POSITIVE_RESPONSE_ONLY
            if reason:
                excluded.append((start, end, reason))
            elif not header:
                if "|" in unit and pending_header is not None:
                    kept.append(pending_header)
                    pending_header = None
                kept.append((start, next_start))
        start = next_start
    if saw_header and not kept and not excluded:
        excluded.append((0, len(text), c.HISTORICAL_EVENT_ONLY))
    # 원문 연결자를 보존해 살아남은 사건 행 안의 문장을 다른 행으로 나누지 않는다.
    score_text = "".join(text[begin:finish] for begin, finish in kept)
    return ChallengeEligibilityScope(score_text if excluded or saw_header else text, tuple(excluded))


def challenge_eligibility_problem(text: str, slot_id: str = "") -> str:
    scope = challenge_eligibility_scope(text, slot_id)
    return scope.excluded_spans[0][2] if scope.excluded_spans and not scope.score_text.strip() else ""


def challenge_incident_row_problem(row: str) -> str:
    """호출자가 같은 행 관계를 확인한 사건 표 행의 현재 지원칸만 제한한다."""
    return _reason(row, table_record=True)


def challenge_eligibility_quote_problem(quote: str, source: str, slot_id: str) -> str:
    """같은 원문의 제외 절만 잘라 지원칸을 되살리는 것을 막는다."""
    if slot_id not in c.CHALLENGE_SLOTS:
        return ""
    problem = challenge_eligibility_problem(quote, slot_id)
    if problem:
        return problem
    # 같은 절에 실제 사건이 있어도 측정 방법만 고른 인용은 그 사건을 빌리지 않는다.
    if c.CREDIT_MEASUREMENT_CONTEXT_RE.search(_surface(source)):
        problem = _reason(quote, credit_context=True)
        if problem:
            return problem
    # 실제 사건과 같은 절에 있는 정책 인용도 전체 원문의 사건을 빌리지 않는다.
    full_surface = _surface(source)
    policy_units = tuple(unit for first, second, unit in c.POLICY_CONTEXT_RULES
                         if first.search(full_surface) and second.search(full_surface))
    problem = _reason(quote, policy_units=policy_units)
    if problem:
        return problem
    scoped = challenge_eligibility_scope(source, slot_id)
    if not scoped.excluded_spans:
        return ""
    if not scoped.score_text.strip():
        return scoped.excluded_spans[0][2]
    at = 0
    while quote and (at := source.find(quote, at)) >= 0:
        end = at + len(quote)
        for start, stop, reason in scoped.excluded_spans:
            if start <= at and end <= stop:
                return reason
        at += 1
    return ""

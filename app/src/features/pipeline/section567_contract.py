"""5~7장 구조 필드의 공통 닫힌 계약과 원문 근거 규칙."""

from __future__ import annotations

import re
from datetime import date
from typing import Final


PLAN_STATUSES: Final[frozenset[str]] = frozenset(
    {"announced", "approved", "conditional"}
)
PLAN_STATUS_LABELS: Final[dict[str, str]] = {
    "announced": "발표·미실행",
    "approved": "승인·확정·미실행",
    "conditional": "조건부·미실행",
}
_PLAN_APPROVED = re.compile(
    r"이사회(?:가|는)?.{0,12}(?:승인했|의결했|승인\s*완료)|"
    r"승인(?:됐|되었|완료|됨)|확정(?:됐|되었|함|했다고|했다)|"
    r"(?:규제기관|규제|인허가|허가).{0,12}(?:취득했|취득함|완료했|완료됨)|"
    r"(?:파트너|필수).{0,12}계약.{0,8}(?:체결했|체결됨|완료)"
)
_PLAN_NOT_APPROVED = re.compile(
    r"미확정|미승인|확정되지|승인되지"
)
_PLAN_NO_CONDITION = re.compile(
    r"(?:선행\s*)?조건(?:이|은|도)?\s*(?:없|불필요)|"
    r"(?:인허가|허가|승인)(?:이|가|은|는)?\s*(?:필요\s*없|불필요)|"
    r"(?:수요\s*(?:확보|충족)|자금\s*(?:확보|조달)|파트너\s*(?:확보|계약))"
    r"(?:이|가|은|는)?\s*(?:조건\s*없|필요\s*없|불필요)"
)
_PLAN_CONDITIONAL = re.compile(
    r"조건으로|조건부|선행\s*조건|"
    r"(?:인허가|허가|승인).{0,12}(?:필요|전|대기|요청|상정)|"
    r"(?:인허가|허가|승인).{0,12}(?:완료|취득)(?:을|를)?\s*(?:조건|전제|후|시)|"
    r"수요.{0,8}(?:확보|충족).{0,6}(?:조건|필요|전제)|"
    r"자금.{0,8}(?:확보|조달).{0,6}(?:조건|필요|전제)|"
    r"파트너.{0,8}(?:확보|계약).{0,6}(?:조건|필요|전제)"
)


def has_plan_condition(evidence: str) -> bool:
    """명시적으로 없다고 한 조건을 제거한 뒤 실제 선행 조건만 찾는다."""

    value = _PLAN_NO_CONDITION.sub("", str(evidence or ""))
    return _PLAN_CONDITIONAL.search(value) is not None


def expected_plan_status(evidence: str) -> str:
    """원문의 승인·조건 표현을 숨기지 않는 최소 계획 상태를 반환한다."""

    value = str(evidence or "")
    if has_plan_condition(value):
        return "conditional"
    if _PLAN_NOT_APPROVED.search(value):
        return "announced"
    if _PLAN_APPROVED.search(value):
        return "approved"
    return "announced"


PLAN_TIMING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"20\d{2}(?:\s*[~\-]\s*20\d{2})?년(?:\s*(?:상|하)반기|\s*\d{1,2}월)?|"
    r"(?:상|하)반기|[1-4]분기|연내|향후\s*\d+\s*년"
)
PLAN_CONDITION_PATTERN: Final[re.Pattern[str]] = _PLAN_CONDITIONAL
PLAN_EXPECTED_EFFECT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:매출|수익|원가|비용|효율|생산성|점유율).{0,12}"
    r"(?:확대|개선|증가|감소|절감|확보|강화|전환|상승|하락)|"
    r"(?:기대|예상)\s*효과"
)
PLAN_EFFECT_BINDING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"기대|예상|전망|목표|위해|도모|목적|효과"
)
INACTIVE_PLAN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"취소|철회|중단|백지화|폐기"
)
PLAN_EXECUTION_SIGNAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"출시|가동|양산|상용화|승인|허가|고객\s*확보|납품|판매\s*개시|"
    r"구축|도입|확대|수주|계약\s*체결|입점|공연|착공|준공"
)


_CLAUSE_SEPARATOR = re.compile(
    r"[.!?;]|,(?=\s)|\s+(?:그리고|그러나|반면|이후|뒤|후)\s+|"
    r"(?:했으며|하였으며|하며|했고|하였고)\s*"
)


def is_company_stated_plan_effect(evidence: str, effect_excerpt: str) -> bool:
    """계획 효과가 과거 결과가 아니라 회사의 기대·목표 절에 결속됐는지 본다."""

    effect = " ".join(str(effect_excerpt or "").split()).casefold()
    if not effect or PLAN_EXPECTED_EFFECT_PATTERN.search(effect_excerpt) is None:
        return False
    clauses = [
        " ".join(value.split()).casefold()
        for value in _CLAUSE_SEPARATOR.split(str(evidence or ""))
        if value.strip()
    ]
    return any(
        effect in clause and PLAN_EFFECT_BINDING_PATTERN.search(clause) is not None
        for clause in clauses
    )


def plan_is_active(evidence: str) -> bool:
    """취소·철회·중단된 과거 계획을 현재 전략으로 승격하지 않는다."""

    return INACTIVE_PLAN_PATTERN.search(str(evidence or "")) is None


def plan_timing_has_passed(plan_timing: str, report_date: date) -> bool:
    """명시한 계획 기간의 끝이 보고서 기준일보다 이미 지났는지 계산한다."""

    value = " ".join(str(plan_timing or "").split())
    years = [int(year) for year in re.findall(r"20\d{2}", value)]
    if not years:
        return False
    end_year = max(years)
    if end_year != report_date.year:
        return end_year < report_date.year
    if re.search(r"하반기|[34]분기", value):
        return False
    if "상반기" in value:
        return report_date > date(end_year, 6, 30)
    quarter = re.search(r"([1-4])분기", value)
    if quarter:
        end_month = int(quarter.group(1)) * 3
        end_day = 31 if end_month in {3, 12} else 30
        return report_date > date(end_year, end_month, end_day)
    month = re.search(r"20\d{2}년\s*(\d{1,2})월", value)
    if month:
        end_month = int(month.group(1))
        next_month = date(end_year + (end_month == 12), end_month % 12 + 1, 1)
        return report_date >= next_month
    # 연도만 적힌 계획은 그 해 말까지 유효한 것으로 보수적으로 본다.
    return False


def excerpts_are_in_distinct_clauses(
    evidence: str, first: str, second: str
) -> bool:
    """두 발췌가 원문의 서로 다른 절에 있을 때만 참을 반환한다."""

    first_value = " ".join(str(first or "").split()).casefold()
    second_value = " ".join(str(second or "").split()).casefold()
    if (
        not first_value
        or not second_value
        or first_value in second_value
        or second_value in first_value
    ):
        return False
    clauses = [
        " ".join(value.split()).casefold()
        for value in _CLAUSE_SEPARATOR.split(str(evidence or ""))
        if value.strip()
    ]
    first_indexes = {i for i, clause in enumerate(clauses) if first_value in clause}
    second_indexes = {i for i, clause in enumerate(clauses) if second_value in clause}
    return any(left != right for left in first_indexes for right in second_indexes)


def excerpts_overlap(first: str, second: str) -> bool:
    """두 구조 발췌가 같은 의미 문자열을 포함해 재사용하는지 확인한다."""

    first_value = re.sub(r"\s", "", str(first or "")).casefold()
    second_value = re.sub(r"\s", "", str(second or "")).casefold()
    return bool(
        first_value
        and second_value
        and (first_value in second_value or second_value in first_value)
    )


NEXT_CHECK_METRIC_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:본계약(?:\s*체결)?(?:\s*여부)?|매출\s*발생(?:\s*여부)?|"
    r"유료\s*고객(?:\s*수)?|장애(?:\s*건수)?|점포당\s*판매(?:량|액)?|"
    r"수주\s*잔고|재고|납기|마진|채널별\s*마진|제작비|조달비용|"
    r"임상\s*이벤트|재계약|환율|현금흐름|재구매|조치\s*이행|"
    r"심사\s*진행|사용\s*기관|수가\s*적용|IP별\s*기여|"
    r"비활동기\s*매출|반복\s*활동|"
    r"[A-Za-z가-힣0-9·\s]{1,24}(?:율|률|액|량|건수|일수|여부|비중|기간|단가|잔고|결과))"
)
_GENERIC_NEXT_CHECK_METRIC: Final[re.Pattern[str]] = re.compile(
    r"(?:문제|과제|부담|해결|개선)\s*여부"
)


def is_objective_next_check_metric(metric: str, issue_scope: str = "") -> bool:
    """문제 문구 반복이 아닌 짧고 관찰 가능한 다음 확인 지표만 받는다."""

    value = " ".join(str(metric or "").split())
    scope = re.sub(r"\s", "", str(issue_scope or "")).casefold()
    compact = re.sub(r"\s", "", value).casefold()
    return bool(
        value
        and NEXT_CHECK_METRIC_PATTERN.fullmatch(value)
        and _GENERIC_NEXT_CHECK_METRIC.fullmatch(value) is None
        and (not scope or compact != scope)
    )


_BINDING_WORD_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z][A-Za-z0-9._-]{1,}|[가-힣]{2,}"
)
_BINDING_STOP_WORDS: Final[frozenset[str]] = frozenset(
    {
        "현재",
        "문제",
        "과제",
        "부담",
        "미해결",
        "대응",
        "진행",
        "추진",
        "회사",
        "반기보고서",
        "사업보고서",
        "영업",
        "현황",
        "관리",
    }
)


def _binding_terms(*values: str) -> set[str]:
    terms: set[str] = set()
    for raw in values:
        for match in _BINDING_WORD_PATTERN.findall(str(raw or "")):
            term = match.casefold()
            term = re.sub(r"(?:에게|에서|으로|의|을|를|이|가|은|는)$", "", term)
            if len(term) >= 2 and term not in _BINDING_STOP_WORDS:
                terms.add(term)
    return terms


def response_is_bound_to_issue(
    issue_scope: str,
    issue_evidence: str,
    response_evidence: str,
    legal_entity: str = "",
) -> bool:
    """대응 원문이 연결된 문제의 대상·범위 핵심어를 실제로 공유하는지 본다."""

    company_terms = _binding_terms(legal_entity)
    scope_terms = _binding_terms(issue_scope) - company_terms
    response_terms = _binding_terms(response_evidence) - company_terms
    if scope_terms:
        return bool(scope_terms & response_terms)
    issue_terms = _binding_terms(issue_evidence) - company_terms
    return bool(issue_terms & response_terms)


INITIAL_SIGNAL_FUTURE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"예정|계획|향후|내년|추진할|출시할|가동할|구축할|"
    r"필요|대기|요청|상정|준비|검토|목표"
)

# 승인·허가·가동·출시·매출·수주는 단어 자체가 아니라 관찰된 동작·결과가
# 함께 있을 때만 위의 일반 진척 표현(완료·취득·발생·시작 등)으로 인정된다.
INITIAL_SIGNAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"시작|착수|완료|확인|개선|증가|감소|체결|납품|검증|"
    r"시험\s*생산|선정|취득|발생|달성|개시|확보|준공|운영\s*중|가동\s*중"
)


def is_observed_initial_signal(value: str) -> bool:
    """착수 뒤 관찰된 진척·결과이며 미래 계획 문구가 아닌지 확인한다."""

    text = str(value or "")
    return bool(
        INITIAL_SIGNAL_PATTERN.search(text)
        and INITIAL_SIGNAL_FUTURE_PATTERN.search(text) is None
    )


VALUE_CHAIN_STAGE_LABELS: Final[dict[str, str]] = {
    "planning": "기획",
    "development": "개발·연구",
    "procurement": "조달",
    "production": "생산·운영",
    "distribution": "유통·물류",
    "sales": "판매·납품",
    "after_sales": "사후 서비스",
}
VALUE_CHAIN_STAGE_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "planning": re.compile(r"기획|설계"),
    "development": re.compile(r"연구\s*개발|연구개발|R&D|개발"),
    "procurement": re.compile(r"조달|원재료|원료|부품|공급받"),
    "production": re.compile(r"생산|제조|가공|공정|처리|운영|시스템"),
    "distribution": re.compile(r"유통|물류|대리점|판매망|채널|플랫폼"),
    "sales": re.compile(r"판매|납품|공급"),
    "after_sales": re.compile(r"유지\s*보수|A/S|사후\s*서비스|점검"),
}

RELATIONSHIP_TYPE_LABELS: Final[dict[str, str]] = {
    "internal_operation": "내부 직접 운영",
    "subsidiary": "종속회사",
    "ownership": "소유·지분",
    "supplier": "공급·조달 계약",
    "distribution": "유통 관계",
    "license": "라이선스",
    "joint_business": "공동사업·공동개발",
    "exclusive_contract": "독점 계약",
    "nonexclusive_contract": "비독점 계약",
}
RELATIONSHIP_TYPE_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "internal_operation": re.compile(r"직접|자체|내부|보유|사용|직영"),
    "subsidiary": re.compile(r"종속회사|자회사|연결\s*대상"),
    "ownership": re.compile(r"소유|지분|보유"),
    "supplier": re.compile(r"조달|공급\s*계약|공급사|원재료|원료|부품"),
    "distribution": re.compile(r"유통|대리점|판매망|채널|플랫폼"),
    "license": re.compile(r"라이선스|사용권|실시권"),
    "joint_business": re.compile(r"공동|협력|제휴"),
    "exclusive_contract": re.compile(r"독점"),
    "nonexclusive_contract": re.compile(r"비독점"),
}
RELATIONSHIP_TYPES_BY_CLAIM: Final[dict[str, frozenset[str]]] = {
    "operating_core": frozenset(
        {"internal_operation", "subsidiary", "ownership"}
    ),
    "partner_role": frozenset(
        {
            "supplier",
            "distribution",
            "license",
            "joint_business",
            "exclusive_contract",
            "nonexclusive_contract",
        }
    ),
}

CUSTOMER_ROLE: Final[re.Pattern[str]] = re.compile(
    r"고객(?:사|군|층)?|구매자|최종\s*사용자|수혜자|매출처|발주처"
)
CUSTOMER_OUTBOUND: Final[re.Pattern[str]] = re.compile(
    r"(?:[A-Za-z가-힣0-9]+(?:사|기업|업체)|고객(?:사)?)"
    r"(?:에(?!서)|에게|를\s*대상으로).{0,40}(?:납품|판매|공급)"
)
PARTNER_ROLE_MARKER: Final[re.Pattern[str]] = re.compile(
    r"유통사|대리점|판매\s*대행|납품\s*대행|유통\s*대행|"
    r"플랫폼(?:을\s*통해|에서|과)|판매망\s*(?:운영|계약)"
)
SUPPLIER_INBOUND_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"공급받|조달|공급사|(?:원재료|원료|부품).{0,20}(?:매입|조달|공급\s*계약)|"
    r"(?:매입|조달).{0,20}(?:원재료|원료|부품)"
)


def looks_like_customer_outbound(evidence: str, subject_label: str = "") -> bool:
    """명시 고객 또는 단순 판매 대상을 7장 운영 파트너에서 가려낸다."""

    value = str(evidence or "")
    subject = " ".join(str(subject_label or "").split())
    named_outbound = bool(
        subject
        and re.search(
            rf"{re.escape(subject)}(?:에(?!서)|에게|를\s*대상으로).{{0,40}}"
            r"(?:납품|판매|공급)",
            value,
        )
    )
    return bool(CUSTOMER_ROLE.search(subject) or named_outbound)


FUTURE_OPERATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"가동\s*준비|구축\s*(?:중|예정|계획)|도입\s*(?:중|예정|계획)|"
    r"시험\s*(?:중|단계)|준비\s*중|향후|예정|계획"
)
CURRENT_OPERATING_ROLE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"운영(?:한다|\s*중|하고|을\s*담당)|사용(?:한다|\s*중|하고|하며)|"
    r"생산(?:한다|\s*중|하고)|제조(?:한다|\s*중|하고)|처리(?:한다|\s*중|하고)|"
    r"공급(?:한다|\s*중|받는다|받고)|조달(?:한다|\s*중|하고)|"
    r"보유(?:한다|\s*중|하고|하며)|소유(?:한다|\s*중|하고|하며)|"
    r"공동\s*(?:연구\s*)?개발.{0,8}(?:수행|진행|운영)(?:한다|\s*중|하고|하며)|"
    r"유통(?:한다|\s*중|하고|하며|을\s*담당|을\s*운영)|"
    r"판매(?:한다|\s*중|하고|하며)|"
    r"납품\s*(?:대행|중)|유지\s*보수|계약\s*(?:유효|이행|중)|거래\s*중"
)


def has_current_operating_role(evidence: str, role_excerpt: str) -> bool:
    """MOU·일회성 체결이 아닌 현재 반복 역할이 원문 발췌에 있는지 확인한다."""

    role = str(role_excerpt or "")
    return bool(
        role
        and role in str(evidence or "")
        and re.search(r"종료|해지|만료|중단|유효\s*여부|검토", role) is None
        and CURRENT_OPERATING_ROLE_PATTERN.search(role)
    )


def internal_operation_is_company_controlled(
    evidence: str,
    legal_entity: str,
    *,
    source_publisher: str = "",
    allow_self_reference: bool = False,
) -> bool:
    """내부 운영은 분석 법인이 직접 통제한다는 원문 주어가 있어야 한다."""

    company = re.sub(
        r"\s|\(주\)|㈜|주식회사|유한회사",
        "",
        str(legal_entity or ""),
    )
    if not company:
        return False
    compact_evidence = re.sub(r"\s", "", str(evidence or ""))
    match = re.search(
        rf"{re.escape(company)}(?P<middle>.{{0,60}}?)(?:직접|자체|내부|보유|사용|직영)",
        compact_evidence,
    )
    if match is not None:
        middle = re.sub(r"^(?:는|은|가|이)", "", match.group("middle"))
        # 회사명 뒤에 다른 실명 주어가 다시 나오면 그 실명의 행동을 분석 법인의
        # 내부 운영으로 승격하지 않는다.
        if re.search(
            r"(?:[A-Za-z][A-Za-z0-9._-]*(?:사)?|[가-힣]{2,}?)(?:가|이|는|은)",
            middle,
        ) is None:
            return True
    publisher = re.sub(
        r"\s|\(주\)|㈜|주식회사|유한회사",
        "",
        str(source_publisher or ""),
    )
    self_reference_is_bound = allow_self_reference or (
        publisher and publisher == company
    )
    return bool(
        self_reference_is_bound
        and re.search(
            r"당사(?:는|가|에서)?.{0,60}(?:직접|자체|내부|보유|사용|직영)",
            compact_evidence,
        )
    )


def ownership_is_company_held(
    evidence: str,
    legal_entity: str,
    *,
    source_publisher: str = "",
    allow_self_reference: bool = False,
) -> bool:
    """소유·지분 관계도 분석 법인이 보유 주체일 때만 인정한다."""

    company = re.sub(
        r"\s|\(주\)|㈜|주식회사|유한회사",
        "",
        str(legal_entity or ""),
    )
    compact_evidence = re.sub(r"\s", "", str(evidence or ""))
    if not company:
        return False
    match = re.search(
        rf"{re.escape(company)}(?P<middle>.{{0,60}}?)(?:소유|지분|보유)",
        compact_evidence,
    )
    if match is not None:
        middle = re.sub(r"^(?:는|은|가|이)", "", match.group("middle"))
        if re.search(
            r"(?:[A-Za-z][A-Za-z0-9._-]*(?:사)?|[가-힣]{2,}?)(?:가|이|는|은)",
            middle,
        ) is None:
            return True
    publisher = re.sub(
        r"\s|\(주\)|㈜|주식회사|유한회사",
        "",
        str(source_publisher or ""),
    )
    self_reference_is_bound = allow_self_reference or (
        publisher and publisher == company
    )
    return bool(
        self_reference_is_bound
        and re.search(
            r"당사(?:는|가|에서)?.{0,60}(?:소유|지분|보유)",
            compact_evidence,
        )
    )

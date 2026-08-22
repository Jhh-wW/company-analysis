"""새 회사분석 정본에 맞춰 원문 문장을 고르고 의미 섹션에 배치한다.

AI의 역할은 이미 수집된 문장의 번호와 배치할 섹션을 고르는 것뿐이다.
보고서에 표시할 사실 문장은 프로그램이 원문에서 복사하고, 기존 W1~W3
검사로 다시 대조한다. 경쟁사 비교는 비교사 공식 자료가 따로 수집된 경우에만
만들 수 있으므로 이 단계에서는 만들지 않는다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping

from src.features.company_specificity.logic import assess_claim
from src.features.pipeline.market_contract import (
    MARKET_STAGE_EVIDENCE_PATTERNS,
    MARKET_STAGES,
)
from src.features.pipeline.section567_contract import (
    FUTURE_OPERATION_PATTERN,
    PLAN_EXECUTION_SIGNAL_PATTERN,
    PLAN_STATUSES,
    PLAN_TIMING_PATTERN,
    RELATIONSHIP_TYPE_PATTERNS,
    RELATIONSHIP_TYPES_BY_CLAIM,
    SUPPLIER_INBOUND_PATTERN,
    VALUE_CHAIN_STAGE_PATTERNS,
    excerpts_are_in_distinct_clauses,
    excerpts_overlap,
    expected_plan_status,
    has_plan_condition,
    has_current_operating_role,
    internal_operation_is_company_controlled,
    is_company_stated_plan_effect,
    is_observed_initial_signal,
    is_objective_next_check_metric,
    looks_like_customer_outbound,
    ownership_is_company_held,
    plan_is_active,
    response_is_bound_to_issue,
)
from src.features.spanselect.logic import number_sentences
from src.features.spanselect.constants import CANONICAL_SELECTION_MAX_TOKENS
from src.shared.span_selection_diagnostics import attach_round_result


CANONICAL_SOURCE_SECTION_IDS: tuple[str, ...] = (
    "identity",
    "business_model",
    "portfolio",
    "past_changes",
    "current_challenges",
    "future_strategy",
    "operations_partners",
    "culture",
)
_SELF_PUBLISHED_FRAGMENT_KINDS = frozenset(
    {"사업", "사업내용", "MD&A", "전략", "신규사업전망", "홈페이지"}
)


def _validation_rejection_reason_code(item: Mapping[str, str]) -> str:
    """사용자용 문구를 원문 없는 닫힌 진단 범주로만 축약한다."""

    explicit = str(item.get("reason_code") or "")
    if explicit:
        return explicit
    reason = str(item.get("reason") or "")
    if reason in {"없는 번호 또는 섹션", "원문 조각 없음"}:
        return "invalid_reference_or_section"
    if reason == "같은 사실 중복 배치":
        return "duplicate_assignment"
    if reason == "섹션과 claim_type 불일치":
        return "claim_type_section_mismatch"
    if reason == "대상 이름이 원문에 없음":
        return "subject_label_not_in_source"
    if "고객·시장" in reason or "시장 " in reason or "시장 단계" in reason:
        return "market_contract_failure"
    if reason.startswith("현재 문제") or reason in {
        "다음 확인 지표가 원문에 없음",
        "객관적 다음 확인 지표가 아님",
    }:
        return "current_issue_contract_failure"
    if reason.startswith("현재 대응") or reason in {
        "대응 사실에 다음 확인 지표 입력",
        "초기 신호가 대응 원문에 없음",
        "대응 행동을 초기 신호로 중복",
        "관찰된 초기 진척·결과가 아님",
        "5장 외 현재 과제 구조 필드 입력",
    }:
        return "current_response_contract_failure"
    if reason in {
        "가치사슬 단계의 직접 근거 없음",
        "관계 유형의 직접 근거 없음",
        "고객사를 운영 파트너로 분류",
        "미실행 계획을 현재 운영으로 분류",
        "현재 반복 운영 역할의 직접 근거 없음",
        "분석 법인의 직접 운영 근거 없음",
        "분석 법인의 소유·지분 근거 없음",
        "공급·조달 방향의 직접 근거 없음",
        "7장 외 운영 구조 필드 입력",
    }:
        return "operations_partner_contract_failure"
    if "계획" in reason or reason == "6장 외 미래 계획 구조 필드 입력":
        return "future_plan_contract_failure"
    if "제품 포트폴리오" in reason or "중점 제품" in reason or "중점 추진" in reason:
        return "portfolio_contract_failure"
    if "사건" in reason or reason == "완료 실행 외 항목에 사건일 입력":
        return "completed_execution_contract_failure"
    if "연결되지 않음" in reason or "결속되지 않음" in reason or reason in {
        "대응 외 문제 연결 입력",
        "변화 해석 외 근거 연결 입력",
    }:
        return "cross_reference_contract_failure"
    if "변화 해석" in reason:
        return "change_basis_contract_failure"
    return "other_validation_failure"


SECTION_GUIDES: dict[str, str] = {
    "identity": (
        "회사가 직접 밝힌 공식 자기정의와 공식 사업 근거를 쉬운 말로 합성한 "
        "정체성 요약·산업 내 역할. 두 유형을 구분하고 회사 소개·공시처럼 "
        "회사가 책임지는 자료만"
    ),
    "business_model": (
        "구매자·사용자·수혜자, 제공 가치, 과금 방식, 수익 경로, "
        "핵심·성장·진입 시장을 설명하는 현재 사실"
    ),
    "portfolio": (
        "현재 실제 출시·판매·운영·투자·유통 확대가 확인되는 핵심 제품·서비스·"
        "브랜드·IP·사업. 단순 계획이나 제품 목록은 제외"
    ),
    "past_changes": (
        "기준일 전 36개월 안에 이미 실행했고 결과나 상태 변화가 확인된 사건, "
        "또는 완료된 3개 사업연도의 실제 실적"
    ),
    "current_challenges": (
        "기준일에도 해결되지 않은 회사 고유 문제와 회사가 이미 시작한 대응. "
        "외부 전망·일반 업계 위험은 제외"
    ),
    "future_strategy": (
        "회사가 공식 발표했지만 아직 실행 결과가 확인되지 않은 계획·목표·조건부 일정. "
        "애널리스트 전망은 제외"
    ),
    "operations_partners": (
        "현재 반복 작동하는 내부 생산·기술·데이터·운영 체계와 외부 파트너·유통·"
        "라이선스의 확인된 역할"
    ),
    "culture": (
        "공식 채용·문화 자료가 밝힌 전사 가치 또는 조직·상황·행동 범위가 명시된 "
        "실제 업무 사례. 직무별 KPI·지원 전략은 제외"
    ),
}


CLAIM_TYPES_BY_SECTION: dict[str, frozenset[str]] = {
    "identity": frozenset(
        {"identity_summary", "official_self_definition", "operating_scope"}
    ),
    "business_model": frozenset({"revenue_model", "customer_market"}),
    "portfolio": frozenset({"priority_product"}),
    # historical_performance는 검증된 DART 표 행에서 프로그램이 만들며 원문
    # 문장 선택 항목으로 중복 생성하지 않는다.
    "past_changes": frozenset({"completed_execution", "change_interpretation"}),
    "current_challenges": frozenset({"current_issue", "current_response"}),
    "future_strategy": frozenset({"future_plan"}),
    "operations_partners": frozenset({"operating_core", "partner_role"}),
    "culture": frozenset({"official_value", "work_example"}),
}

PORTFOLIO_STAGES: tuple[str, ...] = ("주력", "성장", "안정", "신규")
HISTORICAL_PERFORMANCE_BASIS_PREFIX = "historical-performance:"
PRIORITY_SIGNAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "출시·운영": re.compile(r"출시|공개|도입|가동|운영|판매|생산|공급|서비스"),
    "매출·이용증가": re.compile(
        r"(?:매출|판매량|출하량|이용자|가입자).{0,16}(?:증가|성장)|"
        r"(?:증가|성장).{0,16}(?:매출|판매량|출하량|이용자|가입자)"
    ),
    "생산확대": re.compile(
        r"(?:생산량|생산능력|생산s*규모).{0,16}(?:증가|확대|늘)|"
        r"(?:증가|확대|늘).{0,16}(?:생산량|생산능력|생산s*규모)"
    ),
    "투자·증설": re.compile(r"투자|증설|설비|공장|라인|인수|연구개발"),
    "유통·지역확대": re.compile(r"유통|판매망|채널|진출|수출|해외|지역|국가"),
    "공식우선과제": re.compile(r"전략|핵심|중점|우선|주력|집중"),
    "파트너확대": re.compile(r"파트너|제휴|협력|공동|계약"),
}


_PROMPT = """공식 근거 기반 회사분석 보고서의 사실 배치 작업이다.
문장을 새로 쓰지 말고 아래 목록의 문장 번호만 고른다.

규칙
1. 답은 schema에 정의된 구조 필드만 낸다. 원문에 없는 해석·원인·우위·전망을 만들지 않는다.
2. 같은 sid는 한 섹션에만 배치한다. 한 문장에 상태가 섞여 있으면 고르지 않는다.
3. 개발완료·검증·MOU·계약·납품·매출·반복매출을 서로 같은 상태로 취급하지 않는다.
4. 특정 자회사·제품·지역의 사실을 상위 회사·제품군·시장 전체로 넓히지 않는다.
5. 과거는 완료 사실, 현재는 미해결 문제와 진행 중 대응, 미래는 미실행 공식 계획이다.
6. 원문에 직접 인과가 없으면 원인·기여·영향을 뜻하는 문장을 고르지 않는다.
7. 주가·목표가·투자의견·급여·근속·복지·직무별 KPI·자기소개서·면접 조언은 제외한다.
8. 맞는 근거가 없는 섹션은 비운다. 일반론으로 채우지 않는다.
9. claim_type은 문장의 실제 역할과 일치하게 고른다. 조사자가 공식 사업 근거를
   합성한 정체성은 identity_summary, 회사가 직접 밝힌 자기정의만
   official_self_definition으로 고른다. 고객·시장에는 원문에 그대로 있는 관찰 문구를
   market_observation으로 낸다. market_stage(핵심·성장·진입)는 그 단계가 원문에
   직접 적힌 경우에만 내고, 단순 판매·매출 발생이면 비운다.
   현재 중점 제품에는 근거 범위 안의 기능적 product_role, 근거가 충분할 때만
   portfolio_stage(주력·성장·안정·신규), 같은 답의 2장 revenue_model sid,
   원문에서 직접 확인되는 서로 다른 priority_signals 두 개 이상을 함께 낸다.
10. current_issue에는 원문에서 확인되는 다음 판단 지표명을 next_check_metric으로 낸다.
    current_response는 같은 답 안의 current_issue sid를 response_to_sid로 연결하고,
    원문의 대응 행동을 response_action으로 낸다. 그 행동과 다른 절에 별도 초기 진척이
    있을 때만 initial_signal을 낸다.
11. future_plan에는 plan_status(announced·approved·conditional)를 원문의 승인·조건 상태와
    맞춰 낸다. 원문에 있는 경우만 plan_timing·plan_condition·plan_expected_effect를 내고,
    실행 여부를 나중에 확인할 원문 행동을 plan_execution_signal로 낸다.
12. operating_core·partner_role에는 value_chain_stage와 relationship_type을 서로 분리해
    내고, 현재 반복 역할의 원문 발췌를 operation_role로 낸다. 돈을 내는 고객사를
    내부 운영 주체나 파트너로 분류하지 않는다.
13. change_interpretation은 같은 답 안의 completed_execution sid 또는 아래에 따로 제공된
    완료 실적 참조만 basis_sids로 연결한다. 내부 fact_id나 목록에 없는 참조는 만들지 않는다.
14. completed_execution은 원문에 직접 적힌 사건 연도 또는 날짜를 event_date로 낸다.
    공시 발표일을 사건일로 대신 쓰지 않는다.
15. sid·revenue_model_sid·response_to_sid와 숫자형 basis_sids에는 후보 문장 앞
    대괄호 안의 번호만 쓰되, 대괄호 자체는 넣지 않는다. 예: [12-3] 문장을
    가리키면 12-3이다. subject_label과 모든 원문 발췌 필드는 해당 후보 문장에
    연속해서 그대로 있는 짧은 문자열만 쓰고, 없으면 비운다. 당사라고 적혔으면
    회사명으로 바꾸지 말고 당사를 그대로 쓴다.

완료 실적 근거 참조
{historical_performance_lines}

섹션
{section_guides}

후보 문장
{candidate_lines}
"""


@dataclass(frozen=True)
class CanonicalPick:
    """검증된 원문 문장 하나와 그 의미 섹션."""

    section_id: str
    sentence: str
    fragment_id: int
    sid: str = ""
    claim_type: str = ""
    subject_label: str = ""
    market_stage: str = ""
    market_observation: str = ""
    product_role: str = ""
    portfolio_stage: str = ""
    revenue_model_sid: str = ""
    response_to_sid: str = ""
    basis_sids: tuple[str, ...] = field(default_factory=tuple)
    priority_signals: tuple[str, ...] = field(default_factory=tuple)
    event_date: str = ""
    response_action: str = ""
    initial_signal: str = ""
    next_check_metric: str = ""
    plan_status: str = ""
    plan_timing: str = ""
    plan_condition: str = ""
    plan_expected_effect: str = ""
    plan_execution_signal: str = ""
    operation_role: str = ""
    value_chain_stage: str = ""
    relationship_type: str = ""


def _normalize_candidate_sid(value: object) -> str:
    """숫자 후보 SID에 정확히 한 쌍 붙은 표시용 대괄호만 제거한다."""

    raw_sid = str(value or "").strip()
    bracketed_sid = re.fullmatch(
        r"\[([1-9][0-9]*-[1-9][0-9]*)\]", raw_sid
    )
    return bracketed_sid.group(1) if bracketed_sid else raw_sid


def historical_performance_basis_sid(fiscal_year: object) -> str:
    """내부 ``fact_id`` 대신 모델에 보여 줄 완료 실적 참조를 만든다."""

    year = str(fiscal_year or "").strip()
    if not re.fullmatch(r"20\d{2}", year):
        return ""
    return f"{HISTORICAL_PERFORMANCE_BASIS_PREFIX}{year}"


def historical_performance_basis_options(tables: Iterable[Any]) -> dict[str, str]:
    """정본 실적표의 유일한 FY 행만 안전한 선택용 참조로 바꾼다.

    참조는 조립 단계에서 실제 ``FactRecord.fact_id``로 치환된다. 같은 FY 행이
    둘 이상이면 어느 사실인지 추측하지 않고 그 FY를 선택지에서 제외한다.
    """

    candidates: dict[str, list[str]] = {}
    for table in tables:
        if not bool(getattr(table, "is_valid", False)):
            continue
        headers = [str(value or "").strip() for value in getattr(table, "headers", [])]
        if not headers or headers[0] != "사업연도":
            continue
        if str(getattr(table, "display_unit", "") or "").strip() != "억원":
            continue
        caption = " ".join(str(getattr(table, "caption", "") or "").split())
        for row in getattr(table, "rows", []):
            if not isinstance(row, list) or len(row) != len(headers):
                continue
            reference = historical_performance_basis_sid(row[0] if row else "")
            if not reference:
                continue
            fields = " · ".join(
                f"{header} {str(value or '').strip()}"
                for header, value in zip(headers, row)
            )
            candidates.setdefault(reference, []).append(
                f"{caption} · {fields}" if caption else fields
            )
    return {
        reference: descriptions[0]
        for reference, descriptions in candidates.items()
        if len(descriptions) == 1
    }


def _normalized_historical_performance_bases(
    bases: Mapping[str, str] | None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_reference, raw_description in (bases or {}).items():
        reference = str(raw_reference or "").strip()
        description = " ".join(str(raw_description or "").split())
        year = reference.removeprefix(HISTORICAL_PERFORMANCE_BASIS_PREFIX)
        if reference != historical_performance_basis_sid(year) or not description:
            continue
        out[reference] = description
    return out


def build_prompt(
    candidate_lines: list[str],
    historical_performance_bases: Mapping[str, str] | None = None,
) -> str:
    guides = "\n".join(
        f"- {section_id}: {SECTION_GUIDES[section_id]}"
        for section_id in CANONICAL_SOURCE_SECTION_IDS
    )
    performance_bases = _normalized_historical_performance_bases(
        historical_performance_bases
    )
    performance_lines = "\n".join(
        f"- {reference}: {description}"
        for reference, description in performance_bases.items()
    ) or "- 없음"
    return _PROMPT.format(
        section_guides=guides,
        historical_performance_lines=performance_lines,
        candidate_lines="\n".join(candidate_lines),
    )


def answer_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section_id": {
                            "type": "string",
                            "enum": list(CANONICAL_SOURCE_SECTION_IDS),
                        },
                        "sid": {
                            "type": "string",
                            "description": (
                                "후보 문장 앞 대괄호 안의 양의 정수-양의 정수 번호. "
                                "대괄호는 제외한다."
                            ),
                        },
                        "claim_type": {
                            "type": "string",
                            "enum": sorted(
                                {
                                    claim_type
                                    for values in CLAIM_TYPES_BY_SECTION.values()
                                    for claim_type in values
                                }
                            ),
                        },
                        "subject_label": {
                            "type": "string",
                            "description": (
                                "후보 문장에 연속해서 그대로 있는 짧은 대상 문자열. "
                                "없으면 빈 문자열이며 당사 같은 자기지칭을 회사명으로 "
                                "바꾸지 않는다."
                            ),
                        },
                        "market_stage": {
                            "type": "string",
                            "enum": ["", *sorted(MARKET_STAGES)],
                        },
                        "market_observation": {"type": "string"},
                        "product_role": {
                            "type": "string",
                        },
                        "portfolio_stage": {
                            "type": "string",
                            "enum": ["", *PORTFOLIO_STAGES],
                        },
                        "revenue_model_sid": {
                            "type": "string",
                            "description": (
                                "빈 문자열 또는 같은 답의 revenue_model 후보 번호. "
                                "대괄호는 제외한다."
                            ),
                        },
                        "response_to_sid": {
                            "type": "string",
                            "description": (
                                "빈 문자열 또는 같은 답의 current_issue 후보 번호. "
                                "대괄호는 제외한다."
                            ),
                        },
                        "basis_sids": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "description": (
                                    "같은 답의 completed_execution 후보 번호 또는 "
                                    "제공된 완료 실적 참조. 후보 번호의 대괄호는 제외한다."
                                ),
                            },
                            "uniqueItems": True,
                        },
                        "priority_signals": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": list(PRIORITY_SIGNAL_PATTERNS),
                            },
                            "uniqueItems": True,
                        },
                        "event_date": {"type": "string"},
                        "response_action": {"type": "string"},
                        "initial_signal": {"type": "string"},
                        "next_check_metric": {"type": "string"},
                        "plan_status": {
                            "type": "string",
                            "enum": ["", *sorted(PLAN_STATUSES)],
                        },
                        "plan_timing": {"type": "string"},
                        "plan_condition": {"type": "string"},
                        "plan_expected_effect": {"type": "string"},
                        "plan_execution_signal": {"type": "string"},
                        "operation_role": {"type": "string"},
                        "value_chain_stage": {
                            "type": "string",
                            "enum": ["", *sorted(VALUE_CHAIN_STAGE_PATTERNS)],
                        },
                        "relationship_type": {
                            "type": "string",
                            "enum": ["", *sorted(RELATIONSHIP_TYPE_PATTERNS)],
                        },
                    },
                    "required": [
                        "section_id",
                        "sid",
                        "claim_type",
                        "subject_label",
                        "market_stage",
                        "market_observation",
                        "product_role",
                        "portfolio_stage",
                        "revenue_model_sid",
                        "response_to_sid",
                        "basis_sids",
                        "priority_signals",
                        "event_date",
                        "response_action",
                        "initial_signal",
                        "next_check_metric",
                        "plan_status",
                        "plan_timing",
                        "plan_condition",
                        "plan_expected_effect",
                        "plan_execution_signal",
                        "operation_role",
                        "value_chain_stage",
                        "relationship_type",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def select_canonical_spans(
    client: Any,
    frags: dict[int, dict[str, str]],
    steps: list[dict[str, Any]],
    *,
    engine: Any,
    company: str,
    model: str = "",
    historical_performance_bases: Mapping[str, str] | None = None,
) -> tuple[list[CanonicalPick], list[dict[str, str]]]:
    """번호 선택→원문 복사→W1~W3 대조를 거쳐 canonical 사실을 돌려준다."""

    sent_map, candidate_lines, excluded = number_sentences(
        frags, engine.split_sentences
    )
    engine_model = getattr(engine, "MODEL", "")
    used_model = model or engine_model
    if model:
        engine.MODEL = model
    performance_bases = _normalized_historical_performance_bases(
        historical_performance_bases
    )
    # 원문 문장 sid와 표 참조가 충돌하면 어느 근거인지 추측하지 않는다.
    performance_bases = {
        reference: description
        for reference, description in performance_bases.items()
        if reference not in sent_map
    }
    try:
        payload, usage = engine._ask(
            client,
            build_prompt(candidate_lines, performance_bases),
            answer_schema(),
            max_tokens=CANONICAL_SELECTION_MAX_TOKENS,
        )
    finally:
        if model:
            engine.MODEL = engine_model

    if isinstance(usage, dict):
        usage["model"] = used_model
    selected = list((payload or {}).get("items") or [])
    selection_step = {
        "step": "8_정본_사실배치",
        "usage": usage,
        "문장후보수": len(sent_map),
        "제외후보수": excluded,
        "선택수": len(selected),
    }
    steps.append(selection_step)

    if not payload:
        attach_round_result(
            selection_step,
            requested_max_tokens=CANONICAL_SELECTION_MAX_TOKENS,
            provider_selected=len(selected),
            validation_kept=0,
            validation_rejected=0,
        )
        return [], []

    draft_items: list[Any] = []
    used_sids: set[str] = set()
    rejected: list[dict[str, str]] = []
    picks_by_sid: dict[str, CanonicalPick] = {}
    for item in selected:
        section_id = str(item.get("section_id") or "")
        sid = _normalize_candidate_sid(item.get("sid"))
        claim_type = str(item.get("claim_type") or "")
        found = sent_map.get(sid)
        if section_id not in CANONICAL_SOURCE_SECTION_IDS or found is None:
            rejected.append({"sid": sid, "reason": "없는 번호 또는 섹션"})
            continue
        if sid in used_sids:
            rejected.append({"sid": sid, "reason": "같은 사실 중복 배치"})
            continue
        fragment_id, sentence = found
        if fragment_id is None:
            rejected.append({"sid": sid, "reason": "원문 조각 없음"})
            continue
        fragment_kind = str(frags.get(fragment_id, {}).get("종류") or "")
        if claim_type not in CLAIM_TYPES_BY_SECTION.get(section_id, frozenset()):
            rejected.append({"sid": sid, "reason": "섹션과 claim_type 불일치"})
            continue

        normalized_sentence = " ".join(sentence.split()).casefold()
        subject_label = " ".join(str(item.get("subject_label") or "").split())
        if subject_label and subject_label.casefold() not in normalized_sentence:
            rejected.append({"sid": sid, "reason": "대상 이름이 원문에 없음"})
            continue
        market_stage = " ".join(str(item.get("market_stage") or "").split())
        market_observation = " ".join(
            str(item.get("market_observation") or "").split()
        )
        product_role = str(item.get("product_role") or "")
        portfolio_stage = str(item.get("portfolio_stage") or "")
        revenue_model_sid = _normalize_candidate_sid(item.get("revenue_model_sid"))
        response_to_sid = _normalize_candidate_sid(item.get("response_to_sid"))
        raw_basis_values = item.get("basis_sids") or []
        if not isinstance(raw_basis_values, list) or any(
            not str(value).strip() for value in raw_basis_values
        ):
            rejected.append({"sid": sid, "reason": "변화 해석 근거 참조 형식 오류"})
            continue
        raw_basis_sids = [_normalize_candidate_sid(value) for value in raw_basis_values]
        if len(raw_basis_sids) != len(set(raw_basis_sids)):
            rejected.append({"sid": sid, "reason": "변화 해석 근거 참조 중복"})
            continue
        basis_sids = tuple(raw_basis_sids)
        priority_signals = tuple(
            dict.fromkeys(
                str(value)
                for value in item.get("priority_signals") or []
                if str(value) in PRIORITY_SIGNAL_PATTERNS
            )
        )
        event_date = str(item.get("event_date") or "").strip()
        response_action = " ".join(
            str(item.get("response_action") or "").split()
        )
        initial_signal = " ".join(str(item.get("initial_signal") or "").split())
        next_check_metric = " ".join(
            str(item.get("next_check_metric") or "").split()
        )
        plan_status = str(item.get("plan_status") or "").strip()
        plan_timing = " ".join(str(item.get("plan_timing") or "").split())
        plan_condition = " ".join(str(item.get("plan_condition") or "").split())
        plan_expected_effect = " ".join(
            str(item.get("plan_expected_effect") or "").split()
        )
        plan_execution_signal = " ".join(
            str(item.get("plan_execution_signal") or "").split()
        )
        operation_role = " ".join(str(item.get("operation_role") or "").split())
        value_chain_stage = str(item.get("value_chain_stage") or "").strip()
        relationship_type = str(item.get("relationship_type") or "").strip()
        if claim_type == "customer_market":
            if not subject_label or not market_observation:
                rejected.append({"sid": sid, "reason": "고객·시장 관찰 근거 없음"})
                continue
            if market_observation.casefold() not in normalized_sentence:
                rejected.append({"sid": sid, "reason": "시장 관찰이 원문에 없음"})
                continue
            if market_stage not in ("", *MARKET_STAGES):
                rejected.append({"sid": sid, "reason": "허용되지 않는 시장 단계"})
                continue
            if market_stage and not MARKET_STAGE_EVIDENCE_PATTERNS[
                market_stage
            ].search(market_observation):
                rejected.append({"sid": sid, "reason": "시장 단계의 직접 근거 없음"})
                continue
        elif market_stage or market_observation:
            rejected.append({"sid": sid, "reason": "고객·시장 외 시장 정보 입력"})
            continue

        if claim_type == "current_issue":
            if response_action or initial_signal:
                rejected.append({"sid": sid, "reason": "현재 문제에 대응 행동·초기 신호 입력"})
                continue
            if not next_check_metric:
                rejected.append({"sid": sid, "reason": "현재 문제의 다음 확인 지표 없음"})
                continue
            if next_check_metric.casefold() not in normalized_sentence:
                rejected.append({"sid": sid, "reason": "다음 확인 지표가 원문에 없음"})
                continue
            if not is_objective_next_check_metric(
                next_check_metric, subject_label
            ):
                rejected.append({"sid": sid, "reason": "객관적 다음 확인 지표가 아님"})
                continue
        elif claim_type == "current_response":
            if next_check_metric:
                rejected.append({"sid": sid, "reason": "대응 사실에 다음 확인 지표 입력"})
                continue
            if not response_action:
                rejected.append({"sid": sid, "reason": "현재 대응 행동의 원문 발췌 없음"})
                continue
            if response_action.casefold() not in normalized_sentence:
                rejected.append({"sid": sid, "reason": "현재 대응 행동이 원문에 없음"})
                continue
            if initial_signal and initial_signal.casefold() not in normalized_sentence:
                rejected.append({"sid": sid, "reason": "초기 신호가 대응 원문에 없음"})
                continue
            if initial_signal and not excerpts_are_in_distinct_clauses(
                sentence, response_action, initial_signal
            ):
                rejected.append({"sid": sid, "reason": "대응 행동을 초기 신호로 중복"})
                continue
            if initial_signal and not is_observed_initial_signal(initial_signal):
                rejected.append({"sid": sid, "reason": "관찰된 초기 진척·결과가 아님"})
                continue
        elif response_action or initial_signal or next_check_metric:
            rejected.append({"sid": sid, "reason": "5장 외 현재 과제 구조 필드 입력"})
            continue

        plan_values = (
            plan_status,
            plan_timing,
            plan_condition,
            plan_expected_effect,
            plan_execution_signal,
        )
        if claim_type == "future_plan":
            if not plan_is_active(sentence):
                rejected.append({"sid": sid, "reason": "취소·철회·중단된 계획"})
                continue
            if not subject_label:
                rejected.append({"sid": sid, "reason": "미실행 계획의 구체 대상 없음"})
                continue
            if plan_status not in PLAN_STATUSES:
                rejected.append({"sid": sid, "reason": "미실행 계획 상태 없음"})
                continue
            if plan_status != expected_plan_status(sentence):
                rejected.append({"sid": sid, "reason": "계획 승인·조건 상태가 원문과 다름"})
                continue
            plan_excerpts = (
                plan_timing,
                plan_condition,
                plan_expected_effect,
                plan_execution_signal,
            )
            if any(
                value and value.casefold() not in normalized_sentence
                for value in plan_excerpts
            ):
                rejected.append({"sid": sid, "reason": "계획 구조 필드가 원문에 없음"})
                continue
            if plan_timing and PLAN_TIMING_PATTERN.search(plan_timing) is None:
                rejected.append({"sid": sid, "reason": "계획 시점 형식의 직접 근거 없음"})
                continue
            if plan_condition and not has_plan_condition(plan_condition):
                rejected.append({"sid": sid, "reason": "계획 선행 조건의 직접 근거 없음"})
                continue
            if (
                plan_expected_effect
                and not is_company_stated_plan_effect(sentence, plan_expected_effect)
            ):
                rejected.append({"sid": sid, "reason": "회사 제시 효과의 직접 근거 없음"})
                continue
            if plan_expected_effect and excerpts_overlap(
                plan_expected_effect, plan_execution_signal
            ):
                rejected.append({"sid": sid, "reason": "계획 행동을 예상 효과로 중복"})
                continue
            if plan_status == "conditional" and not plan_condition:
                rejected.append({"sid": sid, "reason": "조건부 계획의 선행 조건 없음"})
                continue
            if (
                not plan_execution_signal
                or PLAN_EXECUTION_SIGNAL_PATTERN.search(plan_execution_signal) is None
            ):
                rejected.append({"sid": sid, "reason": "계획 실행 확인 신호 없음"})
                continue
        elif any(plan_values):
            rejected.append({"sid": sid, "reason": "6장 외 미래 계획 구조 필드 입력"})
            continue

        if claim_type in RELATIONSHIP_TYPES_BY_CLAIM:
            stage_pattern = VALUE_CHAIN_STAGE_PATTERNS.get(value_chain_stage)
            relationship_pattern = RELATIONSHIP_TYPE_PATTERNS.get(relationship_type)
            if (
                not subject_label
                or not operation_role
                or operation_role.casefold() not in normalized_sentence
                or stage_pattern is None
                or stage_pattern.search(operation_role) is None
            ):
                rejected.append({"sid": sid, "reason": "가치사슬 단계의 직접 근거 없음"})
                continue
            if (
                relationship_type
                not in RELATIONSHIP_TYPES_BY_CLAIM[claim_type]
                or relationship_pattern is None
                or relationship_pattern.search(
                    sentence
                    if relationship_type == "subsidiary"
                    else operation_role
                )
                is None
            ):
                rejected.append({"sid": sid, "reason": "관계 유형의 직접 근거 없음"})
                continue
            if looks_like_customer_outbound(sentence, subject_label):
                rejected.append({"sid": sid, "reason": "고객사를 운영 파트너로 분류"})
                continue
            if FUTURE_OPERATION_PATTERN.search(sentence):
                rejected.append({"sid": sid, "reason": "미실행 계획을 현재 운영으로 분류"})
                continue
            if not has_current_operating_role(sentence, operation_role):
                rejected.append({"sid": sid, "reason": "현재 반복 운영 역할의 직접 근거 없음"})
                continue
            if (
                relationship_type == "internal_operation"
                and not internal_operation_is_company_controlled(
                    sentence,
                    company,
                    allow_self_reference=(
                        fragment_kind in _SELF_PUBLISHED_FRAGMENT_KINDS
                    ),
                )
            ):
                rejected.append({"sid": sid, "reason": "분석 법인의 직접 운영 근거 없음"})
                continue
            if (
                relationship_type == "ownership"
                and not ownership_is_company_held(
                    sentence,
                    company,
                    allow_self_reference=(
                        fragment_kind in _SELF_PUBLISHED_FRAGMENT_KINDS
                    ),
                )
            ):
                rejected.append({"sid": sid, "reason": "분석 법인의 소유·지분 근거 없음"})
                continue
            if (
                relationship_type == "supplier"
                and SUPPLIER_INBOUND_PATTERN.search(sentence) is None
            ):
                rejected.append({"sid": sid, "reason": "공급·조달 방향의 직접 근거 없음"})
                continue
        elif operation_role or value_chain_stage or relationship_type:
            rejected.append({"sid": sid, "reason": "7장 외 운영 구조 필드 입력"})
            continue

        if claim_type == "priority_product":
            if (
                not subject_label
                or not product_role.strip()
                or product_role in PORTFOLIO_STAGES
                or portfolio_stage not in ("", *PORTFOLIO_STAGES)
                or not revenue_model_sid
            ):
                rejected.append({"sid": sid, "reason": "제품 포트폴리오 역할 없음"})
                continue
            supported_signals = tuple(
                signal
                for signal in priority_signals
                if PRIORITY_SIGNAL_PATTERNS[signal].search(sentence)
            )
            if len(supported_signals) < 2:
                rejected.append({"sid": sid, "reason": "원문에 중점 추진 신호 2개 미만"})
                continue
            priority_signals = supported_signals
        elif product_role or portfolio_stage or revenue_model_sid or priority_signals:
            rejected.append({"sid": sid, "reason": "중점 제품 외 제품 역할·단계·신호 입력"})
            continue
        if claim_type != "current_response" and response_to_sid:
            rejected.append({"sid": sid, "reason": "대응 외 문제 연결 입력"})
            continue
        if claim_type != "change_interpretation" and basis_sids:
            rejected.append({"sid": sid, "reason": "변화 해석 외 근거 연결 입력"})
            continue
        if claim_type == "completed_execution":
            if not re.fullmatch(r"20\d{2}(?:-\d{2}-\d{2})?", event_date):
                rejected.append({"sid": sid, "reason": "완료 실행의 사건 연도·날짜 없음"})
                continue
            event_year = event_date[:4]
            if event_year not in sentence:
                rejected.append({"sid": sid, "reason": "사건 연도·날짜가 원문에 없음"})
                continue
            if len(event_date) == 10:
                compact_sentence = re.sub(r"\s", "", sentence)
                date_variants = {
                    event_date,
                    event_date.replace("-", "."),
                    event_date.replace("-", "/"),
                    f"{event_date[:4]}년{int(event_date[5:7])}월{int(event_date[8:10])}일",
                }
                if not any(value in compact_sentence for value in date_variants):
                    rejected.append({"sid": sid, "reason": "사건 날짜가 원문에 없음"})
                    continue
        elif event_date:
            rejected.append({"sid": sid, "reason": "완료 실행 외 항목에 사건일 입력"})
            continue
        used_sids.add(sid)
        picks_by_sid[sid] = CanonicalPick(
            section_id=section_id,
            sentence=sentence,
            fragment_id=int(fragment_id),
            sid=sid,
            claim_type=claim_type,
            subject_label=subject_label,
            market_stage=market_stage,
            market_observation=market_observation,
            product_role=product_role,
            portfolio_stage=portfolio_stage,
            revenue_model_sid=revenue_model_sid,
            response_to_sid=response_to_sid,
            basis_sids=basis_sids,
            priority_signals=priority_signals,
            event_date=event_date,
            response_action=response_action,
            initial_signal=initial_signal,
            next_check_metric=next_check_metric,
            plan_status=plan_status,
            plan_timing=plan_timing,
            plan_condition=plan_condition,
            plan_expected_effect=plan_expected_effect,
            plan_execution_signal=plan_execution_signal,
            operation_role=operation_role,
            value_chain_stage=value_chain_stage,
            relationship_type=relationship_type,
        )

    invalid_links: set[str] = set()
    for sid, pick in picks_by_sid.items():
        if pick.claim_type == "priority_product":
            target = picks_by_sid.get(pick.revenue_model_sid)
            if target is None or target.claim_type != "revenue_model":
                rejected.append({"sid": sid, "reason": "같은 답의 2장 수익 분류와 연결되지 않음"})
                invalid_links.add(sid)
        if pick.claim_type == "current_response":
            target = picks_by_sid.get(pick.response_to_sid)
            if target is None or target.claim_type != "current_issue":
                rejected.append({"sid": sid, "reason": "같은 답의 미해결 문제와 대응이 연결되지 않음"})
                invalid_links.add(sid)
            elif not response_is_bound_to_issue(
                target.subject_label,
                target.sentence,
                pick.sentence,
                company,
            ):
                rejected.append({"sid": sid, "reason": "대응 원문이 연결된 문제 대상과 결속되지 않음"})
                invalid_links.add(sid)
        if pick.claim_type == "change_interpretation":
            linked_completed = [
                picks_by_sid.get(value)
                for value in pick.basis_sids
                if value not in performance_bases
            ]
            invalid_internal_id = any(
                value.startswith("fact-") for value in pick.basis_sids
            )
            if (
                not pick.basis_sids
                or invalid_internal_id
                or any(
                    base is None or base.claim_type != "completed_execution"
                    for base in linked_completed
                )
            ):
                rejected.append(
                    {
                        "sid": sid,
                        "reason": "같은 답의 완료 실행·제공된 완료 실적과 변화 해석이 연결되지 않음",
                    }
                )
                invalid_links.add(sid)

    pick_by_draft_identity: dict[int, CanonicalPick] = {}
    draft_keys: set[tuple[int, str, str]] = set()
    for sid, pick in picks_by_sid.items():
        if sid in invalid_links:
            continue
        draft_key = (pick.fragment_id, pick.sentence, pick.section_id)
        if draft_key in draft_keys:
            rejected.append(
                {
                    "sid": pick.sid,
                    "reason": "같은 사실 중복 배치",
                    "reason_code": "duplicate_assignment",
                }
            )
            continue
        draft_keys.add(draft_key)
        draft_item = engine.DraftItem(
            sentence=pick.sentence,
            fragment_id=pick.fragment_id,
            block=pick.section_id,
        )
        draft_items.append(draft_item)
        pick_by_draft_identity[id(draft_item)] = pick

    checked = engine.check_draft(
        draft_items,
        {number: str(frag.get("원문") or "") for number, frag in frags.items()},
        [],
    )
    checked_items = [*checked.kept, *(item for item, _reason in checked.deleted)]
    if {id(item) for item in checked_items} != {id(item) for item in draft_items} or len(
        checked_items
    ) != len(draft_items):
        checked_kept: list[Any] = []
        rejected.extend(
            {
                "sid": pick_by_draft_identity[id(item)].sid,
                "reason": "원문 대조 결과 회계 불일치",
                "reason_code": "source_verification_failure",
            }
            for item in draft_items
        )
    else:
        checked_kept = list(checked.kept)
        rejected.extend(
            {
                "sid": pick_by_draft_identity[id(item)].sid,
                "reason": reason,
                "reason_code": "source_verification_failure",
            }
            for item, reason in checked.deleted
        )
    policy_kept: list[CanonicalPick] = []
    for item in checked_kept:
        if item.fragment_id is None:
            continue
        fragment = frags.get(int(item.fragment_id), {})
        decision = assess_claim(
            str(item.block),
            str(item.sentence),
            source_kind=str(fragment.get("종류") or ""),
            company=company,
        )
        selected_pick = pick_by_draft_identity.get(id(item))
        # 기존 company-specificity 9장은 실명 파트너를 전제로 한다. 7장 정본은
        # 파트너가 없어도 위에서 법인 주어·현재 운영·단계·관계를 모두 결속한
        # operating_core를 허용하므로 그 닫힌 경우만 별도로 통과시킨다.
        internal_core_is_bound = bool(
            selected_pick is not None
            and selected_pick.claim_type == "operating_core"
        )
        if not decision.passed and not internal_core_is_bound:
            rejected.append(
                {
                    "sid": "",
                    "reason": decision.reason or "섹션별 사실 기준 미달",
                    "reason_code": "company_specificity_failure",
                }
            )
            continue
        if selected_pick is not None:
            policy_kept.append(selected_pick)

    # 참조 대상도 최종 원문·회사특이성 게이트를 통과한 경우에만 의존 항목을
    # 남긴다. 구조 검증 때 존재했던 대상이 후단에서 탈락한 경우의 고아 링크를
    # 허용하지 않는다.
    final_picks_by_sid = {item.sid: item for item in policy_kept}
    kept: list[CanonicalPick] = []
    for pick in policy_kept:
        link_failure_reason = ""
        if pick.claim_type == "priority_product":
            target = final_picks_by_sid.get(pick.revenue_model_sid)
            if target is None or target.claim_type != "revenue_model":
                link_failure_reason = "같은 답의 2장 수익 분류와 연결되지 않음"
        elif pick.claim_type == "current_response":
            target = final_picks_by_sid.get(pick.response_to_sid)
            if target is None or target.claim_type != "current_issue":
                link_failure_reason = "같은 답의 미해결 문제와 대응이 연결되지 않음"
        elif pick.claim_type == "change_interpretation":
            linked_sids = tuple(
                value for value in pick.basis_sids if value not in performance_bases
            )
            if any(
                (target := final_picks_by_sid.get(value)) is None
                or target.claim_type != "completed_execution"
                for value in linked_sids
            ):
                link_failure_reason = (
                    "같은 답의 완료 실행·제공된 완료 실적과 변화 해석이 연결되지 않음"
                )
        if link_failure_reason:
            rejected.append(
                {
                    "sid": pick.sid,
                    "reason": link_failure_reason,
                    "reason_code": "cross_reference_contract_failure",
                }
            )
            continue
        kept.append(pick)

    steps.append(
        {
            "step": "10_정본_원문대조",
            "유지": len(kept),
            "삭제": len(rejected),
            "삭제사유": [item["reason"] for item in rejected[:10]],
        }
    )
    if len(kept) + len(rejected) != len(selected):
        raise RuntimeError("span-selection 검증 집계가 provider 선택 수와 다릅니다")
    attach_round_result(
        selection_step,
        requested_max_tokens=CANONICAL_SELECTION_MAX_TOKENS,
        provider_selected=len(selected),
        validation_kept=len(kept),
        validation_rejected=len(rejected),
        validation_rejection_reason_counts=Counter(
            _validation_rejection_reason_code(item) for item in rejected
        ),
    )
    return kept, rejected

"""공식 양사 비교 프로그램 문장의 결정론적 직렬화 정본.

비교 생산물은 대상·지표·같은 기간·계산값이 이미 구조화돼 있다. 그 값을 문장으로
옮기는 규칙을 생산자와 AI 전 검증자가 함께 써야, 올바른 문장 뒤에 근거 없는 절을
붙이고 자체 결속 지문만 다시 계산하는 우회를 막을 수 있다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from src.shared.comparison_candidate_basis import (
    comparison_source_candidate_support_terms,
)
from src.shared.company_identity import exact_company_names_equivalent
from src.shared.report_quality.constants import (
    COMPARISON_JUDGMENTS,
    COMPETITIVE_COMPARISON_CLAIM_TYPE,
    COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
    COMPARISON_PROGRAM_CLAIM_TYPES,
)
from src.shared.report_quality.comparison_evidence import (
    comparison_official_text,
    comparison_shared_context,
)
from src.shared.report_quality.evidence_support import (
    MIN_PROSE_EVIDENCE_SUPPORT_TERMS,
    evidence_support_term_mismatches,
    normalized_support_terms,
)


COMPARISON_TARGET_SLOT = "competitive_position:comparison_target"
COMPARISON_METRIC_SLOT = "competitive_position:comparison_metric"
COMPARISON_BASIS_SLOT = "competitive_position:comparison_basis"
COMPARISON_JUDGMENT_SLOT = "competitive_position:comparison_judgment"
COMPARISON_LIMITATION_SLOT = "competitive_position:limitation"
COMPARISON_NUMERIC_METRICS = ("영업이익률", "매출 규모")


class ComparisonContextFact(Protocol):
    legal_entity: str
    claim: str
    claim_slot: str
    state_evidence: str
    evidence_support_terms: Sequence[str]
    comparison_target: str
    comparison_metric: str
    comparison_definition: str
    comparison_basis: str
    comparison_period: str
    comparison_scope: str
    comparison_conditions: Mapping[str, str]
    comparator_source_id: str
    comparator_state_evidence: str
    comparison_judgment: str
    claim_type: str


class ComparisonTargetSource(Protocol):
    source_id: str
    publisher: str


def comparison_target_claim(*, evidence_text: str, comparison_target: str) -> str:
    terms = comparison_source_candidate_support_terms(
        evidence_text,
        comparison_target,
    )
    if len(terms) != 2:
        return ""
    candidate_alias, relation = terms
    return (
        "공식 원문에서 동종 비교 단서("
        f"대상 표기: {candidate_alias}, 관계 표현: {relation})를 확인했으며, "
        f"DART에서 확인한 비교 법인은 {comparison_target}이다."
    )


def comparison_metric_claim(*, comparison_metric: str) -> str:
    return (
        "동일 조건 비교 지표는 공식 원문 계정(매출액, 영업이익)으로 계산한 "
        f"{comparison_metric}이다."
    )


def comparison_metric_summary(metrics: Sequence[str]) -> str:
    """실제 numeric Fact에 존재하는 비교 축만 고정 순서로 직렬화한다."""

    values = {str(metric or "").strip() for metric in metrics}
    if not values or not values <= set(COMPARISON_NUMERIC_METRICS):
        return ""
    if "매출 규모" in values and "영업이익률" not in values:
        return ""
    return "·".join(metric for metric in COMPARISON_NUMERIC_METRICS if metric in values)


def comparison_basis_claim() -> str:
    return (
        "비교 기준은 공식 원문 계정(매출액, 영업이익)을 같은 완료 사업연도·"
        "회계 범위·표준 계정 정의로 맞춘 것이다."
    )


def comparison_limitation_claim() -> str:
    return (
        "공식 원문 계정(매출액, 영업이익)의 이 비교는 한 사업연도의 계산 "
        "결과이며 제품 경쟁력·지속적 경쟁우위·원인 판단을 뜻하지 않는다."
    )


def comparison_profitability_claim(
    *,
    comparison_target: str,
    difference: str,
    direction: str,
) -> str:
    return (
        "연결재무제표(CFS) 매출액과 영업이익으로 계산한 영업이익률은 "
        f"{comparison_target}보다 {difference}%p {direction}."
    )


def comparison_scale_claim(
    *,
    comparison_scope: str,
    comparison_target: str,
    ratio_text: str,
) -> str:
    return (
        f"{comparison_scope} 매출액 규모는 {comparison_target} 대비 "
        f"{ratio_text}였다. 이는 규모 차이이며 경쟁우위 판정이 아니다."
    )


def expected_comparison_context_claim(fact: ComparisonContextFact) -> str:
    """구조 필드와 실제 target 원문으로 다시 만든 9장 맥락 문장."""

    if fact.claim_slot == COMPARISON_TARGET_SLOT:
        return comparison_target_claim(
            evidence_text=fact.state_evidence,
            comparison_target=fact.comparison_target,
        )
    if fact.claim_slot == COMPARISON_METRIC_SLOT:
        return comparison_metric_claim(comparison_metric=fact.comparison_metric)
    if fact.claim_slot == COMPARISON_BASIS_SLOT:
        return comparison_basis_claim()
    if fact.claim_slot == COMPARISON_LIMITATION_SLOT:
        return comparison_limitation_claim()
    return ""


def comparison_context_claim_problems(
    fact: ComparisonContextFact,
) -> tuple[str, ...]:
    """9장 맥락의 exact 문장과 명시 근거어를 한 정본으로 검증한다."""

    problems: list[str] = []
    expected_claim = expected_comparison_context_claim(fact)
    if not expected_claim or fact.claim != expected_claim:
        problems.append("프로그램 비교 맥락 문장이 구조 필드의 정본과 다릅니다")
    support_terms = normalized_support_terms(fact.evidence_support_terms)
    if len(support_terms) < MIN_PROSE_EVIDENCE_SUPPORT_TERMS:
        problems.append("프로그램 비교 맥락 문장의 명시 근거어가 부족합니다")
    mismatches = evidence_support_term_mismatches(
        fact.claim,
        fact.state_evidence,
        support_terms,
    )
    if mismatches:
        problems.append(
            "프로그램 비교 맥락 문장의 근거어가 claim과 원문 양쪽에 없습니다: "
            + ",".join(mismatches)
        )
    return tuple(problems)


def comparison_target_source_problems(
    fact: ComparisonContextFact,
    comparator_source: ComparisonTargetSource | None,
) -> tuple[str, ...]:
    """구조상 비교 대상과 실제 비교사 공식 Source 법인을 결속한다.

    원문에 어떤 단어가 등장한다는 사실만으로 그 단어를 비교 법인명으로 바꿀 수
    없다. 후보를 고른 뒤에는 Fact가 가리키는 comparator Source ID와, 그 Source가
    공식 발행자로 보존한 법인명이 모두 같아야 한다. 법인 표지·구두점 정규화는
    별칭 추측 없이 기존 회사 신원 정본만 사용한다.
    """

    if comparator_source is None:
        return ("비교사 Source가 등록부에 없습니다",)
    problems: list[str] = []
    if (
        not fact.comparator_source_id.strip()
        or fact.comparator_source_id.strip() != comparator_source.source_id.strip()
    ):
        problems.append("비교사 Source ID가 구조 필드와 다릅니다")
    if not exact_company_names_equivalent(
        fact.comparison_target,
        comparator_source.publisher,
    ):
        problems.append("비교 대상 법인명이 비교사 Source 발행 법인과 다릅니다")
    return tuple(problems)


def comparison_program_problems(
    facts: Sequence[ComparisonContextFact],
) -> tuple[str, ...]:
    """비교 프로그램의 개별 사실들이 한 비교 장부를 공유하는지 검사한다."""

    comparison_facts = tuple(
        fact for fact in facts if fact.claim_type in COMPARISON_PROGRAM_CLAIM_TYPES
    )
    if not comparison_facts:
        return ()
    problems: list[str] = []
    if len(comparison_facts) != len(facts):
        problems.append("비교 프로그램에 다른 종류의 사실이 섞였습니다")

    context_facts = tuple(
        fact
        for fact in comparison_facts
        if fact.claim_type == COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE
    )
    numeric_facts = tuple(
        fact
        for fact in comparison_facts
        if fact.claim_type == COMPETITIVE_COMPARISON_CLAIM_TYPE
    )
    required_context_slots = {
        COMPARISON_TARGET_SLOT,
        COMPARISON_METRIC_SLOT,
        COMPARISON_BASIS_SLOT,
        COMPARISON_LIMITATION_SLOT,
    }
    context_slots = tuple(fact.claim_slot for fact in context_facts)
    if (
        set(context_slots) != required_context_slots
        or len(context_slots) != len(required_context_slots)
    ):
        problems.append("비교 프로그램의 대상·지표·기준·한계 맥락이 정확히 한 건씩이 아닙니다")

    numeric_metrics = tuple(fact.comparison_metric for fact in numeric_facts)
    metric_summary = comparison_metric_summary(numeric_metrics)
    if (
        not metric_summary
        or len(numeric_metrics) != len(set(numeric_metrics))
        or any(fact.claim_slot != COMPARISON_JUDGMENT_SLOT for fact in numeric_facts)
    ):
        problems.append("비교 프로그램의 수치 지표 집합이 허용된 실제 비교 축과 다릅니다")
    if any(fact.comparison_metric != metric_summary for fact in context_facts):
        problems.append("비교 맥락의 지표가 실제 수치 Fact 지표 집합과 다릅니다")
    if any(
        fact.comparison_judgment not in COMPARISON_JUDGMENTS
        for fact in comparison_facts
    ):
        problems.append("비교 프로그램에 허용되지 않은 공개 판정이 있습니다")

    profitability_fact = next(
        (
            fact
            for fact in numeric_facts
            if fact.comparison_metric == "영업이익률"
        ),
        None,
    )
    if profitability_fact is not None and any(
        fact.comparison_definition != profitability_fact.comparison_definition
        for fact in context_facts
    ):
        problems.append("비교 맥락의 계정 정의가 실제 영업이익률 Fact와 다릅니다")
    if profitability_fact is not None and any(
        fact.comparison_judgment != profitability_fact.comparison_judgment
        for fact in context_facts
    ):
        problems.append("비교 맥락의 공개 판정이 실제 영업이익률 Fact와 다릅니다")

    common_fields = (
        "comparison_target",
        "comparison_basis",
        "comparison_period",
        "comparison_scope",
        "comparator_source_id",
    )
    for field in common_fields:
        values = {str(getattr(fact, field) or "").strip() for fact in comparison_facts}
        if len(values) != 1 or not next(iter(values), ""):
            problems.append(f"비교 프로그램의 {field} 값이 하나로 닫히지 않았습니다")

    required_conditions = {
        "customer",
        "product",
        "market",
        "self_period",
        "comparator_period",
        "self_definition",
        "comparator_definition",
        "self_accounting_scope",
        "comparator_accounting_scope",
    }
    shared_axes: tuple[str, str, str] | None = None
    for fact in comparison_facts:
        conditions = {
            str(key).strip(): str(value).strip()
            for key, value in fact.comparison_conditions.items()
        }
        if set(conditions) != required_conditions or any(not value for value in conditions.values()):
            problems.append("비교 프로그램의 동일 조건 구조가 완전하지 않습니다")
            continue
        if (
            conditions["self_period"] != fact.comparison_period
            or conditions["comparator_period"] != fact.comparison_period
            or conditions["self_definition"] != fact.comparison_definition
            or conditions["comparator_definition"] != fact.comparison_definition
            or conditions["self_accounting_scope"] != fact.comparison_scope
            or conditions["comparator_accounting_scope"] != fact.comparison_scope
        ):
            problems.append("비교 프로그램의 양사 기간·정의·범위가 Fact 구조와 다릅니다")
        axes = (
            conditions["customer"],
            conditions["product"],
            conditions["market"],
        )
        if shared_axes is None:
            shared_axes = axes
        elif axes != shared_axes:
            problems.append("비교 프로그램의 고객·제품·시장 범위가 사실마다 다릅니다")
    if profitability_fact is not None:
        derived_context = comparison_shared_context(
            self_company=profitability_fact.legal_entity,
            self_text=comparison_official_text(profitability_fact.state_evidence),
            comparator_company=profitability_fact.comparison_target,
            comparator_text=comparison_official_text(
                profitability_fact.comparator_state_evidence
            ),
        )
        expected_axes = tuple(
            derived_context.get(axis, "") for axis in ("customer", "product", "market")
        )
        if not all(expected_axes) or shared_axes != expected_axes:
            problems.append("비교 프로그램의 고객·제품·시장 범위가 양사 원문과 다릅니다")
    return tuple(dict.fromkeys(problems))


__all__ = [
    "COMPARISON_BASIS_SLOT",
    "COMPARISON_JUDGMENT_SLOT",
    "COMPARISON_LIMITATION_SLOT",
    "COMPARISON_METRIC_SLOT",
    "COMPARISON_NUMERIC_METRICS",
    "COMPARISON_TARGET_SLOT",
    "comparison_basis_claim",
    "comparison_context_claim_problems",
    "comparison_limitation_claim",
    "comparison_metric_claim",
    "comparison_metric_summary",
    "comparison_program_problems",
    "comparison_profitability_claim",
    "comparison_scale_claim",
    "comparison_target_claim",
    "comparison_target_source_problems",
    "expected_comparison_context_claim",
]

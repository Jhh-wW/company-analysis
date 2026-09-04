from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

from src.features.report_quality.assessment import (
    assess_generation,
    has_public_numeric_token,
)
from src.features.report_quality.constants import (
    COMPETITIVE_COMPARISON_CLAIM_TYPE,
    COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
    HISTORICAL_PERFORMANCE_RATE_CLAIM_TYPE,
    INTERPRETATION_CLAIM_TYPE,
    NUMERIC_BINDING_VERSION,
    REQUIRED_QUALITY_SECTION_IDS,
    ROUNDING_MODE,
    STRICT_QUALITY_CONTRACT_VERSION,
    STRICT_PUBLIC_CLAIM_TYPES,
    STRICT_REQUIRED_QUALITY_SECTION_IDS,
    VERIFIED_PROSE_CLAIM_TYPE,
)
from src.features.report_quality.dto import (
    ClaimFact,
    ReportCandidate,
    ReportSectionCandidate,
    SourceDocument,
)
from src.features.report_quality.models import (
    QualityGrade,
    QualityProblemCode,
    ReleaseDecision,
    VerificationState,
)
from src.features.report_quality.numeric import (
    EntityScope,
    NumericBinding,
    NumericFormula,
    NumericOperand,
    NumericSign,
    UnitDimension,
    claim_fact_from_binding,
)
from src.shared.comparison_candidate_basis import (
    COMPARISON_BASIS_VERSION,
    encode_comparison_basis_v1,
)
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_quality.comparison_claims import (
    COMPARISON_BASIS_SLOT,
    COMPARISON_JUDGMENT_SLOT,
    COMPARISON_LIMITATION_SLOT,
    COMPARISON_METRIC_SLOT,
    COMPARISON_TARGET_SLOT,
    comparison_basis_claim,
    comparison_limitation_claim,
    comparison_metric_claim,
    comparison_metric_summary,
    comparison_profitability_claim,
    comparison_target_claim,
)
from src.shared.report_quality.comparison_evidence import comparison_shared_context
from src.shared.report_quality.generation import (
    assert_observation_matches_assessment,
    observe_generation,
)


_ITEM_WORDS = (
    "가",
    "나",
    "다",
    "라",
    "마",
    "바",
    "사",
    "아",
    "자",
    "차",
    "카",
    "타",
    "파",
    "하",
)


def _candidate(
    counts: dict[str, int] | None = None,
    *,
    one_document: bool = False,
) -> ReportCandidate:
    requested = counts or {section_id: 5 for section_id in REQUIRED_QUALITY_SECTION_IDS}
    sources: list[SourceDocument] = []
    facts: list[ClaimFact] = []
    sections: list[ReportSectionCandidate] = []
    for source_number, section_id in enumerate(REQUIRED_QUALITY_SECTION_IDS, start=1):
        source_id = f"source-{source_number}"
        identity = (
            "document:dart.fss.or.kr:same-document"
            if one_document
            else f"document:dart.fss.or.kr:document-{source_number}"
        )
        sources.append(SourceDocument(source_id, identity))
        section_fact_ids: list[str] = []
        slots = CLAIM_SLOTS_BY_SECTION[section_id]
        for item_number in range(requested[section_id]):
            fact_id = f"{section_id}-fact-{item_number}"
            section_fact_ids.append(fact_id)
            facts.append(
                ClaimFact(
                    fact_id=fact_id,
                    section_owner=section_id,
                    source_id=source_id,
                    source_identity=identity,
                    verification_state=VerificationState.VERIFIED.value,
                    claim_slot=slots[item_number % len(slots)],
                    evidence_binding_valid=True,
                    claim=(
                        f"{section_id} 장의 {_ITEM_WORDS[item_number]} 사실은 "
                        "공식 원문과 결속됐다."
                    ),
                    subject_scope="연결",
                )
            )
        sections.append(
            ReportSectionCandidate(section_id, tuple(section_fact_ids))
        )
    return ReportCandidate(tuple(sections), tuple(facts), tuple(sources))


def _full_candidate(
    public_counts: dict[str, int] | None = None,
) -> ReportCandidate:
    """FULL 모양 45 claim·독립 문서 8건·원문 조각 결속 후보."""

    sources: list[SourceDocument] = []
    facts: list[ClaimFact] = []
    sections: list[ReportSectionCandidate] = []
    requested = public_counts or {
        section_id: (
            4
            if section_id == "identity"
            else 6
            if section_id == "competitive_position"
            else 5
        )
        for section_id in STRICT_REQUIRED_QUALITY_SECTION_IDS
    }
    for source_number, section_id in enumerate(
        STRICT_REQUIRED_QUALITY_SECTION_IDS,
        start=1,
    ):
        source_id = f"full-source-{source_number}"
        # 9장은 8장과 같은 독립 문서를 공유해 하한을 정확히 8건으로 고정한다.
        document_number = min(source_number, 8)
        identity = f"document:dart.fss.or.kr:full-{document_number}"
        evidence_hash = f"{source_number:064x}"
        sources.append(
            SourceDocument(
                source_id,
                identity,
                exact_evidence_hashes=(evidence_hash,),
                # 9장은 8장과 동일한 문서를 다른 조각으로 인용한다. 문서 전체
                # 지문은 같고 조각 지문만 달라야 독립 문서 하한을 정직하게 잰다.
                document_content_sha256=f"{document_number + 100:064x}",
            )
        )
        fact_ids: list[str] = []
        slots = CLAIM_SLOTS_BY_SECTION[section_id]
        for item_number in range(requested[section_id]):
            fact_id = f"{section_id}-full-fact-{item_number}"
            fact_ids.append(fact_id)
            facts.append(
                ClaimFact(
                    fact_id=fact_id,
                    section_owner=section_id,
                    source_id=source_id,
                    source_identity=identity,
                    verification_state=VerificationState.VERIFIED.value,
                    claim_slot=slots[item_number % len(slots)],
                    evidence_binding_valid=True,
                    claim=(
                        f"{section_id} 장의 {_ITEM_WORDS[item_number]} 사실은 "
                        "공식 원문과 결속됐다."
                    ),
                    subject_scope="연결",
                    supporting_source_ids=(source_id,),
                    supporting_source_identities=(identity,),
                    supporting_evidence_hashes=(evidence_hash,),
                    claim_type=VERIFIED_PROSE_CLAIM_TYPE,
                )
            )
        sections.append(
            ReportSectionCandidate(
                section_id,
                tuple(fact_ids),
                public_sentence_count=len(fact_ids),
            )
        )
    return ReportCandidate(tuple(sections), tuple(facts), tuple(sources))


def _with_interpretations(
    candidate: ReportCandidate,
    counts: dict[str, int],
) -> ReportCandidate:
    seen: dict[str, int] = {}
    converted: list[ClaimFact] = []
    for fact in candidate.facts:
        index = seen.get(fact.section_owner, 0)
        seen[fact.section_owner] = index + 1
        converted.append(
            replace(fact, claim_type=INTERPRETATION_CLAIM_TYPE)
            if index < counts.get(fact.section_owner, 0)
            else fact
        )
    return replace(candidate, facts=tuple(converted))


def _valid_historical_fact(original: ClaimFact) -> ClaimFact:
    """실제 structured-claim과 같은 기존 NumericBinding으로 만든 정상 실적."""

    binding = NumericBinding(
        version=NUMERIC_BINDING_VERSION,
        metric="매출",
        entity_scope=EntityScope.CONSOLIDATED,
        period_start="2024",
        period_end="2025",
        sign=NumericSign.POSITIVE,
        unit="%",
        unit_dimension=UnitDimension.PERCENT,
        formula=NumericFormula.RATE,
        operands=(
            NumericOperand(
                role="start",
                metric="매출",
                entity_scope=EntityScope.CONSOLIDATED,
                period="2024",
                value="100",
                sign=NumericSign.POSITIVE,
                unit="억원",
                unit_dimension=UnitDimension.CURRENCY,
                source_identity=original.source_identity,
            ),
            NumericOperand(
                role="end",
                metric="매출",
                entity_scope=EntityScope.CONSOLIDATED,
                period="2025",
                value="125",
                sign=NumericSign.POSITIVE,
                unit="억원",
                unit_dimension=UnitDimension.CURRENCY,
                source_identity=original.source_identity,
            ),
        ),
        calculated_value="25",
        display_value="25.0",
        rounding_mode=ROUNDING_MODE,
        rounding_places=1,
        tolerance="0",
        source_identity=original.source_identity,
        verification_state=VerificationState.VERIFIED,
    )
    fact = claim_fact_from_binding(
        fact_id=original.fact_id,
        section_owner=original.section_owner,
        source_id=original.source_id,
        claim="연결 매출은 2024년 대비 2025년 25.0% 증가했다.",
        claim_slot=original.claim_slot,
        binding=binding,
    )
    return replace(
        fact,
        evidence_binding_valid=True,
        supporting_source_ids=original.supporting_source_ids,
        supporting_source_identities=original.supporting_source_identities,
        supporting_evidence_hashes=original.supporting_evidence_hashes,
        claim_type=HISTORICAL_PERFORMANCE_RATE_CLAIM_TYPE,
    )


_COMPARISON_OFFICIAL_TEXT = (
    "회사는 전자 제조 고객과 반도체 고객에게 정밀 검사장비 제품을 공급한다. "
    "정밀 검사장비 산업과 반도체 시장에서 사업한다."
)


def _comparison_evidence(*, revenue: int, operating: int) -> str:
    """실제 비교 생산자가 보존하는 두 DART IS 계정 행 fixture."""

    return json.dumps(
        {
            "official_text": _COMPARISON_OFFICIAL_TEXT,
            "financials": {
                "status": "000",
                "reprt_code": "11011",
                "list": [
                    {
                        "account_id": "ifrs-full_Revenue",
                        "account_nm": "매출액",
                        "sj_div": "IS",
                        "fs_div": "CFS",
                        "thstrm_dt": "2025.01.01 ~ 2025.12.31",
                        "thstrm_amount": str(revenue),
                        "reprt_code": "11011",
                        "currency": "KRW",
                    },
                    {
                        "account_id": "dart_OperatingIncomeLoss",
                        "account_nm": "영업이익",
                        "sj_div": "IS",
                        "fs_div": "CFS",
                        "thstrm_dt": "2025.01.01 ~ 2025.12.31",
                        "thstrm_amount": str(operating),
                        "reprt_code": "11011",
                        "currency": "KRW",
                    },
                ],
            }
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _comparison_basis(*, comparator: ClaimFact) -> str:
    """현재 생산자의 닫힌 v1 비교 후보 근거와 같은 정상 basis."""

    return encode_comparison_basis_v1(
        {
            "version": COMPARISON_BASIS_VERSION,
            "candidate_fact_id": comparator.fact_id,
            "candidate_source_id": comparator.source_id,
            "candidate_corp_code": "00000002",
            "candidate_name": "비교사 주식회사",
            "filing_document_id": "20250315000001",
            "evidence_sha256": "a" * 64,
            "overlap_dimension": "시장 겹침",
        }
    )


def _comparison_conditions(*, definition: str) -> dict[str, str]:
    period = "2025-01-01~2025-12-31"
    scope = "연결재무제표(CFS)"
    context = comparison_shared_context(
        self_company="주식회사 알파",
        self_text=_COMPARISON_OFFICIAL_TEXT,
        comparator_company="비교사 주식회사",
        comparator_text=_COMPARISON_OFFICIAL_TEXT,
    )
    assert set(context) == {"customer", "product", "market"}
    return {
        **context,
        "self_period": period,
        "comparator_period": period,
        "self_definition": definition,
        "comparator_definition": definition,
        "self_accounting_scope": scope,
        "comparator_accounting_scope": scope,
    }


def _valid_comparison_fact(
    original: ClaimFact,
    *,
    comparator: ClaimFact,
) -> ClaimFact:
    """공식 비교 생산기의 매출 규모 계산 계약과 같은 정상 비교 사실."""

    scope = "연결재무제표(CFS)"
    target = "비교사 주식회사"
    period = "2025-01-01~2025-12-31"
    definition = "ifrs-full_Revenue|매출액|IS|11011|KRW"

    return replace(
        original,
        claim=(
            f"{scope} 매출액 규모는 {target} 대비 2.0배였다. "
            "이는 규모 차이이며 경쟁우위 판정이 아니다."
        ),
        raw_value="200;100",
        calculation="자사 매출액÷비교사 매출액 = 2.0배",
        display_value="2.0배",
        rounding_rule="자사 매출액÷비교사 매출액, 소수 첫째 자리 ROUND_HALF_UP",
        numeric_checks=("200|1|0|200", "100|1|0|100", "200|100|1|2.0"),
        metric="매출 규모 배수",
        period_start="2025-01-01",
        period_end="2025-12-31",
        sign="positive",
        unit="배",
        unit_dimension="multiple",
        formula="comparison_revenue_ratio_v1",
        supporting_source_ids=(original.source_id, comparator.source_id),
        supporting_source_identities=(
            original.source_identity,
            comparator.source_identity,
        ),
        supporting_evidence_hashes=(
            original.supporting_evidence_hashes[0],
            comparator.supporting_evidence_hashes[0],
        ),
        claim_slot=COMPARISON_JUDGMENT_SLOT,
        claim_type=COMPETITIVE_COMPARISON_CLAIM_TYPE,
        legal_entity="주식회사 알파",
        comparison_target=target,
        comparison_metric="매출 규모",
        state_evidence=_comparison_evidence(revenue=200, operating=40),
        comparison_definition=definition,
        comparison_basis=_comparison_basis(comparator=comparator),
        comparison_period=period,
        comparison_scope=scope,
        comparison_judgment="비교사 대비 2.0배",
        comparator_source_id=comparator.source_id,
        comparator_state_evidence=_comparison_evidence(revenue=100, operating=10),
        comparison_conditions=_comparison_conditions(definition=definition),
    )


def _valid_profitability_comparison_fact(
    original: ClaimFact,
    *,
    comparator: ClaimFact,
) -> ClaimFact:
    """같은 양사 원문에서 매출·영업이익률을 다시 계산하는 정상 비교 사실."""

    scope = "연결재무제표(CFS)"
    target = "비교사 주식회사"
    period = "2025-01-01~2025-12-31"
    definition = (
        "ifrs-full_Revenue|매출액|IS|11011|KRW;"
        "dart_OperatingIncomeLoss|영업이익|IS|11011|KRW"
    )
    return replace(
        original,
        claim=comparison_profitability_claim(
            comparison_target=target,
            difference="10.0",
            direction="높았다",
        ),
        raw_value="200;40;100;10",
        calculation=(
            "양사 영업이익÷매출액×100 후 자사 영업이익률에서 "
            "비교사 영업이익률을 차감"
        ),
        display_value="자사 20.0%; 비교사 10.0%; 차이 10.0%p",
        rounding_rule="각 영업이익률과 차이를 %·%p 소수 첫째 자리 ROUND_HALF_UP",
        numeric_checks=(
            "200|1|0|200",
            "40|1|0|40",
            "100|1|0|100",
            "10|1|0|10",
            "40|2|1|20.0",
            "10|1|1|10.0",
            "10.0|1|1|10.0",
        ),
        metric="영업이익률 차이",
        period_start="2025-01-01",
        period_end="2025-12-31",
        sign="positive",
        unit="%p",
        unit_dimension="percentage_point",
        formula="comparison_operating_margin_difference_v1",
        supporting_source_ids=(original.source_id, comparator.source_id),
        supporting_source_identities=(
            original.source_identity,
            comparator.source_identity,
        ),
        supporting_evidence_hashes=(
            original.supporting_evidence_hashes[0],
            comparator.supporting_evidence_hashes[0],
        ),
        claim_slot=COMPARISON_JUDGMENT_SLOT,
        claim_type=COMPETITIVE_COMPARISON_CLAIM_TYPE,
        legal_entity="주식회사 알파",
        state_evidence=_comparison_evidence(revenue=200, operating=40),
        comparison_target=target,
        comparison_metric="영업이익률",
        comparison_definition=definition,
        comparison_basis=_comparison_basis(comparator=comparator),
        comparison_period=period,
        comparison_scope=scope,
        comparison_judgment="competitive_advantage",
        comparator_source_id=comparator.source_id,
        comparator_state_evidence=_comparison_evidence(revenue=100, operating=10),
        comparison_conditions=_comparison_conditions(definition=definition),
    )


def _valid_comparison_context_fact(
    original: ClaimFact,
    *,
    base: ClaimFact,
    comparator: ClaimFact,
    slot: str,
    claim: str,
    state_evidence: str,
    support_terms: tuple[str, ...],
    metric_summary: str,
) -> ClaimFact:
    """비교 수치 Fact와 같은 장부를 공유하는 production-shaped 맥락 사실."""

    return replace(
        original,
        claim=claim,
        claim_slot=slot,
        claim_type=COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
        legal_entity=base.legal_entity,
        state_evidence=state_evidence,
        evidence_support_terms=support_terms,
        supporting_source_ids=(original.source_id, comparator.source_id),
        supporting_source_identities=(
            original.source_identity,
            comparator.source_identity,
        ),
        supporting_evidence_hashes=(
            original.supporting_evidence_hashes[0],
            comparator.supporting_evidence_hashes[0],
        ),
        comparison_target=base.comparison_target,
        comparison_metric=metric_summary,
        comparison_definition=base.comparison_definition,
        comparison_basis=base.comparison_basis,
        comparison_period=base.comparison_period,
        comparison_scope=base.comparison_scope,
        comparison_judgment=base.comparison_judgment,
        comparator_source_id=comparator.source_id,
        comparator_state_evidence=base.comparator_state_evidence,
        comparison_conditions=dict(base.comparison_conditions),
    )


def _candidate_with_all_public_claim_types() -> ReportCandidate:
    """실제 공개 5종과 완전한 비교 프로그램을 포함한 정상 v3 후보."""

    candidate = _full_candidate()
    facts = {fact.fact_id: fact for fact in candidate.facts}
    comparator = facts["identity-full-fact-1"]
    competitive = [
        facts[f"competitive_position-full-fact-{index}"] for index in range(6)
    ]
    profitability = _valid_profitability_comparison_fact(
        competitive[5],
        comparator=comparator,
    )
    metric_summary = comparison_metric_summary(
        (profitability.comparison_metric,)
    )
    target_evidence = "주식회사 알파는 비교사 주식회사와 경쟁한다."
    context_specs = (
        (
            COMPARISON_TARGET_SLOT,
            comparison_target_claim(
                evidence_text=target_evidence,
                comparison_target=profitability.comparison_target,
            ),
            target_evidence,
            ("비교사 주식회사", "와 경쟁한다"),
        ),
        (
            COMPARISON_METRIC_SLOT,
            comparison_metric_claim(comparison_metric=metric_summary),
            profitability.state_evidence,
            ("매출액", "영업이익"),
        ),
        (
            COMPARISON_BASIS_SLOT,
            comparison_basis_claim(),
            profitability.state_evidence,
            ("매출액", "영업이익"),
        ),
        (
            COMPARISON_LIMITATION_SLOT,
            comparison_limitation_claim(),
            profitability.state_evidence,
            ("매출액", "영업이익"),
        ),
    )
    contexts = tuple(
        _valid_comparison_context_fact(
            competitive[index + 1],
            base=profitability,
            comparator=comparator,
            slot=slot,
            claim=claim,
            state_evidence=state_evidence,
            support_terms=support_terms,
            metric_summary=metric_summary,
        )
        for index, (slot, claim, state_evidence, support_terms) in enumerate(
            context_specs
        )
    )
    replacements = {
        "identity-full-fact-0": replace(
            facts["identity-full-fact-0"],
            claim_type=INTERPRETATION_CLAIM_TYPE,
        ),
        "past_changes-full-fact-0": _valid_historical_fact(
            facts["past_changes-full-fact-0"]
        ),
        **{fact.fact_id: fact for fact in (*contexts, profitability)},
    }
    return replace(
        candidate,
        facts=tuple(
            replacements.get(fact.fact_id, fact) for fact in candidate.facts
        ),
        sources=tuple(
            replace(source, publisher="비교사 주식회사")
            if source.source_id == comparator.source_id
            else source
            for source in candidate.sources
        ),
    )


def test_40개_claim_8개_독립문서와_장별coverage면_complete다() -> None:
    result = assess_generation(_candidate())

    assert result.quality.grade is QualityGrade.COMPLETE
    assert result.quality.substantive_claims == 40
    assert result.quality.document_sources == 8
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED
    assert result.publication_grade is QualityGrade.COMPLETE


def test_실질claim이_하한_미만이면_TOO_FEW_SUBSTANTIVE_CLAIMS다() -> None:
    # 장별 의미범주 하한(MIN_CLAIMS_PER_COVERED_SECTION=2)은 8개 장 모두
    # 정확히 채우되, 전역 실질 claim 총량만 하한(40) 밑으로 떨어뜨린다 —
    # 8개 장 × 2건 = 16건 < 40건. assessment.py의
    # "substantive < contract.min_substantive_claims" 게이트가 실제로
    # 뜨는지 아무도 확인하지 않았던 빈틈을 메운다.
    counts = {section_id: 2 for section_id in REQUIRED_QUALITY_SECTION_IDS}

    result = assess_generation(_candidate(counts))

    assert result.quality.substantive_claims == 16
    assert result.quality.grade is QualityGrade.PARTIAL
    assert QualityProblemCode.TOO_FEW_SUBSTANTIVE_CLAIMS in result.quality.problem_codes
    # ★ 이 시험이 고정하는 것: 장별 coverage(2건)는 정확히 채웠으므로
    #   LOW_SEMANTIC_COVERAGE·LOW_PUBLIC_SENTENCE_COVERAGE·ONE_CLAIM_SECTIONS는
    #   섞이지 않는다 — 전역 총량 부족 게이트 하나만 걸린다는 것을 못 박는다.
    assert result.quality.problem_codes == (
        QualityProblemCode.TOO_FEW_SUBSTANTIVE_CLAIMS,
    )
    assert result.quality.one_claim_sections == ()
    assert result.quality.semantic_underfilled_sections == ()
    assert result.quality.underfilled_sections == ()
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED
    assert result.publication_grade is QualityGrade.PARTIAL


# ══════════════════════════════════════════════════════════
# ★ 값 비교가 아니라 «경계 행동»을 재는 시험.
# 위 시험들은 특정 조합이 어느 등급으로 떨어지는지를 보지만, 여기 세 개는
# 하한 바로 아래/바로 위에서 게이트가 실제로 막고/여는지를 짝으로 확인한다.
# 39·49·7은 시험이 직접 정한 «독립 오라클»이다(생산 상수 import 아님) —
# 누군가 생산 상수를 몰래 낮춰도(예: 40→17) 이 경계 자체는 그대로 39에서
# 막히고 40에서 열려야 하므로, 그 변경이 이 시험을 정직하게 깨뜨린다.
# ══════════════════════════════════════════════════════════


def test_실질claim_39건은_막히고_40건은_통과한다() -> None:
    passed_counts = {section_id: 5 for section_id in REQUIRED_QUALITY_SECTION_IDS}
    blocked_counts = dict(passed_counts)
    blocked_counts["identity"] = 4  # 8*5 - 1 = 39건. 장별 coverage(4개 slot)는
    # 여전히 하한(2) 이상이라 다른 게이트는 안 걸린다.

    passed = assess_generation(_candidate(passed_counts))
    blocked = assess_generation(_candidate(blocked_counts))

    assert passed.quality.substantive_claims == 40
    assert (
        QualityProblemCode.TOO_FEW_SUBSTANTIVE_CLAIMS
        not in passed.quality.problem_codes
    )
    assert passed.quality.grade is QualityGrade.COMPLETE

    assert blocked.quality.substantive_claims == 39
    assert (
        QualityProblemCode.TOO_FEW_SUBSTANTIVE_CLAIMS in blocked.quality.problem_codes
    )
    assert blocked.quality.grade is QualityGrade.PARTIAL


def test_검증비율_49퍼센트는_막히고_50퍼센트는_통과한다() -> None:
    # 8개 장에 총 100건(장별 12~13건)을 만든다. 실제 쓰이는 claim_slot은
    # 장마다 5종류뿐이라 장별 coverage·안내문 게이트는 여유 있게 통과하고,
    # 검증 비율만 정확히 49%/50%로 딱 떨어지도록 총량을 100으로 골랐다.
    counts = {
        "identity": 13,
        "business_model": 13,
        "portfolio": 13,
        "past_changes": 13,
        "current_challenges": 12,
        "future_strategy": 12,
        "operations_partners": 12,
        "culture": 12,
    }
    base = _candidate(counts)
    assert len(base.facts) == 100

    def with_verified_count(verified_count: int) -> ReportCandidate:
        facts = tuple(
            fact
            if index < verified_count
            else replace(fact, verification_state=VerificationState.UNVERIFIED.value)
            for index, fact in enumerate(base.facts)
        )
        return replace(base, facts=facts)

    passed = assess_generation(with_verified_count(50))
    blocked = assess_generation(with_verified_count(49))

    assert passed.quality.verified_ratio == Decimal("0.50")
    assert QualityProblemCode.LOW_VERIFIED_RATIO not in passed.quality.problem_codes
    assert passed.quality.grade is QualityGrade.COMPLETE

    assert blocked.quality.verified_ratio == Decimal("0.49")
    assert QualityProblemCode.LOW_VERIFIED_RATIO in blocked.quality.problem_codes
    assert blocked.quality.grade is QualityGrade.PARTIAL


def test_독립문서_7건은_막히고_8건은_통과한다() -> None:
    passed = _candidate()  # 기본값: 8개 장이 각각 다른 문서 → 8건
    merge_target_identity = passed.sources[0].document_identity
    second_source_id = passed.sources[1].source_id
    merged_sources = tuple(
        replace(source, document_identity=merge_target_identity)
        if source.source_id == second_source_id
        else source
        for source in passed.sources
    )
    blocked = replace(passed, sources=merged_sources)  # 두 번째 장의 문서를
    # 첫 번째 장과 같은 문서로 합쳐 독립 문서를 7건으로 줄인다.

    passed_result = assess_generation(passed)
    blocked_result = assess_generation(blocked)

    assert passed_result.quality.document_sources == 8
    assert (
        QualityProblemCode.TOO_FEW_DOCUMENT_SOURCES
        not in passed_result.quality.problem_codes
    )
    assert passed_result.quality.grade is QualityGrade.COMPLETE

    assert blocked_result.quality.document_sources == 7
    assert (
        QualityProblemCode.TOO_FEW_DOCUMENT_SOURCES
        in blocked_result.quality.problem_codes
    )
    assert blocked_result.quality.grade is QualityGrade.PARTIAL


def test_FULL계약은_주소만있고_정확한원문조각결속이없으면_공개하지않는다() -> None:
    result = assess_generation(
        _candidate(),
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert any(
        "정확한 원문 조각 결속" in problem
        for problem in result.safety.problems
    )


def test_FULL은_한장에_해석3개면_45claim_8문서여도_COMPLETE가_아니다() -> None:
    candidate = _with_interpretations(_full_candidate(), {"identity": 3})

    result = assess_generation(
        candidate,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert result.quality.substantive_claims == 45
    assert result.quality.document_sources == 8
    # 해석은 안전 검수를 통과해도 일반 «검증 사실» 수를 부풀리지 않는다.
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED
    assert len(result.safety.verified_fact_ids) == 45
    assert result.quality.verified_claims == 42
    assert result.quality.verified_ratio == Decimal(42) / Decimal(45)
    assert result.quality.grade is QualityGrade.PARTIAL
    assert result.quality.problem_codes == (
        QualityProblemCode.TOO_MANY_INTERPRETATION_CLAIMS_PER_SECTION,
    )
    assert result.quality.semantic_underfilled_sections == ("identity",)


def test_FULL은_쉬운다른슬롯으로_수를채워도_필수의미칸누락을_막는다() -> None:
    candidate = _full_candidate()
    unrelated_slots = (
        "business_model:sales_channel",
        "business_model:regional_mix",
    )
    attacked = replace(
        candidate,
        facts=tuple(
            replace(
                fact,
                claim_slot=unrelated_slots[index % len(unrelated_slots)],
            )
            if fact.section_owner == "business_model"
            else fact
            for index, fact in enumerate(candidate.facts)
        ),
    )

    result = assess_generation(
        attacked,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert result.quality.substantive_claims == 45
    assert result.quality.document_sources == 8
    assert dict(result.quality.section_public_sentence_counts)["business_model"] == 5
    assert dict(result.quality.section_claim_counts)["business_model"] == 0
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED
    assert result.quality.grade is QualityGrade.PARTIAL
    assert (
        QualityProblemCode.MISSING_REQUIRED_PUBLIC_CLAIM_SLOTS
        in result.quality.problem_codes
    )
    assert result.quality.semantic_underfilled_sections == ("business_model",)


def test_FULL은_한장이_2문장이면_총45claim과_필수칸을채워도_막는다() -> None:
    counts = {
        section_id: (
            2
            if section_id == "identity"
            else 7
            if section_id == "business_model"
            else 6
            if section_id == "competitive_position"
            else 5
        )
        for section_id in STRICT_REQUIRED_QUALITY_SECTION_IDS
    }
    candidate = _full_candidate(counts)

    result = assess_generation(
        candidate,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert result.quality.substantive_claims == 45
    assert result.quality.document_sources == 8
    assert dict(result.quality.section_claim_counts)["identity"] == 2
    assert result.quality.semantic_underfilled_sections == ()
    assert result.quality.underfilled_sections == ("identity",)
    assert result.quality.problem_codes == (
        QualityProblemCode.LOW_PUBLIC_SENTENCE_COVERAGE,
    )
    assert result.quality.grade is QualityGrade.PARTIAL


def test_FULL은_한fact를_문장수3으로_신고해도_한문장으로_센다() -> None:
    candidate = _full_candidate()
    identity = candidate.sections[0]
    padded = replace(
        candidate,
        sections=(
            replace(
                identity,
                fact_ids=(identity.fact_ids[0],),
                public_sentence_count=3,
            ),
            *candidate.sections[1:],
        ),
    )

    result = assess_generation(
        padded,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert dict(result.quality.section_public_sentence_counts)["identity"] == 1
    assert result.quality.underfilled_sections == ("identity",)
    assert (
        QualityProblemCode.LOW_PUBLIC_SENTENCE_COVERAGE
        in result.quality.problem_codes
    )
    assert result.publication_grade is not QualityGrade.COMPLETE


def test_FULL_정상9장은_필수의미칸과_장당3문장을_모두통과한다() -> None:
    result = assess_generation(
        _full_candidate(),
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert result.quality.substantive_claims == 45
    assert result.quality.document_sources == 8
    assert min(dict(result.quality.section_public_sentence_counts).values()) >= 3
    assert result.quality.semantic_underfilled_sections == ()
    assert result.quality.underfilled_sections == ()
    assert result.quality.problem_codes == ()
    assert result.quality.grade is QualityGrade.COMPLETE


def test_FULL의_생산_claim_type_5종은_닫힌계약으로_정상통과한다() -> None:
    """일반 산문·해석·실적·비교·비교맥락이 실제 v2 composer 공개 종류다."""

    candidate = _candidate_with_all_public_claim_types()

    result = assess_generation(
        candidate,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert {fact.claim_type for fact in candidate.facts} == STRICT_PUBLIC_CLAIM_TYPES
    assert result.quality.substantive_claims == 45
    assert result.quality.verified_claims == 44
    assert result.quality.verified_ratio == Decimal(44) / Decimal(45)
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED
    assert result.publication_grade is QualityGrade.COMPLETE


def test_FULL은_비교원값과_파생값을_함께_재계산해도_DART행과_다르면_차단한다() -> None:
    """자체 일관된 숫자도 공식 원문 두 건의 실제 피연산자와 달라서는 안 된다."""

    candidate = _candidate_with_all_public_claim_types()
    comparison = next(
        fact
        for fact in candidate.facts
        if fact.claim_type == COMPETITIVE_COMPARISON_CLAIM_TYPE
    )
    # 공격자는 양사 금액을 모두 두 배로 바꿔 영업이익률 20%·10%, 차이
    # 10%p를 그대로 유지하며 raw·검산 장부를 일관되게 다시 만들었다. 그러나
    # 봉인된 양사 DART 행은 여전히 200·40과 100·10이다.
    attacked = replace(
        comparison,
        raw_value="400;80;200;20",
        numeric_checks=(
            "400|1|0|400",
            "80|1|0|80",
            "200|1|0|200",
            "20|1|0|20",
            "80|4|1|20.0",
            "20|2|1|10.0",
            "10.0|1|1|10.0",
        ),
    )
    candidate = replace(
        candidate,
        facts=tuple(
            attacked if fact.fact_id == attacked.fact_id else fact
            for fact in candidate.facts
        ),
    )

    result = assess_generation(
        candidate,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert result.publication_grade is QualityGrade.INCOMPLETE
    assert any(
        "DART 계정 행의 당기금액" in problem
        for problem in result.safety.problems
    )


def test_FULL은_비교한건의_동일조건만_재봉인해도_프로그램전체를_차단한다() -> None:
    candidate = _candidate_with_all_public_claim_types()
    target = next(
        fact
        for fact in candidate.facts
        if fact.claim_type == COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE
        and fact.claim_slot == COMPARISON_METRIC_SLOT
    )
    attacked_conditions = dict(target.comparison_conditions)
    attacked_conditions["market"] = "전혀 다른 시장"
    attacked = replace(target, comparison_conditions=attacked_conditions)
    candidate = replace(
        candidate,
        facts=tuple(
            attacked if fact.fact_id == attacked.fact_id else fact
            for fact in candidate.facts
        ),
    )

    result = assess_generation(
        candidate,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert result.publication_grade is QualityGrade.INCOMPLETE
    assert any(
        "고객·제품·시장 범위" in problem for problem in result.safety.problems
    )


def test_FULL은_허용된_구조타입이름만_붙인_산문도_안전차단한다() -> None:
    for masquerade_type in (
        HISTORICAL_PERFORMANCE_RATE_CLAIM_TYPE,
        COMPETITIVE_COMPARISON_CLAIM_TYPE,
    ):
        candidate = _full_candidate()
        target = candidate.facts[0]
        candidate = replace(
            candidate,
            facts=(
                replace(target, claim_type=masquerade_type),
                *candidate.facts[1:],
            ),
        )

        result = assess_generation(
            candidate,
            contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        )

        assert result.safety.decision is ReleaseDecision.BLOCKED
        assert result.publication_grade is QualityGrade.INCOMPLETE
        if masquerade_type == HISTORICAL_PERFORMANCE_RATE_CLAIM_TYPE:
            assert any(
                "과거 실적 종류" in problem for problem in result.safety.problems
            )
        else:
            assert any(
                "공식 비교 수치" in problem for problem in result.safety.problems
            )


def test_FULL은_정상비교맥락_뒤에_근거없는절을_붙여도_안전차단한다() -> None:
    candidate = _full_candidate()
    target = next(
        fact
        for fact in candidate.facts
        if fact.fact_id == "competitive_position-full-fact-2"
    )
    canonical_claim = comparison_metric_claim(comparison_metric="매출 규모")
    attacked = replace(
        target,
        claim=canonical_claim + " 따라서 독보적인 제품 경쟁력이 있다.",
        claim_type=COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
        state_evidence="공식 원문 계정에는 매출액과 영업이익이 있다.",
        evidence_support_terms=("매출액", "영업이익"),
        comparison_target="비교사 주식회사",
        comparison_metric="매출 규모",
        comparator_source_id="full-source-1",
        # 실제 FactRecord 공격은 claim을 바꾼 뒤 evidence_binding도 다시
        # 계산한다. DTO의 true는 그 재봉인까지 끝났다는 조건이다.
        evidence_binding_valid=True,
    )
    candidate = replace(
        candidate,
        facts=tuple(
            attacked if fact.fact_id == attacked.fact_id else fact
            for fact in candidate.facts
        ),
        sources=tuple(
            replace(source, publisher="비교사 주식회사")
            if source.source_id == "full-source-1"
            else source
            for source in candidate.sources
        ),
    )

    result = assess_generation(
        candidate,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert result.publication_grade is QualityGrade.INCOMPLETE
    assert any("비교 맥락 문장" in problem for problem in result.safety.problems)


def test_FULL은_원문단어를_비교법인명으로_바꿔도_공식Source와_대조해차단한다() -> None:
    candidate = _full_candidate()
    target = next(
        fact
        for fact in candidate.facts
        if fact.fact_id == "competitive_position-full-fact-2"
    )
    attacked = replace(
        target,
        claim=comparison_metric_claim(comparison_metric="매출 규모"),
        claim_type=COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
        state_evidence="공식 원문 계정에는 매출액과 영업이익이 있다.",
        evidence_support_terms=("매출액", "영업이익"),
        # 원문에 흔히 있는 단어를 법인명처럼 넣었지만 실제 comparator Source
        # 발행자는 주식회사 베타다. 문장 정본만 보면 드러나지 않는 공격이다.
        comparison_target="경쟁",
        comparison_metric="매출 규모",
        comparator_source_id="full-source-1",
    )
    candidate = replace(
        candidate,
        facts=tuple(
            attacked if fact.fact_id == attacked.fact_id else fact
            for fact in candidate.facts
        ),
        sources=tuple(
            replace(source, publisher="주식회사 베타")
            if source.source_id == "full-source-1"
            else source
            for source in candidate.sources
        ),
    )

    result = assess_generation(
        candidate,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert result.publication_grade is QualityGrade.INCOMPLETE
    assert any("비교 대상 법인명" in problem for problem in result.safety.problems)


def test_FULL은_빈값_오타_미지원_claim_type을_사실로_세지않고_차단한다() -> None:
    """타입 문자열 공격은 필수칸·분자·분모 어느 것도 부풀릴 수 없다."""

    for invalid_type in ("", "evidence_based_interpretatio", "future_claim_type"):
        candidate = _full_candidate()
        target = next(
            fact
            for fact in candidate.facts
            if fact.fact_id == "business_model-full-fact-2"
        )
        # sales_channel은 필수 의미칸 밖의 정상 보강 문장이다. 이 한 건을
        # 제외해도 나머지 필수칸·장당 문장·총량은 충분하므로, 실패 원인은
        # 오직 닫히지 않은 타입이어야 한다.
        assert target.claim_slot == "business_model:sales_channel"
        candidate = replace(
            candidate,
            facts=tuple(
                replace(fact, claim_type=invalid_type)
                if fact.fact_id == target.fact_id
                else fact
                for fact in candidate.facts
            ),
        )

        result = assess_generation(
            candidate,
            contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        )
        observation = observe_generation(
            candidate,
            contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        )

        assert result.quality.substantive_claims == 44
        assert result.quality.verified_claims == 44
        assert result.quality.verified_ratio == Decimal(1)
        assert result.quality.grade is QualityGrade.COMPLETE
        assert result.safety.decision is ReleaseDecision.BLOCKED
        assert result.publication_grade is QualityGrade.INCOMPLETE
        assert any(
            target.fact_id in problem and "claim_type" in problem
            for problem in result.safety.problems
        )
        assert observation.substantive_claims == 44
        assert observation.verified_claims == 44
        assert observation.release_allowed is False
        # 영수증에 들어가는 관측 projection도 실제 평가와 다른 숫자를
        # 손으로 주입할 수 없다.
        assert_observation_matches_assessment(observation, result)


def test_FULL은_기준보고서수준_해석8개를_과잉차단하지않는다() -> None:
    candidate = _with_interpretations(
        _full_candidate(),
        {
            section_id: 1
            for section_id in STRICT_REQUIRED_QUALITY_SECTION_IDS[:8]
        },
    )

    result = assess_generation(
        candidate,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert result.quality.substantive_claims == 45
    assert result.quality.verified_claims == 37
    assert result.quality.verified_ratio == Decimal(37) / Decimal(45)
    assert result.quality.problem_codes == ()
    assert result.quality.grade is QualityGrade.COMPLETE
    assert result.publication_grade is QualityGrade.COMPLETE


def test_FULL은_전체해석_개수와_비율을_각각_상한으로_막는다() -> None:
    section_ids = STRICT_REQUIRED_QUALITY_SECTION_IDS
    eleven = _with_interpretations(
        _full_candidate(),
        {
            **{section_id: 2 for section_id in section_ids[:5]},
            section_ids[5]: 1,
        },
    )
    twelve = _with_interpretations(
        _full_candidate(),
        {section_id: 2 for section_id in section_ids[:6]},
    )

    count_blocked = assess_generation(
        eleven,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )
    ratio_blocked = assess_generation(
        twelve,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    # 11/45는 25% 아래지만 절대 개수 10건을 넘으므로 막힌다.
    assert Decimal(11) / Decimal(45) < Decimal("0.25")
    assert count_blocked.quality.problem_codes == (
        QualityProblemCode.EXCESSIVE_INTERPRETATION_CLAIMS,
    )
    # 12/45는 장당 2개 이내여도 전체 25%를 넘으므로 역시 막힌다.
    assert Decimal(12) / Decimal(45) > Decimal("0.25")
    assert ratio_blocked.quality.problem_codes == (
        QualityProblemCode.EXCESSIVE_INTERPRETATION_CLAIMS,
    )
    assert count_blocked.quality.semantic_underfilled_sections == ()
    assert ratio_blocked.quality.semantic_underfilled_sections == ()


def test_FULL_해석도_틀린_CAGR를_기존숫자검산기로_우회하지못한다() -> None:
    candidate = _full_candidate()
    original = next(
        fact for fact in candidate.facts if fact.section_owner == "past_changes"
    )
    binding = NumericBinding(
        version=NUMERIC_BINDING_VERSION,
        metric="매출",
        entity_scope=EntityScope.CONSOLIDATED,
        period_start="2023",
        period_end="2025",
        sign=NumericSign.POSITIVE,
        unit="%",
        unit_dimension=UnitDimension.PERCENT,
        formula=NumericFormula.CAGR,
        operands=(
            NumericOperand(
                role="start",
                metric="매출",
                entity_scope=EntityScope.CONSOLIDATED,
                period="2023",
                value="100",
                sign=NumericSign.POSITIVE,
                unit="억원",
                unit_dimension=UnitDimension.CURRENCY,
                source_identity=original.source_identity,
            ),
            NumericOperand(
                role="end",
                metric="매출",
                entity_scope=EntityScope.CONSOLIDATED,
                period="2025",
                value="124.28",
                sign=NumericSign.POSITIVE,
                unit="억원",
                unit_dimension=UnitDimension.CURRENCY,
                source_identity=original.source_identity,
            ),
        ),
        period_count="2",
        calculated_value="25",
        display_value="25.00",
        rounding_mode=ROUNDING_MODE,
        rounding_places=2,
        tolerance="0.0001",
        source_identity=original.source_identity,
        verification_state=VerificationState.VERIFIED,
    )
    wrong_cagr = claim_fact_from_binding(
        fact_id=original.fact_id,
        section_owner=original.section_owner,
        source_id=original.source_id,
        claim="2년 누적 24.28%를 연평균 25% 이상으로 해석할 수 있다.",
        claim_slot=original.claim_slot,
        binding=binding,
    )
    wrong_cagr = replace(
        wrong_cagr,
        evidence_binding_valid=True,
        supporting_source_ids=original.supporting_source_ids,
        supporting_source_identities=original.supporting_source_identities,
        supporting_evidence_hashes=original.supporting_evidence_hashes,
        claim_type=INTERPRETATION_CLAIM_TYPE,
    )
    candidate = replace(
        candidate,
        facts=tuple(
            wrong_cagr if fact.fact_id == original.fact_id else fact
            for fact in candidate.facts
        ),
    )

    result = assess_generation(
        candidate,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert result.quality.verified_claims == 44
    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert result.publication_grade is QualityGrade.INCOMPLETE
    assert any(
        "공식 재계산 결과에 맞지 않습니다" in problem
        for problem in result.safety.problems
    )


def test_전체40개여도_한문장인_장이_있으면_complete가_아니다() -> None:
    counts = {section_id: 5 for section_id in REQUIRED_QUALITY_SECTION_IDS}
    counts["identity"] = 1
    counts["business_model"] = 9

    result = assess_generation(_candidate(counts))

    assert result.quality.substantive_claims == 40
    assert result.quality.one_claim_sections == ("identity",)
    assert result.quality.grade is QualityGrade.PARTIAL
    assert QualityProblemCode.ONE_CLAIM_SECTIONS in result.quality.problem_codes
    assert (
        QualityProblemCode.LOW_PUBLIC_SENTENCE_COVERAGE
        in result.quality.problem_codes
    )
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED
    assert result.publication_grade is QualityGrade.PARTIAL


def test_빈_장도_보충대상인_얇은_장으로_구조화한다() -> None:
    counts = {section_id: 5 for section_id in REQUIRED_QUALITY_SECTION_IDS}
    counts["identity"] = 0
    counts["business_model"] = 10

    result = assess_generation(_candidate(counts))

    assert "identity" in result.quality.notice_only_sections
    assert "identity" in result.quality.underfilled_sections
    assert (
        QualityProblemCode.LOW_PUBLIC_SENTENCE_COVERAGE
        in result.quality.problem_codes
    )


def test_품질행동은_사람문구가_아닌_닫힌_문제코드를_받는다() -> None:
    candidate = _candidate(one_document=True)
    result = assess_generation(candidate)

    assert result.quality.problem_codes == (
        QualityProblemCode.TOO_FEW_DOCUMENT_SOURCES,
    )
    assert all(
        isinstance(code, QualityProblemCode)
        for code in result.quality.problem_codes
    )
    assert observe_generation(candidate).quality_problem_codes == (
        QualityProblemCode.TOO_FEW_DOCUMENT_SOURCES.value,
    )


def test_FULL처럼_서로다른조각8개가_같은문서면_출처는_한건으로_센다() -> None:
    candidate = _candidate(one_document=True)
    full_shaped = replace(
        candidate,
        sources=tuple(
            replace(source, exact_evidence_hashes=(f"{index:064x}",))
            for index, source in enumerate(candidate.sources, start=1)
        ),
    )

    result = assess_generation(full_shaped)

    assert result.quality.document_sources == 1
    assert result.quality.grade is QualityGrade.PARTIAL
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED


def test_조각지문이_같아도_문서신원이_다르면_독립문서로_센다() -> None:
    """v1은 문서 전체 지문이 생기기 전 identity 의미를 보존한다."""

    candidate = _candidate()
    copied = replace(
        candidate,
        sources=tuple(
            replace(source, exact_evidence_hashes=("a" * 64,))
            for source in candidate.sources
        ),
    )

    result = assess_generation(copied)

    assert result.quality.document_sources == 8
    assert result.quality.grade is QualityGrade.COMPLETE
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED


def test_FULL은_URL신원8개여도_문서전체지문이_같으면_한건으로_센다() -> None:
    candidate = _full_candidate()
    copied = replace(
        candidate,
        sources=tuple(
            replace(source, document_content_sha256="a" * 64)
            for source in candidate.sources
        ),
    )

    result = assess_generation(
        copied,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    # URL/document_identity와 조각 지문은 서로 다르지만 원문 전체 바이트가
    # 같으므로 독립 문서는 한 건이다. 복제 URL로 8문서 하한을 못 넘는다.
    assert len({source.document_identity for source in copied.sources}) == 8
    assert len({source.exact_evidence_hashes for source in copied.sources}) == 9
    assert result.quality.document_sources == 1
    assert result.quality.problem_codes == (
        QualityProblemCode.TOO_FEW_DOCUMENT_SOURCES,
    )
    assert result.quality.grade is QualityGrade.PARTIAL
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED


def test_FULL은_문서전체지문8개가_다르면_독립문서8건으로_센다() -> None:
    candidate = _full_candidate()

    result = assess_generation(
        candidate,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    # 아홉 번째 장은 여덟 번째 문서의 다른 조각을 재사용한다.
    assert len({source.document_identity for source in candidate.sources}) == 8
    assert len({source.document_content_sha256 for source in candidate.sources}) == 8
    assert result.quality.document_sources == 8
    assert QualityProblemCode.TOO_FEW_DOCUMENT_SOURCES not in result.quality.problem_codes
    assert result.quality.grade is QualityGrade.COMPLETE
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED


def test_FULL은_문서전체지문_형식이_손상되면_공개안전을_막는다() -> None:
    candidate = _full_candidate()
    first = candidate.sources[0]
    damaged = replace(
        candidate,
        sources=(
            replace(first, document_content_sha256="대충 만든 지문"),
            *candidate.sources[1:],
        ),
    )

    result = assess_generation(
        damaged,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert result.quality.document_sources == 7
    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert any("문서 전체 해시가 손상" in problem for problem in result.safety.problems)


def test_품질이_충분해도_미검증_claim은_공개안전을_통과하지_못한다() -> None:
    candidate = _candidate()
    first = candidate.facts[0]
    broken = replace(
        candidate,
        facts=(
            replace(first, verification_state=VerificationState.UNVERIFIED.value),
            *candidate.facts[1:],
        ),
    )

    result = assess_generation(broken)

    assert result.quality.grade is QualityGrade.COMPLETE
    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert result.publication_grade is QualityGrade.INCOMPLETE
    assert result.release_allowed is False


def test_평가전_보고서를_complete로_기본간주하지_않는다() -> None:
    result = assess_generation(ReportCandidate((), (), ()))

    assert result.quality.grade is QualityGrade.INCOMPLETE
    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert result.publication_grade is QualityGrade.INCOMPLETE


def test_숫자날짜퍼센트와_영문에_붙은_숫자도_결속대상이다() -> None:
    assert has_public_numeric_token("누적 증감률은 24.28%입니다.")
    assert has_public_numeric_token("2025Q1 실적입니다.")
    assert has_public_numeric_token("H100 제품입니다.")
    assert has_public_numeric_token("매출이 두 배로 늘었습니다.")
    assert has_public_numeric_token("연평균 이십오 퍼센트 이상입니다.")
    assert has_public_numeric_token("이익이 절반으로 줄었습니다.")
    assert not has_public_numeric_token("공식 자료에서 성장 흐름이 확인됩니다.")
    assert not has_public_numeric_token("이 회사의 사업 구조를 설명합니다.")


def test_같은_claim_slot의_서로_다른_원자사실은_허용한다() -> None:
    candidate = _candidate()
    first, second = candidate.facts[:2]
    categorized = replace(
        candidate,
        facts=(
            first,
            replace(second, claim_slot=first.claim_slot),
            *candidate.facts[2:],
        ),
    )

    result = assess_generation(categorized)

    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED


def test_문장이_여러개여도_한가지_의미범주뿐이면_complete가_아니다() -> None:
    candidate = _candidate()
    identity_slot = CLAIM_SLOTS_BY_SECTION["identity"][0]
    shallow = replace(
        candidate,
        facts=tuple(
            replace(fact, claim_slot=identity_slot)
            if fact.section_owner == "identity"
            else fact
            for fact in candidate.facts
        ),
    )

    result = assess_generation(shallow)

    assert result.quality.substantive_claims == 40
    assert result.quality.grade is QualityGrade.PARTIAL
    assert result.quality.semantic_underfilled_sections == ("identity",)
    assert QualityProblemCode.LOW_SEMANTIC_COVERAGE in result.quality.problem_codes
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED
    assert any(
        "서로 다른 의미 claim 범주" in reason
        for reason in result.quality.shortfall_reasons
    )


def test_같은_원자claim을_다른_fact_id로_부풀리면_통과하지_못한다() -> None:
    candidate = _candidate()
    first, second = candidate.facts[:2]
    padded = replace(
        candidate,
        facts=(
            first,
            replace(
                second,
                claim_slot=first.claim_slot,
                claim=f"  {first.claim.upper()}  ",
            ),
            *candidate.facts[2:],
        ),
    )

    result = assess_generation(padded)

    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert any("같은 원자 claim" in problem for problem in result.safety.problems)


def test_다른_장의_claim_slot을_섞으면_통과하지_못한다() -> None:
    candidate = _candidate()
    first = candidate.facts[0]
    broken = replace(
        candidate,
        facts=(
            replace(
                first,
                claim_slot=CLAIM_SLOTS_BY_SECTION["business_model"][0],
            ),
            *candidate.facts[1:],
        ),
    )

    result = assess_generation(broken)

    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert any("장 정책" in problem for problem in result.safety.problems)


def test_원문결속지문이_유효하지_않으면_공개하지_않는다() -> None:
    candidate = _candidate()
    first = candidate.facts[0]
    broken = replace(
        candidate,
        facts=(replace(first, evidence_binding_valid=False), *candidate.facts[1:]),
    )

    result = assess_generation(broken)

    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert any("결속 지문" in problem for problem in result.safety.problems)

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from src.features.report_quality.assessment import (
    assess_generation,
    has_public_numeric_token,
)
from src.features.report_quality.constants import (
    REQUIRED_QUALITY_SECTION_IDS,
    STRICT_QUALITY_CONTRACT_VERSION,
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
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_quality.generation import observe_generation


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


def test_조각8개가_같은문서면_출처는_한건으로_센다() -> None:
    result = assess_generation(_candidate(one_document=True))

    assert result.quality.document_sources == 1
    assert result.quality.grade is QualityGrade.PARTIAL
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED


def test_URL만_다르고_원문바이트가_같으면_출처는_한건으로_센다() -> None:
    candidate = _candidate()
    copied = replace(
        candidate,
        sources=tuple(
            replace(source, exact_evidence_hashes=("a" * 64,))
            for source in candidate.sources
        ),
    )

    result = assess_generation(copied)

    assert result.quality.document_sources == 1
    assert result.quality.grade is QualityGrade.PARTIAL
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED


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

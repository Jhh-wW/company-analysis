"""새 생성 결과를 품질 계약에 연결하는 중립 오케스트레이션 경계.

두 feature가 같은 계약을 직접 쓰도록 shared에 둔 중립 모듈이다. 이 모듈은
현재 composer가 가진 구조만 정직하게 ``report_quality`` DTO로 옮긴다. 결속된
구조화 claim은 그대로 평가하고, 계약이 없는 산문은 텍스트를 정규식으로
분해해 가짜 사실을 만들지 않고 ``결속되지 않은 공개 내용``으로 표시한다.

이 결과는 운영 영향 측정을 위한 shadow 관측값이다. 공개 차단을 켜는 결정은
구조화 claim 생산 경로와 과거 보고서 영향 측정이 끝난 뒤 별도로 해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.shared.report_quality.assessment import assess_generation
from src.shared.report_quality.contract import resolve_contract
from src.shared.report_quality.dto import ReportCandidate, ReportSectionCandidate
from src.shared.report_quality.models import ContractUse
from src.shared.report_quality.models import PublicationPolicy


SHADOW_ASSESSMENT_MODE = "generation-shadow"
#: 표·도식까지는 아직 하나씩 확인하지 못했을 때 머리말 끝에 붙는 한 줄.
#:
#: ★ 2026-08-29 — 옛 문구는 「«새 안전 검사»에서 아직 확인하지 못한 …
#:   전체 내용을 «새 구조로» 검증하는 작업은 아직 끝나지 않았습니다」였다.
#:   「새 안전 검사」·「새 구조」는 우리가 검증 방식을 바꾸는 중이라는 «우리 사정»이고,
#:   내용도 바로 위 제목 줄(「안전 확인 중인 임시 부분 보고서 — …」)과 거의 겹쳤다.
#:   눈가림 독립 평가에서 세 평가자가 모두 이런 내부 문구 노출을 감점 1위로 꼽았다.
#: ★ 그래서 제목이 «이미 말한 것»은 빼고, 제목이 말하지 않는 것 —
#:   «독자가 무엇을 하면 되는지» — 를 남긴다. 고지의 힘은 그대로다.
LEGACY_SHADOW_PUBLICATION_REASON = (
    "표와 도식은 아직 하나씩 확인하지 못했습니다. "
    "숫자를 그대로 인용하기 전에 부록의 원문을 함께 확인해 주세요."
)


@dataclass(frozen=True)
class UnboundGenerationSection:
    """생성기는 공개 문장을 가졌지만 원자 claim 결속은 아직 없는 한 장."""

    section_id: str
    has_public_content: bool
    notice_only: bool


@dataclass(frozen=True)
class GenerationQualityObservation:
    """채널·저장 자료형에 의존하지 않는 생성 시점 shadow 측정값."""

    mode: str
    contract_version: str
    quality_grade: str
    safety_decision: str
    publication_grade: str
    release_allowed: bool
    quality_shortfalls: tuple[str, ...]
    safety_problems: tuple[str, ...]
    substantive_claims: int
    verified_claims: int
    verified_ratio: str
    document_sources: int
    # 새 생성 경로가 사용자에게 PARTIAL 이유를 구체적으로 표시할 수 있게
    # assessor의 장별 결과를 손실 없이 전달한다. 과거 GET은 이 DTO를 만들지 않는다.
    section_public_sentence_counts: tuple[tuple[str, int], ...] = ()
    underfilled_sections: tuple[str, ...] = ()
    notice_only_sections: tuple[str, ...] = ()
    semantic_underfilled_sections: tuple[str, ...] = ()
    # 사람이 읽는 문구와 행동 계약을 분리한 닫힌 품질 코드.
    quality_problem_codes: tuple[str, ...] = ()


def observe_unbound_generation(
    sections: tuple[UnboundGenerationSection, ...],
    *,
    contract_version: str = "",
) -> GenerationQualityObservation:
    """새 composer 생성물을 가짜 FactRecord 없이 versioned assessor로 측정한다.

    ``ContractUse.GENERATION``을 명시해 조회 시점 정책과 섞이지 않게 한다.
    과거 GET은 이 함수를 호출하지 않으며, 기존 ``resolve_contract``도 조회에는
    assessor를 실행하지 않는다고 보장한다.
    """

    candidate = ReportCandidate(
        sections=tuple(
            ReportSectionCandidate(
                section_id=section.section_id,
                fact_ids=(),
                notice_only=section.notice_only,
                has_unbound_public_content=section.has_public_content,
            )
            for section in sections
        ),
        # 구조가 없는 옛 호출은 문장 텍스트·인용 번호로 사실을 발명하지 않는다.
        facts=(),
        sources=(),
        summary_fact_ids=(),
    )
    return observe_generation(candidate, contract_version=contract_version)


def observe_generation(
    candidate: ReportCandidate,
    *,
    contract_version: str = "",
) -> GenerationQualityObservation:
    """구조화된 새 생성 후보를 versioned assessor로 한 번만 측정한다."""

    resolution = resolve_contract(
        contract_version,
        use=ContractUse.GENERATION,
    )
    if not resolution.assess_now:
        raise RuntimeError("새 생성 품질 계약이 평가 비활성 상태입니다")

    assessment = assess_generation(
        candidate,
        contract_version=resolution.resolved_version,
    )
    return GenerationQualityObservation(
        mode=SHADOW_ASSESSMENT_MODE,
        contract_version=assessment.contract_version,
        quality_grade=assessment.quality.grade.value,
        safety_decision=assessment.safety.decision.value,
        publication_grade=assessment.publication_grade.value,
        release_allowed=assessment.release_allowed,
        quality_shortfalls=assessment.quality.shortfall_reasons,
        safety_problems=assessment.safety.problems,
        substantive_claims=assessment.quality.substantive_claims,
        verified_claims=assessment.quality.verified_claims,
        verified_ratio=str(assessment.quality.verified_ratio),
        document_sources=assessment.quality.document_sources,
        section_public_sentence_counts=(
            assessment.quality.section_public_sentence_counts
        ),
        underfilled_sections=assessment.quality.underfilled_sections,
        notice_only_sections=assessment.quality.notice_only_sections,
        semantic_underfilled_sections=(
            assessment.quality.semantic_underfilled_sections
        ),
        quality_problem_codes=tuple(
            code.value for code in assessment.quality.problem_codes
        ),
    )

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
from decimal import Decimal
from typing import Any, Mapping

from src.shared.report_quality.assessment import assess_generation
from src.shared.report_quality.contract import resolve_contract
from src.shared.report_quality.dto import ReportCandidate, ReportSectionCandidate
from src.shared.report_quality.models import ContractUse, GenerationAssessment
from src.shared.report_quality.models import PublicationPolicy


SHADOW_ASSESSMENT_MODE = "generation-shadow"
#: 표·도식까지는 아직 하나씩 확인하지 못했을 때 ``shortfall_reasons``에 붙는
#: 한 줄. ★ 독자 화면에는 «나오지 않는다» — 저장본·관리자 화면·진단이 읽는
#: 내부 기록이다.
#:
#: ★ 2026-09-05 사용자 결정: 서비스가 출시됐고 보고서는 더 이상 「임시」가
#:   아니다. 그래서 웹·PDF·노션의 부분 보고서 고지 블록과 미제공 사유 목록을
#:   «표시»에서 통째로 뺐다(``web/templates/result.html``,
#:   ``export_pdf/logic.py::_grade_notice``,
#:   ``export_notion/logic.py::_v2_grade_notice_blocks``). 이 문장은 그 목록에
#:   들어가므로 독자에게 보이지 않는다.
#: ★ 그래도 «생산은 유지»한다. 이 줄이 없으면 안전 미통과로 공개된 보고서를
#:   나중에 왜 그렇게 냈는지 되짚을 기록이 사라진다. 지운 것은 표시뿐이다.
#: ★ 옛 문구는 「«새 안전 검사»에서 아직 확인하지 못한 …
#:   전체 내용을 «새 구조로» 검증하는 작업은 아직 끝나지 않았습니다」였다.
#:   「새 안전 검사」·「새 구조」는 우리가 검증 방식을 바꾸는 중이라는 «우리 사정»
#:   이라 지금 문구로 바꿨다. 값은 시험이 글자로 못 박고 있으므로 함부로
#:   바꾸지 않는다(``composer/tests/test_pipeline.py``).
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


_OBSERVATION_WIRE_KEYS = frozenset(
    {
        "mode",
        "contract_version",
        "quality_grade",
        "safety_decision",
        "publication_grade",
        "release_allowed",
        "quality_shortfalls",
        "safety_problems",
        "substantive_claims",
        "verified_claims",
        "verified_ratio",
        "document_sources",
        "section_public_sentence_counts",
        "underfilled_sections",
        "semantic_underfilled_sections",
        "notice_only_sections",
        "quality_problem_codes",
    }
)


def generation_quality_observation_to_dict(
    value: GenerationQualityObservation,
) -> dict[str, object]:
    """표시 관측값을 느슨한 문자열 재구성 없이 exact wire로 바꾼다."""

    if type(value) is not GenerationQualityObservation:
        raise TypeError("정확한 GenerationQualityObservation이 필요합니다")
    return {
        "mode": value.mode,
        "contract_version": value.contract_version,
        "quality_grade": value.quality_grade,
        "safety_decision": value.safety_decision,
        "publication_grade": value.publication_grade,
        "release_allowed": value.release_allowed,
        "quality_shortfalls": list(value.quality_shortfalls),
        "safety_problems": list(value.safety_problems),
        "substantive_claims": value.substantive_claims,
        "verified_claims": value.verified_claims,
        "verified_ratio": value.verified_ratio,
        "document_sources": value.document_sources,
        "section_public_sentence_counts": [
            list(item) for item in value.section_public_sentence_counts
        ],
        "underfilled_sections": list(value.underfilled_sections),
        "semantic_underfilled_sections": list(
            value.semantic_underfilled_sections
        ),
        "notice_only_sections": list(value.notice_only_sections),
        "quality_problem_codes": list(value.quality_problem_codes),
    }


def _observation_strings(value: object, *, label: str) -> tuple[str, ...]:
    if type(value) is not list or any(
        type(item) is not str or item != item.strip() or not item
        for item in value
    ):
        raise ValueError(f"{label}은 공백 없는 문자열 JSON 배열이어야 합니다")
    return tuple(value)


def generation_quality_observation_from_dict(
    data: Mapping[str, Any],
) -> GenerationQualityObservation:
    """unknown/missing key와 bool→int 변환을 거절하고 관측 원본을 복원한다."""

    if type(data) is not dict or set(data) != _OBSERVATION_WIRE_KEYS:
        raise ValueError("GenerationQualityObservation key 또는 객체 형식이 다릅니다")
    for key in (
        "mode",
        "contract_version",
        "quality_grade",
        "safety_decision",
        "publication_grade",
        "verified_ratio",
    ):
        value = data[key]
        if type(value) is not str or value != value.strip() or not value:
            raise ValueError(f"GenerationQualityObservation {key}가 손상됐습니다")
    if type(data["release_allowed"]) is not bool:
        raise ValueError("GenerationQualityObservation release_allowed는 bool이어야 합니다")
    for key in ("substantive_claims", "verified_claims", "document_sources"):
        value = data[key]
        if type(value) is not int or value < 0:
            raise ValueError(f"GenerationQualityObservation {key}는 0 이상의 정수여야 합니다")
    ratio = Decimal(data["verified_ratio"])
    if not ratio.is_finite() or format(ratio, "f") != data["verified_ratio"]:
        raise ValueError("GenerationQualityObservation verified_ratio가 canonical이 아닙니다")
    raw_counts = data["section_public_sentence_counts"]
    if type(raw_counts) is not list or any(
        type(item) is not list
        or len(item) != 2
        or type(item[0]) is not str
        or item[0] != item[0].strip()
        or not item[0]
        or type(item[1]) is not int
        or item[1] < 0
        for item in raw_counts
    ):
        raise ValueError("GenerationQualityObservation 장별 공개 문장 수가 손상됐습니다")
    value = GenerationQualityObservation(
        mode=data["mode"],
        contract_version=data["contract_version"],
        quality_grade=data["quality_grade"],
        safety_decision=data["safety_decision"],
        publication_grade=data["publication_grade"],
        release_allowed=data["release_allowed"],
        quality_shortfalls=_observation_strings(
            data["quality_shortfalls"], label="품질 부족 사유"
        ),
        safety_problems=_observation_strings(
            data["safety_problems"], label="안전 문제"
        ),
        substantive_claims=data["substantive_claims"],
        verified_claims=data["verified_claims"],
        verified_ratio=data["verified_ratio"],
        document_sources=data["document_sources"],
        section_public_sentence_counts=tuple(
            (item[0], item[1]) for item in raw_counts
        ),
        underfilled_sections=_observation_strings(
            data["underfilled_sections"], label="공개 문장 부족 장"
        ),
        semantic_underfilled_sections=_observation_strings(
            data["semantic_underfilled_sections"], label="의미 부족 장"
        ),
        notice_only_sections=_observation_strings(
            data["notice_only_sections"], label="안내문 장"
        ),
        quality_problem_codes=_observation_strings(
            data["quality_problem_codes"], label="품질 문제 코드"
        ),
    )
    if generation_quality_observation_to_dict(value) != data:
        raise ValueError("GenerationQualityObservation canonical 왕복이 다릅니다")
    return value


def assert_observation_matches_assessment(
    observation: GenerationQualityObservation,
    assessment: GenerationAssessment,
) -> None:
    """관측 문자열을 권위로 쓰지 않고 같은 평가 원본의 projection인지 확인한다."""

    if type(observation) is not GenerationQualityObservation:
        raise TypeError("정확한 GenerationQualityObservation이 필요합니다")
    if type(assessment) is not GenerationAssessment:
        raise TypeError("정확한 GenerationAssessment가 필요합니다")
    expected = {
        "contract_version": assessment.contract_version,
        "quality_grade": assessment.quality.grade.value,
        "safety_decision": assessment.safety.decision.value,
        "publication_grade": assessment.publication_grade.value,
        "release_allowed": assessment.release_allowed,
        "quality_shortfalls": assessment.quality.shortfall_reasons,
        "safety_problems": assessment.safety.problems,
        "substantive_claims": assessment.quality.substantive_claims,
        "verified_claims": assessment.quality.verified_claims,
        "verified_ratio": str(assessment.quality.verified_ratio),
        "document_sources": assessment.quality.document_sources,
        "section_public_sentence_counts": (
            assessment.quality.section_public_sentence_counts
        ),
        "underfilled_sections": assessment.quality.underfilled_sections,
        "semantic_underfilled_sections": (
            assessment.quality.semantic_underfilled_sections
        ),
        "notice_only_sections": assessment.quality.notice_only_sections,
        "quality_problem_codes": tuple(
            code.value for code in assessment.quality.problem_codes
        ),
    }
    actual = {
        key: getattr(observation, key)
        for key in expected
    }
    if actual != expected:
        raise ValueError("GenerationQualityObservation이 실제 평가 원본과 다릅니다")


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

    _assessment, observation = assess_and_observe_generation(
        candidate,
        contract_version=contract_version,
    )
    return observation


def assess_and_observe_generation(
    candidate: ReportCandidate,
    *,
    contract_version: str = "",
) -> tuple[GenerationAssessment, GenerationQualityObservation]:
    """같은 후보를 정확히 한 번 평가하고 원본 평가와 표시 관측을 함께 준다.

    strict producer transport는 문자열 관측에서 평가를 재구성하지 않고 첫 반환값을
    그대로 운반한다. 기존 SHADOW 호출자는 두 번째 값만 쓰는
    :func:`observe_generation` 계약을 계속 유지한다.
    """

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
    observation = GenerationQualityObservation(
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
    return assessment, observation


__all__ = [
    "GenerationQualityObservation",
    "LEGACY_SHADOW_PUBLICATION_REASON",
    "SHADOW_ASSESSMENT_MODE",
    "UnboundGenerationSection",
    "assert_observation_matches_assessment",
    "assess_and_observe_generation",
    "generation_quality_observation_from_dict",
    "generation_quality_observation_to_dict",
    "observe_generation",
    "observe_unbound_generation",
]

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from src.shared.report_quality.constants import (
    STRICT_QUALITY_CONTRACT_VERSION,
    STRICT_REQUIRED_QUALITY_SECTION_IDS,
)
from src.shared.report_quality.integrity import (
    AssessmentIntegrityError,
    assert_complete_generation_assessment,
)
from src.shared.report_quality.models import (
    GenerationAssessment,
    QualityAssessment,
    QualityGrade,
    ReleaseDecision,
    SafetyAssessment,
)


def _complete_assessment() -> GenerationAssessment:
    fact_ids = tuple(f"fact-{index}" for index in range(1, 46))
    quality = QualityAssessment(
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        grade=QualityGrade.COMPLETE,
        substantive_claims=45,
        verified_claims=45,
        verified_ratio=Decimal("1"),
        document_sources=8,
        notice_only_sections=(),
        one_claim_sections=(),
        section_claim_counts=tuple(
            (section_id, 5)
            for section_id in STRICT_REQUIRED_QUALITY_SECTION_IDS
        ),
        shortfall_reasons=(),
        section_public_sentence_counts=tuple(
            (section_id, 5)
            for section_id in STRICT_REQUIRED_QUALITY_SECTION_IDS
        ),
        underfilled_sections=(),
        semantic_underfilled_sections=(),
        problem_codes=(),
    )
    safety = SafetyAssessment(
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        decision=ReleaseDecision.RELEASE_ALLOWED,
        verified_fact_ids=fact_ids,
        unverified_fact_ids=(),
        rejected_fact_ids=(),
        problems=(),
    )
    return GenerationAssessment(
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        quality=quality,
        safety=safety,
        publication_grade=QualityGrade.COMPLETE,
    )


def test_서로_맞는_완성_판정만_출고_재검산을_통과한다() -> None:
    assert_complete_generation_assessment(_complete_assessment())


@pytest.mark.parametrize(
    "quality_change",
    (
        {"substantive_claims": 1, "verified_claims": 1},
        {"verified_ratio": Decimal("0.99")},
        {"document_sources": 7},
        {
            "section_public_sentence_counts": tuple(
                (section_id, 1 if index == 0 else 5)
                for index, section_id in enumerate(
                    STRICT_REQUIRED_QUALITY_SECTION_IDS
                )
            )
        },
    ),
)
def test_완성_문자열이어도_숫자가_계약과_다르면_거절한다(
    quality_change: dict[str, object],
) -> None:
    assessment = _complete_assessment()
    forged = replace(
        assessment,
        quality=replace(assessment.quality, **quality_change),
    )

    with pytest.raises(AssessmentIntegrityError):
        assert_complete_generation_assessment(forged)


def test_검증_fact_목록을_줄이고_개수만_완성으로_꾸밀_수_없다() -> None:
    assessment = _complete_assessment()
    forged = replace(
        assessment,
        safety=replace(
            assessment.safety,
            verified_fact_ids=assessment.safety.verified_fact_ids[:-1],
        ),
    )

    with pytest.raises(AssessmentIntegrityError):
        assert_complete_generation_assessment(forged)


def test_판정_dataclass_하위클래스로_완성을_위조할_수_없다() -> None:
    assessment = _complete_assessment()

    class ForgedGenerationAssessment(GenerationAssessment):
        pass

    forged = ForgedGenerationAssessment(
        contract_version=assessment.contract_version,
        quality=assessment.quality,
        safety=assessment.safety,
        publication_grade=assessment.publication_grade,
    )

    with pytest.raises(TypeError):
        assert_complete_generation_assessment(forged)


def test_bool_개수나_숫자_fact_id를_정상값으로_보정하지_않는다() -> None:
    assessment = _complete_assessment()
    bool_count = replace(
        assessment,
        quality=replace(assessment.quality, substantive_claims=True),
    )
    numeric_fact = replace(
        assessment,
        safety=replace(
            assessment.safety,
            verified_fact_ids=(1, *assessment.safety.verified_fact_ids[1:]),  # type: ignore[arg-type]
        ),
    )

    with pytest.raises(AssessmentIntegrityError):
        assert_complete_generation_assessment(bool_count)
    with pytest.raises(AssessmentIntegrityError):
        assert_complete_generation_assessment(numeric_fact)

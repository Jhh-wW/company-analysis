from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from src.shared.generation_validation_receipt import (
    GenerationValidationReceipt,
    ValidationRound,
    generation_assessment_from_dict,
    generation_assessment_to_dict,
)
from src.shared.report_quality.constants import (
    LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
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
from src.shared.report_evidence.policy import required_slots_for


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
            (section_id, len(required_slots_for(section_id)))
            for section_id in STRICT_REQUIRED_QUALITY_SECTION_IDS
        ),
        shortfall_reasons=(),
        section_public_sentence_counts=tuple(
            (
                section_id,
                4
                if section_id == "identity"
                else 6
                if section_id == "competitive_position"
                else 5,
            )
            for section_id in STRICT_REQUIRED_QUALITY_SECTION_IDS
        ),
        underfilled_sections=(),
        semantic_underfilled_sections=(),
        section_interpretation_counts=tuple(
            (section_id, 0)
            for section_id in STRICT_REQUIRED_QUALITY_SECTION_IDS
        ),
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


def test_v3는_해석을뺀_검증사실수와_전체안전fact수를_분리해_재검산한다() -> None:
    assessment = _complete_assessment()
    separated = replace(
        assessment,
        quality=replace(
            assessment.quality,
            verified_claims=37,
            verified_ratio=Decimal(37) / Decimal(45),
            section_interpretation_counts=tuple(
                (section_id, 1 if index < 8 else 0)
                for index, section_id in enumerate(
                    STRICT_REQUIRED_QUALITY_SECTION_IDS
                )
            ),
        ),
    )

    assert_complete_generation_assessment(separated)


def test_v3_해석11건으로_꾸민_COMPLETE는_무결성재검산도_거절한다() -> None:
    assessment = _complete_assessment()
    forged = replace(
        assessment,
        quality=replace(
            assessment.quality,
            verified_claims=34,
            verified_ratio=Decimal(34) / Decimal(45),
        ),
    )

    with pytest.raises(AssessmentIntegrityError, match="해석 claim 수"):
        assert_complete_generation_assessment(forged)


def test_과거_v2_완성영수증은_발급당시_verified뜻으로_계속읽는다() -> None:
    assessment = _complete_assessment()
    legacy = replace(
        assessment,
        contract_version=LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
        quality=replace(
            assessment.quality,
            contract_version=LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
            section_interpretation_counts=(),
        ),
        safety=replace(
            assessment.safety,
            contract_version=LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
        ),
    )

    assert_complete_generation_assessment(legacy)
    wire = generation_assessment_to_dict(legacy)
    assert "section_interpretation_counts" not in wire["quality"]
    assert generation_assessment_from_dict(wire) == legacy


def test_v3_영수증은_장별해석수를_누락할수없다() -> None:
    wire = generation_assessment_to_dict(_complete_assessment())
    wire["quality"].pop("section_interpretation_counts")

    with pytest.raises(ValueError, match="키 또는 객체 형식"):
        generation_assessment_from_dict(wire)


def test_한장에_해석3개를_몰고_평가를_재구성해도_새영수증을_만들수없다() -> None:
    assessment = _complete_assessment()
    counts = (3, 1, 1, 1, 1, 1, 0, 0, 0)
    forged = replace(
        assessment,
        quality=replace(
            assessment.quality,
            verified_claims=37,
            verified_ratio=Decimal(37) / Decimal(45),
            section_interpretation_counts=tuple(
                (section_id, count)
                for section_id, count in zip(
                    STRICT_REQUIRED_QUALITY_SECTION_IDS,
                    counts,
                )
            ),
        ),
    )
    # 공격자가 평가 wire와 그 canonical 표현을 통째로 다시 만들 수 있다고
    # 가정한다. 단순 옛 digest 불일치가 아니라 v3 장별 상한 자체가 막아야 한다.
    rebuilt = generation_assessment_from_dict(
        generation_assessment_to_dict(forged)
    )

    with pytest.raises(AssessmentIntegrityError, match="한 장 해석 claim 수"):
        GenerationValidationReceipt(
            company_id="company-1",
            candidate_sha256="a" * 64,
            assessment=rebuilt,
            round=ValidationRound.PRIMARY,
            writer_calls=9,
            reviewer_calls=1,
            section_sha256s=tuple(
                (section_id, "b" * 64)
                for section_id in STRICT_REQUIRED_QUALITY_SECTION_IDS
            ),
            evidence_packet_sha256s=tuple(
                (section_id, "c" * 64)
                for section_id in STRICT_REQUIRED_QUALITY_SECTION_IDS
            ),
        )


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

from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal

import pytest

from src.features.report_recovery.constants import (
    MAX_TOTAL_AI_CALLS,
    PRIMARY_AI_CALLS,
)
from src.features.report_recovery.logic import (
    decide_post_validation,
    decide_preflight,
)
from src.features.report_recovery.models import (
    RecoveryAction,
    SupplementAuthorization,
)
from src.shared.generation_validation_receipt import (
    GenerationValidationReceipt,
    ValidationRound,
)
from src.shared.report_evidence.constants import (
    GenerationGateStatus,
    ReportExecutionOutcome,
)
from src.shared.report_evidence.models import GenerationGateDecision
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_quality.models import (
    GenerationAssessment,
    QualityAssessment,
    QualityGrade,
    QualityProblemCode,
    ReleaseDecision,
    SafetyAssessment,
)


_CONTRACT_VERSION = "quality-v-test"


def _sha256(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _section_sha256s(prefix: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        (section_id, _sha256(f"{prefix}:{section_id}"))
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    )


def _gate(status: GenerationGateStatus) -> GenerationGateDecision:
    required = REQUIRED_EVIDENCE_SECTION_IDS
    if status is GenerationGateStatus.READY_FOR_GENERATION:
        ready, insufficient, unknown = required, (), ()
        outcome = None
    elif status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE:
        ready, insufficient, unknown = required[:-1], required[-1:], ()
        outcome = ReportExecutionOutcome.INSUFFICIENT_EVIDENCE
    else:
        ready, insufficient, unknown = required[:-1], (), required[-1:]
        outcome = ReportExecutionOutcome.TRANSIENT_FAILURE
    return GenerationGateDecision(
        company_id="corp-1",
        status=status,
        outcome=outcome,
        required_section_ids=required,
        ready_section_ids=ready,
        insufficient_section_ids=insufficient,
        unknown_section_ids=unknown,
        reason_codes=(),
    )


def _assessment(
    *,
    problem_codes: tuple[QualityProblemCode, ...] = (),
    underfilled: tuple[str, ...] = (),
    semantic_underfilled: tuple[str, ...] = (),
    one_claim: tuple[str, ...] = (),
    notice_only: tuple[str, ...] = (),
    safety_blocked: bool = False,
) -> GenerationAssessment:
    shortfalls = ("구조화 품질 부족",) if problem_codes else ()
    grade = QualityGrade.PARTIAL if problem_codes else QualityGrade.COMPLETE
    quality = QualityAssessment(
        contract_version=_CONTRACT_VERSION,
        grade=grade,
        substantive_claims=35 if problem_codes else 45,
        verified_claims=35 if problem_codes else 45,
        verified_ratio=Decimal("1"),
        document_sources=3,
        notice_only_sections=notice_only,
        one_claim_sections=one_claim,
        section_claim_counts=tuple(
            (section_id, 5) for section_id in REQUIRED_EVIDENCE_SECTION_IDS
        ),
        shortfall_reasons=shortfalls,
        section_public_sentence_counts=tuple(
            (section_id, 5) for section_id in REQUIRED_EVIDENCE_SECTION_IDS
        ),
        underfilled_sections=underfilled,
        semantic_underfilled_sections=semantic_underfilled,
        problem_codes=problem_codes,
    )
    safety = SafetyAssessment(
        contract_version=_CONTRACT_VERSION,
        decision=(
            ReleaseDecision.BLOCKED
            if safety_blocked
            else ReleaseDecision.RELEASE_ALLOWED
        ),
        verified_fact_ids=("fact-1",),
        unverified_fact_ids=(),
        rejected_fact_ids=(),
        problems=("수치 근거 불일치",) if safety_blocked else (),
    )
    return GenerationAssessment(
        contract_version=_CONTRACT_VERSION,
        quality=quality,
        safety=safety,
        publication_grade=(
            QualityGrade.INCOMPLETE if safety_blocked else quality.grade
        ),
    )


def _primary(
    assessment: GenerationAssessment,
    *,
    company_id: str = "corp-1",
    candidate_sha256: str = "a" * 64,
) -> GenerationValidationReceipt:
    return GenerationValidationReceipt(
        company_id=company_id,
        candidate_sha256=candidate_sha256,
        assessment=assessment,
        round=ValidationRound.PRIMARY,
        writer_calls=9,
        reviewer_calls=1,
        section_sha256s=_section_sha256s("primary-section"),
        evidence_packet_sha256s=_section_sha256s("evidence-packet"),
    )


def _supplement(
    primary: GenerationValidationReceipt,
    authorization: SupplementAuthorization,
    assessment: GenerationAssessment,
    *,
    company_id: str | None = None,
    candidate_sha256: str = "b" * 64,
    base_receipt_sha256: str | None = None,
    section_ids: tuple[str, ...] | None = None,
    section_sha256s: tuple[tuple[str, str], ...] | None = None,
    evidence_packet_sha256s: tuple[tuple[str, str], ...] | None = None,
) -> GenerationValidationReceipt:
    completed = section_ids or authorization.section_ids
    if section_sha256s is None:
        result_sections = dict(primary.section_sha256s)
        for section_id in completed:
            result_sections[section_id] = _sha256(f"supplement:{section_id}")
        section_sha256s = tuple(
            (section_id, result_sections[section_id])
            for section_id in REQUIRED_EVIDENCE_SECTION_IDS
        )
    return GenerationValidationReceipt(
        company_id=company_id or primary.company_id,
        candidate_sha256=candidate_sha256,
        assessment=assessment,
        round=ValidationRound.SUPPLEMENT,
        writer_calls=len(completed),
        reviewer_calls=1,
        section_sha256s=section_sha256s,
        evidence_packet_sha256s=(
            evidence_packet_sha256s or primary.evidence_packet_sha256s
        ),
        base_receipt_sha256=(base_receipt_sha256 or primary.receipt_sha256),
        supplemented_section_ids=completed,
    )


def _recoverable(*section_ids: str) -> GenerationAssessment:
    return _assessment(
        problem_codes=(
            QualityProblemCode.LOW_PUBLIC_SENTENCE_COVERAGE,
            QualityProblemCode.TOO_FEW_SUBSTANTIVE_CLAIMS,
        ),
        underfilled=tuple(section_ids),
    )


@pytest.mark.parametrize(
    "status",
    (
        GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE,
        GenerationGateStatus.STOP_TRANSIENT_FAILURE,
    ),
)
def test_자료부족이나_조회장애는_AI전에_무차감중단한다(
    status: GenerationGateStatus,
) -> None:
    decision = decide_preflight(_gate(status))

    assert decision.action is RecoveryAction.STOP_NO_CHARGE
    assert decision.projected_total_ai_calls == 0
    assert not decision.publish_allowed
    assert not decision.charge_allowed


def test_아홉장이_ready일때만_기본10호출을_허용한다() -> None:
    decision = decide_preflight(_gate(GenerationGateStatus.READY_FOR_GENERATION))

    assert decision.action is RecoveryAction.RUN_PRIMARY
    assert decision.observed_total_ai_calls == 0
    assert decision.authorized_additional_ai_calls == PRIMARY_AI_CALLS == 10
    assert decision.projected_total_ai_calls == 10


def test_일부장만_ready인_축소게이트로_유료호출을_열수없다() -> None:
    shortened = GenerationGateDecision(
        company_id="corp-1",
        status=GenerationGateStatus.READY_FOR_GENERATION,
        outcome=None,
        required_section_ids=("identity",),
        ready_section_ids=("identity",),
        insufficient_section_ids=(),
        unknown_section_ids=(),
        reason_codes=(),
    )

    with pytest.raises(ValueError, match="필수 아홉 장"):
        decide_preflight(shortened)


@pytest.mark.parametrize(
    ("sections", "expected_calls"),
    [
        (("identity",), 12),
        (("identity", "culture"), 13),
    ],
)
def test_얇은장_두개이하는_정확한후보와장에_보충을_결속한다(
    sections: tuple[str, ...],
    expected_calls: int,
) -> None:
    primary = _primary(_recoverable(*sections))
    decision = decide_post_validation(primary)

    assert decision.action is RecoveryAction.RUN_SUPPLEMENTS
    assert decision.supplement_section_ids == sections
    assert decision.supplement_authorization is not None
    assert decision.supplement_authorization.company_id == primary.company_id
    assert (
        decision.supplement_authorization.base_candidate_sha256
        == primary.candidate_sha256
    )
    assert (
        decision.supplement_authorization.base_receipt_sha256
        == primary.receipt_sha256
    )
    assert decision.observed_total_ai_calls == 10
    assert decision.projected_total_ai_calls == expected_calls
    assert decision.projected_total_ai_calls <= MAX_TOTAL_AI_CALLS == 13


def test_의미범주가_얇은장도_문구해석없이_보충대상이된다() -> None:
    primary = _primary(
        _assessment(
            problem_codes=(QualityProblemCode.LOW_SEMANTIC_COVERAGE,),
            semantic_underfilled=("business_model",),
        )
    )

    decision = decide_post_validation(primary)

    assert decision.action is RecoveryAction.RUN_SUPPLEMENTS
    assert decision.supplement_section_ids == ("business_model",)


def test_얇은장이_세개면_비싼보충을_시작하지않는다() -> None:
    primary = _primary(_recoverable("identity", "culture", "portfolio"))

    decision = decide_post_validation(primary)

    assert decision.action is RecoveryAction.STOP_NO_CHARGE
    assert decision.projected_total_ai_calls == 10


def test_안전실패나_출처수부족은_즉시_무차감으로끝낸다() -> None:
    safety = decide_post_validation(_primary(_assessment(safety_blocked=True)))
    source_shortage = decide_post_validation(
        _primary(
            _assessment(
                problem_codes=(QualityProblemCode.TOO_FEW_DOCUMENT_SOURCES,),
                underfilled=("identity",),
            )
        )
    )

    assert safety.action is RecoveryAction.STOP_NO_CHARGE
    assert source_shortage.action is RecoveryAction.STOP_NO_CHARGE
    assert safety.projected_total_ai_calls == 10
    assert source_shortage.projected_total_ai_calls == 10


def test_완성평가영수증만_공개와_정상차감을_함께허용한다() -> None:
    primary = _primary(_assessment())

    decision = decide_post_validation(primary)

    assert decision.action is RecoveryAction.RELEASE_COMPLETE
    assert decision.observed_total_ai_calls == 10
    assert decision.authorized_additional_ai_calls == 0
    assert decision.publish_allowed
    assert decision.charge_allowed


def test_보충뒤_완성본은_실제12호출을_기록하고_공개한다() -> None:
    primary = _primary(_recoverable("identity"))
    first = decide_post_validation(primary)
    assert first.supplement_authorization is not None
    supplement = _supplement(
        primary,
        first.supplement_authorization,
        _assessment(),
    )

    decision = decide_post_validation(
        primary,
        supplement_authorization=first.supplement_authorization,
        supplement_receipt=supplement,
    )

    assert decision.action is RecoveryAction.RELEASE_COMPLETE
    assert decision.observed_total_ai_calls == 12
    assert decision.projected_total_ai_calls == 12
    assert decision.publish_allowed
    assert decision.charge_allowed


def test_보충뒤에도_실패하면_두번째승인없이_무차감끝낸다() -> None:
    primary = _primary(_recoverable("identity", "culture"))
    first = decide_post_validation(primary)
    assert first.supplement_authorization is not None
    supplement = _supplement(
        primary,
        first.supplement_authorization,
        _recoverable("identity"),
    )

    decision = decide_post_validation(
        primary,
        supplement_authorization=first.supplement_authorization,
        supplement_receipt=supplement,
    )

    assert decision.action is RecoveryAction.STOP_NO_CHARGE
    assert decision.observed_total_ai_calls == 13
    assert decision.supplement_authorization is None
    assert not decision.charge_allowed


def test_같은후보지문을_보충했다고_제출하면_공개하지않는다() -> None:
    primary = _primary(_recoverable("identity"))
    first = decide_post_validation(primary)
    assert first.supplement_authorization is not None
    unchanged = _supplement(
        primary,
        first.supplement_authorization,
        _assessment(),
        candidate_sha256=primary.candidate_sha256,
    )

    decision = decide_post_validation(
        primary,
        supplement_authorization=first.supplement_authorization,
        supplement_receipt=unchanged,
    )

    assert decision.action is RecoveryAction.STOP_NO_CHARGE
    assert decision.reason_code == "supplement_candidate_unchanged"


def test_다른장이나_다른회사의_보충영수증은_거절한다() -> None:
    primary = _primary(_recoverable("identity"))
    first = decide_post_validation(primary)
    assert first.supplement_authorization is not None
    forged_authorization = SupplementAuthorization(
        company_id=primary.company_id,
        base_candidate_sha256=primary.candidate_sha256,
        base_receipt_sha256=primary.receipt_sha256,
        section_ids=("culture",),
    )
    wrong_section = _supplement(
        primary,
        forged_authorization,
        _assessment(),
        section_ids=("culture",),
    )
    with pytest.raises(ValueError, match="승인이 기본 후보"):
        decide_post_validation(
            primary,
            supplement_authorization=forged_authorization,
            supplement_receipt=wrong_section,
        )

    wrong_company = _supplement(
        primary,
        first.supplement_authorization,
        _assessment(),
        company_id="corp-2",
    )
    with pytest.raises(ValueError, match="회사가 기본 결과"):
        decide_post_validation(
            primary,
            supplement_authorization=first.supplement_authorization,
            supplement_receipt=wrong_company,
        )


def test_보충중_승인밖장이나_근거꾸러미를_바꾸면_거절한다() -> None:
    primary = _primary(_recoverable("identity"))
    first = decide_post_validation(primary)
    assert first.supplement_authorization is not None

    changed_sections = dict(primary.section_sha256s)
    changed_sections["identity"] = _sha256("new-identity")
    changed_sections["culture"] = _sha256("forged-culture")
    changed_outside = _supplement(
        primary,
        first.supplement_authorization,
        _assessment(),
        section_sha256s=tuple(
            (section_id, changed_sections[section_id])
            for section_id in REQUIRED_EVIDENCE_SECTION_IDS
        ),
    )
    with pytest.raises(ValueError, match="승인하지 않은 장"):
        decide_post_validation(
            primary,
            supplement_authorization=first.supplement_authorization,
            supplement_receipt=changed_outside,
        )

    changed_packets = _supplement(
        primary,
        first.supplement_authorization,
        _assessment(),
        evidence_packet_sha256s=_section_sha256s("forged-packet"),
    )
    with pytest.raises(ValueError, match="근거 꾸러미"):
        decide_post_validation(
            primary,
            supplement_authorization=first.supplement_authorization,
            supplement_receipt=changed_packets,
        )


def test_승인된장_지문이_그대로면_보충완료로_인정하지않는다() -> None:
    primary = _primary(_recoverable("identity"))
    first = decide_post_validation(primary)
    assert first.supplement_authorization is not None
    unchanged_section = _supplement(
        primary,
        first.supplement_authorization,
        _assessment(),
        section_sha256s=primary.section_sha256s,
    )

    with pytest.raises(ValueError, match="내용 지문이 바뀌지"):
        decide_post_validation(
            primary,
            supplement_authorization=first.supplement_authorization,
            supplement_receipt=unchanged_section,
        )


def test_다른기본영수증이나_실제호출수가_없는보충은_거절한다() -> None:
    primary = _primary(_recoverable("identity"))
    first = decide_post_validation(primary)
    assert first.supplement_authorization is not None
    wrong_base = _supplement(
        primary,
        first.supplement_authorization,
        _assessment(),
        base_receipt_sha256="c" * 64,
    )
    with pytest.raises(ValueError, match="정확한 기본 평가"):
        decide_post_validation(
            primary,
            supplement_authorization=first.supplement_authorization,
            supplement_receipt=wrong_base,
        )

    unmetered = GenerationValidationReceipt(
        company_id=primary.company_id,
        candidate_sha256="b" * 64,
        assessment=_assessment(),
        round=ValidationRound.SUPPLEMENT,
        writer_calls=0,
        reviewer_calls=1,
        section_sha256s=_section_sha256s("supplement-section"),
        evidence_packet_sha256s=primary.evidence_packet_sha256s,
        base_receipt_sha256=primary.receipt_sha256,
        supplemented_section_ids=("identity",),
    )
    with pytest.raises(ValueError, match="작성 호출 수"):
        decide_post_validation(
            primary,
            supplement_authorization=first.supplement_authorization,
            supplement_receipt=unmetered,
        )


def test_사람문구나_boolean으로_출고판정을_속일_API가_없다() -> None:
    with pytest.raises(TypeError):
        decide_post_validation(  # type: ignore[call-arg]
            underfilled_section_ids=(),
            other_quality_shortfalls=False,
            safety_problems=False,
            supplemented_section_ids=("identity",),
        )


def test_평가나_후보가_바뀌면_영수증지문도_바뀐다() -> None:
    primary = _primary(_recoverable("identity"))
    changed_assessment = replace(
        primary.assessment,
        quality=replace(
            primary.assessment.quality,
            substantive_claims=34,
        ),
    )
    changed = _primary(changed_assessment)
    other_candidate = _primary(
        primary.assessment,
        candidate_sha256="f" * 64,
    )

    assert changed.assessment_sha256 != primary.assessment_sha256
    assert changed.receipt_sha256 != primary.receipt_sha256
    assert other_candidate.receipt_sha256 != primary.receipt_sha256


def test_불일치한평가등급이나_문제코드는_영수증이되지못한다() -> None:
    inconsistent_publication = replace(
        _assessment(),
        publication_grade=QualityGrade.PARTIAL,
    )
    with pytest.raises(ValueError, match="공개 등급"):
        _primary(inconsistent_publication)

    untyped_code = replace(
        _recoverable("identity"),
        quality=replace(
            _recoverable("identity").quality,
            problem_codes=("low_public_sentence_coverage",),  # type: ignore[arg-type]
        ),
    )
    with pytest.raises(TypeError, match="닫힌 QualityProblemCode"):
        _primary(untyped_code)

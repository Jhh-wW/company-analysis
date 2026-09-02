"""FULL producer evidence와 공용 검증 영수증 wire의 적대 회귀."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from src.shared.generation_validation_receipt import (
    GenerationValidationReceipt,
    ValidationRound,
    receipt_from_dict,
    receipt_from_json,
    receipt_to_dict,
    receipt_to_json,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_generation.models import (
    GenerationCallLedger,
    GenerationCallRecord,
    GenerationProducerEvidence,
    GenerationRunMetrics,
    assert_canonical_producer_evidence,
    producer_evidence_from_dict,
    producer_evidence_to_dict,
    generation_metrics_from_dict,
    generation_metrics_to_dict,
)
from src.shared.report_quality.generation import (
    GenerationQualityObservation,
    assert_observation_matches_assessment,
    generation_quality_observation_from_dict,
    generation_quality_observation_to_dict,
)
from src.shared.report_quality.constants import STRICT_QUALITY_CONTRACT_VERSION
from src.shared.report_quality.models import (
    GenerationAssessment,
    QualityAssessment,
    QualityGrade,
    QualityProblemCode,
    ReleaseDecision,
    SafetyAssessment,
)


def _digests(seed: int) -> tuple[tuple[str, str], ...]:
    return tuple(
        (section_id, format((seed + index) % 16, "x") * 64)
        for index, section_id in enumerate(REQUIRED_EVIDENCE_SECTION_IDS)
    )


def _complete_assessment() -> GenerationAssessment:
    counts = tuple((section_id, 5) for section_id in REQUIRED_EVIDENCE_SECTION_IDS)
    fact_ids = tuple(f"fact-{index}" for index in range(45))
    return GenerationAssessment(
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        quality=QualityAssessment(
            contract_version=STRICT_QUALITY_CONTRACT_VERSION,
            grade=QualityGrade.COMPLETE,
            substantive_claims=45,
            verified_claims=45,
            verified_ratio=Decimal("1"),
            document_sources=9,
            notice_only_sections=(),
            one_claim_sections=(),
            section_claim_counts=counts,
            shortfall_reasons=(),
            section_public_sentence_counts=counts,
            underfilled_sections=(),
            semantic_underfilled_sections=(),
            problem_codes=(),
        ),
        safety=SafetyAssessment(
            contract_version=STRICT_QUALITY_CONTRACT_VERSION,
            decision=ReleaseDecision.RELEASE_ALLOWED,
            verified_fact_ids=fact_ids,
            unverified_fact_ids=(),
            rejected_fact_ids=(),
            problems=(),
        ),
        publication_grade=QualityGrade.COMPLETE,
    )


def _partial_assessment() -> GenerationAssessment:
    counts = tuple(
        (section_id, 1 if section_id == "identity" else 5)
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    )
    return GenerationAssessment(
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        quality=QualityAssessment(
            contract_version=STRICT_QUALITY_CONTRACT_VERSION,
            grade=QualityGrade.PARTIAL,
            substantive_claims=41,
            verified_claims=41,
            verified_ratio=Decimal("1"),
            document_sources=9,
            notice_only_sections=(),
            one_claim_sections=("identity",),
            section_claim_counts=counts,
            shortfall_reasons=("identity 장의 의미와 공개 문장이 부족합니다",),
            section_public_sentence_counts=counts,
            underfilled_sections=("identity",),
            semantic_underfilled_sections=("identity",),
            problem_codes=(
                QualityProblemCode.ONE_CLAIM_SECTIONS,
                QualityProblemCode.LOW_SEMANTIC_COVERAGE,
                QualityProblemCode.LOW_PUBLIC_SENTENCE_COVERAGE,
            ),
        ),
        safety=SafetyAssessment(
            contract_version=STRICT_QUALITY_CONTRACT_VERSION,
            decision=ReleaseDecision.RELEASE_ALLOWED,
            verified_fact_ids=tuple(f"base-fact-{index}" for index in range(41)),
            unverified_fact_ids=(),
            rejected_fact_ids=(),
            problems=(),
        ),
        publication_grade=QualityGrade.PARTIAL,
    )


def _record(
    sequence: int,
    *,
    role: str,
    role_index: int,
    section_id: str,
    validation_round: ValidationRound = ValidationRound.PRIMARY,
    outcome: str = "returned",
) -> GenerationCallRecord:
    return GenerationCallRecord(
        sequence=sequence,
        role=role,
        role_index=role_index,
        section_id=section_id,
        prompt_sha256=f"{sequence:064x}",
        response_sha256=(f"{sequence + 100:064x}" if outcome == "returned" else ""),
        outcome=outcome,
        validation_round=validation_round,
        error_kind=("RuntimeError" if outcome == "failed" else ""),
    )


def _primary_ledger() -> GenerationCallLedger:
    records = tuple(
        _record(
            index,
            role="writer",
            role_index=index,
            section_id=section_id,
        )
        for index, section_id in enumerate(REQUIRED_EVIDENCE_SECTION_IDS, start=1)
    )
    return GenerationCallLedger(
        (*records, _record(10, role="reviewer", role_index=1, section_id="bundled"))
    )


def _primary_receipt(
    *,
    assessment: GenerationAssessment | None = None,
    candidate_sha256: str = "c" * 64,
    section_sha256s: tuple[tuple[str, str], ...] | None = None,
) -> GenerationValidationReceipt:
    return GenerationValidationReceipt(
        company_id="00123456",
        candidate_sha256=candidate_sha256,
        assessment=assessment or _complete_assessment(),
        round=ValidationRound.PRIMARY,
        writer_calls=9,
        reviewer_calls=1,
        section_sha256s=section_sha256s or _digests(1),
        evidence_packet_sha256s=_digests(8),
    )


def _evidence(
    *,
    receipts: tuple[GenerationValidationReceipt, ...] | None = None,
    ledger: GenerationCallLedger | None = None,
) -> GenerationProducerEvidence:
    chain = receipts or (_primary_receipt(),)
    final = chain[-1]
    return GenerationProducerEvidence(
        company_id=final.company_id,
        evidence_generation_sha256="a" * 64,
        build_identity_sha256="b" * 64,
        candidate_sha256=final.candidate_sha256,
        assessment=final.assessment,
        public_manifest_sha256="d" * 64,
        public_content_sha256="e" * 64,
        # ★ 공개 봉인 projection 지문(v3). 지문 A(public_content)와
        #   «다른 값»으로 둔다 — 같은 값으로 두면 둘을 뒤바꿔 배선해도 시험이
        #   못 잡는다.
        public_projection_sha256="f" * 64,
        section_sha256s=final.section_sha256s,
        evidence_packet_sha256s=final.evidence_packet_sha256s,
        validation_receipts=chain,
        call_ledger=ledger or _primary_ledger(),
    )


def test_primary_9_writer_1_bundled_reviewer가_exact_wire로_왕복한다():
    evidence = _evidence()
    payload = producer_evidence_to_dict(evidence)

    assert producer_evidence_from_dict(payload) == evidence
    assert len(assert_canonical_producer_evidence(evidence)) == 64
    assert evidence.writer_calls == 9
    assert evidence.reviewer_calls == 1


def test_빈_호출장부는_AI를_부르기_전_상태로_허용한다():
    ledger = GenerationCallLedger(())

    assert ledger.records == ()
    assert ledger.writer_calls == 0
    assert ledger.reviewer_calls == 0


def test_COMPLETE_이름만_붙인_모순된_평가는_성공_영수증을_못_만든다():
    forged = replace(
        _complete_assessment(),
        quality=replace(
            _complete_assessment().quality,
            substantive_claims=1,
            verified_claims=1,
            verified_ratio=Decimal("1"),
        ),
        safety=replace(
            _complete_assessment().safety,
            verified_fact_ids=("fact-only",),
        ),
    )
    with pytest.raises(ValueError, match="하한|최소|완성"):
        _primary_receipt(assessment=forged)


def test_failed_call_10건은_성공_producer_evidence가_될_수_없다():
    failed_records = tuple(
        replace(
            record,
            response_sha256="",
            outcome="failed",
            error_kind="RuntimeError",
        )
        for record in _primary_ledger().records
    )
    with pytest.raises(ValueError, match="실패한 AI 호출"):
        _evidence(ledger=GenerationCallLedger(failed_records))


def test_영수증_밖_추가호출과_장중복은_숨길_수_없다():
    primary = _primary_ledger()
    extra = _record(
        11,
        role="writer",
        role_index=10,
        section_id="identity",
        validation_round=ValidationRound.SUPPLEMENT,
    )
    with pytest.raises(ValueError, match="SUPPLEMENT|호출"):
        _evidence(ledger=GenerationCallLedger((*primary.records, extra)))


def test_PRIMARY_SUPPLEMENT_chain은_대상장만_바꾸고_전체_ledger를_봉인한다():
    base_sections = _digests(1)
    primary = _primary_receipt(
        assessment=_partial_assessment(),
        candidate_sha256="c" * 64,
        section_sha256s=base_sections,
    )
    final_sections = tuple(
        (section_id, "f" * 64 if section_id == "identity" else digest)
        for section_id, digest in base_sections
    )
    supplement = GenerationValidationReceipt(
        company_id="00123456",
        candidate_sha256="d" * 64,
        assessment=_complete_assessment(),
        round=ValidationRound.SUPPLEMENT,
        writer_calls=1,
        reviewer_calls=1,
        section_sha256s=final_sections,
        evidence_packet_sha256s=primary.evidence_packet_sha256s,
        base_receipt_sha256=primary.receipt_sha256,
        supplemented_section_ids=("identity",),
    )
    ledger = GenerationCallLedger(
        (
            *_primary_ledger().records,
            _record(
                11,
                role="writer",
                role_index=1,
                section_id="identity",
                validation_round=ValidationRound.SUPPLEMENT,
            ),
            _record(
                12,
                role="reviewer",
                role_index=1,
                section_id="bundled",
                validation_round=ValidationRound.SUPPLEMENT,
            ),
        )
    )

    evidence = _evidence(receipts=(primary, supplement), ledger=ledger)

    assert evidence.writer_calls == 10
    assert evidence.reviewer_calls == 2
    assert tuple(record.sequence for record in ledger.records) == tuple(range(1, 13))
    supplement_records = tuple(
        record
        for record in ledger.records
        if record.validation_round is ValidationRound.SUPPLEMENT
    )
    assert tuple(record.role_index for record in supplement_records) == (1, 1)
    assert producer_evidence_from_dict(producer_evidence_to_dict(evidence)) == evidence


def test_PRIMARY_9대1과_SUPPLEMENT_2대1은_각자_1부터_세고_전체는_1부터13이다() -> None:
    base_sections = _digests(1)
    primary = _primary_receipt(
        assessment=_partial_assessment(),
        candidate_sha256="c" * 64,
        section_sha256s=base_sections,
    )
    targets = ("identity", "business_model")
    final_sections = tuple(
        (section_id, "f" * 64 if section_id in targets else digest)
        for section_id, digest in base_sections
    )
    supplement = GenerationValidationReceipt(
        company_id=primary.company_id,
        candidate_sha256="d" * 64,
        assessment=_complete_assessment(),
        round=ValidationRound.SUPPLEMENT,
        writer_calls=2,
        reviewer_calls=1,
        section_sha256s=final_sections,
        evidence_packet_sha256s=primary.evidence_packet_sha256s,
        base_receipt_sha256=primary.receipt_sha256,
        supplemented_section_ids=targets,
    )
    ledger = GenerationCallLedger(
        (
            *_primary_ledger().records,
            _record(
                11,
                role="writer",
                role_index=1,
                section_id="identity",
                validation_round=ValidationRound.SUPPLEMENT,
            ),
            _record(
                12,
                role="writer",
                role_index=2,
                section_id="business_model",
                validation_round=ValidationRound.SUPPLEMENT,
            ),
            _record(
                13,
                role="reviewer",
                role_index=1,
                section_id="bundled",
                validation_round=ValidationRound.SUPPLEMENT,
            ),
        )
    )

    evidence = _evidence(receipts=(primary, supplement), ledger=ledger)

    assert tuple(record.sequence for record in ledger.records) == tuple(range(1, 14))
    primary_records = tuple(
        record
        for record in ledger.records
        if record.validation_round is ValidationRound.PRIMARY
    )
    supplement_records = tuple(
        record
        for record in ledger.records
        if record.validation_round is ValidationRound.SUPPLEMENT
    )
    assert tuple(
        record.role_index for record in primary_records if record.role == "writer"
    ) == tuple(range(1, 10))
    assert tuple(
        record.role_index for record in primary_records if record.role == "reviewer"
    ) == (1,)
    assert tuple(
        record.role_index for record in supplement_records if record.role == "writer"
    ) == (1, 2)
    assert tuple(
        record.role_index for record in supplement_records if record.role == "reviewer"
    ) == (1,)
    assert evidence.writer_calls == 11
    assert evidence.reviewer_calls == 2


def test_PRIMARY_reviewer보다_SUPPLEMENT_writer가_먼저오면_거절한다() -> None:
    primary_writers = _primary_ledger().records[:-1]
    records = (
        *primary_writers,
        _record(
            10,
            role="writer",
            role_index=1,
            section_id="identity",
            validation_round=ValidationRound.SUPPLEMENT,
        ),
        _record(
            11,
            role="reviewer",
            role_index=1,
            section_id="bundled",
            validation_round=ValidationRound.PRIMARY,
        ),
    )

    with pytest.raises(ValueError, match="SUPPLEMENT.*PRIMARY"):
        GenerationCallLedger(records)


def test_PRIMARY없이_SUPPLEMENT호출만_있는_장부는_거절한다() -> None:
    records = (
        _record(
            1,
            role="writer",
            role_index=1,
            section_id="identity",
            validation_round=ValidationRound.SUPPLEMENT,
        ),
        _record(
            2,
            role="reviewer",
            role_index=1,
            section_id="bundled",
            validation_round=ValidationRound.SUPPLEMENT,
        ),
    )

    with pytest.raises(ValueError, match="PRIMARY.*시작"):
        GenerationCallLedger(records)


def test_PRIMARY_SUPPLEMENT_PRIMARY_회차재진입은_거절한다() -> None:
    records = (
        _record(1, role="writer", role_index=1, section_id="identity"),
        _record(
            2,
            role="writer",
            role_index=1,
            section_id="identity",
            validation_round=ValidationRound.SUPPLEMENT,
        ),
        _record(3, role="writer", role_index=2, section_id="business_model"),
    )

    with pytest.raises(ValueError, match="SUPPLEMENT.*PRIMARY"):
        GenerationCallLedger(records)


def test_SUPPLEMENT가_비대상장을_바꾸면_성공_evidence를_못_만든다():
    base_sections = _digests(1)
    primary = _primary_receipt(
        assessment=_partial_assessment(),
        section_sha256s=base_sections,
    )
    forged_sections = tuple(
        (
            section_id,
            "f" * 64 if section_id in {"identity", "business_model"} else digest,
        )
        for section_id, digest in base_sections
    )
    supplement = GenerationValidationReceipt(
        company_id=primary.company_id,
        candidate_sha256="d" * 64,
        assessment=_complete_assessment(),
        round=ValidationRound.SUPPLEMENT,
        writer_calls=1,
        reviewer_calls=1,
        section_sha256s=forged_sections,
        evidence_packet_sha256s=primary.evidence_packet_sha256s,
        base_receipt_sha256=primary.receipt_sha256,
        supplemented_section_ids=("identity",),
    )
    ledger = GenerationCallLedger(
        (
            *_primary_ledger().records,
            _record(
                11,
                role="writer",
                role_index=1,
                section_id="identity",
                validation_round=ValidationRound.SUPPLEMENT,
            ),
            _record(
                12,
                role="reviewer",
                role_index=1,
                section_id="bundled",
                validation_round=ValidationRound.SUPPLEMENT,
            ),
        )
    )
    with pytest.raises(ValueError, match="대상 밖 장"):
        _evidence(receipts=(primary, supplement), ledger=ledger)


@pytest.mark.parametrize(
    ("role", "old_global_index", "section_id"),
    (("writer", 10, "identity"), ("reviewer", 2, "bundled")),
)
def test_SUPPLEMENT의_옛전역_role_index_10과2는_새회차영수증이_아니다(
    role: str,
    old_global_index: int,
    section_id: str,
) -> None:
    records = (
        *_primary_ledger().records,
        _record(
            11,
            role=role,
            role_index=old_global_index,
            section_id=section_id,
            validation_round=ValidationRound.SUPPLEMENT,
        ),
    )

    with pytest.raises(ValueError, match=f"SUPPLEMENT {role}.*1부터"):
        GenerationCallLedger(records)


@pytest.mark.parametrize("role", ("writer", "reviewer"))
@pytest.mark.parametrize("bad_indexes", ((2,), (1, 1), (1, 3)))
def test_SUPPLEMENT_role의_round별_gap과duplicate를_거절한다(
    role: str,
    bad_indexes: tuple[int, ...],
) -> None:
    records = list(_primary_ledger().records)
    for offset, role_index in enumerate(bad_indexes, start=1):
        records.append(
            _record(
                10 + offset,
                role=role,
                role_index=role_index,
                section_id=(
                    "bundled"
                    if role == "reviewer"
                    else ("identity" if offset == 1 else "business_model")
                ),
                validation_round=ValidationRound.SUPPLEMENT,
            )
        )

    with pytest.raises(ValueError, match=f"SUPPLEMENT {role}.*1부터"):
        GenerationCallLedger(tuple(records))


def test_SUPPLEMENT호출을_PRIMARY로_위장하면_receipt_chain과_결속되지않는다() -> None:
    base_sections = _digests(1)
    primary = _primary_receipt(
        assessment=_partial_assessment(),
        candidate_sha256="c" * 64,
        section_sha256s=base_sections,
    )
    final_sections = tuple(
        (section_id, "f" * 64 if section_id == "identity" else digest)
        for section_id, digest in base_sections
    )
    supplement = GenerationValidationReceipt(
        company_id=primary.company_id,
        candidate_sha256="d" * 64,
        assessment=_complete_assessment(),
        round=ValidationRound.SUPPLEMENT,
        writer_calls=1,
        reviewer_calls=1,
        section_sha256s=final_sections,
        evidence_packet_sha256s=primary.evidence_packet_sha256s,
        base_receipt_sha256=primary.receipt_sha256,
        supplemented_section_ids=("identity",),
    )
    disguised = GenerationCallLedger(
        (
            *_primary_ledger().records,
            _record(
                11,
                role="writer",
                role_index=10,
                section_id="identity",
                validation_round=ValidationRound.PRIMARY,
            ),
            _record(
                12,
                role="reviewer",
                role_index=2,
                section_id="bundled",
                validation_round=ValidationRound.PRIMARY,
            ),
        )
    )

    with pytest.raises(ValueError, match="검증 영수증|SUPPLEMENT|호출 수"):
        _evidence(receipts=(primary, supplement), ledger=disguised)


def test_receipt_wire는_unknown_missing_enum_digest_변조를_거절한다():
    receipt = _primary_receipt()
    payload = receipt_to_dict(receipt)
    assert receipt_from_dict(deepcopy(payload)) == receipt

    attacks = []
    unknown = deepcopy(payload)
    unknown["unknown"] = True
    attacks.append(unknown)
    missing = deepcopy(payload)
    missing.pop("assessment")
    attacks.append(missing)
    enum_forged = deepcopy(payload)
    enum_forged["round"] = "PRIMARY "
    attacks.append(enum_forged)
    grade_forged = deepcopy(payload)
    grade_forged["assessment"]["quality"]["grade"] = "완성 "
    attacks.append(grade_forged)
    digest_forged = deepcopy(payload)
    digest_forged["receipt_sha256"] = "0" * 64
    attacks.append(digest_forged)

    for attack in attacks:
        with pytest.raises((TypeError, ValueError)):
            receipt_from_dict(attack)


def test_receipt_json은_canonical_exact_bytes만_받는다():
    receipt = _primary_receipt()
    canonical = receipt_to_json(receipt)
    assert receipt_from_json(canonical.encode("utf-8")) == receipt

    noncanonical = json.dumps(receipt_to_dict(receipt), ensure_ascii=False)
    assert noncanonical != canonical
    with pytest.raises(ValueError, match="canonical"):
        receipt_from_json(noncanonical)


def test_receipt와_producer_subclass는_release_wire에서_거절한다():
    class ForgedReceipt(GenerationValidationReceipt):
        pass

    receipt = _primary_receipt()
    forged_receipt = ForgedReceipt(
        company_id=receipt.company_id,
        candidate_sha256=receipt.candidate_sha256,
        assessment=receipt.assessment,
        round=receipt.round,
        writer_calls=receipt.writer_calls,
        reviewer_calls=receipt.reviewer_calls,
        section_sha256s=receipt.section_sha256s,
        evidence_packet_sha256s=receipt.evidence_packet_sha256s,
    )
    with pytest.raises(TypeError, match="정확한"):
        receipt_to_dict(forged_receipt)

    evidence = _evidence()

    class ForgedEvidence(GenerationProducerEvidence):
        pass

    forged_evidence = ForgedEvidence(
        company_id=evidence.company_id,
        evidence_generation_sha256=evidence.evidence_generation_sha256,
        build_identity_sha256=evidence.build_identity_sha256,
        candidate_sha256=evidence.candidate_sha256,
        assessment=evidence.assessment,
        public_manifest_sha256=evidence.public_manifest_sha256,
        public_content_sha256=evidence.public_content_sha256,
        public_projection_sha256=evidence.public_projection_sha256,
        section_sha256s=evidence.section_sha256s,
        evidence_packet_sha256s=evidence.evidence_packet_sha256s,
        validation_receipts=evidence.validation_receipts,
        call_ledger=evidence.call_ledger,
    )
    with pytest.raises(TypeError, match="정확한"):
        assert_canonical_producer_evidence(forged_evidence)


def test_producer_wire_unknown_contract와_manifest_digest_제거는_fail_closed다():
    payload = producer_evidence_to_dict(_evidence())
    unknown = deepcopy(payload)
    unknown["unknown"] = "field"
    with pytest.raises(ValueError, match="key"):
        producer_evidence_from_dict(unknown)

    removed = deepcopy(payload)
    removed.pop("public_manifest_sha256")
    with pytest.raises(ValueError, match="key"):
        producer_evidence_from_dict(removed)

    downgraded = deepcopy(payload)
    downgraded["version"] = "generation-producer-evidence-v1"
    with pytest.raises(ValueError, match="지원하지 않는"):
        producer_evidence_from_dict(downgraded)


def test_실제_생성지표와_품질관측은_exact_wire로_왕복하고_평가와_맞는다():
    metrics = GenerationRunMetrics(9, 8, 45, 45)
    assert generation_metrics_from_dict(generation_metrics_to_dict(metrics)) == metrics
    assessment = _complete_assessment()
    assessment = replace(
        assessment,
        quality=replace(
            assessment.quality,
            semantic_underfilled_sections=("identity",),
            problem_codes=(QualityProblemCode.LOW_SEMANTIC_COVERAGE,),
            shortfall_reasons=("identity 장의 실제 설명이 부족합니다",),
            grade=QualityGrade.INCOMPLETE,
        ),
        publication_grade=QualityGrade.INCOMPLETE,
    )
    observation = GenerationQualityObservation(
        mode="generation-shadow",
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
        semantic_underfilled_sections=("identity",),
        notice_only_sections=assessment.quality.notice_only_sections,
        quality_problem_codes=(QualityProblemCode.LOW_SEMANTIC_COVERAGE.value,),
    )
    payload = generation_quality_observation_to_dict(observation)
    restored = generation_quality_observation_from_dict(payload)
    assert restored == observation
    assert_observation_matches_assessment(restored, assessment)

    forged = deepcopy(payload)
    forged["verified_claims"] = 44
    with pytest.raises(ValueError, match="평가 원본"):
        assert_observation_matches_assessment(
            generation_quality_observation_from_dict(forged),
            assessment,
        )
    unknown = deepcopy(payload)
    unknown["unknown"] = True
    with pytest.raises(ValueError, match="key"):
        generation_quality_observation_from_dict(unknown)

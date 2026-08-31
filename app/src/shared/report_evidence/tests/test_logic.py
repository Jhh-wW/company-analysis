from __future__ import annotations

import hashlib
import json

import pytest

from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    GenerationGateStatus,
    ReportExecutionOutcome,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.logic import (
    assess_generation_gate,
    build_section_bundle,
)
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    CollectedEvidenceDocument,
    CollectionAttempt,
    DocumentTextRange,
    EvidenceFragment,
    InjectedSlotFacts,
)


def _document(*, company_id: str = "corp-1", document_id: str = "doc-1") -> CollectedEvidenceDocument:
    return CollectedEvidenceDocument(
        company_id=company_id,
        document_id=document_id,
        canonical_url="https://example.com/official",
        source_tier=SourceTier.TIER_1_OFFICIAL,
        source_kind="official_homepage",
        publisher="예시회사",
        title="공식 회사소개",
        published_on="2026-08-01",
        collected_at="2026-08-31T00:00:00+00:00",
        content_sha256="a" * 64,
        identity_binding="dart-homepage-link:001",
        usable_ranges=(DocumentTextRange(0, 100),),
        collector_version="collector-v1",
        parser_version="parser-v1",
        requirement=SourceRequirement.REQUIRED,
    )


def _fragment(*, slot_id: str = "business_model", document_id: str = "doc-1") -> EvidenceFragment:
    text = "회사는 공식 제품을 고객에게 직접 판매해 수익을 얻습니다."
    return EvidenceFragment(
        fragment_id=f"fragment-{slot_id}",
        document_id=document_id,
        location="본문 1문단",
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
        section_id="business_model",
        slot_id=slot_id,
        score_millis=900,
        reason_codes=("official_direct_statement",),
    )


def _attempt(
    *,
    slot_id: str,
    state: CollectionState,
    requirement: SourceRequirement = SourceRequirement.REQUIRED,
) -> CollectionAttempt:
    return CollectionAttempt(
        attempt_id=f"attempt-{slot_id}-{state.value}-{requirement.value}",
        source_kind="official_homepage",
        requirement=requirement,
        state=state,
        slot_ids=(slot_id,),
        reason_code=f"homepage_{state.value.lower()}",
    )


def _candidate(
    *,
    fragments: tuple[EvidenceFragment, ...] = (),
    attempts: tuple[CollectionAttempt, ...] = (),
    readiness: EvidenceReadiness = EvidenceReadiness.UNKNOWN,
) -> ChapterEvidenceCandidates:
    return ChapterEvidenceCandidates(
        company_id="corp-1",
        section_id="business_model",
        documents=(_document(),),
        fragments=fragments,
        attempts=attempts,
        candidate_readiness=readiness,
        reason_codes=(),
        estimated_tokens=500,
        max_chars=5000,
        max_estimated_tokens=2000,
    )


def test_근거가_찬_필수칸은_선택형_출처_실패가_있어도_ready다() -> None:
    candidate = _candidate(
        fragments=(_fragment(),),
        attempts=(
            _attempt(
                slot_id="background_detail",
                state=CollectionState.FAILED,
                requirement=SourceRequirement.OPTIONAL,
            ),
        ),
    )

    bundle = build_section_bundle(
        candidate, required_slot_ids=("business_model",)
    )

    assert bundle.readiness is EvidenceReadiness.READY
    assert bundle.missing_slot_ids == ()


def test_필수칸이_다_차도_정책밖_근거조각은_작성묶음에_들어갈_수_없다() -> None:
    candidate = _candidate(
        fragments=(
            _fragment(),
            _fragment(slot_id="other_section:foreign_slot"),
        ),
        readiness=EvidenceReadiness.READY,
    )

    with pytest.raises(ValueError, match="필수 정책에 없는 의미 칸의 근거 조각"):
        build_section_bundle(candidate, required_slot_ids=("business_model",))


def test_정상적으로_확인한_뒤_빈_필수칸만_insufficient다() -> None:
    candidate = _candidate(
        attempts=(
            _attempt(slot_id="customer_market", state=CollectionState.MISSING),
        ),
    )

    bundle = build_section_bundle(
        candidate, required_slot_ids=("customer_market",)
    )

    assert bundle.readiness is EvidenceReadiness.INSUFFICIENT
    assert bundle.reason_codes == (
        "evidence_absent_after_check:customer_market",
        "producer_readiness_disagreed:unknown_to_insufficient",
    )


@pytest.mark.parametrize("state", [CollectionState.FAILED, CollectionState.TRUNCATED])
def test_필수_조회_실패와_절단은_자료부족이_아니라_unknown이다(
    state: CollectionState,
) -> None:
    candidate = _candidate(
        attempts=(_attempt(slot_id="customer_market", state=state),),
    )

    bundle = build_section_bundle(
        candidate, required_slot_ids=("customer_market",)
    )

    assert bundle.readiness is EvidenceReadiness.UNKNOWN
    assert f"required_path_{state.value.lower()}:customer_market" in bundle.reason_codes


def test_아예_확인하지_않은_필수칸은_unknown이다() -> None:
    bundle = build_section_bundle(
        _candidate(), required_slot_ids=("customer_market",)
    )

    assert bundle.readiness is EvidenceReadiness.UNKNOWN
    assert "required_path_unobserved:customer_market" in bundle.reason_codes


def test_codex의_검증된_사실은_정확한_의미칸만_채운다() -> None:
    bundle = build_section_bundle(
        _candidate(),
        required_slot_ids=("historical_performance",),
        injected_slot_facts=(
            InjectedSlotFacts(
                slot_id="historical_performance", fact_ids=("fact-2025-revenue",)
            ),
        ),
    )

    assert bundle.readiness is EvidenceReadiness.READY
    assert bundle.injected_slot_facts[0].fact_ids == ("fact-2025-revenue",)


def test_필수_아홉장_중_bundle_누락은_ai전_일시실패다() -> None:
    ready = build_section_bundle(
        _candidate(fragments=(_fragment(),)),
        required_slot_ids=("business_model",),
    )

    decision = assess_generation_gate(
        company_id="corp-1",
        bundles=(ready,),
        required_section_ids=("business_model", "culture"),
    )

    assert decision.status is GenerationGateStatus.STOP_TRANSIENT_FAILURE
    assert decision.outcome is ReportExecutionOutcome.TRANSIENT_FAILURE
    assert decision.can_call_ai is False
    assert decision.unknown_section_ids == ("culture",)


def test_unknown이_없고_한장이라도_insufficient면_ai를_부르지_않는다() -> None:
    insufficient = build_section_bundle(
        _candidate(
            attempts=(
                _attempt(slot_id="business_model", state=CollectionState.MISSING),
            )
        ),
        required_slot_ids=("business_model",),
    )

    decision = assess_generation_gate(
        company_id="corp-1",
        bundles=(insufficient,),
        required_section_ids=("business_model",),
    )

    assert decision.status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE
    assert decision.outcome is ReportExecutionOutcome.INSUFFICIENT_EVIDENCE
    assert decision.can_call_ai is False


def test_모든_필수장이_ready일_때만_ai호출을_허용하고_complete라_부르지_않는다() -> None:
    ready = build_section_bundle(
        _candidate(fragments=(_fragment(),)),
        required_slot_ids=("business_model",),
    )

    decision = assess_generation_gate(
        company_id="corp-1",
        bundles=(ready,),
        required_section_ids=("business_model",),
    )

    assert decision.status is GenerationGateStatus.READY_FOR_GENERATION
    assert decision.outcome is None
    assert decision.can_call_ai is True


def test_긴_사유코드에_장_접두를_붙여도_게이트가_예외로_죽지_않는다() -> None:
    long_reason_a = "a" * 120
    long_reason_b = "a" * 119 + "b"
    candidate = ChapterEvidenceCandidates(
        company_id="corp-1",
        section_id="business_model",
        documents=(_document(),),
        fragments=(_fragment(),),
        attempts=(),
        candidate_readiness=EvidenceReadiness.READY,
        reason_codes=(long_reason_a, long_reason_b),
        estimated_tokens=500,
        max_chars=5000,
        max_estimated_tokens=2000,
    )
    bundle = build_section_bundle(
        candidate,
        required_slot_ids=("business_model",),
    )

    decision = assess_generation_gate(
        company_id="corp-1",
        bundles=(bundle,),
        required_section_ids=("business_model",),
    )

    assert decision.can_call_ai is True
    assert len(decision.reason_codes) == 2
    assert len(set(decision.reason_codes)) == 2
    assert all(len(code) <= 120 for code in decision.reason_codes)
    assert all(":sha256_" in code for code in decision.reason_codes)


def test_문서와_조각의_회사_및_원본_결속을_강제한다() -> None:
    with pytest.raises(ValueError, match="원본 문서"):
        _candidate(fragments=(_fragment(document_id="other-document"),))

    with pytest.raises(ValueError, match="다른 회사"):
        ChapterEvidenceCandidates(
            company_id="corp-1",
            section_id="business_model",
            documents=(_document(company_id="corp-2"),),
            fragments=(),
            attempts=(),
            candidate_readiness=EvidenceReadiness.UNKNOWN,
            reason_codes=(),
            estimated_tokens=0,
            max_chars=100,
            max_estimated_tokens=100,
        )


def test_근거_원문_hash가_다르면_생성즉시_거절한다() -> None:
    with pytest.raises(ValueError, match="일치하지 않습니다"):
        EvidenceFragment(
            fragment_id="fragment-1",
            document_id="doc-1",
            location="본문",
            text_sha256="0" * 64,
            text="실제 원문",
            section_id="identity",
            slot_id="company_identity",
            score_millis=500,
            reason_codes=("official",),
        )


def test_enum값은_json에서_영어_기계코드로_보존된다() -> None:
    assert json.loads(json.dumps({"state": CollectionState.TRUNCATED})) == {
        "state": "TRUNCATED"
    }


def test_사유코드에는_원문이나_사람문장을_넣을_수_없다() -> None:
    with pytest.raises(ValueError, match="기계 코드"):
        _attempt(slot_id="business_model", state=CollectionState.FAILED).__class__(
            attempt_id="attempt-raw-reason",
            source_kind="official_homepage",
            requirement=SourceRequirement.REQUIRED,
            state=CollectionState.FAILED,
            slot_ids=("business_model",),
            reason_code="접속 실패: 실제 응답 원문",
        )


def test_예상_토큰이_상한을_넘으면_후보를_만들지_않는다() -> None:
    with pytest.raises(ValueError, match="예상 토큰"):
        ChapterEvidenceCandidates(
            company_id="corp-1",
            section_id="business_model",
            documents=(_document(),),
            fragments=(),
            attempts=(),
            candidate_readiness=EvidenceReadiness.UNKNOWN,
            reason_codes=(),
            estimated_tokens=101,
            max_chars=100,
            max_estimated_tokens=100,
        )

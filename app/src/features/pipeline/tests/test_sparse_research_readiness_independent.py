"""희소 공식 근거의 조사 연속과 최종 출고를 분리하는 독립 반례.

이 파일은 production 구현을 바꾸지 않는다. ``supplementary_research_allowed``는
최종 보고서 작성 허가(``can_call_ai``)가 아니라, 확인 완료된 부족에서
동일 owner/원장 안의 보조 뉴스 조사를 계속할 수 있는 예정 계약이다.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

from src.features.composer.constants import DART_DOCUMENT_URL_TEMPLATE
from src.features.pipeline.official_evidence_preflight import (
    assess_official_evidence,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP,
    FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT,
    FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_TRANSIENT,
    FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID,
)
from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    GenerationGateStatus,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    CollectedEvidenceDocument,
    CollectionAttempt,
    DocumentTextRange,
    EvidenceFragment,
)
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
)
from src.shared.report_evidence.runtime_port import (
    OfficialEvidenceCollectionResult,
    UnclassifiedEvidenceObservation,
)


_COMPANY_ID = "00126380"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _missing_attempt(section_id: str, *, state: CollectionState) -> CollectionAttempt:
    return CollectionAttempt(
        company_id=_COMPANY_ID,
        attempt_id=f"dart:{section_id}:{state.value.lower()}",
        source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
        requirement=SourceRequirement.REQUIRED,
        state=state,
        slot_ids=collector_slots_for(section_id),
        reason_code=(
            "document_fetch_missing"
            if state is CollectionState.MISSING
            else "document_fetch_failed"
        ),
    )


def _ready_dart_candidate(section_id: str, index: int) -> ChapterEvidenceCandidates:
    receipt = f"20260330{index:06d}"
    document_id = f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{receipt}"
    text = f"{section_id} 장의 검증된 DART 원문 사실입니다."
    slots = collector_slots_for(section_id)
    document = CollectedEvidenceDocument(
        company_id=_COMPANY_ID,
        document_id=document_id,
        canonical_url=DART_DOCUMENT_URL_TEMPLATE.format(document_id=receipt),
        source_tier=SourceTier.TIER_1_OFFICIAL,
        source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
        publisher="전자공시시스템",
        title=f"사업보고서 {index}",
        published_on="2026-03-30",
        collected_at="2026-09-08",
        content_sha256=_sha(f"DART document {index}"),
        exact_evidence_hashes=(_sha(text),),
        identity_binding=f"corp_code={_COMPANY_ID}",
        usable_ranges=(DocumentTextRange(0, len(text)),),
        collector_version="independent-fixture-v1",
        parser_version="independent-fixture-v1",
        requirement=SourceRequirement.REQUIRED,
    )
    fragment = EvidenceFragment(
        company_id=_COMPANY_ID,
        fragment_id=f"dart-fragment-{section_id}",
        document_id=document_id,
        location="0-30",
        text_sha256=_sha(text),
        text=text,
        section_id=section_id,
        slot_id=slots[0],
        covered_slot_ids=slots,
        score_millis=900,
        reason_codes=("fixture_match",),
    )
    return ChapterEvidenceCandidates(
        company_id=_COMPANY_ID,
        section_id=section_id,
        documents=(document,),
        fragments=(fragment,),
        attempts=(_missing_attempt(section_id, state=CollectionState.OK),),
        candidate_readiness=EvidenceReadiness.READY,
        reason_codes=(),
        estimated_tokens=10,
        max_chars=10_000,
        max_estimated_tokens=10_000,
    )


def _result(
    *,
    ready_count: int,
    unclassified: bool = False,
    failed_required_dart: bool = False,
    integrity_broken: bool = False,
) -> OfficialEvidenceCollectionResult:
    candidates: list[ChapterEvidenceCandidates] = []
    for index, section_id in enumerate(REQUIRED_EVIDENCE_SECTION_IDS, start=1):
        if index <= ready_count:
            candidate = _ready_dart_candidate(section_id, index)
            if integrity_broken and index == 1:
                candidate = replace(
                    candidate,
                    reason_codes=("document_company_mismatch:fixture",),
                )
            candidates.append(candidate)
            continue
        state = (
            CollectionState.FAILED
            if failed_required_dart and index == ready_count + 1
            else CollectionState.MISSING
        )
        candidates.append(
            ChapterEvidenceCandidates(
                company_id=_COMPANY_ID,
                section_id=section_id,
                documents=(),
                fragments=(),
                attempts=(_missing_attempt(section_id, state=state),),
                candidate_readiness=(
                    EvidenceReadiness.UNKNOWN
                    if state is CollectionState.FAILED
                    else EvidenceReadiness.INSUFFICIENT
                ),
                reason_codes=(),
                estimated_tokens=0,
                max_chars=10_000,
                max_estimated_tokens=10_000,
            )
        )
    observation = (
        UnclassifiedEvidenceObservation(
            company_id=_COMPANY_ID,
            document_count=1,
            fragment_count=1,
            observation_sha256="a" * 64,
        )
        if unclassified
        else None
    )
    return OfficialEvidenceCollectionResult(
        company_id=_COMPANY_ID,
        candidates=tuple(candidates),
        unclassified_evidence=observation,
    )


def test_bound_dart_and_ready_two_sections_block_release_but_continue_news_research() -> None:
    preflight = assess_official_evidence(_result(ready_count=2))

    assert preflight.decision.status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE
    assert preflight.can_call_ai is False
    assert (
        preflight.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT
    )
    # 예정 계약: 이 값은 SHADOW 출고 허가가 아니라 동일 owner의 bounded
    # 뉴스 snapshot/body 조사를 이어갈 수 있다는 뜻이다.
    assert preflight.supplementary_research_allowed is True


def test_actual_dart_fragment_classifier_gap_is_not_falsely_ready_and_continues_research() -> None:
    preflight = assess_official_evidence(_result(ready_count=2, unclassified=True))

    assert preflight.decision.status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE
    assert preflight.can_call_ai is False
    assert preflight.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP
    # 무분류 관측은 fragment/READY가 아니다. 다만 이미 결속된 별도 DART
    # fragment가 있으므로 최신 보조 뉴스 조사의 대상 법인 문맥은 남아 있다.
    assert preflight.supplementary_research_allowed is True


def test_classifier_gap_without_original_fragment_does_not_bypass_as_research_continuation() -> None:
    preflight = assess_official_evidence(_result(ready_count=0, unclassified=True))

    assert preflight.can_call_ai is False
    assert preflight.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP
    assert preflight.supplementary_research_allowed is False


def test_required_dart_failure_does_not_continue_research_even_with_bound_original() -> None:
    preflight = assess_official_evidence(
        _result(ready_count=2, failed_required_dart=True)
    )

    assert preflight.decision.status is GenerationGateStatus.STOP_TRANSIENT_FAILURE
    assert preflight.can_call_ai is False
    assert preflight.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_TRANSIENT
    assert preflight.supplementary_research_allowed is False


def test_company_binding_error_does_not_continue_research_even_with_bound_original() -> None:
    preflight = assess_official_evidence(
        _result(ready_count=2, integrity_broken=True)
    )

    assert preflight.can_call_ai is False
    assert preflight.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID
    assert preflight.supplementary_research_allowed is False


def test_ready_three_sections_are_shadow_release_path_not_research_only_status() -> None:
    preflight = assess_official_evidence(_result(ready_count=3))

    assert preflight.can_call_ai is True
    assert preflight.dart_partial_fallback is True
    assert preflight.supplementary_research_allowed is False

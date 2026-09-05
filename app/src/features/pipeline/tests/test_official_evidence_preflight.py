"""공식 근거 사전검사가 AI 없이 닫힌 원인으로 멈추는지 검증한다."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from src.features.composer.constants import DART_DOCUMENT_URL_TEMPLATE, SECTION_IDS
from src.features.composer.port import (
    CollectedFragment,
    SectionEvidencePacket,
    SectionEvidencePacketSet,
)
from src.features.company_comparison.official_sources import (
    dart_profile_attestation_material,
)
from src.features.pipeline.official_evidence_preflight import (
    assess_official_evidence,
    assess_packet_document_sources,
)
from src.features.pipeline.evidence_transport import (
    RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY,
    RAW_EVIDENCE_SECTION_IDS_KEY,
    build_section_evidence_packet_set,
)
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP,
    FINAL_GATE_DETAIL_PREFLIGHT_DOCUMENT_SOURCES_INSUFFICIENT,
    FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT,
    FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_TRANSIENT,
    FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID,
)
from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    GenerationGateStatus,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
    SOURCE_KIND_ROBOTS_TXT,
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
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_KIND_REVENUE_AND_ORDERS,
)


_COMPANY_ID = "00126380"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _packet_set(
    identities: tuple[str, ...],
    *,
    content_hashes: tuple[str, ...] | None = None,
    conflicting_first_identity: bool = False,
) -> SectionEvidencePacketSet:
    assert identities
    hashes = content_hashes or tuple("" for _identity in identities)
    assert len(hashes) == len(identities)
    packets: list[SectionEvidencePacket] = []
    for index, section_id in enumerate(SECTION_IDS):
        item_index = index % len(identities)
        fragments = [
            CollectedFragment(
                fragment_id=f"fragment-{index}",
                kind="fixture",
                text=f"{section_id}의 공식 근거",
                document_identity=identities[item_index],
                document_content_sha256=hashes[item_index],
                supported_claim_slots=(
                    CLAIM_SLOTS_BY_SECTION[section_id][0],
                ),
            )
        ]
        if index == 0 and conflicting_first_identity:
            fragments.append(
                CollectedFragment(
                    fragment_id="fragment-conflict",
                    kind="fixture",
                    text="같은 신원이 가리키는 다른 원문",
                    document_identity=identities[item_index],
                    document_content_sha256="f" * 64,
                    supported_claim_slots=(
                        CLAIM_SLOTS_BY_SECTION[section_id][0],
                    ),
                )
            )
        packets.append(
            SectionEvidencePacket(
                company_id=_COMPANY_ID,
                evidence_generation_sha256="e" * 64,
                section_id=section_id,
                fragments=tuple(fragments),
            )
        )
    return SectionEvidencePacketSet(
        company_id=_COMPANY_ID,
        evidence_generation_sha256="e" * 64,
        packets=tuple(packets),
    )


def _result(
    *,
    document_count: int = 9,
    first_state: CollectionState | None = None,
) -> OfficialEvidenceCollectionResult:
    texts = {
        section_id: f"{section_id}의 공식 회사 사실과 구체적인 사업 근거입니다."
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    }
    all_hashes = tuple(_sha(text) for text in texts.values())
    attestation_source_id, attestation_evidence = (
        dart_profile_attestation_material(
            profile={
                "status": "000",
                "corp_code": _COMPANY_ID,
                "corp_name": "가나다전자",
                "hm_url": "https://example.com",
            },
            corp_code=_COMPANY_ID,
            company_name="가나다전자",
        )
    )
    assert attestation_source_id and attestation_evidence
    candidates: list[ChapterEvidenceCandidates] = []
    for index, section_id in enumerate(REQUIRED_EVIDENCE_SECTION_IDS):
        slots = collector_slots_for(section_id)
        if index == 0 and first_state is not None:
            readiness = (
                EvidenceReadiness.UNKNOWN
                if first_state is CollectionState.FAILED
                else EvidenceReadiness.INSUFFICIENT
            )
            candidates.append(
                ChapterEvidenceCandidates(
                    company_id=_COMPANY_ID,
                    section_id=section_id,
                    documents=(),
                    fragments=(),
                    attempts=(
                        CollectionAttempt(
                            company_id=_COMPANY_ID,
                            attempt_id="attempt-first",
                            source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
                            requirement=SourceRequirement.REQUIRED,
                            state=first_state,
                            slot_ids=slots,
                            reason_code="fixture_state",
                        ),
                    ),
                    candidate_readiness=readiness,
                    reason_codes=(),
                    estimated_tokens=0,
                    max_chars=10_000,
                    max_estimated_tokens=10_000,
                )
            )
            continue

        document_index = index % document_count
        document_id = f"document-{document_index}"
        canonical_url = f"https://example.com/document-{document_index}"
        document = CollectedEvidenceDocument(
            company_id=_COMPANY_ID,
            document_id=document_id,
            canonical_url=canonical_url,
            source_tier=SourceTier.TIER_1_OFFICIAL,
            source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
            publisher="가나다전자",
            title=f"공식 자료 {document_index}",
            published_on="2026-09-04",
            collected_at="2026-09-04",
            content_sha256=_sha(f"document body {document_index}"),
            exact_evidence_hashes=all_hashes,
            identity_binding="dart_profile_homepage",
            domain_attestation_source_id=attestation_source_id,
            domain_attestation_evidence=attestation_evidence,
            usable_ranges=(DocumentTextRange(0, 10),),
            collector_version="fixture-v1",
            parser_version="fixture-v1",
            requirement=SourceRequirement.REQUIRED,
        )
        text = texts[section_id]
        fragment = EvidenceFragment(
            company_id=_COMPANY_ID,
            fragment_id=f"fragment-{section_id}",
            document_id=document_id,
            location=f"section:{section_id}",
            text_sha256=_sha(text),
            text=text,
            section_id=section_id,
            slot_id=slots[0],
            covered_slot_ids=slots,
            score_millis=900,
            reason_codes=("fixture_match",),
        )
        candidates.append(
            ChapterEvidenceCandidates(
                company_id=_COMPANY_ID,
                section_id=section_id,
                documents=(document,),
                fragments=(fragment,),
                attempts=(),
                candidate_readiness=EvidenceReadiness.READY,
                reason_codes=(),
                estimated_tokens=50,
                max_chars=10_000,
                max_estimated_tokens=10_000,
            )
        )
    return OfficialEvidenceCollectionResult(
        company_id=_COMPANY_ID,
        candidates=tuple(candidates),
    )


def _with_dart_evidence(
    result: OfficialEvidenceCollectionResult,
) -> OfficialEvidenceCollectionResult:
    """정상 후보 하나를 실제 DART 문서와 결속된 근거로 바꾼다."""

    candidates = list(result.candidates)
    target_index = next(
        index
        for index, candidate in enumerate(candidates)
        if candidate.documents and candidate.fragments
    )
    target = candidates[target_index]
    receipt_number = "20260330000001"
    document_id = f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{receipt_number}"
    candidates[target_index] = replace(
        target,
        documents=(
            replace(
                target.documents[0],
                document_id=document_id,
                canonical_url=DART_DOCUMENT_URL_TEMPLATE.format(
                    document_id=receipt_number
                ),
                source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
                domain_attestation_source_id="",
                domain_attestation_evidence="",
            ),
        ),
        fragments=(replace(target.fragments[0], document_id=document_id),),
    )
    return OfficialEvidenceCollectionResult(
        company_id=result.company_id,
        candidates=tuple(candidates),
    )


def _without_section_evidence(
    result: OfficialEvidenceCollectionResult,
    section_ids: tuple[str, ...],
    *,
    identity_mismatch_section_id: str | None = None,
) -> OfficialEvidenceCollectionResult:
    """지정한 장의 근거를 지워 «확인은 했지만 자료가 없다»로 만든다.

    우리은행 실측 모양을 그대로 옮긴다. REQUIRED 웹 경로는 정상 응답을 받았지만
    의미 칸을 못 채워 MISSING이라 그 장은 UNKNOWN이 아니라 INSUFFICIENT가 된다.
    신원 대조에 실패한 경로는 wide_collect가 OPTIONAL MISSING attempt만 남기고
    문서는 하나도 내보내지 않으므로 documents를 붙이지 않는다.
    """

    candidates = []
    for candidate in result.candidates:
        if candidate.section_id not in section_ids:
            candidates.append(candidate)
            continue
        slots = collector_slots_for(candidate.section_id)
        attempts = [
            CollectionAttempt(
                company_id=_COMPANY_ID,
                attempt_id=f"web:{candidate.section_id}",
                source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
                requirement=SourceRequirement.REQUIRED,
                state=CollectionState.MISSING,
                slot_ids=slots,
                reason_code="no_keyword_signal",
            )
        ]
        if candidate.section_id == identity_mismatch_section_id:
            attempts.append(
                CollectionAttempt(
                    company_id=_COMPANY_ID,
                    attempt_id=f"web-identity:{candidate.section_id}",
                    source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
                    requirement=SourceRequirement.OPTIONAL,
                    state=CollectionState.MISSING,
                    slot_ids=slots,
                    reason_code="root_identity_mismatch",
                )
            )
        candidates.append(
            replace(
                candidate,
                documents=(),
                fragments=(),
                attempts=tuple(attempts),
                candidate_readiness=EvidenceReadiness.INSUFFICIENT,
            )
        )
    return OfficialEvidenceCollectionResult(
        company_id=result.company_id,
        candidates=tuple(candidates),
    )


def _identity_mismatch_attempt(
    attempt_id: str, slots: tuple[str, ...]
) -> CollectionAttempt:
    """신원 대조 실패로 닫힌 웹 시도. wide_collect는 항상 OPTIONAL MISSING이다."""

    return CollectionAttempt(
        company_id=_COMPANY_ID,
        attempt_id=attempt_id,
        source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
        requirement=SourceRequirement.OPTIONAL,
        state=CollectionState.MISSING,
        slot_ids=slots,
        reason_code="root_identity_mismatch",
    )


def test_아홉장과_독립문서하한이_모두_차야_AI를_허용한다() -> None:
    preflight = assess_official_evidence(_result())

    assert preflight.can_call_ai is True
    assert preflight.detail_code == ""


def test_formal_의미칸검사는_뒤에_합쳐질_구조화문서를_미리_빼지_않는다() -> None:
    preflight = assess_official_evidence(_result(document_count=1))

    assert preflight.can_call_ai is True
    assert preflight.independent_document_count == 1
    assert preflight.detail_code == ""


def test_정상확인후_자료부족과_조회실패를_다르게_분류한다() -> None:
    insufficient = assess_official_evidence(
        _result(first_state=CollectionState.MISSING)
    )
    transient = assess_official_evidence(
        _result(first_state=CollectionState.FAILED)
    )

    assert (
        insufficient.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT
    )
    assert (
        transient.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_TRANSIENT
    )


def test_DART핵심장이_있으면_홈페이지장애는_부분보고서로_전환한다() -> None:
    observed = _with_dart_evidence(_result())
    candidates = list(observed.candidates)
    target_index = next(
        index
        for index, candidate in enumerate(candidates)
        if candidate.section_id == "culture"
    )
    target = candidates[target_index]
    candidates[target_index] = replace(
        target,
        documents=(),
        fragments=(),
        attempts=(
            CollectionAttempt(
                company_id=_COMPANY_ID,
                attempt_id="homepage:culture",
                source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
                requirement=SourceRequirement.REQUIRED,
                state=CollectionState.FAILED,
                slot_ids=collector_slots_for("culture"),
                reason_code="document_fetch_failed",
            ),
        ),
        candidate_readiness=EvidenceReadiness.UNKNOWN,
    )

    preflight = assess_official_evidence(
        OfficialEvidenceCollectionResult(
            company_id=observed.company_id,
            candidates=tuple(candidates),
        )
    )

    assert preflight.decision.status is GenerationGateStatus.STOP_TRANSIENT_FAILURE
    assert preflight.dart_partial_fallback is True
    assert preflight.dart_partial_reason == "transient_web_failure"
    assert preflight.can_call_ai is True
    assert preflight.detail_code == ""


def test_DART근거가_있으면_사업모델_웹장애도_부분보고서로_진행한다() -> None:
    observed = _with_dart_evidence(_result())
    candidates = list(observed.candidates)
    target_index = next(
        index
        for index, candidate in enumerate(candidates)
        if candidate.section_id == "business_model"
    )
    target = candidates[target_index]
    candidates[target_index] = replace(
        target,
        documents=(),
        fragments=(),
        attempts=(
            CollectionAttempt(
                company_id=_COMPANY_ID,
                attempt_id="homepage:business-model",
                source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
                requirement=SourceRequirement.REQUIRED,
                state=CollectionState.FAILED,
                slot_ids=collector_slots_for("business_model"),
                reason_code="document_fetch_failed",
            ),
        ),
        candidate_readiness=EvidenceReadiness.UNKNOWN,
    )

    preflight = assess_official_evidence(
        OfficialEvidenceCollectionResult(
            company_id=observed.company_id,
            candidates=tuple(candidates),
        )
    )

    assert preflight.decision.status is GenerationGateStatus.STOP_TRANSIENT_FAILURE
    assert preflight.dart_partial_fallback is True
    assert preflight.can_call_ai is True
    assert preflight.detail_code == ""


def test_robots_선택경로만_실패하면_DART_부분보고서를_허용한다() -> None:
    observed = _with_dart_evidence(_result())
    candidates = list(observed.candidates)
    target_index = next(
        index
        for index, candidate in enumerate(candidates)
        if candidate.section_id == "business_model"
    )
    target = candidates[target_index]
    candidates[target_index] = replace(
        target,
        documents=(),
        fragments=(),
        attempts=(
            CollectionAttempt(
                company_id=_COMPANY_ID,
                attempt_id="homepage:business-model",
                source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
                requirement=SourceRequirement.REQUIRED,
                state=CollectionState.MISSING,
                slot_ids=collector_slots_for("business_model"),
                reason_code="document_not_found",
            ),
            CollectionAttempt(
                company_id=_COMPANY_ID,
                attempt_id="robots:business-model",
                source_kind=SOURCE_KIND_ROBOTS_TXT,
                requirement=SourceRequirement.OPTIONAL,
                state=CollectionState.FAILED,
                slot_ids=collector_slots_for("business_model"),
                reason_code="robots_fetch_failed",
            ),
        ),
        candidate_readiness=EvidenceReadiness.UNKNOWN,
    )

    preflight = assess_official_evidence(
        OfficialEvidenceCollectionResult(
            company_id=observed.company_id,
            candidates=tuple(candidates),
        )
    )

    assert preflight.decision.status is GenerationGateStatus.STOP_TRANSIENT_FAILURE
    assert preflight.dart_partial_fallback is True
    assert preflight.can_call_ai is True
    assert preflight.detail_code == ""


def test_INSUFFICIENT라도_DART근거와_READY_3장이상이면_부분보고서로_진행한다() -> None:
    # 우리은행 실측 모양: 아홉 장 중 일곱 장이 READY인데 두 장의 자료가 없어
    # STOP_INSUFFICIENT_EVIDENCE로 닫혔고, 남은 웹 경로는 신원 대조에 실패해
    # MISSING만 남겼다. 예전에는 이 조합이 보고서 0건이었다.
    observed = _without_section_evidence(
        _with_dart_evidence(_result()),
        ("portfolio", "culture"),
        identity_mismatch_section_id="portfolio",
    )

    preflight = assess_official_evidence(observed)

    assert preflight.decision.status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE
    assert len(preflight.decision.ready_section_ids) == 7
    assert preflight.decision.insufficient_section_ids == ("portfolio", "culture")
    assert preflight.dart_partial_fallback is True
    assert preflight.dart_partial_reason == "insufficient_with_ready_sections"
    assert preflight.can_call_ai is True
    assert preflight.detail_code == ""


def test_READY가_최소_공개_장수_미만이면_INSUFFICIENT는_그대로_막힌다() -> None:
    # 공개 가능한 부분 보고서의 최소 장 수는 3장이다. 경계 양쪽을 같이 본다 —
    # 3장이면 열리고 2장이면 닫혀야, 하한이 실제로 판정에 쓰이는 것이 된다.
    blocked_ids = (
        "portfolio",
        "past_changes",
        "current_challenges",
        "future_strategy",
        "operations_partners",
        "culture",
    )
    exactly_minimum = assess_official_evidence(
        _without_section_evidence(
            _with_dart_evidence(_result()),
            blocked_ids,
            identity_mismatch_section_id="portfolio",
        )
    )
    below_minimum = assess_official_evidence(
        _without_section_evidence(
            _with_dart_evidence(_result()),
            (*blocked_ids, "competitive_position"),
            identity_mismatch_section_id="portfolio",
        )
    )

    assert len(exactly_minimum.decision.ready_section_ids) == 3
    assert exactly_minimum.dart_partial_fallback is True
    assert exactly_minimum.can_call_ai is True

    assert len(below_minimum.decision.ready_section_ids) == 2
    assert below_minimum.dart_partial_fallback is False
    assert below_minimum.dart_partial_reason == ""
    assert below_minimum.can_call_ai is False
    assert (
        below_minimum.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT
    )

    # 하한 아래에서는 무분류 관측이 있을 때 내부 분류 범위 결함으로 닫는
    # 기존 규칙도 그대로다.
    below_minimum_source = _without_section_evidence(
        _with_dart_evidence(_result()),
        (*blocked_ids, "competitive_position"),
        identity_mismatch_section_id="portfolio",
    )
    below_minimum_unclassified = assess_official_evidence(
        OfficialEvidenceCollectionResult(
            company_id=below_minimum_source.company_id,
            candidates=below_minimum_source.candidates,
            unclassified_evidence=UnclassifiedEvidenceObservation(
                company_id=_COMPANY_ID,
                document_count=1,
                fragment_count=1,
                observation_sha256="d" * 64,
            ),
        )
    )

    assert below_minimum_unclassified.dart_partial_fallback is False
    assert (
        below_minimum_unclassified.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP
    )


def test_DART근거가_결속되지_않으면_INSUFFICIENT_구제는_없다() -> None:
    # 남은 장이 아무리 많아도 부분 보고서의 본문은 DART 원문이 받쳐야 한다.
    observed = _without_section_evidence(
        _result(),
        ("portfolio", "culture"),
        identity_mismatch_section_id="portfolio",
    )

    preflight = assess_official_evidence(observed)

    assert len(preflight.decision.ready_section_ids) == 7
    assert preflight.dart_partial_fallback is False
    assert preflight.dart_partial_reason == ""
    assert preflight.can_call_ai is False
    assert (
        preflight.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT
    )


def test_REQUIRED_DART_실패는_INSUFFICIENT_구제를_막는다() -> None:
    # DART 필수 경로를 끝까지 확인하지 못한 상태를 «확인 완료»로 바꾸지 않는다.
    observed = _without_section_evidence(
        _with_dart_evidence(_result()),
        ("portfolio", "culture"),
        identity_mismatch_section_id="portfolio",
    )
    candidates = list(observed.candidates)
    target_index = next(
        index
        for index, candidate in enumerate(candidates)
        if candidate.section_id == "identity"
    )
    candidates[target_index] = replace(
        candidates[target_index],
        attempts=(
            CollectionAttempt(
                company_id=_COMPANY_ID,
                attempt_id="document:dart_business_report:20260330000001",
                source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
                requirement=SourceRequirement.REQUIRED,
                state=CollectionState.FAILED,
                slot_ids=collector_slots_for("identity"),
                reason_code="document_fetch_failed",
            ),
        ),
    )

    preflight = assess_official_evidence(
        OfficialEvidenceCollectionResult(
            company_id=observed.company_id,
            candidates=tuple(candidates),
        )
    )

    assert preflight.decision.status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE
    assert len(preflight.decision.ready_section_ids) == 7
    assert preflight.dart_partial_fallback is False
    assert preflight.can_call_ai is False
    assert (
        preflight.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_TRANSIENT
    )


def test_신원대조_실패는_웹문서가_결속된_장에서만_INSUFFICIENT_구제를_막는다() -> None:
    """차단 기준은 «신원 대조에 실패했나»가 아니라 «그 장에 웹 자료가 남았나»다.

    wide_collect의 신원 대조 실패 경로는 fail-closed다. 문서를 만드는 자리가
    ``match is not None`` 안에만 있어서 MISSING attempt만 남고 documents는 0건이
    된다. 우리은행 실측도 그랬다 — 수집된 문서 12건이 전부 DART였고, 신원 불일치
    시도가 남긴 문서·조각은 0건이었다. 그래서 그 사유만으로는 막지 않는다.
    반대로 같은 장에 웹 문서가 이미 결속돼 있으면 어느 호스트에서 온 문장인지
    이 계층이 구분할 수 없으므로 그때는 막는다. 두 경우를 한자리에서 대조한다 —
    바뀌는 변수는 «불일치 attempt가 붙은 장에 웹 문서가 있는가» 하나뿐이다.
    """

    # (가) 우리은행형: 불일치 attempt가 붙은 portfolio는 문서를 하나도 안 남겼다.
    unbound = _without_section_evidence(
        _with_dart_evidence(_result()),
        ("portfolio", "culture"),
        identity_mismatch_section_id="portfolio",
    )
    mismatch_candidate = next(
        candidate
        for candidate in unbound.candidates
        if candidate.section_id == "portfolio"
    )
    assert mismatch_candidate.documents == ()
    assert mismatch_candidate.fragments == ()

    passed = assess_official_evidence(unbound)

    assert passed.dart_partial_fallback is True
    assert passed.dart_partial_reason == "insufficient_with_ready_sections"
    assert passed.can_call_ai is True
    assert passed.detail_code == ""

    # (나) 같은 fixture에서 웹 문서가 살아 있는 장에 같은 불일치 사유를 붙인다.
    candidates = list(unbound.candidates)
    target_index = next(
        index
        for index, candidate in enumerate(candidates)
        if candidate.section_id == "business_model"
    )
    target = candidates[target_index]
    assert target.documents[0].source_kind == SOURCE_KIND_OFFICIAL_WEB_PAGE
    candidates[target_index] = replace(
        target,
        attempts=(
            _identity_mismatch_attempt(
                "web-identity:business-model",
                collector_slots_for("business_model"),
            ),
        ),
    )

    blocked = assess_official_evidence(
        OfficialEvidenceCollectionResult(
            company_id=unbound.company_id,
            candidates=tuple(candidates),
        )
    )

    assert blocked.decision.status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE
    assert len(blocked.decision.ready_section_ids) == 7
    assert blocked.dart_partial_fallback is False
    assert blocked.dart_partial_reason == ""
    assert blocked.can_call_ai is False
    assert (
        blocked.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT
    )


def test_읽은원문을_분류못했으면_회사의_자료부족이_아니라_내부범위결함이다() -> None:
    # 이 fixture에는 DART 문서와 결속된 조각이 없다(_result는 웹 문서만 만든다).
    # 그래서 INSUFFICIENT 부분 보고서 갈래가 열리지 않고, 무분류 관측이 있으면
    # 내부 분류 범위 결함으로 닫히는 원래 뜻이 그대로 유지된다.
    insufficient = _result(first_state=CollectionState.MISSING)
    observation = UnclassifiedEvidenceObservation(
        company_id=_COMPANY_ID,
        document_count=1,
        fragment_count=2,
        observation_sha256="a" * 64,
    )
    observed = OfficialEvidenceCollectionResult(
        company_id=insufficient.company_id,
        candidates=insufficient.candidates,
        unclassified_evidence=observation,
    )

    preflight = assess_official_evidence(observed)

    assert preflight.can_call_ai is False
    assert (
        preflight.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP
    )


def test_무분류관측이_있어도_DART부분보고서_조건을_채우면_진행한다() -> None:
    # 분류 범위 결함은 «닫아야 할 만큼 나쁜가»가 아니라 «다른 안전한 출구가
    # 있는가»로 갈린다. DART 원문이 받쳐 주고 공개 최소치를 넘는 장이 READY면
    # 분류 못 한 조각을 근거로 승격하지 않은 채 부분 보고서로 내보낸다.
    # 이 갈래가 없으면 우리은행처럼 7장이 READY여도 보고서가 0건이 된다.
    partial = _without_section_evidence(
        _with_dart_evidence(_result()),
        ("portfolio", "culture"),
        identity_mismatch_section_id="portfolio",
    )
    observed = OfficialEvidenceCollectionResult(
        company_id=partial.company_id,
        candidates=partial.candidates,
        unclassified_evidence=UnclassifiedEvidenceObservation(
            company_id=_COMPANY_ID,
            document_count=1,
            fragment_count=2,
            observation_sha256="c" * 64,
        ),
    )

    preflight = assess_official_evidence(observed)

    assert preflight.dart_partial_fallback is True
    assert preflight.dart_partial_reason == "insufficient_with_ready_sections"
    assert preflight.detail_code == ""
    assert preflight.can_call_ai is True


def test_무분류관측이_있어도_조회실패가_섞이면_일시장애를_우선한다() -> None:
    transient = _result(first_state=CollectionState.FAILED)
    observed = OfficialEvidenceCollectionResult(
        company_id=transient.company_id,
        candidates=transient.candidates,
        unclassified_evidence=UnclassifiedEvidenceObservation(
            company_id=_COMPANY_ID,
            document_count=1,
            fragment_count=1,
            observation_sha256="b" * 64,
        ),
    )

    preflight = assess_official_evidence(observed)

    assert (
        preflight.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_TRANSIENT
    )


def test_REQUIRED_DART_실패를_웹조각이_채운슬롯으로_덮지_않는다() -> None:
    observed = _result()
    candidates = list(observed.candidates)
    first = candidates[0]
    candidates[0] = replace(
        first,
        attempts=(
            CollectionAttempt(
                company_id=_COMPANY_ID,
                attempt_id="document:dart_business_report:20250315000001",
                source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
                requirement=SourceRequirement.REQUIRED,
                state=CollectionState.FAILED,
                slot_ids=collector_slots_for(first.section_id),
                reason_code="document_fetch_failed",
            ),
        ),
    )

    preflight = assess_official_evidence(
        OfficialEvidenceCollectionResult(
            company_id=observed.company_id,
            candidates=tuple(candidates),
        )
    )

    # 모든 의미 칸은 웹 조각으로 채워져 일반 bundle 판정만 보면 READY다.
    # 그래도 필수 DART 확인이 끝나지 않았으므로 AI 호출은 막아야 한다.
    assert preflight.decision.status is GenerationGateStatus.READY_FOR_GENERATION
    assert preflight.can_call_ai is False
    assert (
        preflight.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_TRANSIENT
    )


def test_문서결속_배선오류를_외부자료부족으로_분류하지_않는다() -> None:
    result = _result()
    candidates = list(result.candidates)
    candidates[0] = replace(
        candidates[0], reason_codes=("fragment_document_missing:1",)
    )

    preflight = assess_official_evidence(
        OfficialEvidenceCollectionResult(
            company_id=result.company_id,
            candidates=tuple(candidates),
        )
    )

    assert preflight.can_call_ai is False
    assert preflight.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


def test_출처없는_legacy는_글자만_같아도_formal문서로_승격하지_않는다() -> None:
    result = _result()
    first_fragment = result.candidates[0].fragments[0]
    legacy = {
        1: {
            "종류": "사업내용",
            "원문": first_fragment.text,
        }
    }

    merged, added = merge_official_evidence_fragments(legacy, result)
    # 글자만 같고 문서 결속 필드가 하나도 없으므로 기존 번호 1은 그대로
    # 남고, typed 근거는 새 번호를 받는다.
    assert added == 9
    assert merged[1] == legacy[1]
    typed_number = next(
        number
        for number, fragment in merged.items()
        if fragment.get(RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY)
        == (first_fragment.fragment_id,)
    )
    assert typed_number != 1
    assert merged[typed_number]["종류"] == SOURCE_KIND_OFFICIAL_WEB_PAGE
    assert merged[typed_number][RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY] == (
        first_fragment.fragment_id,
    )
    assert merged[typed_number][RAW_EVIDENCE_SECTION_IDS_KEY] == ("identity",)
    packets = build_section_evidence_packet_set(
        corp_id=_COMPANY_ID,
        source_generation_sha256="f" * 64,
        # 출처 없는 legacy 자체는 packet 계약에서 별도로 거절된다. 여기서는
        # 새로 발급된 typed 번호의 장·origin transport만 독립 검증한다.
        frags={
            number: fragment
            for number, fragment in merged.items()
            if number != 1
        },
        filing_meta=None,
    )
    assert tuple(packet.section_id for packet in packets.packets) == (
        *REQUIRED_EVIDENCE_SECTION_IDS,
    )
    identity_packet = packets.packets[0]
    assert str(typed_number) in {
        fragment.fragment_id for fragment in identity_packet.fragments
    }


def test_매출표_문서ID가_formal_DART문서와_맞으면_기존_인용번호를_지킨다() -> None:
    result = _result()
    candidates = list(result.candidates)
    first = candidates[0]
    receipt_number = "20260330000001"
    formal_document_id = f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{receipt_number}"
    candidates[0] = replace(
        first,
        documents=(
            replace(
                first.documents[0],
                document_id=formal_document_id,
                canonical_url=DART_DOCUMENT_URL_TEMPLATE.format(
                    document_id=receipt_number
                ),
                source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
            ),
        ),
        fragments=(
            replace(first.fragments[0], document_id=formal_document_id),
        ),
    )
    dart_result = OfficialEvidenceCollectionResult(
        company_id=result.company_id,
        candidates=tuple(candidates),
    )
    first_fragment = dart_result.candidates[0].fragments[0]
    revenue_cite_number = 17
    revenue_fragment = {
        "종류": LEGACY_KIND_REVENUE_AND_ORDERS,
        "원문": first_fragment.text,
        "문서ID": receipt_number,
        "문서명": "사업보고서",
        "원문위치": "매출 구성 원문 표 1",
    }

    merged, added = merge_official_evidence_fragments(
        {revenue_cite_number: revenue_fragment},
        dart_result,
    )

    # 명시한 DART 접수번호가 formal 문서와 정확히 일치하므로 표가 이미
    # 가리키는 공개 번호 17을 유지하고, 나머지 여덟 조각만 새 번호를 받는다.
    assert added == 8
    assert merged[revenue_cite_number]["종류"] == (
        SOURCE_KIND_DART_BUSINESS_REPORT
    )
    assert merged[revenue_cite_number][RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY] == (
        first_fragment.fragment_id,
    )
    assert merged[revenue_cite_number]["문서ID"] == formal_document_id


def test_모든_실제_packet을_합친_뒤에도_한문서면_AI전에_차단한다() -> None:
    result = _result(document_count=1)
    merged, _added = merge_official_evidence_fragments({}, result)
    packets = build_section_evidence_packet_set(
        corp_id=_COMPANY_ID,
        source_generation_sha256="f" * 64,
        frags=merged,
        filing_meta=None,
    )

    preflight = assess_packet_document_sources(packets)

    assert preflight.can_call_ai is False
    assert preflight.independent_document_count == 1
    assert (
        preflight.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_DOCUMENT_SOURCES_INSUFFICIENT
    )


def test_같은원문을_서로다른_URL로_복제해도_한문서로_센다() -> None:
    identities = tuple(f"url:https://example.com/copy-{index}" for index in range(9))
    packets = _packet_set(
        identities,
        content_hashes=tuple("a" * 64 for _identity in identities),
    )

    preflight = assess_packet_document_sources(packets)

    assert preflight.independent_document_count == 1
    assert preflight.can_call_ai is False


def test_원문hash없는_임의URL과_위조document_host는_문서수를_못채운다() -> None:
    url_packets = _packet_set(
        tuple(f"url:https://attacker.example/{index}" for index in range(9))
    )
    forged_packets = _packet_set(
        tuple(f"document:attacker.example:{index}" for index in range(9))
    )

    assert assess_packet_document_sources(url_packets).independent_document_count == 0
    assert assess_packet_document_sources(forged_packets).independent_document_count == 0


def test_hash없는_legacy는_DART접수번호와_재무API_정본만_별도집계한다() -> None:
    identities = tuple(
        f"document:dart.fss.or.kr:20260{index:09d}"
        for index in range(8)
    )
    # 위 문자열이 14자리 접수번호인지 공허하지 않게 확인한다.
    assert all(len(identity.rpartition(":")[2]) == 14 for identity in identities)

    preflight = assess_packet_document_sources(_packet_set(identities))

    assert preflight.independent_document_count == 8
    assert preflight.can_call_ai is True


def test_같은문서신원이_서로다른_원문hash를_가리키면_내부오류다() -> None:
    packets = _packet_set(
        ("url:https://example.com/document",),
        content_hashes=("a" * 64,),
        conflicting_first_identity=True,
    )

    preflight = assess_packet_document_sources(packets)

    assert preflight.can_call_ai is False
    assert preflight.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID

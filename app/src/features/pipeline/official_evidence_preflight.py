"""공식 근거의 준비 상태와 확보 자료를 사용하는 작성 허용을 분리한다.

자료 수·의미 칸·수집 완료 여부는 축약 필요성과 미확인 안내에 사용한다.
회사·문서 결속이 깨진 입력은 작성 근거로 승격하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.features.composer.constants import (
    DART_DOCUMENT_HOST,
    DART_FINANCIAL_API_DOCUMENT_ID,
    DART_FINANCIAL_API_HOST,
    DART_FINANCIAL_API_URL,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP,
    FINAL_GATE_DETAIL_PREFLIGHT_DOCUMENT_SOURCES_INSUFFICIENT,
    FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT,
    FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INCOMPLETE,
    FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID,
)
from src.shared.report_evidence.constants import (
    COLLECTION_CAP_TRUNCATION_REASON_CODES,
    CollectionState,
    GenerationGateStatus,
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_QUARTERLY_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
    SourceRequirement,
)
from src.shared.report_evidence.logic import assess_generation_gate, build_section_bundle
from src.shared.report_evidence.models import CollectionAttempt, GenerationGateDecision
from src.features.composer.port import SectionEvidencePacketSet
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_quality.constants import MIN_DOCUMENT_SOURCES
from src.shared.report_quality.source_identity import (
    document_identity_components,
    document_identity_from_parts,
)


# 외부에서 자료를 못 찾은 상태가 아니라 수집 결과의 회사·문서 결속이 깨진
# 상태다. 이 사유들을 자료 부족으로 위장하면 사용자는 같은 입력을 반복하고,
# 운영자는 수집기 배선 결함을 보지 못한다.
_INTERNAL_INTEGRITY_REASON_PREFIXES = (
    "document_company_mismatch:",
    "fragment_company_mismatch:",
    "fragment_document_missing:",
    "fragment_not_bound_to_document:",
    "attempt_company_mismatch:",
)
_DART_RECEIPT_RE = re.compile(r"[0-9]{14}")
_DART_FINANCIAL_IDENTITY = document_identity_from_parts(
    document_id=DART_FINANCIAL_API_DOCUMENT_ID,
    host=DART_FINANCIAL_API_HOST,
    url=DART_FINANCIAL_API_URL,
)
_DART_DOCUMENT_SOURCE_KINDS = frozenset(
    {
        SOURCE_KIND_DART_BUSINESS_REPORT,
        SOURCE_KIND_DART_AUDIT_REPORT,
        SOURCE_KIND_DART_SEMIANNUAL_REPORT,
        SOURCE_KIND_DART_QUARTERLY_REPORT,
    }
)
_INCOMPLETE_COLLECTION_STATES = frozenset(
    {CollectionState.FAILED, CollectionState.TRUNCATED}
)
# 작성 경로의 전환 사유다. 수집 완료나 해당 원문의 사실 검증을 뜻하지 않는다.
DART_PARTIAL_REASON_REQUIRED_COLLECTION_INCOMPLETE = "required_collection_incomplete"
DART_PARTIAL_REASON_TRANSIENT_WEB_FAILURE = "transient_web_failure"
DART_PARTIAL_REASON_INSUFFICIENT_WITH_READY_SECTIONS = (
    "insufficient_with_ready_sections"
)
DART_PARTIAL_REASON_TOO_FEW_DOCUMENTS_FOR_FULL = "too_few_documents_for_full"
# formal 수집이 끝난 뒤 packet을 합칠 때 «새 문서 신원»으로 더해질 수 있는
# 최대 건수다. 이 여유분을 더해도 완성 하한에 못 미칠 때만 미리 부분 보고서로
# 내려서, 정식이 될 수 있었던 회사를 앞당겨 강등하지 않는다. 각 항의 근거는
# 아래와 같고, 실제로는 겹칠 수 있으므로 셋 다 별개로 세는 것이 최대치다.
#   1) DART 재무 API 정본 1건 — 이 legacy 조각의 신원은 항상
#      ``_DART_FINANCIAL_IDENTITY``로 굳고(``evidence_transport.py:200-205``),
#      이 종류는 formal 수집 종류 목록에 없어서
#      (``shared/report_evidence/constants.py:38-46``) 수집기 집계에는 절대
#      들어가지 않는다. 즉 항상 새로 더해지는 1건이다.
#   2) 매출 구성표 1건 — 조각의 문서ID가 공시 접수번호라
#      (``real.py``의 ``_bind_revenue_table_evidence_fragments``) DART 문서
#      신원 1건이 된다. 그 접수번호가 이미 수집한 공시와 같으면 0건이므로
#      최대치로 1건을 잡는다.
#   3) 공식 양사 비교 1건 — FULL은 비교 생산물을 반드시 붙이고
#      (``real.py``의 ``_run_v2_composer``가 부르는
#      ``attach_comparison_program_evidence``), 그 상대사 공시가 새 문서
#      신원이 된다. 기존 비교 브리지 fixture로 잰 실측 델타가 +1이었다
#      (비교 전 9건 → 비교 뒤 10건. 같이 붙는 웹 전용 신원은 원문 hash가 없어
#      문서 수를 늘리지 못한다).
LATE_PACKET_DOCUMENT_SOURCES = 3


def _has_usable_dart_evidence(result: OfficialEvidenceCollectionResult) -> bool:
    """실제 본문 조각과 연결된 DART 문서가 하나 이상 있는지 확인한다."""

    for candidate in result.candidates:
        dart_document_ids = {
            document.document_id
            for document in candidate.documents
            if document.source_kind in _DART_DOCUMENT_SOURCE_KINDS
        }
        if any(
            fragment.document_id in dart_document_ids
            for fragment in candidate.fragments
        ):
            return True
    return False


def _is_stable_legacy_document_identity(identity: str) -> bool:
    """옛 조각 중 생산 경계가 실제로 만들 수 있는 안정 ID만 인정한다."""

    host, document_id = document_identity_components(identity)
    return (
        host == DART_DOCUMENT_HOST
        and _DART_RECEIPT_RE.fullmatch(document_id) is not None
    ) or identity == _DART_FINANCIAL_IDENTITY


def _blocks_release(attempt: CollectionAttempt) -> bool:
    """이 미완료 수집이 FULL 출고를 막아야 하는지 판정한다.

    «수집 미완료»(관측)와 «출고 차단»은 다른 질문이다. 선택(OPTIONAL) 경로는
    그 의미 칸의 유일한 확인 길이 아니므로 상태와 무관하게 출고를 막지 않는다
    — 장 준비도 판정(``shared/report_evidence/logic.py``)이 OPTIONAL 실패를
    강등해 읽는 것과 같은 정책이다. 필수(REQUIRED) 경로의 실패·잘림은 막는다.
    단 DART 문서가 아닌 필수 경로가 설계 상한(쪽 수·바이트·시간)에 닿아 멈춘
    잘림은 «정한 만큼 읽었다»는 뜻이라 막지 않는다. DART 필수 문서는 어떤
    사유로 잘려도 막는다.
    """

    if attempt.state not in _INCOMPLETE_COLLECTION_STATES:
        return False
    if attempt.requirement is not SourceRequirement.REQUIRED:
        return False
    return not (
        attempt.source_kind not in _DART_DOCUMENT_SOURCE_KINDS
        and attempt.state is CollectionState.TRUNCATED
        and attempt.reason_code in COLLECTION_CAP_TRUNCATION_REASON_CODES
    )


@dataclass(frozen=True)
class NonblockingIncompleteAttempt:
    """출고를 막지 않은 미완료 수집 한 건의 원문 없는 관측.

    선택 경로 미완료와, 설계 상한 예외로 출고를 막지 않은 필수 경로 잘림에
    같은 모양으로 쓴다. URL·원문·회사 식별자·시도 식별자는 싣지 않는다.
    종류·상태·닫힌 사유 코드만 남겨 진단 단계가 개수로 줄일 수 있게 한다.
    """

    source_kind: str
    state: CollectionState
    reason_code: str


@dataclass(frozen=True)
class OfficialEvidencePreflight:
    """AI 호출 가능 여부와 원문 없는 닫힌 사유 코드."""

    decision: GenerationGateDecision
    independent_document_count: int
    detail_code: str = ""
    dart_partial_fallback: bool = False
    # 어느 갈래로 부분 보고서 전환을 허용했는지 남긴다. 값은
    # ``DART_PARTIAL_REASON_*`` 값이며, 전환이 없으면 빈 문자열이다.
    dart_partial_reason: str = ""
    # 공식 근거의 장 분류가 적어도 확인된 DART 원문은 있을 수 있다. 이것은
    # 보완 조사만 허용하는 관측이며, can_call_ai나 최종 출고 허가가 아니다.
    supplementary_research_allowed: bool = False
    # 관측: 끝까지 못 읽은 경로(FAILED·TRUNCATED)가 하나라도 있으면 참이다.
    # 요구 수준을 가리지 않는다. 캐시 적격과 «자료 확인 완료» 표시가 이 값을
    # 쓴다 — 불완전한 수집을 캐시로 굳히거나 «완료»로 보이지 않게 한다.
    collection_incomplete: bool = False
    # 출고 차단: ``_blocks_release``가 참인 미완료만 센다. FULL을 부분
    # 보고서로 내리는 판단은 이 값을 쓴다. 참이면 collection_incomplete도 참이다.
    release_blocking_incomplete: bool = False
    # 출고를 막지 않은 선택 경로 미완료. 같은 시도가 여러 장에 복제돼 있어도
    # 한 번만 싣는다(시도 식별자로 중복 제거하되 식별자 자체는 싣지 않는다).
    optional_incomplete_attempts: tuple[NonblockingIncompleteAttempt, ...] = ()
    # 설계 상한 예외(``_blocks_release``)로 출고를 막지 않은 필수 경로 잘림.
    # 지금은 만드는 생산자가 없지만(광역 수집기의 잘림은 전부 OPTIONAL), 생기면
    # 진단 단계에서 선택 경로 미완료와 구분해 사후에 읽을 수 있게 따로 싣는다.
    design_cap_exempt_attempts: tuple[NonblockingIncompleteAttempt, ...] = ()

    @property
    def can_call_ai(self) -> bool:
        return self.detail_code != FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID and (
            self.dart_partial_fallback or (
                not self.detail_code and self.decision.can_call_ai
            )
        )


@dataclass(frozen=True)
class PacketDocumentSourcePreflight:
    """실제 9장 packet 전체가 가진 독립 문서 하한 판정."""

    independent_document_count: int
    detail_code: str = ""

    @property
    def can_call_ai(self) -> bool:
        return self.detail_code != FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID

    @property
    def partial_required(self) -> bool:
        return self.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_DOCUMENT_SOURCES_INSUFFICIENT


def empty_collector_sections(
    result: OfficialEvidenceCollectionResult,
) -> tuple[dict[str, object], ...]:
    """재판정 대상인 장과 비어 있는 수집 의미 칸을 정책 순서로 돌려준다."""

    candidates_by_id = {
        candidate.section_id: candidate for candidate in result.candidates
    }
    empty_sections: list[dict[str, object]] = []
    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        bundle = build_section_bundle(
            candidates_by_id[section_id],
            required_slot_ids=collector_slots_for(section_id),
        )
        if bundle.missing_slot_ids:
            empty_sections.append(
                {
                    "section_id": section_id,
                    "missing_slot_ids": bundle.missing_slot_ids,
                }
            )
    return tuple(empty_sections)


def assess_official_evidence(
    result: OfficialEvidenceCollectionResult,
) -> OfficialEvidencePreflight:
    """수집 관측과 작성 허용을 분리하고 확보 근거로 부분 작성을 잇는다.

    실패·잘림·의미 칸 부족은 원래 decision과 진단에 남긴다. 부분 작성은
    완료 증명이 아니며 각 문장의 회사·원문·인용 검증은 그대로 적용한다.
    부분 보고서 전환(출고 차단)은 ``_blocks_release``가 고른 필수 경로 미완료만
    센다. 선택 경로 미완료는 관측(``collection_incomplete``)과 진단
    (``optional_incomplete_attempts``)에만 남는다.
    """
    candidates_by_id = {
        candidate.section_id: candidate for candidate in result.candidates
    }
    bundles = tuple(
        build_section_bundle(
            candidates_by_id[section_id],
            required_slot_ids=collector_slots_for(section_id),
        )
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    )
    decision = assess_generation_gate(
        company_id=result.company_id,
        bundles=bundles,
        required_section_ids=REQUIRED_EVIDENCE_SECTION_IDS,
    )
    integrity_is_broken = any(
        reason_code.startswith(_INTERNAL_INTEGRITY_REASON_PREFIXES)
        for candidate in result.candidates
        for reason_code in candidate.reason_codes
    )
    incomplete_attempts = tuple(
        attempt
        for candidate in result.candidates
        for attempt in candidate.attempts
        if attempt.state in _INCOMPLETE_COLLECTION_STATES
    )
    # 관측(incomplete_attempts)과 출고 차단(blocking_attempts)을 나눈다. 선택
    # 경로의 상한 잘림·실패까지 차단으로 세면 필수 근거가 충분해도 FULL에 못
    # 간다(2026-09-22 상장사 평가 3회는 선택 경로의 쪽 수 상한 잘림 2건과 IR
    # 실패 1건만으로 전부 «웹 일시 장애» 전환으로 기록됐다).
    blocking_attempts = tuple(
        attempt for attempt in incomplete_attempts if _blocks_release(attempt)
    )
    required_dart_collection_incomplete = any(
        attempt.source_kind in _DART_DOCUMENT_SOURCE_KINDS
        for attempt in blocking_attempts
    )
    full_document_floor_unreachable = (
        result.independent_document_count + LATE_PACKET_DOCUMENT_SOURCES
        < MIN_DOCUMENT_SOURCES
    )
    partial_required = (
        bool(blocking_attempts)
        or not decision.can_call_ai
        or full_document_floor_unreachable
    )
    partial_reason = ""
    detail_code = ""
    if integrity_is_broken:
        detail_code = FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID
    elif required_dart_collection_incomplete:
        partial_reason = DART_PARTIAL_REASON_REQUIRED_COLLECTION_INCOMPLETE
        detail_code = FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INCOMPLETE
    elif blocking_attempts:
        partial_reason = DART_PARTIAL_REASON_TRANSIENT_WEB_FAILURE
        detail_code = FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INCOMPLETE
    elif not decision.can_call_ai:
        partial_reason = DART_PARTIAL_REASON_INSUFFICIENT_WITH_READY_SECTIONS
        detail_code = (
            FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP
            if result.unclassified_evidence is not None
            else FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT
        )
    elif full_document_floor_unreachable:
        partial_reason = DART_PARTIAL_REASON_TOO_FEW_DOCUMENTS_FOR_FULL
        detail_code = FINAL_GATE_DETAIL_PREFLIGHT_DOCUMENT_SOURCES_INSUFFICIENT
    return OfficialEvidencePreflight(
        decision=decision,
        independent_document_count=result.independent_document_count,
        detail_code=detail_code,
        dart_partial_fallback=partial_required and not integrity_is_broken,
        dart_partial_reason=partial_reason,
        # 공식 원문이 있으면 보완조사를 이어갈 수 있다. 보완조사 실패도
        # 확보한 사실을 지우거나 미확인 범위를 완료로 바꾸지 않는다.
        supplementary_research_allowed=(
            not integrity_is_broken
            and decision.status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE
            and _has_usable_dart_evidence(result)
        ),
        collection_incomplete=bool(incomplete_attempts),
        release_blocking_incomplete=bool(blocking_attempts),
        optional_incomplete_attempts=_nonblocking_observations(
            incomplete_attempts, requirement=SourceRequirement.OPTIONAL
        ),
        design_cap_exempt_attempts=_nonblocking_observations(
            incomplete_attempts, requirement=SourceRequirement.REQUIRED
        ),
    )


def _nonblocking_observations(
    incomplete_attempts: tuple[CollectionAttempt, ...],
    *,
    requirement: SourceRequirement,
) -> tuple[NonblockingIncompleteAttempt, ...]:
    """출고를 막지 않은 미완료를 요구 수준별로, 실제 시도 하나당 한 번만 남긴다.

    수집 생산부는 한 시도를 의미 칸이 겹치는 모든 장에 같은 식별자로 복제한다.
    그대로 세면 광역 잘림 한 건이 아홉 건으로 부풀어 진단이 거짓말을 한다.
    비차단 판단은 ``_blocks_release`` 하나만 본다 — OPTIONAL은 늘 비차단이고,
    REQUIRED는 설계 상한 예외에 든 잘림만 여기 남는다.
    """

    seen_attempt_ids: set[str] = set()
    observations: list[NonblockingIncompleteAttempt] = []
    for attempt in incomplete_attempts:
        if attempt.requirement is not requirement or _blocks_release(attempt):
            continue
        if attempt.attempt_id in seen_attempt_ids:
            continue
        seen_attempt_ids.add(attempt.attempt_id)
        observations.append(
            NonblockingIncompleteAttempt(
                source_kind=attempt.source_kind,
                state=attempt.state,
                reason_code=attempt.reason_code,
            )
        )
    return tuple(observations)


def assess_packet_document_sources(
    packets: SectionEvidencePacketSet,
) -> PacketDocumentSourcePreflight:
    """formal·legacy·구조화 표를 합친 실제 작성 후보의 문서 수를 센다.

    formal collector 직후에는 재무 API와 매출표 전용 조각이 아직 합쳐지지
    않는다. 그때 8건을 검사하면 최종 후보가 충족할 수 있는 회사도 일찍
    거절한다. 최종 packet도 8건보다 적으면 확보 근거를 사용하는 부분
    보고서로 전환한다. 문서 수가 회사·인용 검증을 대신하지 않는다.
    """

    hashes_by_identity: dict[str, set[str]] = {}
    for packet in packets.packets:
        for fragment in packet.fragments:
            if fragment.counts_toward_document_floor is False:
                continue
            identity = fragment.document_identity.strip()
            if not identity:
                continue
            hashes = hashes_by_identity.setdefault(identity, set())
            if fragment.document_content_sha256:
                hashes.add(fragment.document_content_sha256)

    # 하나의 안정 문서 신원이 서로 다른 원문 hash를 가리키면 snapshot/packet
    # 배선 오류다. 자료 부족으로 오표기하지 않고 내부 계약 오류로 닫는다.
    if any(len(hashes) > 1 for hashes in hashes_by_identity.values()):
        return PacketDocumentSourcePreflight(
            independent_document_count=0,
            detail_code=FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID,
        )

    typed_content_hashes = {
        next(iter(hashes))
        for hashes in hashes_by_identity.values()
        if hashes
    }
    # legacy 모양에는 문서 전체 hash가 없다. DART 접수번호·재무 API처럼
    # 발행 시스템의 안정 document ID로 검증되는 신원만 별도 한 건으로 센다.
    # URL만 있는 legacy 웹 조각은 같은 원문을 주소만 바꿔 복제해도 알아낼
    # 정보가 없으므로 최소 문서 수를 늘리는 데 쓰지 않는다. 공식 웹은 formal
    # typed 경로의 content hash가 있을 때만 위 집합으로 집계된다.
    stable_legacy_document_identities = {
        identity
        for identity, hashes in hashes_by_identity.items()
        if not hashes and _is_stable_legacy_document_identity(identity)
    }
    count = len(typed_content_hashes) + len(stable_legacy_document_identities)
    return PacketDocumentSourcePreflight(
        independent_document_count=count,
        detail_code=(
            ""
            if count >= MIN_DOCUMENT_SOURCES
            else FINAL_GATE_DETAIL_PREFLIGHT_DOCUMENT_SOURCES_INSUFFICIENT
        ),
    )

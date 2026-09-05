"""FULL 생성 전에 공식 근거의 의미 칸과 독립 문서 수를 판정한다.

수집기는 후보를 만들고, 이 모듈은 그 후보가 유료 작성에 들어가도 되는지만
판정한다. 작가가 채울 수 없는 구조화 칸(3개년 실적·독립 비교)은 각 전용
검증기가 맡으므로 여기서는 수집기가 책임지는 칸만 본다.
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
from src.features.report_standard.constants import (
    MINIMUM_PUBLISHABLE_SECTION_COUNT,
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
    GenerationGateStatus,
    OFFICIAL_WEB_SOURCE_KINDS,
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_QUARTERLY_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
    SourceRequirement,
    SOURCE_KIND_ROBOTS_TXT,
)
from src.shared.report_evidence.logic import assess_generation_gate, build_section_bundle
from src.shared.report_evidence.models import GenerationGateDecision
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
# 부분 보고서는 실제 DART 원문 조각이 하나라도 결속된 경우에만 허용한다.
# 빠진 장의 최종 사용 가능 여부는 뒤쪽 composer 품질 검사가 다시 판정한다.
_WEB_FAILURE_SOURCE_KINDS = frozenset(
    {*OFFICIAL_WEB_SOURCE_KINDS, SOURCE_KIND_ROBOTS_TXT}
)
_WEB_IDENTITY_REJECTION_REASON_CODES = frozenset(
    {"root_identity_mismatch", "cross_domain_identity_mismatch"}
)
# 부분 보고서로 전환한 갈래를 진단에 남기는 닫힌 두 값이다. 회사·URL·원문을
# 담지 않으므로 steps 로그에 그대로 실어도 된다.
DART_PARTIAL_REASON_TRANSIENT_WEB_FAILURE = "transient_web_failure"
DART_PARTIAL_REASON_INSUFFICIENT_WITH_READY_SECTIONS = (
    "insufficient_with_ready_sections"
)


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


def _identity_rejected_web_evidence_is_bound(
    result: OfficialEvidenceCollectionResult,
) -> bool:
    """신원 대조에 실패한 웹 시도가 실제 웹 자료를 남긴 장이 있는지 본다.

    ``wide_collect``의 신원 대조 실패 경로는 fail-closed다. 두 사유를 붙이는
    자리(``wide_collect.py:1084-1091``)가 속한 ``_collect_identity_verified_
    candidate``에서 문서를 만드는 블록은 ``match is not None`` 안에만 있어서
    (``wide_collect.py:1092``·append는 1165), 신원 대조에 실패한 시도는 MISSING
    attempt만 남기고 documents를 하나도 내보내지 않는다. 우리은행 실측도 같았다
    — 수집 문서 12건이 전부 DART였고 불일치 시도가 남긴 문서·조각은 0건이다.

    그렇다고 «불일치가 있으면 웹 문서가 없다»는 아니다. 신원이 확인돼 결속된
    host는 다른 함수(``_visit_page``의 1424, ``_run_ir_pdf_phase``의 1753)에서
    계속 문서를 만든다. 그래서 전역이 아니라 장 단위로 본다 — 불일치 시도가
    붙은 그 장에 웹 종류의 문서가 실제로 결속돼 있을 때만 막는다. 그때는 어느
    호스트에서 온 문장인지 이 계층이 구분할 수 없기 때문이다.
    """

    for candidate in result.candidates:
        if not any(
            attempt.reason_code in _WEB_IDENTITY_REJECTION_REASON_CODES
            for attempt in candidate.attempts
        ):
            continue
        if any(
            document.source_kind in OFFICIAL_WEB_SOURCE_KINDS
            for document in candidate.documents
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


@dataclass(frozen=True)
class OfficialEvidencePreflight:
    """AI 호출 가능 여부와 원문 없는 닫힌 사유 코드."""

    decision: GenerationGateDecision
    independent_document_count: int
    detail_code: str = ""
    dart_partial_fallback: bool = False
    # 어느 갈래로 부분 보고서 전환을 허용했는지 남긴다. 값은
    # ``DART_PARTIAL_REASON_*`` 두 개뿐이고, 전환이 없으면 빈 문자열이다.
    dart_partial_reason: str = ""

    @property
    def can_call_ai(self) -> bool:
        return self.dart_partial_fallback or (
            not self.detail_code and self.decision.can_call_ai
        )


@dataclass(frozen=True)
class PacketDocumentSourcePreflight:
    """실제 9장 packet 전체가 가진 독립 문서 하한 판정."""

    independent_document_count: int
    detail_code: str = ""

    @property
    def can_call_ai(self) -> bool:
        return not self.detail_code


def assess_official_evidence(
    result: OfficialEvidenceCollectionResult,
) -> OfficialEvidencePreflight:
    """formal 수집기의 아홉 장 의미 칸 준비 상태를 검사한다.

    일시 장애가 하나라도 있으면 ``transient``로 남긴다. 독립 문서 하한은
    재무 API·매출표까지 실제 packet에 합친 뒤 ``assess_packet_document_sources``
    가 검사한다. 여기서 formal 문서만 세어 미리 막으면 정상 후보도 과소평가한다.

    아홉 장을 다 채우지 못했더라도 DART 근거가 결속돼 있으면 두 갈래로
    부분 보고서(SHADOW)를 허용한다 — 웹 경로가 막힌 경우(갈래 1)와, 확인은
    끝냈지만 일부 장의 자료가 없는 경우(갈래 2)다. 어느 갈래로 열렸는지는
    ``dart_partial_reason``에 남는다.
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
    # build_section_bundle은 이미 다른 공식 문서가 채운 슬롯에 대해서는
    # attempt를 보지 않는다. 그 규칙만 쓰면 DART 필수 경로가 TypeError나
    # 일시 장애로 실패해도 홈페이지 조각이 같은 슬롯을 채웠다는 이유로
    # READY가 된다. DART 원문을 실제로 확인하지 못한 상태를 "확인 완료"로
    # 바꾸지 않도록, formal 수집 전체에서 REQUIRED DART 실패를 별도로 본다.
    required_dart_collection_incomplete = any(
        attempt.source_kind in _DART_DOCUMENT_SOURCE_KINDS
        and attempt.requirement is SourceRequirement.REQUIRED
        and attempt.state in _INCOMPLETE_COLLECTION_STATES
        for candidate in result.candidates
        for attempt in candidate.attempts
    )
    web_identity_was_rejected = any(
        attempt.reason_code in _WEB_IDENTITY_REJECTION_REASON_CODES
        for candidate in result.candidates
        for attempt in candidate.attempts
    )
    incomplete_attempts = tuple(
        attempt
        for candidate in result.candidates
        for attempt in candidate.attempts
        if attempt.state in _INCOMPLETE_COLLECTION_STATES
    )
    # 두 갈래 모두 «내부 배선은 멀쩡하고, 필수 DART 확인이 끝났고, 실제 DART
    # 원문 조각이 결속돼 있다»를 전제로 한다. 이 셋 중 하나라도 깨지면 어떤
    # 부분 보고서도 만들지 않는다.
    dart_partial_prerequisites_hold = (
        not integrity_is_broken
        and not required_dart_collection_incomplete
        and _has_usable_dart_evidence(result)
    )
    # (갈래 1) 회사 웹 경로가 «막혀서» 확인을 끝내지 못한 경우. 하이브 실측이
    # 이 모양이었다 — robots.txt 거부로 FAILED가 나 STOP_TRANSIENT_FAILURE로
    # 닫혔고, DART 근거로 SHADOW 부분 보고서가 정상 생성됐다.
    transient_partial_fallback = (
        dart_partial_prerequisites_hold
        and not web_identity_was_rejected
        and decision.status is GenerationGateStatus.STOP_TRANSIENT_FAILURE
        and bool(incomplete_attempts)
        and all(
            attempt.source_kind in _WEB_FAILURE_SOURCE_KINDS
            for attempt in incomplete_attempts
        )
    )
    # (갈래 2) 확인은 끝냈는데 일부 장의 자료가 없는 경우. 우리은행 실측이
    # 이 모양이었다 — DART ir_url이 비어 공식 웹 후보가 없었고 남은 웹 경로는
    # 전부 신원 대조에 실패해 MISSING으로 닫혔다. 아홉 장 중 일곱 장이 READY
    # 인데도 갈래 1의 조건(FAILED/TRUNCATED)에 걸리지 않아 보고서가 0건
    # 나왔다. 공개 가능한 최소 장 수를 넘겼다면 그 일곱 장은 실제로 확인된
    # 자료이므로, FULL이 아니라 부분 보고서로 내보낸다. 하한은 부분 보고서
    # 출고 계약과 같은 정본(MINIMUM_PUBLISHABLE_SECTION_COUNT)을 쓴다.
    insufficient_partial_fallback = (
        dart_partial_prerequisites_hold
        and not _identity_rejected_web_evidence_is_bound(result)
        and decision.status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE
        and len(decision.ready_section_ids) >= MINIMUM_PUBLISHABLE_SECTION_COUNT
    )
    # 게이트 판정은 한 상태만 갖는다 — 두 갈래는 동시에 참이 될 수 없다.
    dart_partial_fallback = transient_partial_fallback or insufficient_partial_fallback
    dart_partial_reason = (
        DART_PARTIAL_REASON_TRANSIENT_WEB_FAILURE
        if transient_partial_fallback
        else (
            DART_PARTIAL_REASON_INSUFFICIENT_WITH_READY_SECTIONS
            if insufficient_partial_fallback
            else ""
        )
    )
    if integrity_is_broken:
        detail_code = FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID
    elif required_dart_collection_incomplete:
        detail_code = FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_TRANSIENT
    elif dart_partial_fallback:
        detail_code = ""
    elif decision.status is GenerationGateStatus.STOP_TRANSIENT_FAILURE:
        detail_code = FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_TRANSIENT
    elif decision.status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE:
        # 현재 결정론 분류기의 단어 목록은 일부 예시에 불과하고 «해당 뜻이
        # 원문에 없다»를 증명하는 완전한 분류기가 아니다. 실제 원문 조각을
        # 읽었지만 의미 칸을 붙이지 못한 관측이 하나라도 있으면 회사를
        # 자료 부족으로 탓할 수 없다. 무분류 조각은 근거로 승격하지 않은 채
        # 내부 분류 범위 결함으로 닫아 선결제·AI 호출 전에 운영자에게 보낸다.
        #
        # 여기까지 왔다는 것은 갈래 2가 거짓이라는 뜻이다 — 즉 DART 근거가
        # 결속되지 않았거나, READY 장이 공개 최소치보다 적거나, 신원 대조에
        # 실패한 웹 자료가 실제로 섞여 있다. 그때만 이 두 사유로 닫는다.
        detail_code = (
            FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP
            if result.unclassified_evidence is not None
            else FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT
        )
    else:
        detail_code = ""

    return OfficialEvidencePreflight(
        decision=decision,
        independent_document_count=result.independent_document_count,
        detail_code=detail_code,
        dart_partial_fallback=dart_partial_fallback,
        dart_partial_reason=dart_partial_reason,
    )


def assess_packet_document_sources(
    packets: SectionEvidencePacketSet,
) -> PacketDocumentSourcePreflight:
    """formal·legacy·구조화 표를 합친 실제 작성 후보의 문서 수를 센다.

    formal collector 직후에는 재무 API와 매출표 전용 조각이 아직 합쳐지지
    않는다. 그때 8건을 검사하면 최종 후보가 충족할 수 있는 회사도 일찍
    거절한다. 반대로 packet까지 합친 뒤에도 8건보다 적으면 작성기가 어떤
    문장을 골라도 최종 품질 하한을 채울 수 없으므로 AI 전에 안전하게 멈춘다.
    """

    hashes_by_identity: dict[str, set[str]] = {}
    for packet in packets.packets:
        for fragment in packet.fragments:
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

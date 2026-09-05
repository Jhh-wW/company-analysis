"""원문·내부 검증 문구 없이 파이프라인의 최종 게이트를 분류한다."""

from __future__ import annotations

from typing import Final, Iterable

from src.shared.report_quality.models import QualityProblemCode


FINAL_GATE_REASON_COMPARISON_BLOCKED: Final[str] = "comparison_blocked"
FINAL_GATE_REASON_PUBLISH_BLOCKED: Final[str] = "publish_blocked"
#: STRICT 품질 게이트가 사실 수·문서 수·검증 비율 또는 해석 문장 천장
#: 미달로 막았을 때만 붙는 사유.
#: 나머지 v2 출고 검증 실패(구조·안전 결속 등)는 여전히
#: FINAL_GATE_REASON_PUBLISH_BLOCKED다 — 이 코드는 그 뭉뚱그림 중
#: «품질 하한» 한 갈래만 떼어낸 것이다.
FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR: Final[str] = (
    "publish_blocked_quality_floor"
)
FINAL_GATE_REASON_MISSING_IDENTITY: Final[str] = "publish_missing_identity"
FINAL_GATE_REASON_MISSING_REVENUE: Final[str] = "publish_missing_revenue"
FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE: Final[str] = (
    "publish_missing_identity_revenue"
)
#: 이 조사 하나에 미리 잡아 둔 AI 예약액을 다 써서 필수 단계(장 작성)를
#: 이어가지 못했을 때 붙는 사유. 회사 자료 부족도, 출고 검증 실패도 아니라
#: 운영 한도 문제이므로 다른 사유와 뭉뚱그리지 않는다 — 사유가 없으면 화면이
#: 「보고서를 만들다 오류가 났습니다」로만 끝나 원인을 아무도 모른다(실측).
FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED: Final[str] = "request_budget_exhausted"
#: 본조사 provider를 한 번도 부르기 전에 하루 비용 한도의 예약이 거절된
#: 경우다. 실행 도중 요청 몫을 다 쓴 위 사유와 구분해야 사용자가 같은
#: 조사를 곧바로 반복하지 않고 자정 이후에 다시 시작할 수 있다.
FINAL_GATE_REASON_START_BUDGET_RESERVATION_DENIED: Final[str] = (
    "start_budget_reservation_denied"
)
FINAL_GATE_REASON_OTHER_GATE: Final[str] = "other_gate"
#: 공식 근거 사전 게이트(collector 재생 조각9, 이번 조각 범위 밖)가 붙이는
#: 두 사유. 여기 shared에 미리 두어 조각9가 real.py에 배선할 때 사유코드를
#: 임시 문자열로 짓지 않고 이 권위만 참조하게 한다.
FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT: Final[str] = (
    "official_evidence_insufficient"
)
FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT: Final[str] = (
    "official_evidence_transient"
)
FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION: Final[str] = (
    "official_evidence_configuration"
)
FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT: Final[str] = (
    "internal_evidence_contract"
)
FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED: Final[str] = (
    "evidence_classification_undetermined"
)
FINAL_GATE_DIAGNOSTIC_TABLE: Final[str] = "pipeline_final_gate_diagnostics"
FINAL_GATE_DIAGNOSTIC_SCHEMA_VERSION: Final[int] = 1
FINAL_GATE_DIAGNOSTIC_COLUMNS: Final[frozenset[str]] = frozenset(
    {"run_id", "schema_version", "reason_code", "recorded_at"}
)
SAFE_FINAL_GATE_REASONS: Final[frozenset[str]] = frozenset(
    {
        FINAL_GATE_REASON_COMPARISON_BLOCKED,
        FINAL_GATE_REASON_MISSING_IDENTITY,
        FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE,
        FINAL_GATE_REASON_MISSING_REVENUE,
        FINAL_GATE_REASON_PUBLISH_BLOCKED,
        FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
        FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
        FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT,
        FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION,
        FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
        FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED,
        FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED,
        FINAL_GATE_REASON_START_BUDGET_RESERVATION_DENIED,
        FINAL_GATE_REASON_OTHER_GATE,
    }
)

# V2ValidationError는 사람용 ``problems``와 별도로 아래 닫힌 기계 코드만
# ``problem_codes``로 운반한다. report_recovery namespace는 오류 발생 위치를
# 나타낼 뿐 분류 의미를 바꾸지 않으므로, 분류 직전에 정확히 한 번 벗긴다.
_REPORT_RECOVERY_PREFIX: Final[str] = "report_recovery:"

FINAL_GATE_DETAIL_PREFLIGHT_DOCUMENT_SOURCES_INSUFFICIENT: Final[str] = (
    "preflight_document_sources_insufficient"
)
FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT: Final[str] = (
    "preflight_official_evidence_insufficient"
)
FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_TRANSIENT: Final[str] = (
    "preflight_official_evidence_transient"
)
FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_CONFIGURATION: Final[str] = (
    "preflight_official_evidence_configuration"
)
FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP: Final[str] = (
    "preflight_classifier_coverage_gap"
)
FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID: Final[str] = "preflight_packet_invalid"
FINAL_GATE_DETAIL_PREFLIGHT_TABLE_CITE_INVALID: Final[str] = (
    "preflight_table_cite_invalid"
)
FINAL_GATE_DETAIL_PREFLIGHT_TABLE_EVIDENCE_INVALID_LEGACY: Final[str] = (
    "preflight_table_evidence_invalid"
)
FINAL_GATE_DETAIL_PREFLIGHT_UNREGISTERED_FRAGMENT_KIND: Final[str] = (
    "preflight_unregistered_fragment_kind"
)
FINAL_GATE_DETAIL_EVIDENCE_TRANSPORT_INVALID: Final[str] = (
    "evidence_transport_invalid"
)
FINAL_GATE_DETAIL_EVIDENCE_MANIFEST_BINDING_INVALID: Final[str] = (
    "evidence_manifest_binding_invalid"
)
FINAL_GATE_DETAIL_PUBLIC_MANIFEST_BINDING_INVALID: Final[str] = (
    "public_manifest_binding_invalid"
)

# 회사의 공식 자료를 정상적으로 모두 확인했지만 독립 문서 하한 자체가
# 불가능한 경우다. 일반 ``too_few_document_sources``는 생성 뒤 품질 하한이므로
# 여기에 넣지 않는다.
OFFICIAL_EVIDENCE_INSUFFICIENT_PROBLEM_CODES: Final[frozenset[str]] = frozenset(
    {
        FINAL_GATE_DETAIL_PREFLIGHT_DOCUMENT_SOURCES_INSUFFICIENT,
        FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT,
    }
)

# 공식 자료가 적다는 결론을 내린 것이 아니라, 일시적인 접속·응답 문제로
# 충분성을 확정하지 못한 경우다. 수집 경계는 아래 닫힌 코드만
# 넘기고 provider 예외문·URL·응답 원문은 넘기지 않는다.
OFFICIAL_EVIDENCE_TRANSIENT_PROBLEM_CODES: Final[frozenset[str]] = frozenset(
    {FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_TRANSIENT}
)

# DART 인증키·권한처럼 같은 입력으로 재시도해도 회복되지 않고 운영 설정을
# 고쳐야 하는 경우다. 사용자 입력·회사 자료 문제로 표시하지 않는다.
OFFICIAL_EVIDENCE_CONFIGURATION_PROBLEM_CODES: Final[frozenset[str]] = frozenset(
    {FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_CONFIGURATION}
)

# 공식 원문을 실제로 읽었지만 현재 결정론 분류기가 필요한 의미 칸을
# 확인하지 못한 상태다. 그 내용이 정말 없었는지, 표현을 못 알아본 것인지는
# 이 관측만으로 확정할 수 없으므로 회사 자료 부족이나 내부 오류 어느 쪽으로도
# 단정하지 않는다.
EVIDENCE_CLASSIFICATION_UNDETERMINED_PROBLEM_CODES: Final[frozenset[str]] = (
    frozenset({FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP})
)

# 사용자의 회사나 자료 탓이 아니라 우리 코드가 근거를 장·표·manifest로
# 운반하고 결속하는 계약을 어긴 경우다. 문자열 포함 검사로 분류하면 원문
# 예외가 우연히 같은 낱말을 포함할 수 있으므로 승인된 코드의 정확 일치만
# 허용한다.
INTERNAL_EVIDENCE_CONTRACT_PROBLEM_CODES: Final[frozenset[str]] = frozenset(
    {
        FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID,
        FINAL_GATE_DETAIL_PREFLIGHT_TABLE_CITE_INVALID,
        # 이미 배포된 composer가 내는 정확한 옛 코드. 새 생산자는 위 cite
        # 코드만 사용하되, 배포 중 실행의 진단을 publish_blocked로 잃지 않는다.
        FINAL_GATE_DETAIL_PREFLIGHT_TABLE_EVIDENCE_INVALID_LEGACY,
        FINAL_GATE_DETAIL_PREFLIGHT_UNREGISTERED_FRAGMENT_KIND,
        FINAL_GATE_DETAIL_EVIDENCE_TRANSPORT_INVALID,
        FINAL_GATE_DETAIL_EVIDENCE_MANIFEST_BINDING_INVALID,
        FINAL_GATE_DETAIL_PUBLIC_MANIFEST_BINDING_INVALID,
    }
)

#: STRICT 품질 게이트가 «품질 하한 미달»로 판정할 때 quality_problem_codes에
#: 실리는 닫힌 코드. V2ValidationError.problem_codes에 이 중 하나라도 있어야
#: FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR로 구분한다 — 승인된 50%
#: 검증 비율(low_verified_ratio)도 40건·8건과 같은 «수치 품질 하한»이다.
#: 이 목록 밖의 품질 코드(구조 코드 4개: too_many_notice_only_sections ·
#: one_claim_sections · low_semantic_coverage · low_public_sentence_coverage)와
#: 구조·안전 결속 오류는 기존 FINAL_GATE_REASON_PUBLISH_BLOCKED를 그대로
#: 유지한다.
QUALITY_FLOOR_PROBLEM_CODES: Final[frozenset[str]] = frozenset(
    {
        QualityProblemCode.TOO_FEW_SUBSTANTIVE_CLAIMS.value,
        QualityProblemCode.TOO_FEW_DOCUMENT_SOURCES.value,
        QualityProblemCode.LOW_VERIFIED_RATIO.value,
        QualityProblemCode.TOO_MANY_INTERPRETATION_CLAIMS_PER_SECTION.value,
        QualityProblemCode.EXCESSIVE_INTERPRETATION_CLAIMS.value,
        QualityProblemCode.MISSING_REQUIRED_PUBLIC_CLAIM_SLOTS.value,
    }
)


def classify_v2_validation_final_gate_reason(
    problem_codes: Iterable[str],
) -> str:
    """v2 출고 검증 실패(``V2ValidationError``)의 최종 게이트 사유를 고른다.

    ``pipeline.real``과 (앞으로 붙을) shadow 진단 하네스가 이 분류를
    하나의 권위로 공유하도록 여기(shared)에 순수 함수로 둔다 — 부작용이
    없고, 입력(문제 코드 목록) 밖의 어떤 상태도 읽거나 쓰지 않는다.

    공식 자료 자체 부족·일시 확인 실패·분류 불확정·내부 근거 계약 오류·
    생성 뒤 품질 하한만 닫힌 코드의 정확 일치로 분리한다. 다른 품질 코드·
    미등록 안전 오류·원문 예외 또는 빈 입력은 기존 ``publish_blocked``를 유지한다.
    """

    normalized_codes = {
        normalized.removeprefix(_REPORT_RECOVERY_PREFIX)
        for code in problem_codes
        if type(code) is str and (normalized := code.strip())
    }

    # 내부 배선 결함이 섞였는데 회사 자료 부족으로 기록하면 운영자가 정상
    # 회사를 탓하게 된다. 따라서 내부 계약 > 일시 실패 > 분류 불확정 >
    # 실제 자료 부족 > 생성 뒤 품질 하한 순으로 더 구체적이고 복구 가능한
    # 사유를 우선한다. 설정 오류는 내부 배선보다는 구체적이지만, 다른 내부
    # 결속 오류가 함께 있으면 후자를 먼저 수리해야 하므로 그 다음에 둔다.
    if normalized_codes & INTERNAL_EVIDENCE_CONTRACT_PROBLEM_CODES:
        return FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
    if normalized_codes & OFFICIAL_EVIDENCE_CONFIGURATION_PROBLEM_CODES:
        return FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION
    if normalized_codes & OFFICIAL_EVIDENCE_TRANSIENT_PROBLEM_CODES:
        return FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT
    if normalized_codes & EVIDENCE_CLASSIFICATION_UNDETERMINED_PROBLEM_CODES:
        return FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED
    if normalized_codes & OFFICIAL_EVIDENCE_INSUFFICIENT_PROBLEM_CODES:
        return FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT
    if normalized_codes & QUALITY_FLOOR_PROBLEM_CODES:
        return FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR
    return FINAL_GATE_REASON_PUBLISH_BLOCKED

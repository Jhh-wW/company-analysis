"""원문·내부 검증 문구 없이 파이프라인의 최종 게이트를 분류한다."""

from __future__ import annotations

from typing import Final, Iterable

from src.shared.report_quality.models import QualityProblemCode


FINAL_GATE_REASON_COMPARISON_BLOCKED: Final[str] = "comparison_blocked"
FINAL_GATE_REASON_PUBLISH_BLOCKED: Final[str] = "publish_blocked"
#: v2 STRICT 품질 게이트가 «품질 하한 미달»(too_few_substantive_claims ·
#: too_few_document_sources · low_verified_ratio)로 막았을 때만 붙는 사유.
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
        FINAL_GATE_REASON_OTHER_GATE,
    }
)

#: v2 STRICT 품질 게이트가 «품질 하한 미달»로 판정할 때 quality_problem_codes에
#: 실리는 세 코드. V2ValidationError.problem_codes에 이 중 하나라도 있어야
#: FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR로 구분한다 — 승인된 50%
#: 검증 비율(low_verified_ratio)도 40건·8건과 같은 «수치 품질 하한»이다.
#: 이 세 개 밖의 품질 코드(구조 코드 4개: too_many_notice_only_sections ·
#: one_claim_sections · low_semantic_coverage · low_public_sentence_coverage)와
#: 구조·안전 결속 오류는 기존 FINAL_GATE_REASON_PUBLISH_BLOCKED를 그대로
#: 유지한다.
QUALITY_FLOOR_PROBLEM_CODES: Final[frozenset[str]] = frozenset(
    {
        QualityProblemCode.TOO_FEW_SUBSTANTIVE_CLAIMS.value,
        QualityProblemCode.TOO_FEW_DOCUMENT_SOURCES.value,
        QualityProblemCode.LOW_VERIFIED_RATIO.value,
    }
)


def classify_v2_validation_final_gate_reason(
    problem_codes: Iterable[str],
) -> str:
    """v2 출고 검증 실패(``V2ValidationError``)의 최종 게이트 사유를 고른다.

    ``pipeline.real``과 (앞으로 붙을) shadow 진단 하네스가 이 분류를
    하나의 권위로 공유하도록 여기(shared)에 순수 함수로 둔다 — 부작용이
    없고, 입력(문제 코드 목록) 밖의 어떤 상태도 읽거나 쓰지 않는다.

    모든 ``V2ValidationError``를 하나로 뭉뚱그리지 않는다 — «품질 하한
    미달» 세 코드(``QUALITY_FLOOR_PROBLEM_CODES``) 중 하나라도 있을 때만
    새 사유로 떼어내고, 그 외(다른 품질 코드·구조 결속·생산 증거 등 안전
    오류, 혹은 ``problem_codes``가 비어 있는 기존 호출자)는 기존
    ``publish_blocked``를 유지한다.
    """

    if set(problem_codes) & QUALITY_FLOOR_PROBLEM_CODES:
        return FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR
    return FINAL_GATE_REASON_PUBLISH_BLOCKED

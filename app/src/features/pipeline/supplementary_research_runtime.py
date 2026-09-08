"""보완조사로 만든 결과를 저장·재사용 전에 최종 검사한다."""

from dataclasses import replace

from src.core.source_verification_adapter import supplementary_research_source_verifier
from src.features.pipeline.port import Outcome, RunResult
from src.features.pipeline.supplementary_research_runtime_constants import (
    SUPPLEMENTARY_RESEARCH_INVALID_RESULT_CODES,
    SUPPLEMENTARY_RESEARCH_RELEASE_STEP,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
    FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult


def enforce_supplementary_research_release(
    result: RunResult,
    *,
    official_evidence: OfficialEvidenceCollectionResult | None,
    steps: list[dict],
) -> RunResult:
    """생성 실패는 그대로 두고, 실제 보고서 후보만 출고 권한과 분리한다."""

    if result.outcome is not Outcome.REPORT:
        return result
    from src.features.pipeline.supplementary_research_release import (
        assess_supplementary_research_release,
    )

    decision = assess_supplementary_research_release(
        result.report,
        official_evidence=official_evidence,
        source_verifier=supplementary_research_source_verifier(),
    )
    steps.append({
        "step": SUPPLEMENTARY_RESEARCH_RELEASE_STEP,
        "허용": decision.allowed,
        "사유코드": decision.code,
        "검증본문장": list(decision.qualified_section_ids),
    })
    if decision.allowed:
        return result
    gate_reason = (
        FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
        if decision.code in SUPPLEMENTARY_RESEARCH_INVALID_RESULT_CODES
        else FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR
    )
    return replace(
        result,
        outcome=Outcome.GATE_STOPPED,
        report=None,
        message=(
            "추가 조사한 내용도 보고서의 기본 근거와 공개 기준을 충족하지 못해 "
            "내보내지 않았습니다. 확인 범위와 제한은 조사 기록에 남겼습니다."
        ),
        charged=False,
        final_gate_reason=gate_reason,
        cache_hit="",
        generation_cache_eligible=False,
        generation_evidence=None,
        reused_content_snapshot_id="",
        reused_artifact_id="",
    )

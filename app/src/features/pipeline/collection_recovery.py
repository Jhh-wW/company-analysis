"""수집 실패 관측과 이미 검증한 자료를 함께 다음 단계로 전달한다."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import CancelledError
from dataclasses import replace
from typing import Any

from src.features.pipeline.constants import (
    COLLECTION_RECOVERY_STEP,
    EMPTY_COLLECTION_CANDIDATE_BUDGET,
    PARTIAL_GENERATION_IDENTITY_VERSION,
)
from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SourceRequirement,
)
from src.shared.report_evidence.models import ChapterEvidenceCandidates, CollectionAttempt
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS, collector_slots_for
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.engine_build_identity import EngineBuildIdentityChangedError
from src.shared.generation_coordination import GenerationCoordinationError


def raise_if_request_interrupted(error: BaseException | None) -> None:
    """자료원 장애와 요청 취소·소유권 상실을 분리해 전역 중단을 전파한다."""
    if isinstance(error, (GenerationCoordinationError, EngineBuildIdentityChangedError, CancelledError)):
        raise error


def record_collection_failure(
    steps: list[dict[str, Any]], *, source: str, reason: str, error: Exception | None = None,
) -> None:
    """응답 원문을 노출하지 않고 실패한 경계만 기록한다."""
    raise_if_request_interrupted(error)
    steps.append({
        "step": COLLECTION_RECOVERY_STEP,
        "자료원": source,
        "상태": "FAILED",
        "사유코드": reason,
        "오류종류": type(error).__name__ if error is not None else "",
        "안내": "확인을 완료하지 못한 범위는 미확인으로 남기고 확보 자료로 계속합니다",
    })


def retain_official_collection(
    company_id: str,
    previous: OfficialEvidenceCollectionResult | None,
    *,
    reason_code: str,
) -> OfficialEvidenceCollectionResult:
    """마지막 정상 수집 결과를 보존하고 전체 경계의 실패 관측을 덧붙인다.

    유효한 반환값을 받지 못했으면 실패 시도만 만든다. 실패 응답 안의 임의
    본문이나 미검증 회사·문서를 정상 근거로 복구하지 않는다.
    """
    candidates = []
    for index, section_id in enumerate(REQUIRED_EVIDENCE_SECTION_IDS):
        attempt = CollectionAttempt(
            company_id=company_id,
            attempt_id=f"pipeline:{reason_code}:{section_id}",
            source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
            requirement=SourceRequirement.REQUIRED,
            state=CollectionState.FAILED,
            slot_ids=collector_slots_for(section_id),
            reason_code=reason_code,
        )
        if previous is not None:
            candidate = previous.candidates[index]
            candidates.append(replace(candidate, attempts=(*candidate.attempts, attempt)))
        else:
            candidates.append(ChapterEvidenceCandidates(
                company_id=company_id, section_id=section_id,
                documents=(), fragments=(), attempts=(attempt,),
                candidate_readiness=EvidenceReadiness.UNKNOWN,
                reason_codes=(reason_code,), estimated_tokens=0,
                max_chars=EMPTY_COLLECTION_CANDIDATE_BUDGET,
                max_estimated_tokens=EMPTY_COLLECTION_CANDIDATE_BUDGET,
            ))
    if previous is not None:
        return replace(previous, candidates=tuple(candidates))
    return OfficialEvidenceCollectionResult(company_id=company_id, candidates=tuple(candidates))


def partial_generation_digest(
    *, company_id: str, official_snapshot: str, source_identity: Any,
    failure_steps: list[dict[str, Any]],
) -> str:
    """불완전 수집은 정상 캐시 신원과 분리해 생성 입력으로만 결속한다."""
    payload = {
        "version": PARTIAL_GENERATION_IDENTITY_VERSION,
        "company_id": company_id,
        "official_snapshot": official_snapshot,
        "dart_receipt_numbers": source_identity.dart_receipt_numbers,
        "financial_payload_digest": source_identity.financial_payload_digest,
        "collection_failures": [
            (step["자료원"], step["사유코드"])
            for step in failure_steps if step.get("step") == COLLECTION_RECOVERY_STEP
        ],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def audit_rows_from_response(response: object) -> list[dict[str, Any]]:
    """정상 빈 응답과 조회 실패를 구분한다."""
    if not isinstance(response, dict):
        raise ValueError("DART 공시목록 응답 모양이 올바르지 않습니다")
    status, rows = response.get("status"), response.get("list")
    if status == "013" and rows in (None, []):
        return []
    if status != "000" or not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("DART 공시목록 상태 또는 목록 모양이 올바르지 않습니다")
    return rows


def report_evidence_availability(
    official_evidence: OfficialEvidenceCollectionResult | None,
    *,
    has_fragments: bool,
    failure_steps: list[dict[str, Any]] | None = None,
) -> Any:
    """정상 완료로 승격하지 않고 원래 수집 시도를 작성기 안내로 투영한다."""
    from src.features.composer.evidence_availability import (
        EvidenceAvailability,
        UnverifiedScope,
        unverified_scope_from_attempt,
    )

    scopes = []
    if official_evidence is not None:
        for candidate in official_evidence.candidates:
            for attempt in candidate.attempts:
                scope = unverified_scope_from_attempt(
                    attempt.source_kind, attempt.state.value, attempt.reason_code,
                )
                if scope is not None and scope not in scopes:
                    scopes.append(scope)
    for step in failure_steps or ():
        if step.get("step") != COLLECTION_RECOVERY_STEP:
            continue
        scope = UnverifiedScope(
            kind=f"pipeline:{step['사유코드']}",
            label=f"{step['자료원']} 확인을 완료하지 못했습니다",
        )
        if scope not in scopes:
            scopes.append(scope)
    return EvidenceAvailability(
        collection_state="partial" if has_fragments else "none",
        unverified_scopes=tuple(scopes),
    )

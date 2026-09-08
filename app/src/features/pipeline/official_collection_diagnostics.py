"""공식 수집 결과를 run별로 남길 최소 닫힌 관측으로 줄인다."""

from __future__ import annotations

from collections import Counter

from src.features.pipeline.official_collection_diagnostic_constants import (
    ALLOWED_OFFICIAL_COLLECTION_REASON_CODES,
    ALLOWED_OFFICIAL_COLLECTION_REQUIREMENTS,
    ALLOWED_OFFICIAL_COLLECTION_SOURCE_KINDS,
    ALLOWED_OFFICIAL_COLLECTION_STATES,
    OFFICIAL_COLLECTION_DIAGNOSTICS_STEP,
    UNKNOWN_OFFICIAL_COLLECTION_VALUE,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult


def _allowed(value: str, allowed: frozenset[str]) -> str:
    return value if value in allowed else UNKNOWN_OFFICIAL_COLLECTION_VALUE


def official_collection_attempt_step(
    result: OfficialEvidenceCollectionResult,
) -> dict[str, object]:
    """장 재분류가 복제한 attempt를 한 실제 시도로만 집계한다.

    ``attempt_id``는 이 함수 안에서만 중복 제거에 쓰며 run 진단에는 직렬화하지
    않는다. CollectionAttempt에 없는 transport 예외 상세도 만들지 않는다.
    """

    seen_attempt_ids: set[str] = set()
    histogram: Counter[tuple[str, str, str, str]] = Counter()
    for candidate in result.candidates:
        for attempt in candidate.attempts:
            if attempt.attempt_id in seen_attempt_ids:
                continue
            seen_attempt_ids.add(attempt.attempt_id)
            source_kind = _allowed(
                attempt.source_kind,
                ALLOWED_OFFICIAL_COLLECTION_SOURCE_KINDS,
            )
            state = _allowed(
                attempt.state.value,
                ALLOWED_OFFICIAL_COLLECTION_STATES,
            )
            reason_code = _allowed(
                attempt.reason_code,
                ALLOWED_OFFICIAL_COLLECTION_REASON_CODES,
            )
            requirement = _allowed(
                attempt.requirement.value,
                ALLOWED_OFFICIAL_COLLECTION_REQUIREMENTS,
            )
            histogram[(source_kind, state, reason_code, requirement)] += 1

    return {
        "step": OFFICIAL_COLLECTION_DIAGNOSTICS_STEP,
        "attempt_count": len(seen_attempt_ids),
        "histogram": [
            {
                "source_kind": source_kind,
                "state": state,
                "reason_code": reason_code,
                "requirement": requirement,
                "count": count,
            }
            for (source_kind, state, reason_code, requirement), count
            in sorted(histogram.items())
        ],
    }

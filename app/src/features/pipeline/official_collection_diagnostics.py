"""공식 수집 결과를 run별로 남길 최소 닫힌 관측으로 줄인다."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from src.features.pipeline.official_collection_diagnostic_constants import (
    ALLOWED_OFFICIAL_COLLECTION_REASON_CODES,
    ALLOWED_OFFICIAL_COLLECTION_REQUIREMENTS,
    ALLOWED_OFFICIAL_COLLECTION_SOURCE_KINDS,
    ALLOWED_OFFICIAL_COLLECTION_STATES,
    NONBLOCKING_REASON_OPTIONAL_PATH,
    NONBLOCKING_REASON_REQUIRED_DESIGN_CAP,
    OFFICIAL_COLLECTION_DIAGNOSTICS_STEP,
    OPTIONAL_INCOMPLETE_DIAGNOSTICS_STEP,
    UNKNOWN_OFFICIAL_COLLECTION_VALUE,
)
from src.features.pipeline.official_evidence_preflight import (
    NonblockingIncompleteAttempt,
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
    scan_counts: Counter[str] = Counter()
    for candidate in result.candidates:
        for attempt in candidate.attempts:
            if attempt.attempt_id in seen_attempt_ids:
                continue
            seen_attempt_ids.add(attempt.attempt_id)
            scan = attempt.document_scan
            if scan is not None:
                scan_counts[scan.state] += 1
                scan_counts["selection_compressed"] += int(scan.selection_compressed)
                scan_counts["line_index_saturated"] += int(scan.line_index_saturated)
                scan_counts["windowed_paragraphs"] += int(scan.windowed_paragraphs)
                scan_counts["total_chars"] += scan.total_chars
                scan_counts["scanned_chars"] += scan.scanned_chars
                scan_counts["candidates_seen"] += scan.candidates_seen
                scan_counts["candidates_retained"] += scan.candidates_retained
                scan_counts["unclassified_seen"] += scan.unclassified_seen
                scan_counts["unclassified_retained"] += scan.unclassified_retained
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
        **({"document_scan": dict(sorted(scan_counts.items()))} if scan_counts else {}),
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


def optional_incomplete_attempt_step(
    attempts: Iterable[NonblockingIncompleteAttempt],
    *,
    design_cap_exempt: Iterable[NonblockingIncompleteAttempt] = (),
) -> dict[str, object]:
    """출고를 막지 않은 미완료를 비차단 근거·종류·상태·사유별 개수로 줄인다.

    선택 경로 미완료(``attempts``)와, 설계 상한 예외로 막지 않은 필수 경로
    잘림(``design_cap_exempt``)을 한 단계에 싣고 행마다 비차단 근거를 적는다.
    사전검사가 이미 시도 식별자로 중복을 걸러 실제 시도 하나당 한 원소만
    넘긴다. 값은 공식 수집 진단과 같은 닫힌 어휘로만 싣고, 목록 밖 값은
    unknown으로 닫는다 — URL·원문·회사 식별자는 들어올 자리가 없다.
    """

    histogram: Counter[tuple[str, str, str, str]] = Counter()
    for nonblocking_reason, group in (
        (NONBLOCKING_REASON_OPTIONAL_PATH, attempts),
        (NONBLOCKING_REASON_REQUIRED_DESIGN_CAP, design_cap_exempt),
    ):
        for attempt in group:
            histogram[
                (
                    nonblocking_reason,
                    _allowed(
                        attempt.source_kind, ALLOWED_OFFICIAL_COLLECTION_SOURCE_KINDS
                    ),
                    _allowed(attempt.state.value, ALLOWED_OFFICIAL_COLLECTION_STATES),
                    _allowed(
                        attempt.reason_code, ALLOWED_OFFICIAL_COLLECTION_REASON_CODES
                    ),
                )
            ] += 1
    return {
        "step": OPTIONAL_INCOMPLETE_DIAGNOSTICS_STEP,
        "attempt_count": sum(histogram.values()),
        "histogram": [
            {
                "nonblocking_reason": nonblocking_reason,
                "source_kind": source_kind,
                "state": state,
                "reason_code": reason_code,
                "count": count,
            }
            for (nonblocking_reason, source_kind, state, reason_code), count in sorted(
                histogram.items()
            )
        ],
    }

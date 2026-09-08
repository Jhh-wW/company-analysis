"""두 기능의 검수 중간 관측을 원문 없는 닫힌 진단으로 정규화한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.shared.report_quality.review_diagnostic_constants import (
    CANDIDATE_FINGERPRINT_RE,
    REVIEW_ITEMS,
    REVIEW_KINDS,
    REVIEW_REASONS,
    REVIEW_SECTION_IDS,
)


def observed_review_outcomes(
    diagnostics: Sequence[object],
) -> tuple[dict[str, object], ...]:
    """최종 보고서가 없어도 호출하며 원문·응답·임의 필드를 저장하지 않는다.

    빈 검증항목은 추가 검증 대상이 없지만 응답 자체가 invalid였던 실제
    관측일 수 있으므로 보존한다. 회사 자료 부족 여부를 추론하지 않는다.
    """
    results: dict[tuple[str, str, str], dict[str, object]] = {}
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, Mapping):
            continue
        section, kind, reason, fingerprint = (diagnostic.get(key) for key in (
            "section_id", "kind", "reason_code", "candidate_sha256",
        ))
        items = diagnostic.get("verification_items")
        if (not isinstance(section, str) or section not in REVIEW_SECTION_IDS
            or kind not in REVIEW_KINDS
            or (section == "summary") != (kind == "요약")
            or reason not in REVIEW_REASONS
            or not isinstance(fingerprint, str)
            or CANDIDATE_FINGERPRINT_RE.fullmatch(fingerprint) is None
            or not isinstance(items, (list, tuple))
            or any(item not in REVIEW_ITEMS for item in items)):
            continue
        key = (section, kind, fingerprint)
        results[key] = {
            "section_id": section, "kind": kind, "reason_code": reason,
            "candidate_sha256": fingerprint,
            "verification_items": tuple(dict.fromkeys(items)),
        }
    return tuple(results.values())

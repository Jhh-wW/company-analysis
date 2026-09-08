"""검수 제외 기록을 공개 결과와 대조하고 닫힌 진단으로 전달한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256

from src.features.composer.port import ComposedReport
from src.shared.report_quality.review_diagnostics import observed_review_outcomes


def final_review_outcomes(
    report: ComposedReport, diagnostics: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """보충 뒤 살아남은 후보와 중복 기록을 빼고 원문 없는 필드만 반환한다."""
    retained: set[tuple[str, str, str]] = set()
    for section in report.sections:
        retained.update((section.section_id, "본문", sha256(sentence.text.encode()).hexdigest())
                        for sentence in section.sentences)
        retained.update((section.section_id, "도식", sha256(" ".join(row.cells).encode()).hexdigest())
                        for row in section.flow_rows)
    retained.update(("summary", "요약", sha256(sentence.text.encode()).hexdigest())
                    for sentence in report.summary)
    report_sections = {section.section_id for section in report.sections} | {"summary"}
    results: dict[tuple[str, str, str], dict[str, object]] = {}
    for diagnostic in observed_review_outcomes(diagnostics):
        section = str(diagnostic["section_id"])
        kind = str(diagnostic["kind"])
        fingerprint = str(diagnostic["candidate_sha256"])
        if section not in report_sections:
            continue
        key = (section, kind, fingerprint)
        if key in retained:
            continue
        results[key] = diagnostic
    return tuple(results.values())

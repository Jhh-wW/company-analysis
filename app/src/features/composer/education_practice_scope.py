"""자기 인용의 교육·실습 모드를 회사의 완료 실행과 구분한다."""

from __future__ import annotations

from collections.abc import Mapping

from src.features.composer.education_practice_scope_constants import (
    EDUCATION_PRACTICE_PROBLEM,
    EDUCATION_PRACTICE_SLOTS,
    EXPLICIT_PROMPT_RE,
    SUMMARY_ACTUAL_EXECUTION_RE,
)
from src.features.composer.prose_own_source import prose_own_source_problem
from src.shared.report_evidence.practice_context import actual_execution_source_text


def education_practice_scope_problem(
    text: str,
    own_sources: Mapping[str, str],
    *,
    section_id: str,
    claim_slot: str = "",
    practice_context_by_source_id: Mapping[str, str] | None = None,
) -> str:
    """예시만 인용한 실제 실행 후보를 제한하고 다른 의미칸은 보존한다.

    문맥 없는 일반 '확인합니다'는 실제 직원 업무 원칙일 수 있으므로
    어미만으로 판정하지 않는다. 원문 범위의 모드는 생산·운송 경계에서
    검증한 문맥만 받으며 회사명이나 뉴스 매체별 예외는 만들지 않는다.
    """
    if section_id == "summary":
        if not SUMMARY_ACTUAL_EXECUTION_RE.search(text):
            return ""
    elif section_id != "past_changes" or claim_slot not in EDUCATION_PRACTICE_SLOTS:
        return ""
    if not own_sources:
        return ""
    contexts = practice_context_by_source_id or {}
    practice_sources = []
    actual_sources = {}
    for source_id, source in own_sources.items():
        raw_context = contexts.get(source_id, "")
        if raw_context:
            from src.shared.report_evidence.practice_context import parse_practice_context

            context = parse_practice_context(raw_context, fragment_text=source)
            practice_sources.append(bool(context))
            actual = actual_execution_source_text(source, practice_marker=context["text"])
            if actual:
                actual_sources[source_id] = actual
            if not context:
                actual_sources[source_id] = source
        else:
            practice = bool(EXPLICIT_PROMPT_RE.search(source))
            practice_sources.append(practice)
            if not practice:
                actual_sources[source_id] = source
            elif actual_execution_source_text(source):
                actual_sources[source_id] = actual_execution_source_text(source)
    if any(practice_sources) and (
        not actual_sources or prose_own_source_problem(text, actual_sources)
    ):
        return EDUCATION_PRACTICE_PROBLEM
    return ""

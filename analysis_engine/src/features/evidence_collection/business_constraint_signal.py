"""자기 사업의 성장 제약과 그에 연결된 현재 대응만 별도 후보로 운반한다.

출력은 원문의 연속 부분구간이다. 문제나 대응을 새로 쓰지 않으며, 행위주체와
사업 범위는 수집기의 기존 문맥 검증·운반 계약으로 제한한다.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from features.evidence_collection import business_constraint_signal_constants as c
from features.evidence_collection.source_context import validate_source_context


@dataclass(frozen=True)
class BusinessConstraintSignal:
    start: int
    end: int
    issue_start: int
    issue_end: int
    response_start: int
    response_end: int


def _foreign_named_subject(text: str, actor_name: str) -> bool:
    for match in c.NAMED_SUBJECT_RE.finditer(text):
        name = match["suffix"] or match["prefix"]
        normalized = re.sub(r"\s+", "", c.LEGAL_FORM_RE.sub("", name))
        if not actor_name or normalized != actor_name:
            return True
    return False


def business_constraint_signals(
    text: str, *, source_context_json: str = "",
) -> tuple[BusinessConstraintSignal, ...]:
    """명시 항목 안의 같은 회사 제약→현재 대응 구간을 찾는다.

    성장·경쟁·한계 단독 신호는 사용하지 않는다. 향후 계획만 있는 대응이나
    다른 주체·해소된 과거 제약을 현재 대응 후보로 추가하지 않는다.
    """
    context = validate_source_context(source_context_json)
    actor = context.get("actor", "") if context.get("origin") == "company_heading" else ""
    actor_name = re.sub(r"\s+", "", c.LEGAL_FORM_RE.sub("", actor))
    result = []
    for section in c.SECTION_START_RE.finditer(text):
        stop = c.SECTION_END_RE.search(text, section.end())
        section_end = stop.start() if stop else len(text)
        area = text[section.end():section_end]
        for constraint in c.CONSTRAINT_RE.finditer(area):
            prefix = area[:constraint.start()] + constraint["object"]
            # 법인 표제의 정확한 이름 또는 회사 자기 서술만 주어 연결로 쓴다.
            subject = c.SELF_RE.search(prefix)
            if subject is None and actor_name:
                compact = re.sub(r"\s+", "", prefix)
                if re.search(re.escape(actor_name) + r"(?:은|는|이|가)", compact) is None:
                    continue
            elif subject is None:
                continue
            if (c.UNBOUND_PREFIX_RE.search(prefix) or c.OTHER_SUBJECT_RE.search(prefix)
                    or c.OTHER_BUSINESS_OWNER_RE.search(area[:constraint.end()])
                    or _foreign_named_subject(prefix, actor_name)):
                continue
            tail = area[constraint.end():]
            sentence_end = c.SENTENCE_END_RE.search(tail)
            clause_end = sentence_end.start() if sentence_end else len(tail)
            clause = tail[:clause_end]
            response = c.CURRENT_RESPONSE_RE.search(clause)
            if response is None or c.COMPLETED_CONSTRAINT_RE.search(clause[:response.end()]):
                continue
            # 명시 타주체의 대응을 앞 회사의 문제 해결로 빌리지 않는다.
            if (c.OTHER_SUBJECT_RE.search(clause[:response.end()])
                    or c.UNRELATED_RESPONSE_RE.search(clause[:response.end()])
                    or _foreign_named_subject(clause[:response.end()], actor_name)):
                continue
            issue_start = section.end() + constraint.start()
            issue_end = section.end() + constraint.end()
            response_start = issue_end + response.start()
            response_end = issue_end + response.end()
            # 같은 항목의 후속 계획·조건도 원문에 남긴다. 현재 대응의 근거 범위는
            # response_start/end이며, 항목 전체가 현재 진행이라는 뜻은 아니다.
            end = section_end
            while end > section.start() and text[end - 1].isspace():
                end -= 1
            result.append(BusinessConstraintSignal(section.start(), end, issue_start, issue_end,
                                                   response_start, response_end))
            # 같은 항목의 연속 범위는 한 번만 운반한다. 여러 신호가 별도 조각·
            # 출처가 되는 것은 아니며 최종 문장 검수도 그대로 필요하다.
            break
    return tuple(result)

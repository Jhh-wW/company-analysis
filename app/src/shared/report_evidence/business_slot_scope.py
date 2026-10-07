"""실제 고객·사업 역할을 보존하며 관리 문구의 해당 의미칸만 제한한다."""
from dataclasses import dataclass
import unicodedata

from src.shared.report_evidence import business_slot_scope_constants as c
from src.shared.report_evidence.challenge_eligibility import (
    challenge_eligibility_scope, challenge_eligibility_problem,
    challenge_eligibility_quote_problem,
)
from src.shared.report_evidence.challenge_eligibility_constants import CHALLENGE_SLOTS
from src.shared.report_evidence.overhead_allocation_scope import overhead_allocation_scope


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _administration(text: str, slot_id: str) -> bool:
    surface = _surface(text)
    if slot_id == c.CUSTOMER_SLOT:
        return bool(c.CUSTOMER_ADMIN_RE.search(surface) and c.CUSTOMER_ADMIN_ACTION_RE.search(surface))
    return bool(c.OPERATING_ADMIN_RE.search(surface) or (
        c.CAREER_PROFILE_RE.search(surface) and (
            c.PERSONAL_POSITION_RE.search(surface) or c.COMPOUND_PERSONAL_POSITION_RE.search(surface))))


def _personal_past_role(text: str, context: str = "") -> bool:
    surface = _surface(text)
    return bool(c.PERSONAL_CAREER_PAST_CONTEXT_RE.search(_surface(context or text))
                and c.PERSONAL_PAST_RESPONSIBILITY_RE.search(surface)
                and not c.COMPANY_ACTION_SUBJECT_RE.search(surface)
                and not c.CURRENT_OPERATING_RESPONSIBILITY_RE.search(surface))


def _business_fact(text: str, slot_id: str, context: str = "") -> bool:
    surface = _surface(text)
    career_company_plan = bool(
        c.CAREER_PROFILE_RE.search(_surface(context or text))
        and not c.OPERATING_ADMIN_RE.search(_surface(context or text))
        and c.COMPANY_ACTION_SUBJECT_RE.search(surface)
        and c.COMPANY_OPERATING_PLAN_RE.search(surface))
    return bool(c.BUSINESS_SERVICE_RE.search(surface) or (
        slot_id == c.CUSTOMER_SLOT and c.CUSTOMER_DEFINITION_RE.search(surface)
        and not _administration(text, slot_id)) or (
        slot_id == c.OPERATING_ROLE_SLOT and (
            (c.OPERATING_ACTION_RE.search(surface) and not _personal_past_role(text, context))
            or c.CURRENT_OPERATING_RESPONSIBILITY_RE.search(surface) or career_company_plan)))


@dataclass(frozen=True)
class BusinessSlotScope:
    score_text: str
    excluded_clauses: tuple[str, ...]
    excluded_spans: tuple[tuple[int, int], ...] = ()


def business_slot_scope(text: str, slot_id: str) -> BusinessSlotScope:
    """전체 원문을 바꾸지 않고 해당 칸의 채점 입력만 따로 만든다."""
    if slot_id in CHALLENGE_SLOTS:
        scoped = challenge_eligibility_scope(text, slot_id)
        return BusinessSlotScope(
            scoped.score_text,
            tuple(text[start:end] for start, end, _ in scoped.excluded_spans),
            tuple((start, end) for start, end, _ in scoped.excluded_spans),
        )
    if slot_id not in c.BUSINESS_SCOPE_SLOTS:
        return BusinessSlotScope(text, ())
    kept, excluded, excluded_spans = [], [], []
    allocation_spans = (overhead_allocation_scope(text).excluded_spans
                        if slot_id == c.OPERATING_ROLE_SLOT else ())
    sentence_cursor = 0
    for sentence in c.SENTENCE_BOUNDARY_RE.split(text):
        sentence_start = text.find(sentence, sentence_cursor)
        sentence_cursor = sentence_start + len(sentence)
        context = _administration(sentence, slot_id)
        unit_cursor = 0
        for unit in c.CLAUSE_BOUNDARY_RE.split(sentence):
            unit_start = sentence.find(unit, unit_cursor)
            unit_cursor = unit_start + len(unit)
            if not unit.strip():
                continue
            absolute_start = sentence_start + unit_start
            unit_end = absolute_start + len(unit)
            pieces = []
            cursor = absolute_start
            for begin, end in allocation_spans:
                begin, end = max(begin, absolute_start), min(end, unit_end)
                if begin >= end:
                    continue
                if cursor < begin:
                    pieces.append((cursor, begin, False))
                pieces.append((begin, end, True))
                cursor = end
            if cursor < unit_end:
                pieces.append((cursor, unit_end, False))
            for begin, end, allocation_excluded in pieces:
                piece = text[begin:end]
                if not piece.strip():
                    continue
                # 정책 범위만 제한하여 쉼표 없이 연결된 실제 사건도 보존한다.
                # 남은 부분에는 기존 경력·관리 문맥 검사를 그대로 적용한다.
                is_admin = _administration(piece, slot_id) or context
                if allocation_excluded or (is_admin and not _business_fact(piece, slot_id, sentence)):
                    excluded.append(piece)
                    excluded_spans.append((begin, end))
                else:
                    kept.append(piece)
    return BusinessSlotScope("\n".join(kept), tuple(excluded), tuple(excluded_spans))


def business_slot_scope_problem(text: str, slot_id: str) -> str:
    if slot_id in CHALLENGE_SLOTS:
        return challenge_eligibility_problem(text, slot_id)
    scoped = business_slot_scope(text, slot_id)
    remaining = c.REMAINING_SUPPORT_RE.get(slot_id)
    return c.REJECT_BUSINESS_SLOT_SCOPE if scoped.excluded_clauses and not (
        _business_fact(scoped.score_text, slot_id, text)
        or (remaining and remaining.search(_surface(scoped.score_text)))
    ) else ""


def business_slot_quote_problem(text: str, slot_id: str, quote_start: int, quote_end: int) -> str:
    """정확 인용이 원문의 제외 절을 잘라 학력·관리 문맥을 잃어도 제한한다."""
    if slot_id in CHALLENGE_SLOTS:
        return challenge_eligibility_quote_problem(
            text[quote_start:quote_end], text, slot_id,
        )
    quote = text[quote_start:quote_end]
    problem = business_slot_scope_problem(quote, slot_id)
    if problem:
        return problem
    if slot_id == c.OPERATING_ROLE_SLOT and _personal_past_role(quote, text[:quote_end]):
        return c.REJECT_BUSINESS_SLOT_SCOPE
    if (slot_id == c.OPERATING_ROLE_SLOT and c.CAREER_PROFILE_RE.search(_surface(text))
            and (c.PERSONAL_POSITION_RE.search(_surface(quote))
                 or c.COMPOUND_PERSONAL_POSITION_RE.search(_surface(quote)))
            and not _business_fact(quote, slot_id)):
        return c.REJECT_BUSINESS_SLOT_SCOPE
    scoped = business_slot_scope(text, slot_id)
    if any(start < quote_end and quote_start < end for start, end in scoped.excluded_spans):
        if not _business_fact(quote, slot_id):
            return c.REJECT_BUSINESS_SLOT_SCOPE
    return ""

"""실제 고객·사업 역할을 보존하며 관리 문구의 해당 의미칸만 제한한다."""
from dataclasses import dataclass
import unicodedata

from src.shared.report_evidence import business_slot_scope_constants as c


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _administration(text: str, slot_id: str) -> bool:
    surface = _surface(text)
    if slot_id == c.CUSTOMER_SLOT:
        return bool(c.CUSTOMER_ADMIN_RE.search(surface) and c.CUSTOMER_ADMIN_ACTION_RE.search(surface))
    return bool(c.OPERATING_ADMIN_RE.search(surface))


def _business_fact(text: str, slot_id: str) -> bool:
    surface = _surface(text)
    return bool(c.BUSINESS_SERVICE_RE.search(surface) or (
        slot_id == c.CUSTOMER_SLOT and c.CUSTOMER_DEFINITION_RE.search(surface)
        and not _administration(text, slot_id)) or (
        slot_id == c.OPERATING_ROLE_SLOT and c.OPERATING_ACTION_RE.search(surface)))


@dataclass(frozen=True)
class BusinessSlotScope:
    score_text: str
    excluded_clauses: tuple[str, ...]


def business_slot_scope(text: str, slot_id: str) -> BusinessSlotScope:
    """전체 원문을 바꾸지 않고 해당 칸의 채점 입력만 따로 만든다."""
    if slot_id not in c.BUSINESS_SCOPE_SLOTS:
        return BusinessSlotScope(text, ())
    kept, excluded = [], []
    for sentence in c.SENTENCE_BOUNDARY_RE.split(text):
        context = _administration(sentence, slot_id)
        for unit in c.CLAUSE_BOUNDARY_RE.split(sentence):
            if not unit.strip():
                continue
            # 쉼표 앞의 관리 문맥을 잃으면 뒤의 '제조물' 등이 사업 역할로 재진입한다.
            is_admin = _administration(unit, slot_id) or context
            if is_admin and not _business_fact(unit, slot_id):
                excluded.append(unit)
            else:
                kept.append(unit)
    return BusinessSlotScope("\n".join(kept), tuple(excluded))


def business_slot_scope_problem(text: str, slot_id: str) -> str:
    scoped = business_slot_scope(text, slot_id)
    remaining = c.REMAINING_SUPPORT_RE.get(slot_id)
    return c.REJECT_BUSINESS_SLOT_SCOPE if scoped.excluded_clauses and not (
        _business_fact(scoped.score_text, slot_id)
        or (remaining and remaining.search(_surface(scoped.score_text)))
    ) else ""

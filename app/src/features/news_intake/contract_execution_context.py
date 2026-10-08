"""기사의 같은 계약·행사에 명시된 미래 이행기간을 단문에서 지우지 않는다."""
import datetime as dt
import re

from src.features.news_intake import contract_execution_context_constants as c
from src.features.news_intake import quote_selection_constants as qc
from src.features.news_intake.identity_names import company_query_names
from src.features.news_intake.models import NewsCompanyContext


def _future_start(text: str, as_of: dt.date) -> dt.date | None:
    match = c.CONTRACT_FUTURE_START_RE.search(text)
    if match is None:
        return None
    try:
        date = dt.date(int(match['year']), int(match['month'] or 1), int(match['day'] or 1))
    except ValueError:
        return None
    return date if date > as_of else None


def _compact(text: str) -> str:
    return ''.join(text.split()).casefold()


def _target_subject(text: str, names: tuple[str, ...]) -> bool:
    return any(re.match(c.CONTRACT_SUBJECT_PREFIX + re.escape(name) + c.CONTRACT_TARGET_PARTICLE,
                        text, re.I) for name in names)


def _other_actor(text: str, names: tuple[str, ...]) -> bool:
    allowed = {_compact(name) for name in names} | c.CONTRACT_GENERIC_ACTORS
    return any(_compact(match[1]) not in allowed for match in c.CONTRACT_OTHER_ACTOR_RE.finditer(text))


def _existing_fulfillment(text: str, body: str, start: int, names: tuple[str, ...],
                          entities: set[str], as_of: dt.date) -> bool:
    """미래 갱신과 별개로 같은 행사·행동이 현재 진행 중임을 원문에서 확인한다."""
    actions = set(c.CONTRACT_ACTION_NAME_RE.findall(text))
    units = tuple(qc.QUOTE_SENTENCE_RE.finditer(body))
    for index, unit in enumerate(units):
        value = unit.group()
        action = c.CONTRACT_ONGOING_ACTION_RE.search(value)
        current_entities = {_compact(match[1]) for match in c.CONTRACT_ENTITY_RE.finditer(value)}
        if (action is None or action['action'] not in actions or _future_start(value, as_of)
                or not _target_subject(value, names) or _other_actor(value, names)
                or not entities.issubset(current_entities)
                or abs(unit.start() - start) > c.CONTRACT_CONTEXT_MAX_CHARS
                or c.CONTRACT_ENDED_RE.search(value)):
            continue
        if (any(int(year) < as_of.year for year in c.CONTRACT_EXPLICIT_YEAR_RE.findall(value))
                and '부터' not in value):
            continue
        # 명시 현재 이행 뒤의 같은 회사 종료·부정은 과거 지원을 되살리지 않는다.
        if any(_target_subject(entry.group(), names) and c.CONTRACT_ENDED_RE.search(entry.group())
               and (not c.CONTRACT_ENTITY_RE.search(entry.group())
                    or any(entity in _compact(entry.group()) for entity in entities))
               for entry in units[index + 1:]
               if entry.start() <= max(start, unit.end())
               and entry.start() - unit.end() <= c.CONTRACT_CONTEXT_MAX_CHARS):
            continue
        return True
    return False


def contract_execution_context_problem(text: str, body: str, company: NewsCompanyContext,
                                       as_of: dt.date, *, temporal_status: str) -> str:
    """동일 대상의 미래계약이 입증된 이행문장에만 제약을 준다. 승인 함수가 아니다.

    같은 미래기간을 적은 행사 소제목은 제약에만 쓰며, 소제목만으로 회사행동을 승인하지 않는다.
    원 선택 ID·좌표·날짜를 바꾸거나 없던 인용을 복구하지 않는다.
    """
    start = body.find(text)
    if start < 0 or body.find(text, start + 1) >= 0 or not c.CONTRACT_FULFILLMENT_RE.search(text):
        return ''
    future_in_text = _future_start(text, as_of)
    if temporal_status == 'planned' and future_in_text:
        return ''
    if not future_in_text and (c.CONTRACT_EXPLICIT_NOW_RE.search(text) or c.CONTRACT_REALIZED_ACTION_RE.search(text)):
        return ''
    names = company_query_names(company)
    if not _target_subject(text, names):
        return ''
    entities = {_compact(match[1]) for match in c.CONTRACT_ENTITY_RE.finditer(text)
                if len(_compact(match[1])) >= c.CONTRACT_ENTITY_MIN_CHARS
                and _compact(match[1]) not in c.CONTRACT_GENERIC_ENTITIES}
    if not entities:
        return ''
    if future_in_text:
        return c.CONTRACT_CONTEXT_REASON
    if _existing_fulfillment(text, body, start, names, entities, as_of):
        return ''
    units = list(qc.QUOTE_SENTENCE_RE.finditer(body[:start + len(text)]))
    for index, unit in enumerate(units):
        future = _future_start(unit.group(), as_of)
        if future is None or not _target_subject(unit.group(), names):
            continue
        if start < unit.start() or start - unit.start() > c.CONTRACT_CONTEXT_MAX_CHARS:
            continue
        relevant = [entry for entry in units[index:] if entry.start() <= start]
        if len(relevant) > c.CONTRACT_CONTEXT_MAX_UNITS:
            continue
        context = body[unit.start():start]
        if not c.CONTRACT_COMPLETED_RE.search(context) or _other_actor(context, names):
            continue
        # 이행 상대는 미래기간 문장 자체 또는 같은 기간을 반복한 바로 앞 소제목에 있어야 한다.
        event_context = unit.group()
        if index:
            previous = units[index - 1]
            if _future_start(previous.group(), as_of) == future and not body[previous.end():unit.start()].strip():
                event_context += previous.group()
        if not any(entity in _compact(event_context) for entity in entities):
            continue
        return c.CONTRACT_CONTEXT_REASON
    return ''

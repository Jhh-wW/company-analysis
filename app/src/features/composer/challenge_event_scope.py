"""과거 사고·제재 기록의 날짜를 지워 현재 과제로 보이지 않게 한다."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import unicodedata

from src.features.composer import challenge_event_constants as c
from src.features.composer.challenge_constants import CHALLENGE_RESPONSE_CELL_COUNT, CHALLENGE_RESPONSE_CELL_INDEX


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _days(text: str):
    return frozenset(tuple(int(part) for part in day) for day in c.EVENT_DAY_RE.findall(text))


@dataclass(frozen=True)
class _EventRow:
    date: str
    actor: str
    response: str
    category: str


def _event_rows(source: str) -> tuple[_EventRow, ...]:
    """원문 표의 날짜·주체·대응 열을 같은 행 그대로 읽는다."""
    mapping = None
    found = []
    for unit in c.TABLE_UNIT_SPLIT_RE.split(source):
        parts = tuple(part.strip() for part in unit.split("|"))
        compact = tuple(_surface(part) for part in parts)
        date_columns = [i for i, value in enumerate(compact) if value in c.EVENT_DATE_HEADERS]
        if date_columns:
            date_index = date_columns[0]
            actor_index = next((i for i, value in enumerate(compact) if value in c.EVENT_ACTOR_HEADERS), None)
            response_index = next((i for i, value in enumerate(compact) if value in c.EVENT_RESPONSE_HEADERS), None)
            category = "accident" if "재해" in compact[date_index] or "사고" in compact[date_index] else "sanction"
            mapping = (len(parts), date_index, actor_index, response_index, category)
            continue
        if mapping is None:
            continue
        width, date_index, actor_index, response_index, category = mapping
        if len(parts) != width or not _days(parts[date_index]):
            # 다른 표/본문으로 넘어가면 이전 머리말을 빌리지 않는다.
            if "|" not in unit or not any(c.EVENT_DATE_RE.search(part) for part in parts):
                mapping = None
            continue
        found.append(_EventRow(
            date=parts[date_index], actor=parts[actor_index] if actor_index is not None else "",
            response=parts[response_index] if response_index is not None else "",
            category=category,
        ))
    return tuple(found)


def _selected_rows(candidate: str, rows: Sequence[_EventRow]) -> tuple[_EventRow, ...]:
    days = _days(candidate)
    months = frozenset(tuple(int(part) for part in month) for month in c.EVENT_MONTH_RE.findall(candidate))
    years = frozenset(c.EVENT_YEAR_RE.findall(candidate))
    return tuple(row for row in rows if (
        bool(_days(row.date) & days) if days else
        bool(frozenset(day[:2] for day in _days(row.date)) & months) if months else
        bool(frozenset(c.EVENT_YEAR_RE.findall(row.date)) & years)
    ))


def _action_states(text: str) -> dict[str, set[str]]:
    surface = _surface(text)
    matches = tuple(c.ACTION_RE.finditer(surface))
    states: dict[str, set[str]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(surface)
        tail = surface[match.end():min(end, match.end() + c.ACTION_STATE_MAX_CHARS)]
        state = "pending" if c.ACTION_PENDING_RE.search(tail) else "completed" if c.ACTION_COMPLETED_RE.search(tail) else ""
        if state:
            states.setdefault(match.group(), set()).add(state)
    return states


def _table_relation_problem(candidate: str, sources: Mapping[str, str], cells: Sequence[str] | None) -> str:
    surface = _surface(candidate)
    if cells is not None and len(cells) == CHALLENGE_RESPONSE_CELL_COUNT:
        response = _surface(str(cells[CHALLENGE_RESPONSE_CELL_INDEX]))
        if _possible_response_problem(response, sources, candidate=candidate):
            return c.RESPONSE_SCOPE_PROBLEM
    rows = tuple(row for source in sources.values() for row in _event_rows(source))
    if not rows:
        return ""
    selected = _selected_rows(candidate, rows)
    if not selected:
        return ""
    accident_claim = bool(c.ACTUAL_ACCIDENT_CLAIM_RE.search(surface))
    if accident_claim:
        selected = tuple(row for row in selected if row.category == "accident")
        if not selected:
            return c.EVENT_SCOPE_PROBLEM
    elif c.PENALTY_RE.search(surface):
        selected = tuple(row for row in selected if row.category == "sanction")
        if not selected:
            return c.EVENT_SCOPE_PROBLEM
    else:
        return ""
    # 여러 법인의 기록을 보고서 대상 회사의 무주어 사건으로 바꾸지 않는다.
    named = tuple(row for row in selected if _surface(row.actor)
                  not in c.UNKNOWN_EVENT_ACTORS and _surface(row.actor) in surface)
    mentioned_actors = {_surface(row.actor) for row in rows
                        if _surface(row.actor) not in c.UNKNOWN_EVENT_ACTORS
                        and _surface(row.actor) in surface}
    if mentioned_actors and not named:
        return c.EVENT_SCOPE_PROBLEM
    if named:
        selected = named
    elif len({
        _surface(row.actor) for row in rows
        if _surface(row.actor) not in c.SELF_EVENT_ACTORS | c.UNKNOWN_EVENT_ACTORS
    }) > 1 and any(_surface(row.actor) not in c.SELF_EVENT_ACTORS | c.UNKNOWN_EVENT_ACTORS for row in selected):
        # 도급사 사건은 원문의 고용 관계와 후보의 도급사 표시를 함께 보존한다.
        if not (accident_claim and c.CONTRACTOR_ACTOR_RE.search(surface)
                and all(c.CONTRACTOR_ACTOR_RE.search(_surface(row.actor)) for row in selected)):
            return c.EVENT_SCOPE_PROBLEM
    candidate_states = _action_states(candidate)
    for action, states in candidate_states.items():
        if not any(c.ACTION_RE.search(_surface(row.response)) and action in _surface(row.response) for row in selected):
            if any(action in _surface(row.response) for row in rows):
                return c.EVENT_SCOPE_PROBLEM
        if "completed" in states:
            matching = [_action_states(row.response).get(action, set()) for row in selected]
            if any("pending" in values and "completed" not in values for values in matching):
                return c.TIME_BINDING_PROBLEM
        if "pending" in states:
            matching = [_action_states(row.response).get(action, set()) for row in selected]
            if matching and all("completed" in values and "pending" not in values for values in matching):
                return c.TIME_BINDING_PROBLEM
    return ""


def _possible_response_problem(response: str, sources: Mapping[str, str], *, candidate: str = "") -> bool:
    if not (c.PENALTY_RE.search(response) and c.CONDITIONAL_PENALTY_RE.search(response)):
        return False
    if c.PREVENTIVE_RESPONSE_RE.search(response):
        candidate_actions = _action_states(response)
        rows = tuple(row for source in sources.values() for row in _event_rows(source))
        selected = _selected_rows(candidate, rows) if candidate else ()
        source_actions = ([_action_states(row.response) for row in selected]
                          if rows else [_action_states(source) for source in sources.values()])
        if any("completed" in states and any("completed" in value.get(action, set()) for value in source_actions)
               for action, states in candidate_actions.items()):
            return False
    return True


def _claim_units(candidate: str) -> tuple[str, ...]:
    sentences = tuple(unit for unit in c.CLAIM_SENTENCE_BOUNDARY_RE.split(candidate) if unit.strip())
    if len(sentences) > 1:
        return tuple(unit for sentence in sentences for unit in _claim_units(sentence))
    starts = [match.start() for match in c.EVENT_YEAR_RE.finditer(candidate)]
    if len(starts) < 2:
        return (candidate,)
    # 한 문장의 서로 다른 발생기간과 그 뒤 상태를 각 기간에 결속한다.
    return tuple(candidate[0 if index == 0 else start:starts[index + 1] if index + 1 < len(starts) else len(candidate)]
                 for index, start in enumerate(starts))


def _dated_event_records(source: str):
    table = bool(c.EVENT_TABLE_HEADER_RE.search(_surface(source)) and "|" in source)
    header_categories = {
        name for name, pattern in c.EVENT_CATEGORY_RES.items()
        if table and pattern.search(_surface(source.split(";", 1)[0]))
    }
    records = []
    for unit in c.EVENT_RECORD_SPLIT_RE.split(source):
        years = frozenset(c.EVENT_DATE_RE.findall(unit))
        if not years:
            continue
        surface = _surface(unit)
        if not table and not c.ACTUAL_EVENT_RE.search(surface):
            continue
        categories = {name for name, pattern in c.EVENT_CATEGORY_RES.items() if pattern.search(surface)}
        categories.update(header_categories)
        if categories:
            records.append((categories, years, bool(c.ONGOING_RECORD_RE.search(surface)), _days(unit)))
    return records


def challenge_event_scope_problem(text: str, sources: Mapping[str, str], *, cells: Sequence[str] | None = None) -> str:
    """자기 인용의 날짜 있는 실제 사건만 제약한다. 원문을 수정하거나 사실을 추가하지 않는다.

    실제 발생일·처분일이 있는 사건은 본문과 표에도 그 발생 기간을 남겨야 한다.
    발행일·법 시행일·일반적인 조건부 규정은 실제 사건 날짜로 승격하지 않는다.
    완료·진행 여부는 해당 발생 기간의 행 안에서만 확인하며 다른 행으로 빌리지 않는다.
    """
    candidate = " ".join(cells) if cells is not None else text
    if cells is not None and len(cells) == CHALLENGE_RESPONSE_CELL_COUNT and _possible_response_problem(_surface(str(cells[CHALLENGE_RESPONSE_CELL_INDEX])), sources, candidate=candidate):
        return c.RESPONSE_SCOPE_PROBLEM
    units = _claim_units(candidate)
    if len(units) > 1:
        return next((problem for unit in units if (problem := challenge_event_scope_problem(unit, sources))), "")
    surface = _surface(candidate)
    # 실제로 받거나 발생한 사건과 제재 가능성·사고 예방 위험은 다른 주장이다.
    claim_surface = _surface(text)
    actions = _action_states(candidate)
    if (c.PENALTY_RE.search(surface) and any("completed" in states for states in actions.values())
            and sources and all(c.CONDITIONAL_PENALTY_RE.search(_surface(source)) for source in sources.values())
            and not any(c.ACTUAL_EVENT_RE.search(_surface(source)) or _event_rows(source) for source in sources.values())):
        if not any("completed" in states and any("completed" in _action_states(source).get(action, set())
                    for source in sources.values()) for action, states in actions.items()):
            return c.EVENT_SCOPE_PROBLEM
    if (c.GENERAL_EVENT_RISK_RE.search(claim_surface) and not c.ACTUAL_EVENT_RE.search(claim_surface)
            and not any("completed" in states for states in actions.values())):
        if cells is not None and len(cells) == CHALLENGE_RESPONSE_CELL_COUNT:
            response = _surface(str(cells[CHALLENGE_RESPONSE_CELL_INDEX]))
            if _possible_response_problem(response, sources, candidate=candidate):
                return c.RESPONSE_SCOPE_PROBLEM
        return ""
    relation_problem = _table_relation_problem(candidate, sources, cells)
    if relation_problem:
        return relation_problem
    categories = {name for name, pattern in c.EVENT_CATEGORY_RES.items() if pattern.search(surface)}
    if not categories:
        return ""
    candidate_years = frozenset(c.EVENT_YEAR_RE.findall(candidate))
    candidate_days = _days(candidate)
    records = [record for source in sources.values() for record in _dated_event_records(source)]
    for category in categories:
        relevant = [record for record in records if category in record[0]]
        if not relevant:
            continue
        selected = [record for record in relevant if (
            record[3] & candidate_days if candidate_days else record[1] & candidate_years
        )]
        if not selected:
            return c.TIME_BINDING_PROBLEM
        # 같은 연도에 완료·진행 사건이 섞이면 어느 사건인지 연도만으로 정하지 않는다.
        # 명시적인 현재 완료 상태는 진행 주장과 다르다. 다른 절의 미해결·진행
        # 표지는 제거하지 않아 뒤의 완료 문구로 현재 문제를 면제하지 못한다.
        current_claim = c.CURRENT_COMPLETED_ACTION_RE.sub("", surface)
        if c.CURRENT_EVENT_RE.search(current_claim) and not all(record[2] for record in selected):
            return c.TIME_BINDING_PROBLEM
    return ""

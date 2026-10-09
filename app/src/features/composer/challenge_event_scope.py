"""과거 사고·제재 기록의 날짜를 지워 현재 과제로 보이지 않게 한다."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
import unicodedata

from src.features.composer import challenge_event_constants as c
from src.features.composer.challenge_constants import CHALLENGE_RESPONSE_CELL_COUNT, CHALLENGE_RESPONSE_CELL_INDEX


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _days(text: str):
    return frozenset(tuple(int(part) for part in day) for day in c.EVENT_DAY_RE.findall(text))


def _activity_records(text: str, *, claim: bool, additional_actions: tuple[str, ...] = ()):
    """짧은 명시 활동명과 그 절의 진행 표지만 읽는다. 일반 의미 승인은 하지 않는다."""
    records = []
    activity_re = re.compile(
        c.RESPONSE_ACTIVITY_RE.pattern + "|" + "|".join(re.escape(action) for action in additional_actions)
    ) if additional_actions else c.RESPONSE_ACTIVITY_RE
    for unit in c.RESPONSE_ACTIVITY_UNIT_RE.split(unicodedata.normalize('NFKC', text)):
        header = c.RESPONSE_CURRENT_HEADER_RE.fullmatch(unit)
        header_current = header is not None
        if header is not None:
            unit = header['body']
        matches = tuple(activity_re.finditer(unit))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(unit)
            tail = unit[match.end():end]
            rest = unit[match.end():]
            words = c.RESPONSE_ACTIVITY_WORD_RE.findall(unit[:match.start()])
            # 명시 목적어 두 어절을 고르기 전에 닫힌 방식 부사만 제외한다.
            # 숫자·정도·부정·시점 표현과 활동 상태 검사는 그대로 남긴다.
            if (len(words) >= c.RESPONSE_ACTIVITY_HEAD_WORDS
                    and words[-1] in c.RESPONSE_ACTIVITY_MANNER_WORDS
                    and c.RESPONSE_ACTIVITY_MANNER_OBJECT_RE.search(words[-2])):
                words = words[:-1]
            selected_words = words[-c.RESPONSE_ACTIVITY_HEAD_WORDS:]
            selected_words = [word for word in selected_words if word not in c.RESPONSE_ACTIVITY_HEAD_CONNECTORS]
            if len(selected_words) > 1 and c.RESPONSE_ACTIVITY_SUBJECT_WORD_RE.search(selected_words[0]):
                selected_words = selected_words[1:]
            head = c.RESPONSE_ACTIVITY_PARTICLE_RE.sub('', ''.join(selected_words))
            head = c.RESPONSE_ACTIVITY_HEAD_RE.sub('', head).casefold()
            foreign = c.RESPONSE_FOREIGN_ACTOR_RE.search(unit[:match.start()])
            current = bool(c.RESPONSE_CURRENT_STATE_RE.search(tail)
                           or c.RESPONSE_ACTIVITY_PROGRESS_TAIL_RE.search(tail)
                           or c.RESPONSE_PERFORMING_TAIL_RE.search(tail)
                           or c.RESPONSE_PASSIVE_PERFORMING_TAIL_RE.search(tail)
                           or header_current)
            if not claim and not current:
                current = bool(c.RESPONSE_PRESENT_VERB_TAIL_RE.search(tail)
                               and not c.RESPONSE_PURPOSE_SUBJECT_RE.search(unit[:match.start()]))
            if not current and c.RESPONSE_CURRENT_STATE_RE.search(rest):
                # 원문과 주장 모두 활동 목록의 접속 관계가 있을 때만 뒤 진행
                # 상태를 함께 읽는다. 앞 활동의 성과·이후 절차까지 넓히지 않는다.
                current = bool(c.RESPONSE_ACTION_JOIN_RE.search(tail)
                               and not c.RESPONSE_FOREIGN_ACTOR_RE.search(tail))
            if c.RESPONSE_OTHER_STATE_RE.search(c.RESPONSE_CONTINUATION_RE.sub('', tail)):
                current = False
            if not claim and c.RESPONSE_UNREAL_PREFIX_RE.search(unit[:match.start()]):
                current = False
            head_words = tuple(c.RESPONSE_ACTIVITY_PARTICLE_RE.sub('', word).casefold() for word in selected_words)
            records.append((match.group(), head, foreign['actor'] if foreign else '', current, head_words))
    return tuple(records)


def response_current_activity_problem(candidate: str, sources: Mapping[str, str]) -> str:
    """명사형 대응에 없는 현재진행을 붙이는 경계만 닫고 원문 활동은 보존한다."""
    passive_performing = tuple(c.RESPONSE_PASSIVE_PERFORMING_RE.finditer(candidate))
    performing = (*c.RESPONSE_PERFORMING_RE.finditer(candidate), *passive_performing)
    if not c.RESPONSE_CURRENT_STATE_RE.search(candidate) and not performing:
        return ''
    additional_actions = tuple(dict.fromkeys(
        match['activity'] for match in (*c.RESPONSE_NOMINAL_PROGRESS_RE.finditer(candidate), *performing)
        if match['activity'] not in c.RESPONSE_PROGRESS_CONTROLS
        if not c.RESPONSE_ACTIVITY_RE.fullmatch(match['activity'])
    ))
    claim_records = tuple(record for record in _activity_records(
        candidate, claim=True, additional_actions=additional_actions) if record[3])
    if not claim_records:
        return ''
    rows = tuple(row for source in sources.values() for row in _event_rows(source))
    units = tuple(sources.values())
    if rows:
        selected = _selected_rows(candidate, rows)
        named = tuple(row for row in rows if _surface(row.actor)
                      not in c.UNKNOWN_EVENT_ACTORS and c.EVENT_ACTOR_LEGAL_FORM_RE.sub('', _surface(row.actor)) in _surface(candidate))
        if named:
            selected = tuple(row for row in selected if row in named) if selected else named
        if not selected and len(rows) == 1 and _surface(rows[0].actor) in c.SELF_EVENT_ACTORS:
            selected = rows
        # 동일 법인의 여러 행은 날짜 없이 서로의 진행 상태를 빌리지 않는다.
        units = tuple(row.response for row in selected) if len(selected) == 1 else ()
    source_records = tuple(record for unit in units for record in _activity_records(
        unit, claim=False, additional_actions=additional_actions))
    action_sources = tuple(row.response for row in rows) if rows else tuple(sources.values())
    all_actions = {record[0] for source in action_sources for record in _activity_records(
        source, claim=False, additional_actions=additional_actions)}
    for action, head, actor, _, head_words in claim_records:
        if action not in all_actions:
            if any(match['activity'] == action for match in passive_performing):
                # 새로 읽는 명시 수동 진행은 활동명 자체가 없는 원문에도
                # 현재 상태를 빌리지 않는다. 일반 활동의 의미 검수는 유지한다.
                return c.TIME_BINDING_PROBLEM
            continue  # 없는 활동 자체의 의미 검수는 기존 계약이 담당한다.
        if not any(source_action == action and current and (not head or not source_head or source_head == head or source_head in head_words)
                   and (not source_actor or source_actor == actor)
                   for source_action, source_head, source_actor, current, _ in source_records):
            return c.TIME_BINDING_PROBLEM
    return ''


@dataclass(frozen=True)
class _LitigationRow:
    event: str
    markers: frozenset[str]
    raw_row: str
    start: int
    end: int


def _litigation_rows_and_notes(source: str):
    """표 머리말·심급·금액이 명시된 행과 각주를 원문 좌표에 묶는다.

    공백을 접어 탐색하되 결과는 원문 연속 범위로 복원한다. 발행일이나
    숫자만으로 사건을 만들지 않으며 이 함수의 빈 결과는 의미 승인이 아니다.
    """
    chars, positions = [], []
    for index, char in enumerate(source):
        normalized = unicodedata.normalize("NFKC", char)
        for value in normalized:
            if not value.isspace():
                chars.append(value)
                positions.append(index)
    compact = "".join(chars)
    header = c.LITIGATION_TABLE_HEADER_RE.search(compact)
    if not header:
        return (), {}
    definitions = tuple(c.LITIGATION_FOOTNOTE_RE.finditer(compact, header.end()))
    if not definitions:
        return (), {}
    body_end = definitions[0].start()
    rows = []
    start = header.end()
    for ending in c.LITIGATION_ROW_END_RE.finditer(compact, start, body_end):
        raw_start, raw_end = positions[start], positions[ending.end() - 1] + 1
        raw_row = source[raw_start:raw_end]
        court = c.LITIGATION_COURT_RE.search(raw_row)
        if not court:
            return (), {}
        prefix = _surface(raw_row[:court.start()])
        markers = frozenset(c.LITIGATION_MARKER_RE.findall(_surface(raw_row)))
        event = c.LITIGATION_MARKER_RE.sub("", prefix).strip(";|")
        if not event or any(char.isdigit() for char in event):
            return (), {}
        rows.append(_LitigationRow(event, markers, raw_row, raw_start, raw_end))
        start = ending.end()
    # 마지막 행 뒤에 다른 표나 식별 못한 행이 끼면 앞 표의 적용 범위를 빌리지 않는다.
    if compact[start:body_end].strip(";|.,") or not rows:
        return (), {}
    notes = {}
    for index, definition in enumerate(definitions):
        end = definitions[index + 1].start() if index + 1 < len(definitions) else len(compact)
        body = c.LITIGATION_FOOTNOTE_BODY_RE.match(compact, definition.end(), end)
        if body is None or definition["marker"] in notes:
            return (), {}
        notes[definition["marker"]] = body.group()
    return tuple(rows), notes


def litigation_footnote_scope_problem(candidate: str, sources: Mapping[str, str]) -> str:
    """일부 사건의 각주 평가를 다른 사건이나 소송 전체로 넓히는 경계만 제한한다."""
    surface = _surface(candidate)
    properties = tuple(pattern for pattern in c.LITIGATION_ASSESSMENT_PROPERTIES if pattern.search(surface))
    if not properties:
        return ""
    for source in sources.values():
        rows, notes = _litigation_rows_and_notes(source)
        if not rows:
            continue
        mentions = tuple((match.start(), match.end(), row)
                         for row in rows for match in re.finditer(re.escape(row.event), surface))
        # 같은 위치의 긴 사건명 안에 든 접두 사건은 별도 언급으로 세지 않는다.
        # 다른 위치에서 짧은 사건도 명시하면 그 행의 각주 범위는 계속 검사한다.
        selected = tuple(row for row in rows if any(
            mentioned is row and not any(left <= start and end <= right and right - left > end - start
                                        for left, right, _ in mentions)
            for start, end, mentioned in mentions))
        if not selected:
            explicit = c.LITIGATION_EXPLICIT_NOTE_RE.search(surface)
            if explicit:
                marker = "(*" + (explicit["number"] or explicit["marker_number"]) + ")"
                selected = tuple(row for row in rows if marker in row.markers)
            elif c.LITIGATION_GLOBAL_CLAIM_RE.search(surface):
                selected = rows
        if not selected:
            continue
        for pattern in properties:
            applicable = {marker for marker, note in notes.items() if pattern.search(note)}
            if applicable and any(not (row.markers & applicable) for row in selected):
                return c.EVENT_SCOPE_PROBLEM
    return ""


@dataclass(frozen=True)
class _EventRow:
    date: str
    actor: str
    response: str
    category: str
    event: str = ""
    raw_row: str = ""
    start: int = 0
    end: int = 0
    columns_unambiguous: bool = False


def _table_units_with_spans(source: str):
    start = 0
    for match in c.TABLE_UNIT_SPLIT_RE.finditer(source):
        yield source[start:match.start()], start
        start = match.end()
    yield source[start:], start


def _event_rows(source: str) -> tuple[_EventRow, ...]:
    """원문 표의 날짜·주체·대응 열을 같은 행 그대로 읽는다."""
    mapping = None
    found = []
    for unit, unit_start in _table_units_with_spans(source):
        parts = tuple(part.strip() for part in unit.split("|"))
        compact = tuple(_surface(part) for part in parts)
        date_columns = [i for i, value in enumerate(compact) if value in c.EVENT_DATE_HEADERS]
        if date_columns:
            date_index = date_columns[0]
            actor_index = next((i for i, value in enumerate(compact) if value in c.EVENT_ACTOR_HEADERS), None)
            primary_response_indices = [i for i, value in enumerate(compact) if value in c.EVENT_PRIMARY_RESPONSE_HEADERS]
            response_indices = primary_response_indices or [i for i, value in enumerate(compact) if value in c.EVENT_RESPONSE_HEADERS]
            response_index = response_indices[0] if response_indices else None
            category = "accident" if "재해" in compact[date_index] or "사고" in compact[date_index] else "sanction"
            event_indices = tuple(i for i, value in enumerate(compact) if value in c.EVENT_CONTENT_HEADERS)
            unambiguous = (len(date_columns) == 1
                           and sum(value in c.EVENT_ACTOR_HEADERS for value in compact) == 1
                           and len(response_indices) == 1)
            mapping = (len(parts), date_index, actor_index, response_index, category, event_indices, unambiguous)
            continue
        if mapping is None:
            continue
        width, date_index, actor_index, response_index, category, event_indices, unambiguous = mapping
        if len(parts) != width or not _days(parts[date_index]):
            # 다른 표/본문으로 넘어가면 이전 머리말을 빌리지 않는다.
            if "|" not in unit or not any(c.EVENT_DATE_RE.search(part) for part in parts):
                mapping = None
            continue
        raw_parts = unit.split("|")
        event = ""
        if event_indices:
            first, last = event_indices[0], event_indices[-1]
            # 같은 열의 원문 연속 범위만 가져오며 중간에 다른 뜻의 열이 있으면 힌트로 쓰지 않는다.
            if event_indices == tuple(range(first, last + 1)):
                left = sum(len(part) + 1 for part in raw_parts[:first])
                right = sum(len(part) + 1 for part in raw_parts[:last]) + len(raw_parts[last])
                event = unit[left:right].strip()
        left_space = len(unit) - len(unit.lstrip())
        raw_row = unit.strip()
        found.append(_EventRow(
            date=parts[date_index], actor=parts[actor_index] if actor_index is not None else "",
            response=parts[response_index] if response_index is not None else "",
            category=category,
            event=event, raw_row=raw_row,
            start=unit_start + left_space, end=unit_start + left_space + len(raw_row),
            columns_unambiguous=unambiguous,
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
    current_problem = response_current_activity_problem(candidate, sources)
    if current_problem:
        return current_problem
    footnote_problem = litigation_footnote_scope_problem(candidate, sources)
    if footnote_problem:
        return footnote_problem
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

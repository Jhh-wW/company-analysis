"""과제·기간·기대효과 표의 명사형을 현재 실행이나 완료로 승격하지 않는다."""
from __future__ import annotations

import re
from collections.abc import Mapping

from src.features.composer.research_table_status_scope_constants import (
    RESEARCH_CLAUSE_BOUNDARY_RE, RESEARCH_DESCRIPTION_RE, RESEARCH_EFFECT_MIN_MATCH_WORDS,
    RESEARCH_EFFECT_MIN_WORD_LENGTH, RESEARCH_EFFECT_STOP_WORDS,
    RESEARCH_PERIOD_RE, RESEARCH_ROW_SEPARATOR_RE, RESEARCH_SOURCE_LIMIT_RE,
    RESEARCH_STATE_RE, RESEARCH_TABLE_HEADER_RE, RESEARCH_TABLE_STATE_UNSUPPORTED,
    RESEARCH_TITLE_ACTIVITY_RE, RESEARCH_TITLE_MIN_TARGET_LENGTH, RESEARCH_WORD_RE,
)


def _compact(text: str) -> str:
    return re.sub(r'[^가-힣A-Za-z0-9]', '', text).casefold()


def _row(unit: str, header: re.Match[str]) -> tuple[str, str] | None:
    cells = [cell.strip() for cell in unit.strip().strip('|').split('|')]
    columns = 3 if header.group('third') else 2
    if len(cells) != columns or not cells[0]:
        return None
    if _compact(header.group('second')) == '연구기간':
        if not RESEARCH_PERIOD_RE.fullmatch(cells[1]):
            return None
        # 기간은 행 구분일 뿐이다. 제목 속 완료를 상태 셀로 사용하지 않는다.
        return cells[0], cells[2] if columns == 3 else ''
    return cells[0], cells[1]


def _rows(source: str) -> tuple[tuple[str, str], ...]:
    """날짜는 행 구분에만 쓴다. 기간이 현재여도 실행 상태를 만들지 않는다."""
    header = RESEARCH_TABLE_HEADER_RE.search(source)
    if not header:
        return ()
    rows = []
    for unit in RESEARCH_ROW_SEPARATOR_RE.split(source[header.end():]):
        if row := _row(unit, header):
            rows.append(row)
    return tuple(rows)


def _anchor(text: str, title: str, effect: str) -> int | None:
    """과제명 또는 구체 기대효과 어구를 결속한다. 날짜와 표 머리글은 쓰지 않는다."""
    compact = _compact(text)
    wanted = _compact(title)
    variants = [wanted]
    activity = RESEARCH_TITLE_ACTIVITY_RE.search(title)
    if activity:
        target = _compact(title[:activity.start()])
        if len(target) >= RESEARCH_TITLE_MIN_TARGET_LENGTH:
            # '장치 기술 개발'을 '장치 기술을 개발한다'로 활용한 동일 대상도 결속한다.
            variants.append(target)
    for wanted in variants:
        if not wanted or wanted not in compact:
            continue
        # 원문 문자 좌표를 유지한 상태 절을 평가하기 위해 끝 위치를 되돌린다.
        for end in range(1, len(text) + 1):
            if wanted in _compact(text[:end]):
                return end
    words = [w for w in RESEARCH_WORD_RE.findall(effect)
             if len(w) >= RESEARCH_EFFECT_MIN_WORD_LENGTH and w not in RESEARCH_EFFECT_STOP_WORDS]
    matches = [re.search(re.escape(word), text, re.IGNORECASE) for word in dict.fromkeys(words)]
    found = [m for m in matches if m]
    if len(found) >= RESEARCH_EFFECT_MIN_MATCH_WORDS:
        return max(m.end() for m in found)
    return None


def _clauses(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in RESEARCH_CLAUSE_BOUNDARY_RE.split(text) if part.strip())


def _states(text: str) -> set[str]:
    return {m.lastgroup for m in RESEARCH_STATE_RE.finditer(text)}


def _source_supports(source: str, title: str, effect: str, wanted: str) -> bool:
    """같은 행 또는 그 과제를 명시한 직접 문장만 상태 증거다."""
    for row_title, row_effect in _rows(source):
        if _compact(row_title) == _compact(title):
            if not RESEARCH_SOURCE_LIMIT_RE.search(row_effect) and wanted in _states(row_effect):
                return True
    # 표 바깥의 직접 실행 문장도 보존하되 다른 행에서 상태를 빌리지 않는다.
    header = RESEARCH_TABLE_HEADER_RE.search(source)
    if header:
        units = RESEARCH_ROW_SEPARATOR_RE.split(source[header.end():])
        # 표 앞과 뒤의 직접 문장을 보존한다. 파싱된 행만 상태 차용에서 제외한다.
        prose_units = []
        for unit in units:
            if _row(unit, header) is None:
                prose_units.append(unit)
        prose = source[:header.start()] + '\n' + '\n'.join(prose_units)
    else:
        prose = source
    for clause in _clauses(prose):
        # 다른 과제가 같은 기술 어구를 쓰더라도 그 과제의 상태를 빌리지 않는다.
        anchor = _anchor(clause, title, '')
        if (anchor is not None and not RESEARCH_SOURCE_LIMIT_RE.search(clause)
                and wanted in _states(clause[anchor:])):
            return True
    return False


def research_table_status_problem(text: str, own_sources: Mapping[str, str]) -> str:
    """자기 인용의 연구 표에 기대는 실행·완료 문장만 검사한다.

    과제 기재·기간·기대효과 표현과 일반 날짜/제목 인용은 이 제한에 들지 않는다.
    각 과제의 직접 상태를 요구하며 다른 과제·행의 진행/완료는 빌리지 않는다.
    """
    if not RESEARCH_STATE_RE.search(text):
        return ''
    rows = tuple(row for source in own_sources.values() for row in _rows(source))
    if not rows:
        return ''
    for clause in _clauses(text):
        for title, effect in rows:
            anchor = _anchor(clause, title, effect)
            if anchor is None:
                continue
            # 여러 과제명을 한 상태 동사에 묶은 문장은 각 행이 같은 상태를 지원해야 한다.
            match = RESEARCH_STATE_RE.search(clause, anchor)
            if not match:
                continue
            description = RESEARCH_DESCRIPTION_RE.search(clause, anchor, match.start())
            if description:
                other_anchor = any(
                    other_title != title
                    and (position := _anchor(clause, other_title, '')) is not None
                    and description.end() < position <= match.start()
                    for other_title, _ in rows
                )
                if other_anchor:
                    # A의 과제 기재와 B의 실제 진행은 서로 다른 서술이다.
                    continue
            if not any(_source_supports(source, title, effect, match.lastgroup)
                       for source in own_sources.values()):
                return RESEARCH_TABLE_STATE_UNSUPPORTED
    return ''

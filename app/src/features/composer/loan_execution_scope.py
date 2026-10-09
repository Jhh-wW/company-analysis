"""대출 한도·다른 대출 실행을 특정 용도의 실제 차입으로 바꾸지 않는다.

명시된 한도/실행 표와 알려진 대출 용도가 있을 때만 검사한다. 원문을 수정하지
않으며 빈 사유는 주체·기간·수치 또는 전체 의미의 승인이나 면제가 아니다.
"""
from collections.abc import Mapping
from decimal import Decimal
import re
import unicodedata

from src.features.composer.loan_execution_scope_constants import (
    LOAN_ACTUAL_ACTION_RE, LOAN_AMOUNT_RE,
    LOAN_AMOUNT_UNIT_RE, LOAN_CLAUSE_RE, LOAN_COORDINATION_RE,
    LOAN_EXECUTED_AMOUNT_RE, LOAN_EXECUTION_HEADER_RE, LOAN_EXECUTION_PROBLEM,
    LOAN_FAMILIES, LOAN_INSTITUTION_COLUMN, LOAN_INSTITUTION_RE,
    LOAN_LIMIT_HEADER_RE, LOAN_NON_ACTUAL_RE, LOAN_PURPOSE_COLUMN,
    LOAN_SOURCE_ROW_RE, LOAN_TABLE_COLUMNS,
    LOAN_NON_ACTUAL_AFTER_RE, LOAN_INSTITUTION_PREFIX_RE,
    LOAN_INSTITUTION_GENERIC_RE, LOAN_EMPTY_EXECUTION_RE,
    LOAN_DIRECT_SUBJECT_RE,
    LOAN_PRESENT_ACTION_RE,
)


def _surface(text: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def _positive_amount(value: str) -> bool:
    return bool(LOAN_AMOUNT_RE.fullmatch(value)
                and Decimal(value.replace(',', '')) > 0)


def _loan_rows(sources: Mapping[str, str]) -> list[tuple[str, str, bool | None]]:
    rows = []
    for source in sources.values():
        execution_index = None
        for row in LOAN_SOURCE_ROW_RE.split(source):
            cells = [_surface(cell) for cell in row.split('|')]
            if len(cells) != LOAN_TABLE_COLUMNS:
                execution_index = None
                continue
            limits = [index for index, cell in enumerate(cells) if LOAN_LIMIT_HEADER_RE.fullmatch(cell)]
            executions = [index for index, cell in enumerate(cells) if LOAN_EXECUTION_HEADER_RE.fullmatch(cell)]
            if len(limits) == len(executions) == 1:
                execution_index = executions[0]
                continue
            if execution_index is None:
                continue
            for family, pattern in LOAN_FAMILIES:
                if pattern.search(cells[LOAN_PURPOSE_COLUMN]):
                    value = cells[execution_index]
                    state = (_positive_amount(value) if LOAN_AMOUNT_RE.fullmatch(value)
                             or LOAN_EMPTY_EXECUTION_RE.fullmatch(value) else None)
                    rows.append((family, cells[LOAN_INSTITUTION_COLUMN],
                                 state))
    return rows


def _institutions(text: str, known: set[str]) -> set[str]:
    institutions = set()
    for match in LOAN_INSTITUTION_RE.finditer(text):
        token = match.group()
        if token in known:
            institutions.add(token)
            continue
        aliases = [name for name in known if token.endswith(name)
                   and LOAN_INSTITUTION_PREFIX_RE.fullmatch(token[:-len(name)])]
        if aliases:
            institutions.add(max(aliases, key=len))
        elif not LOAN_INSTITUTION_GENERIC_RE.fullmatch(token):
            institutions.add(token)
    return institutions


def _actual_loan_claims(clause: str, known: set[str], *,
                        direct_own_only: bool = False,
                        present_only: bool = False) -> list[tuple[str, set[str], bool]]:
    claims = []
    actions = tuple(LOAN_ACTUAL_ACTION_RE.finditer(clause))
    for family, pattern in LOAN_FAMILIES:
        for item in pattern.finditer(clause):
            prior = [action.end() for action in actions if action.end() <= item.start()]
            prefix = clause[max(prior, default=0):item.start()]
            institutions = _institutions(prefix, known)
            if not institutions and prior:
                institutions = _institutions(clause[:max(prior)], known)
            tail = clause[item.end():]
            amount = LOAN_EXECUTED_AMOUNT_RE.match(tail)
            action = LOAN_ACTUAL_ACTION_RE.search(tail)
            if direct_own_only:
                # 은행의 위치와 무관하게 해당 차입 술어 앞의 마지막 명시 주어를 읽는다.
                end = item.end() + (action.start() if action else 0)
                subjects = tuple(LOAN_DIRECT_SUBJECT_RE.finditer(clause[:end]))
                if subjects and subjects[-1]['foreign']:
                    continue
            if amount and _positive_amount(amount['amount']):
                if not present_only:
                    claims.append((family, institutions, False))
                continue
            if (action is None or LOAN_NON_ACTUAL_RE.search(tail[:action.start()])
                    or LOAN_NON_ACTUAL_AFTER_RE.search(tail[action.end():])):
                continue
            present = bool(LOAN_PRESENT_ACTION_RE.match(tail, action.start()))
            if present_only and not present:
                continue
            relation = tail[:action.start()]
            for _, other_pattern in LOAN_FAMILIES:
                relation = other_pattern.sub('', relation)
            relation = LOAN_AMOUNT_UNIT_RE.sub('', relation)
            if LOAN_COORDINATION_RE.fullmatch(relation):
                claims.append((family, institutions, present))
    return claims


def _direct_loan_support(family: str, institution: str, sources: Mapping[str, str],
                         known: set[str], *, present_required: bool) -> bool:
    for source in sources.values():
        for unit in LOAN_SOURCE_ROW_RE.split(source):
            if '|' in unit:
                continue
            for clause in LOAN_CLAUSE_RE.split(_surface(unit)):
                for source_family, institutions, _ in _actual_loan_claims(
                        clause, known, direct_own_only=True, present_only=present_required):
                    if source_family == family and (institution in institutions if institution else True):
                        return True
    return False


def loan_execution_problem(text: str, sources: Mapping[str, str]) -> str:
    """자기 표에서 해당 용도의 실행이 증명되지 않으면 실제 차입 단정을 제한한다.

    '-'는 차입이 절대로 없다는 뜻으로 바꾸지 않는다. 다만 한도나 지급보증,
    다른 용도의 양수 실행액이 해당 대출을 실제 받고 있다는 증명은 아니다.
    """
    rows = _loan_rows(sources)
    if not rows:
        return ''
    known = {institution for _, institution, _ in rows}
    for clause in LOAN_CLAUSE_RE.split(_surface(text)):
        for family, institutions, present in _actual_loan_claims(clause, known):
            matching = [row for row in rows if row[0] == family]
            if not matching:
                continue
            for institution in institutions or {''}:
                supported = [state for _, provider, state in matching
                             if not institution or provider == institution]
                if any(state is True or state is None for state in supported):
                    continue
                if _direct_loan_support(family, institution, sources, known,
                                        present_required=present):
                    continue
                return LOAN_EXECUTION_PROBLEM
    return ''

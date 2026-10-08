"""5장 본문과 표에 같은 사업 과제 범위를 적용한다."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.features.composer.accounting_policy_constants import ACCOUNTING_POLICY_BOILERPLATE
from src.features.composer.accounting_policy_guard import accounting_policy_problem
from src.features.composer.challenge_accounting_policy import is_challenge_accounting_policy
from src.features.composer.challenge_industry_scope import industry_only_challenge_problem
from src.features.composer.constants import CHALLENGE_FLOW_SECTION_ID
from src.features.composer.challenge_constants import (
    CHALLENGE_RESPONSE_CELL_COUNT, CHALLENGE_RESPONSE_CELL_INDEX,
)
from src.features.composer.challenge_event_scope import (
    challenge_event_scope_problem, _event_rows, _selected_rows, _surface,
)
from src.features.composer.challenge_business_scope_constants import (
    PROCEDURAL_ISSUE_ONLY, PROCEDURAL_ISSUE_UNIT_RE, PROCEDURAL_LABEL_RELATION_RE,
    PROCEDURAL_COURT_ISSUE_RE, PROCEDURAL_ACTION_ISSUE_RE, PROCEDURAL_EVENT_LABEL_RE,
    PROCEDURAL_TABLE_ISSUE_RE, TABLE_ACCOUNTING_RESPONSE_UNIT_RE,
    TABLE_ACCOUNTING_RESPONSE_ONLY_RE,
)
from src.shared.report_evidence.challenge_eligibility import (
    challenge_eligibility_problem, challenge_eligibility_quote_problem,
    challenge_incident_row_problem,
    challenge_issue_problem,
)


def _procedural_issue_problem(text: str, *, table_cell: bool = False) -> str:
    """사건명·법원·심급·절차 상태만인 후보를 직접 사업 문제로 세지 않는다.

    형식 밖의 술어·사업 영향이 섞이면 의미 검수에 남긴다. 자기 인용의 다른 절이나
    표의 대응 셀은 절차 전용 후보에 사업 영향을 빌려주지 않는다.
    """
    units = tuple(unit for unit in PROCEDURAL_ISSUE_UNIT_RE.split(text) if unit.strip())
    if not units:
        return ""
    for unit in units:
        compact = "".join(unit.split())
        match = (PROCEDURAL_COURT_ISSUE_RE.fullmatch(compact)
                 or PROCEDURAL_ACTION_ISSUE_RE.fullmatch(compact)
                 or PROCEDURAL_EVENT_LABEL_RE.fullmatch(compact)
                 or (PROCEDURAL_TABLE_ISSUE_RE.fullmatch(compact) if table_cell else None))
        if not match or PROCEDURAL_LABEL_RELATION_RE.search(match.group("label")):
            return ""
    return PROCEDURAL_ISSUE_ONLY


def _accounting_response_cell_problem(text: str) -> str:
    units = tuple(unit for unit in TABLE_ACCOUNTING_RESPONSE_UNIT_RE.split(text)
                  if unit.strip())
    if units and all(TABLE_ACCOUNTING_RESPONSE_ONLY_RE.fullmatch("".join(unit.split()))
                     for unit in units):
        return ACCOUNTING_POLICY_BOILERPLATE
    return ""


def challenge_business_problem(text: str, sources: Mapping[str, str], *, cells: Sequence[str] | None = None,
                               require_current: bool = True, claim_slot: str = "") -> str:
    """자기 인용 전체가 재무 조건뿐인 경우 표현을 바꿔도 사업 과제가 되지 않는다.

    혼합 원문은 그대로 보존한다. 사건표는 같은 행의 날짜·주체·사건 종류·대응 상태를
    기계로 제약하며 나머지 의미 관계는 기존 의미 검수가 판정한다. 다른 장의 실적표나
    인용하지 않은 근거는 이 함수에 넘기지 않는다.
    """
    problem = accounting_policy_problem(text, section_id=CHALLENGE_FLOW_SECTION_ID)
    if problem:
        return problem
    own_texts = tuple(value for value in sources.values() if value.strip())
    if own_texts and all(is_challenge_accounting_policy(value) for value in own_texts):
        return ACCOUNTING_POLICY_BOILERPLATE
    problem = challenge_event_scope_problem(text, sources, cells=cells)
    if problem or not require_current:
        return problem
    # 표의 대응 셀이나 같은 인용의 다른 절에서 문제 관계를 빌리지 않는다.
    issue_text = cells[0] if cells else text
    problem = industry_only_challenge_problem(
        issue_text, claim_slot=("current_challenges:issue" if cells is not None else claim_slot),
    )
    if problem:
        return problem
    if cells is not None or claim_slot == "current_challenges:issue":
        problem = _procedural_issue_problem(issue_text, table_cell=cells is not None)
        if problem:
            return problem
        problem = challenge_issue_problem(issue_text)
        if problem:
            return problem
    if cells is not None and len(cells) == CHALLENGE_RESPONSE_CELL_COUNT:
        problem = _accounting_response_cell_problem(cells[CHALLENGE_RESPONSE_CELL_INDEX])
        if problem:
            return problem
    candidate = " ".join(cells) if cells is not None else text
    problem = challenge_eligibility_problem(candidate)
    if problem:
        return problem
    rows = tuple(row for source in own_texts for row in _event_rows(source))
    selected = _selected_rows(candidate, rows)
    named = tuple(row for row in selected if row.actor and _surface(row.actor) in _surface(candidate))
    if named:
        selected = named
    row_problems = tuple(challenge_incident_row_problem(row.raw_row) for row in selected)
    if row_problems and all(row_problems):
        return row_problems[0]
    # 긍정 성과 원문이나 제외된 사건 절의 인용만으로 실제 대응을 만들지 않는다.
    source_problems = tuple(challenge_eligibility_quote_problem(
        candidate, source, ("current_challenges:issue" if claim_slot == "current_challenges:issue"
                            else "current_challenges:response"),
    ) for source in own_texts)
    return source_problems[0] if source_problems and all(source_problems) else ""

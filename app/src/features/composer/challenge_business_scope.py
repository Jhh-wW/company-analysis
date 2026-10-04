"""5장 본문과 표에 같은 사업 과제 범위를 적용한다."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.features.composer.accounting_policy_constants import ACCOUNTING_POLICY_BOILERPLATE
from src.features.composer.accounting_policy_guard import accounting_policy_problem
from src.features.composer.challenge_accounting_policy import is_challenge_accounting_policy
from src.features.composer.constants import CHALLENGE_FLOW_SECTION_ID
from src.features.composer.challenge_event_scope import (
    challenge_event_scope_problem, _event_rows, _selected_rows, _surface,
)
from src.shared.report_evidence.challenge_eligibility import (
    challenge_eligibility_problem, challenge_eligibility_quote_problem,
    challenge_incident_row_problem,
)


def challenge_business_problem(text: str, sources: Mapping[str, str], *, cells: Sequence[str] | None = None,
                               require_current: bool = True) -> str:
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
        candidate, source, "current_challenges:issue",
    ) for source in own_texts)
    return source_problems[0] if source_problems and all(source_problems) else ""

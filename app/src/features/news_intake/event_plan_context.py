"""지난 행사에 종속된 예정 문장을 현재 계획으로 승격하지 않는다."""

import datetime as dt
import re

from src.features.news_intake import event_plan_context_constants as c
from src.features.news_intake import quote_selection_constants as qc
from src.features.news_intake.identity_names import company_query_names
from src.features.news_intake.models import NewsCandidate, NewsCompanyContext


def event_plan_context_problem(
    text: str, body: str, candidate: NewsCandidate, company: NewsCompanyContext,
    as_of: dt.date, *, temporal_status: str,
) -> str:
    """명시 일정·회사·행사 문맥이 한 가지로 결속될 때만 오래된 계획을 제외한다.

    일정이 지났다고 실행 완료를 추론하지 않는다. 다른 회사·다른 행사·상시 사업은
    이 경계로 제한하지 않으며, 인용 원문이나 사건일 메타데이터를 바꾸지 않는다.
    """
    if temporal_status != 'planned' or not c.EVENT_PLANNED_ACTION_RE.search(text):
        return ''
    if c.EVENT_NEW_CYCLE_RE.search(text):
        return ''
    start = body.find(text)
    schedules = list(c.EVENT_SCHEDULE_RE.finditer(body))
    if start < 0 or body.find(text, start + 1) >= 0 or len(schedules) != 1:
        return ''
    schedule = schedules[0]
    if c.EVENT_AMBIGUOUS_RE.search(body):
        return ''
    published = dt.date.fromisoformat(candidate.published_on)
    if not schedule['year'] and not schedule['upcoming']:
        return ''
    try:
        event_date = dt.date(int(schedule['year'] or published.year),
                             int(schedule['month']), int(schedule['day']))
        if not schedule['year'] and event_date < published:
            event_date = event_date.replace(year=event_date.year + 1)
    except ValueError:
        return ''
    if event_date >= as_of:
        return ''
    prefix = body[max(0, schedule.start() - c.EVENT_CONTEXT_MAX_CHARS):schedule.start()]
    owners = [match for name in company_query_names(company)
              for match in re.finditer(r'(?<![가-힣A-Za-z0-9])' + re.escape(name)
                                       + c.EVENT_OWNER_SUFFIX, prefix, re.I)]
    if not owners:
        return ''
    names = {name for owner in owners for name in c.EVENT_NAME_RE.findall(owner.group())}
    if len(names) != 1:
        return ''
    name = next(iter(names))
    if name in text:
        return c.EVENT_PLAN_EXPIRED
    # 같은 행사의 구체 안내만 연결한다. 독립 출시·투자·상시 서비스 계획은 읽지 않는다.
    if start < schedule.end() or start - schedule.end() > c.EVENT_CONTEXT_MAX_CHARS:
        return ''
    if c.EVENT_NAME_RE.search(text):
        return ''
    preceding = list(qc.QUOTE_SENTENCE_RE.finditer(body[schedule.end():start]))
    local_context = ' '.join(unit.group() for unit in preceding[-c.EVENT_CONTEXT_PREVIOUS_UNITS:])
    return c.EVENT_PLAN_EXPIRED if c.EVENT_CONTEXT_RE.search(local_context) else ''

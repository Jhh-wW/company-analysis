"""4장 완료 실행 슬롯의 명시적 오래된 사건만 제외한다.

verified_prose로 저장되는 완료 실행도 기존 36개월 계약을 따른다. 날짜가
불명확하거나 역사와 최근 실행이 섞인 문장의 날짜를 추정하지 않는다.
"""

from calendar import monthrange
from datetime import date

from src.features.composer.past_execution_period_constants import (
    PAST_EXECUTION_COMPLETED_RE,
    PAST_EXECUTION_COMPLETED_SLOT,
    PAST_EXECUTION_CONNECTED_ENDINGS,
    PAST_EXECUTION_DATE_CONTEXT_RE,
    PAST_EXECUTION_DATE_RE,
    PAST_EXECUTION_LEAP_FALLBACK_DAY,
    PAST_EXECUTION_LOOKBACK_YEARS,
    PAST_EXECUTION_MONTHS_IN_YEAR,
    PAST_EXECUTION_NEGATION_RE,
    PAST_EXECUTION_OUTSIDE_PERIOD,
    PAST_EXECUTION_RELATIVE_DATE_RE,
    PAST_EXECUTION_SENTENCE_RE,
)


def past_completed_execution_period_problem(
    text: str, *, claim_slot: str, baseline_date: str | None,
) -> str:
    """자체 완료일이 모두 범위 밖으로 명백할 때만 장 배치 사유를 반환한다."""
    if claim_slot != PAST_EXECUTION_COMPLETED_SLOT or not baseline_date:
        return ""
    try:
        baseline = date.fromisoformat(baseline_date)
        try:
            cutoff = baseline.replace(year=baseline.year - PAST_EXECUTION_LOOKBACK_YEARS)
        except ValueError:
            cutoff = baseline.replace(
                year=baseline.year - PAST_EXECUTION_LOOKBACK_YEARS,
                day=PAST_EXECUTION_LEAP_FALLBACK_DAY,
            )
    except (TypeError, ValueError):
        return ""

    outside_events = 0
    for sentence in PAST_EXECUTION_SENTENCE_RE.split(text):
        if not sentence.strip():
            continue
        events = tuple(PAST_EXECUTION_COMPLETED_RE.finditer(sentence))
        if not events:
            return ""
        # 알려진 완료 동사 뒤의 다른 사건은 이 좁은 문법으로 시점을 단정하지 않는다.
        last_event = events[-1]
        if (PAST_EXECUTION_DATE_RE.search(sentence[last_event.end():])
                or (last_event.group().endswith(PAST_EXECUTION_CONNECTED_ENDINGS)
                    and sentence[last_event.end():].strip())):
            return ""
        if (PAST_EXECUTION_RELATIVE_DATE_RE.search(sentence)
                or PAST_EXECUTION_NEGATION_RE.search(sentence)):
            return ""
        for event in events:
            preceding = tuple(PAST_EXECUTION_DATE_RE.finditer(sentence[:event.start()]))
            if not preceding:
                return ""
            matched = preceding[-1]
            prefix = sentence[:matched.start()]
            bridge = sentence[matched.end():event.start()]
            # 공시일·설립된 법인의 연혁·준비 시작일을 실행 완료일로 삼지 않는다.
            if (PAST_EXECUTION_DATE_CONTEXT_RE.search(bridge)
                    or PAST_EXECUTION_DATE_CONTEXT_RE.search(prefix)):
                return ""
            year = int(matched['year'])
            month_text = matched['ko_month'] or matched['iso_month']
            day_text = matched['ko_day'] or matched['iso_day']
            month = int(month_text) if month_text else PAST_EXECUTION_MONTHS_IN_YEAR
            try:
                # 연도·월만 있으면 그 기간 전체가 범위 밖일 때만 제외한다.
                latest = date(year, month, int(day_text) if day_text else monthrange(year, month)[1])
            except ValueError:
                return ""
            if latest >= cutoff:
                return ""
            outside_events += 1
    return PAST_EXECUTION_OUTSIDE_PERIOD if outside_events else ""

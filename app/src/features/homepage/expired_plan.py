"""단일 공식 계획의 명시 기한이 지났을 때만 미래 후보에서 제외한다.

구간의 원문이나 사실 상태는 바꾸지 않는다. 둘 이상의 일정·다른 미래절이
섞이면 보존하며, 기한 경과를 완료·취소의 증거로 해석하지 않는다.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime

from src.core.clock import KST
from src.features.homepage.expired_plan_constants import (
    CAPABILITY_DEPENDENCY_RE,
    CURRENT_CAPABILITY_RE,
    EVENT_EXPLANATION_RE,
    EVENT_IDENTITY_RE,
    EVENT_LABEL_COUNT_RE,
    EVENT_NOTICE_CLAUSE_RE,
    EVENT_SCHEDULE_LABEL_RE,
    EVENT_TOPIC_SIGNAL,
    INDEPENDENT_PREFIX_RE,
    LAST_MONTH,
    MONTHS_PER_QUARTER,
    ONLY_SCHEDULE_COUNT,
    PLAN_CLAUSE_RE,
    PLAN_DEADLINE_TAIL_RE,
    PLAN_END_RE,
    PLAN_PERIOD_RE,
    PLAN_SIGNAL_RE,
    SHORT_YEAR_CENTURY,
    YEARS_PER_CENTURY,
)
from src.shared.report_evidence.date_normalization import normalize_official_source_date


def _collection_date(value: str) -> date | None:
    try:
        return date.fromisoformat(normalize_official_source_date(value))
    except ValueError:
        # 이전 수집 계약의 timezone 있는 ISO 시각도 같은 한국 영업일로 대조한다.
        # 날짜 일부만 잘라 잘못된 시각을 정상 기준일로 바꾸지는 않는다.
        try:
            stamp = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        return stamp.astimezone(KST).date() if stamp.tzinfo is not None else None


def _period_end(match: re.Match[str], baseline: date) -> date | None:
    year = int(match["year"])
    if year < YEARS_PER_CENTURY:
        if not match["quarter"] or not (
            SHORT_YEAR_CENTURY <= baseline.year < SHORT_YEAR_CENTURY + YEARS_PER_CENTURY
        ):
            return None
        year += SHORT_YEAR_CENTURY
    try:
        if match["quarter"]:
            month = int(match["quarter"]) * MONTHS_PER_QUARTER
        else:
            month = int(match["month"]) if match["month"] else LAST_MONTH
        day = int(match["day"]) if match["day"] else calendar.monthrange(year, month)[1]
        return date(year, month, day)
    except ValueError:
        return None


def _single_event_notices(text: str, period: re.Match[str]) -> bool:
    """단일 일시 표제와 그 행사 참가 안내만 남은 구간인가."""
    if (
        not period["day"]
        or len(EVENT_LABEL_COUNT_RE.findall(text)) != ONLY_SCHEDULE_COUNT
        or EVENT_SCHEDULE_LABEL_RE.search(text[:period.start()]) is None
        or EVENT_IDENTITY_RE.search(text) is None
    ):
        return False
    notices = 0
    for clause_match in PLAN_CLAUSE_RE.finditer(text):
        clause = clause_match.group()
        signals = tuple(PLAN_SIGNAL_RE.finditer(clause))
        if not signals:
            continue
        if (
            all(signal.group() == EVENT_TOPIC_SIGNAL for signal in signals)
            and EVENT_EXPLANATION_RE.fullmatch(clause)
        ):
            continue
        if EVENT_NOTICE_CLAUSE_RE.fullmatch(clause) is None:
            return False
        notices += 1
    return notices >= ONLY_SCHEDULE_COUNT


def _closed_deadline_clause(text: str, period: re.Match[str], deadline: re.Match[str]) -> bool:
    """독립절·알 수 없는 꼬리를 삭제하지 않는 닫힌 일정 표현인가."""
    for clause in PLAN_CLAUSE_RE.finditer(text):
        if clause.start() <= period.start() < clause.end():
            if (
                PLAN_SIGNAL_RE.search(text[clause.start():period.start()])
                or INDEPENDENT_PREFIX_RE.search(text[clause.start():period.start()])
                or PLAN_END_RE.fullmatch(text[deadline.end():clause.end()]) is None
            ):
                return False
        elif (
            CURRENT_CAPABILITY_RE.fullmatch(clause.group()) is None
            or PLAN_SIGNAL_RE.search(clause.group())
            or CAPABILITY_DEPENDENCY_RE.search(clause.group())
        ):
            return False
    return True


def only_expired_plan(text: str, *, collected_at: str) -> bool:
    """유일한 명시 계획 기한이 수집일보다 앞설 때만 참이다."""
    baseline = _collection_date(collected_at)
    if baseline is None:
        return False
    periods = tuple(PLAN_PERIOD_RE.finditer(text))
    if len(periods) != ONLY_SCHEDULE_COUNT:
        return False
    period = periods[0]
    end = _period_end(period, baseline)
    if end is None or end >= baseline:
        return False

    deadline = PLAN_DEADLINE_TAIL_RE.match(text, period.end())
    if deadline is not None:
        return _closed_deadline_clause(text, period, deadline)
    return _single_event_notices(text, period)

"""지난 예정일을 현재 미래계획으로 복사하지 않는다. 실제 완료는 추정하지 않는다."""

from calendar import monthrange
from collections.abc import Mapping, Sequence
from datetime import date
import re
import unicodedata

from src.features.composer.plan_timing_constants import (
    PLAN_CURRENT_DATE_RE, PLAN_HISTORICAL_REPORT_RE, PLAN_NONCURRENT_SOURCE_SUFFIX_RE,
    PLAN_PAST_QUOTE_RE, PLAN_PRESENT_NEGATED_RE, PLAN_TARGET_YEAR_OUTDATED, PLAN_THEN_RE,
)
from src.features.composer.scheduled_plan_date_constants import (
    SCHEDULE_CHANGED_RE, SCHEDULE_CLAUSE_RE, SCHEDULE_CONTEXT_CHARS,
    SCHEDULE_DATE_LINK_RE, SCHEDULE_DATE_RE, SCHEDULE_FUTURE_RE,
    SCHEDULE_MODIFIER_RE, SCHEDULE_NON_TARGET_RE, SCHEDULE_NORMALIZE_RE,
    SCHEDULE_OBJECT_RE, SCHEDULE_REPORTED_PLAN_RE,
    SCHEDULE_REFERENCE_PREFIX, SCHEDULE_PERIODIC_EVENT_PREFIX_RE,
    SCHEDULE_AGENDA_RE, SCHEDULE_AGENDA_ACTION_RE,
    SCHEDULE_RELATED_AGENDA_RE, SCHEDULE_UNQUALIFIED_REAPPOINTMENT_RE,
)


def _surface(text: str) -> str:
    return SCHEDULE_NORMALIZE_RE.sub("", unicodedata.normalize("NFKC", text))


def _period_end(match: re.Match[str]) -> date | None:
    """월·분기·연도는 마지막 날까지 지나야 만료된다. 특정 날짜를 추정하지 않는다."""
    year = int(match["year"])
    month_text = match["month"] or match["iso_month"]
    day_text = match["day"] or match["iso_day"]
    month = int(month_text) if month_text else int(match["quarter"]) * 3 if match["quarter"] else 12
    try:
        return date(year, month, int(day_text) if day_text else monthrange(year, month)[1])
    except ValueError:
        return None


def _historical(clause: str, start: int, end: int) -> bool:
    """계획 자체를 과거 자료의 말로 설명한 경우만 회고로 남긴다."""
    return bool((PLAN_PAST_QUOTE_RE.search(clause[end:]) or SCHEDULE_REPORTED_PLAN_RE.search(clause)) and (
        PLAN_THEN_RE.search(clause[:start])
        or PLAN_HISTORICAL_REPORT_RE.search(clause[:start])
    ))


def _reaffirmed(claim: str, sources: Mapping[str, str], baseline: date) -> bool:
    """같은 주장 전체를 명시 현재 기준일에 재확인한 자기 원문만 예외로 읽는다."""
    candidate = _surface(claim).rstrip("。")
    for source in sources.values():
        for clause in SCHEDULE_CLAUSE_RE.split(source):
            for stamp in PLAN_CURRENT_DATE_RE.finditer(clause):
                try:
                    end = date(int(stamp["year"]), int(stamp["month"] or 0), int(stamp["day"] or 0))
                except ValueError:
                    continue
                # 현재 기준일은 월·일이 명시돼야 한다. 게시일·연도는 빌리지 않는다.
                if not stamp["month"] or not stamp["day"] or end is None or end.year != baseline.year or end > baseline:
                    continue
                tail = _surface(clause[stamp.end():])
                position = tail.find(candidate)
                if position < 0:
                    continue
                suffix = tail[position + len(candidate):]
                if not (PLAN_NONCURRENT_SOURCE_SUFFIX_RE.search(suffix) or PLAN_PRESENT_NEGATED_RE.search(suffix)):
                    return True
    return False


def _dated_plans(clause: str):
    for stamp in SCHEDULE_DATE_RE.finditer(clause):
        end_date = _period_end(stamp)
        if end_date is None:
            continue
        tail = clause[stamp.end():stamp.end() + SCHEDULE_CONTEXT_CHARS]
        link = SCHEDULE_DATE_LINK_RE.match(tail)
        # '개최 예정인'은 행사 수식어다. 끝의 과거 완료를 현재 계획으로 바꾸지 않는다.
        predicate = next((item for item in SCHEDULE_FUTURE_RE.finditer(tail, link.end())
                          if not item.group().endswith("예정인")), None)
        # 보고연도·실적기간·사업 시작 이후를 다음 예정일로 바꾸지 않는다.
        if predicate and SCHEDULE_NON_TARGET_RE.search(tail[:predicate.start()]):
            continue
        if predicate is None or SCHEDULE_CHANGED_RE.search(tail[:predicate.end()]):
            continue
        if SCHEDULE_DATE_RE.search(tail[:predicate.start()]):
            continue
        absolute_end = stamp.end() + predicate.end()
        if _historical(clause, stamp.start(), absolute_end):
            continue
        yield stamp, end_date, predicate, tail


def _same_agenda(source_body: str, claim_body: str, claim_intro: str) -> bool:
    """안건 상정의 마지막 '결의'만으로 다른 안건의 날짜를 빌리지 않는다."""
    if not (SCHEDULE_AGENDA_RE.search(source_body) or SCHEDULE_AGENDA_RE.search(claim_body)):
        return True
    source_action = SCHEDULE_AGENDA_ACTION_RE.search(source_body)
    claim_action = SCHEDULE_AGENDA_ACTION_RE.search(claim_body)
    if claim_action is None and SCHEDULE_UNQUALIFIED_REAPPOINTMENT_RE.match(claim_body):
        # '감사 선임과 관련하여 같은 행사에서 중임 건'의 명시된 앞 안건만 잇는다.
        related = SCHEDULE_RELATED_AGENDA_RE.search(claim_intro)
        if related:
            claim_body = related["agenda"]
            claim_action = SCHEDULE_AGENDA_ACTION_RE.search(claim_body)
    if not (source_action and claim_action) or source_action.group() != claim_action.group():
        return False
    target = _surface(source_body[:source_action.start()]).rstrip("의")
    return bool(target and target in _surface(claim_body[:claim_action.start()]))


def _same_undated_event(claim: str, source_clause: str, tail: str) -> bool:
    """자기 원문의 같은 행사와 같은 목적어·예정행위만 날짜 제약으로 운반한다."""
    event = SCHEDULE_MODIFIER_RE.search(tail)
    if event is None:
        return False
    compact = _surface(claim)
    event_name = event["event"]
    shortened = SCHEDULE_PERIODIC_EVENT_PREFIX_RE.sub("", event_name, count=1)
    reference = re.search(
        SCHEDULE_REFERENCE_PREFIX + r"(?:제(?P<term>\d+)기)?"
        + "(?:" + "|".join(re.escape(value) for value in dict.fromkeys((event_name, shortened))) + ")"
        + r"(?:에서|에|을|를)", compact,
    )
    direct = re.search(r"(?:제(?P<term>\d+)기)?" + re.escape(event_name) + r"(?:에서|에|을|를)", compact)
    claim_event = reference or direct
    if claim_event is None:
        return False
    # 지시어 유무와 무관하게 명시된 다른 회차의 날짜는 운반하지 않는다.
    if claim_event["term"] and claim_event["term"] != event["term"]:
        return False
    source_compact = _surface(source_clause)
    source_event = re.search(re.escape(event_name) + r"(?:에서|에|을|를)", source_compact)
    if source_event is None:
        return False
    for predicate in SCHEDULE_FUTURE_RE.finditer(source_clause):
        obj = SCHEDULE_OBJECT_RE.search(source_clause[:predicate.start()])
        if obj is None:
            continue
        for claim_predicate in SCHEDULE_FUTURE_RE.finditer(claim):
            claim_object = SCHEDULE_OBJECT_RE.search(claim[:claim_predicate.start()])
            if (claim_object and _surface(obj["object"]) == _surface(claim_object["object"])
                    and predicate["action"] == claim_predicate["action"]):
                source_body = source_compact[source_event.end():len(_surface(source_clause[:predicate.start()]))]
                claim_body = compact[claim_event.end():len(_surface(claim[:claim_predicate.start()]))]
                if _same_agenda(source_body, claim_body, compact[:claim_event.start()]):
                    return True
    return False


def scheduled_plan_date_problem(
    text: str, sources: Mapping[str, str], cells: Sequence[str] | None = None,
    *, baseline_date: str | None = None,
) -> str:
    """빈 반환은 승인이 아니다. 자기 원문·시점 증명 검사는 기존 경로가 이어서 한다."""
    try:
        baseline = date.fromisoformat(baseline_date or "")
    except (TypeError, ValueError):
        return ""
    for candidate in (cells if cells is not None else (text,)):
        for clause in SCHEDULE_CLAUSE_RE.split(candidate):
            dated = tuple(_dated_plans(clause))
            if any(end < baseline for _, end, _, _ in dated) and not _reaffirmed(clause, sources, baseline):
                return PLAN_TARGET_YEAR_OUTDATED
            # 명시 다른 예정일을 원문의 과거 행사와 합치지 않는다.
            if SCHEDULE_DATE_RE.search(clause) or not SCHEDULE_FUTURE_RE.search(clause):
                continue
            if _historical(clause, 0, SCHEDULE_FUTURE_RE.search(clause).end()):
                continue
            if SCHEDULE_CHANGED_RE.search(clause):
                continue
            matching_periods = []
            for source in sources.values():
                for source_clause in SCHEDULE_CLAUSE_RE.split(source):
                    for _, end, _, tail in _dated_plans(source_clause):
                        if _same_undated_event(clause, source_clause, tail):
                            matching_periods.append(end)
            if matching_periods and all(end < baseline for end in matching_periods) and not _reaffirmed(clause, sources, baseline):
                return PLAN_TARGET_YEAR_OUTDATED
    return ""

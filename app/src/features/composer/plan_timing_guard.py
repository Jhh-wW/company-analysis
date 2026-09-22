"""지난 목표연도를 확인 없이 현재 진행·예정으로 제시하지 않는다.

명시 목표연도가 기준연도보다 앞선 경우만 검사한다. 날짜나 완료 사실을
추정하여 고쳐 쓰지 않으며, 다른 시점·계획 검수의 근거 요건도 완화하지 않는다.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from re import Match
import unicodedata

from src.features.composer.plan_timing_constants import (
    DATE_PERIOD_FIRST_COMPONENT, PLAN_CHANGED_RE, PLAN_CHANGE_DENIED_RE, PLAN_CURRENT_DATE_RE,
    PLAN_GOAL_RE, PLAN_HISTORICAL_REPORT_RE, PLAN_LINK_INTERRUPTED_RE,
    PLAN_NONCURRENT_SOURCE_SUFFIX_RE, PLAN_NORMALIZE_RE, PLAN_PAST_QUOTE_RE, PLAN_PREDICATE_RE,
    PLAN_PRESENT_NEGATED_RE, PLAN_SENTENCE_SPLIT_RE, PLAN_TARGET_YEAR_OUTDATED, PLAN_THEN_RE,
    PLAN_TIMING_CONTEXT_CHARS, PLAN_TIMING_NEGATION_CHARS,
    PLAN_YEAR_AFTER_ACTIVITY_RE, PLAN_YEAR_BEFORE_ACTIVITY_RE,
)


def _compact(text: str) -> str:
    return PLAN_NORMALIZE_RE.sub("", unicodedata.normalize("NFKC", text).casefold())


def _recorded_date(match: Match[str]) -> date | None:
    try:
        return date(
            int(match["year"]),
            int(match["month"] or DATE_PERIOD_FIRST_COMPONENT),
            int(match["day"] or DATE_PERIOD_FIRST_COMPONENT),
        )
    except ValueError:
        return None


def _reaffirmed_currently(claim: str, sources: Mapping[str, str], baseline: date) -> bool:
    """같은 문장 안의 명시 최신 기준시점과 동일 주장을 함께 요구한다.

    문서 게시일·별도 문장의 현재 표지는 활동의 최신 근거로 빌리지 않는다.
    의미가 비슷하다는 추정도 하지 않고 공백·문장부호 차이만 정규화한다.
    """
    compact_claim = _compact(claim)
    for source in sources.values():
        for clause in PLAN_SENTENCE_SPLIT_RE.split(source):
            for match in PLAN_CURRENT_DATE_RE.finditer(clause):
                current_at = _recorded_date(match)
                if current_at is None or current_at.year != baseline.year or current_at > baseline:
                    continue
                compact_source = _compact(clause[match.end():])
                position = compact_source.find(compact_claim)
                while position >= 0:
                    suffix = compact_source[position + len(compact_claim):]
                    if not (
                        PLAN_PRESENT_NEGATED_RE.search(suffix)
                        or PLAN_NONCURRENT_SOURCE_SUFFIX_RE.search(suffix)
                    ):
                        return True
                    position = compact_source.find(compact_claim, position + len(compact_claim))
    return False


def _historical_quote(clause: str, target_start: int, predicate_end: int, baseline: date) -> bool:
    """과거 기준이 있는 인용의 닫는 서술어에만 회고 예외를 결속한다."""
    if not PLAN_PAST_QUOTE_RE.search(clause[predicate_end:]):
        return False
    prefix = clause[:target_start]
    if PLAN_THEN_RE.search(prefix):
        return True
    return any(
        recorded_at is not None and recorded_at <= baseline
        for recorded_at in (
            _recorded_date(match) for match in PLAN_HISTORICAL_REPORT_RE.finditer(prefix)
        )
    )


def plan_timing_problem(
    text: str,
    sources: Mapping[str, str],
    cells: Sequence[str] | None = None,
    *,
    baseline_date: str | None = None,
) -> str:
    """지난 목표와 현재 서술이 결속되면 거절하며, 미확인 날짜는 추정하지 않는다."""
    if not baseline_date:
        return ""
    try:
        baseline = date.fromisoformat(baseline_date)
    except (TypeError, ValueError):
        return ""
    # 도식에서는 다른 칸의 연도·계획을 연결해 새 의미를 만들지 않는다.
    for candidate in (cells if cells is not None else (text,)):
        for clause in PLAN_SENTENCE_SPLIT_RE.split(candidate):
            targets = (
                *PLAN_YEAR_BEFORE_ACTIVITY_RE.finditer(clause),
                *PLAN_YEAR_AFTER_ACTIVITY_RE.finditer(clause),
            )
            for target in targets:
                if int(target["year"]) >= baseline.year:
                    continue
                tail = clause[target.end():target.end() + PLAN_TIMING_CONTEXT_CHARS]
                goal = PLAN_GOAL_RE.search(tail)
                if goal is None or PLAN_LINK_INTERRUPTED_RE.search(tail[:goal.start()]):
                    continue
                predicate = PLAN_PREDICATE_RE.search(tail, goal.start())
                if predicate is None or predicate.lastgroup == "past":
                    continue
                if any(
                    not PLAN_CHANGE_DENIED_RE.search(tail[change.end():])
                    for change in PLAN_CHANGED_RE.finditer(tail, 0, predicate.end())
                ):
                    continue
                if PLAN_PRESENT_NEGATED_RE.search(
                    tail[predicate.end():predicate.end() + PLAN_TIMING_NEGATION_CHARS]
                ):
                    continue
                predicate_end = target.end() + predicate.end()
                if _historical_quote(clause, target.start(), predicate_end, baseline):
                    continue
                claim = clause[:predicate_end]
                if _reaffirmed_currently(claim, sources, baseline):
                    continue
                return PLAN_TARGET_YEAR_OUTDATED
    return ""

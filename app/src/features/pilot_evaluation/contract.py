"""Evaluation-only contract for the authorized P01-P10 real-company reports.

The immutable comparison manifest still contains 25 candidates, but the user
authorized paid execution only for the first ten listed companies. Human
judgments collected here calibrate the automatic checks. They never authorize
an individual production report and are not read by runtime release code. This
module performs no provider calls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Final, Iterable

from src.shared.final_gate_diagnostics import SAFE_FINAL_GATE_REASONS


class PilotCategory(str, Enum):
    LISTED = "listed"
    UNLISTED_DISCLOSURE = "unlisted_disclosure"
    SPARSE_OR_AMBIGUOUS = "sparse_or_ambiguous"


REQUIRED_CATEGORY_COUNTS: Final[dict[PilotCategory, int]] = {
    PilotCategory.LISTED: 10,
    PilotCategory.UNLISTED_DISCLOSURE: 0,
    PilotCategory.SPARSE_OR_AMBIGUOUS: 0,
}
TOTAL_CASES: Final[int] = 10
MIN_CORRECT_IDENTITIES: Final[int] = 10
MIN_COMPLETE_REPORTS: Final[int] = 8
MIN_AUTO_USER_AGREEMENT: Final[float] = 0.90
MAX_P90_ELAPSED_SEC: Final[float] = 30 * 60
MAX_AVG_AI_COST_KRW: Final[float] = 300.0
MAX_P90_AI_COST_KRW: Final[float] = 500.0


@dataclass(frozen=True)
class PilotCase:
    case_id: str
    category: PilotCategory
    company_name: str


@dataclass(frozen=True)
class PilotResult:
    case: PilotCase
    legal_entity_correct: bool
    completed: bool
    stopped: bool
    error_type: str
    automatic_judgment: str
    user_judgment: str
    judgments_agree: bool
    elapsed_sec: float
    internal_ai_cost_krw: float
    wrong_legal_entity_released: bool = False
    partial_report_released: bool = False
    major_fact_citation_numeric_error_auto_passed: bool = False
    final_gate_reason: str = ""


@dataclass(frozen=True)
class PilotSummary:
    passed: bool
    reasons: tuple[str, ...]
    case_count: int
    correct_identities: int
    complete_reports: int
    agreement_rate: float
    p90_elapsed_sec: float
    average_ai_cost_krw: float
    p90_ai_cost_krw: float


def _nearest_rank_p90(values: Iterable[float]) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    return ordered[max(0, math.ceil(len(ordered) * 0.9) - 1)]


def _validate_result(result: PilotResult) -> None:
    if not result.case.case_id.strip() or not result.case.company_name.strip():
        raise ValueError("파일럿 실행 전 case ID와 회사명 목록을 확정해야 합니다")
    if result.completed == result.stopped:
        raise ValueError("파일럿 결과는 완성 또는 중단 중 정확히 하나여야 합니다")
    if not result.automatic_judgment.strip() or not result.user_judgment.strip():
        raise ValueError("자동판정과 사용자 품질판정을 모두 기록해야 합니다")
    if result.elapsed_sec < 0 or result.internal_ai_cost_krw < 0:
        raise ValueError("처리시간과 내부 AI 원가는 0 이상이어야 합니다")
    if result.final_gate_reason and result.final_gate_reason not in SAFE_FINAL_GATE_REASONS:
        raise ValueError("파일럿 최종 게이트 사유가 허용된 닫힌 코드가 아닙니다")
    if result.completed and result.final_gate_reason:
        raise ValueError("완성 보고서에는 최종 게이트 사유가 있을 수 없습니다")


def evaluate_pilot(results: Iterable[PilotResult]) -> PilotSummary:
    """Evaluate the thresholds fixed before viewing the ten paid outcomes."""

    rows = tuple(results)
    for row in rows:
        _validate_result(row)
    reasons: list[str] = []
    if len(rows) != TOTAL_CASES:
        reasons.append("P01~P10 10/10 실행 및 사용자 품질기록이 필요합니다")
    category_counts = {
        category: sum(row.case.category is category for row in rows)
        for category in PilotCategory
    }
    if category_counts != REQUIRED_CATEGORY_COUNTS:
        reasons.append("실제 유료 표본은 P01~P10 상장사 10건이어야 합니다")
    if len({row.case.case_id for row in rows}) != len(rows):
        reasons.append("파일럿 case ID가 중복됐습니다")

    correct = sum(row.legal_entity_correct for row in rows)
    complete = sum(row.completed for row in rows)
    agreement = (
        sum(row.judgments_agree for row in rows) / len(rows) if rows else 0.0
    )
    completed_rows = tuple(row for row in rows if row.completed)
    p90_elapsed = _nearest_rank_p90(row.elapsed_sec for row in completed_rows)
    avg_cost = mean(row.internal_ai_cost_krw for row in rows) if rows else 0.0
    p90_cost = _nearest_rank_p90(row.internal_ai_cost_krw for row in rows)

    if correct < MIN_CORRECT_IDENTITIES:
        reasons.append("정확한 법인 식별이 10/10 미만입니다")
    if any(row.wrong_legal_entity_released for row in rows):
        reasons.append("잘못된 법인이 출고된 사례가 있습니다")
    if complete < MIN_COMPLETE_REPORTS:
        reasons.append("완전한 보고서가 80% 미만입니다")
    if any(row.partial_report_released for row in rows):
        reasons.append("부분 보고서가 출고된 사례가 있습니다")
    if any(
        row.major_fact_citation_numeric_error_auto_passed for row in rows
    ):
        reasons.append("중대 사실·인용·수치 오류가 자동통과한 사례가 있습니다")
    if agreement < MIN_AUTO_USER_AGREEMENT:
        reasons.append("자동판정과 사용자 판정 일치율이 90% 미만입니다")
    if p90_elapsed > MAX_P90_ELAPSED_SEC:
        reasons.append("완료 건 처리시간 P90이 30분을 넘습니다")
    if avg_cost > MAX_AVG_AI_COST_KRW:
        reasons.append("AI 변동원가 평균이 목표 300원을 넘습니다")
    if p90_cost > MAX_P90_AI_COST_KRW:
        reasons.append("AI 변동원가 P90이 목표 500원을 넘습니다")
    return PilotSummary(
        passed=not reasons,
        reasons=tuple(reasons),
        case_count=len(rows),
        correct_identities=correct,
        complete_reports=complete,
        agreement_rate=agreement,
        p90_elapsed_sec=p90_elapsed,
        average_ai_cost_krw=float(avg_cost),
        p90_ai_cost_krw=p90_cost,
    )

"""이미 계산된 SHADOW 관측값을 다시 판정하지 않고 세기만 하는 순수 집계기.

★ 이 모듈은 DB·파일·네트워크를 전혀 모른다. 호출부(`admin_dashboard`)가
  저장소에서 이미 복원한 `GenerationQualityObservation` 값들을 건네주면
  그 값 그대로 센다. `MIN_SUBSTANTIVE_CLAIMS`·`MIN_VERIFIED_RATIO` 같은
  생산 임계값은 여기서 절대 import하지 않는다 — 임계값이 바뀌어도 과거에
  저장된 `release_allowed`·`quality_problem_codes`가 이미 그 결과를 담고
  있으므로, 이 모듈이 다시 판정하면 판정 기준이 두 곳에 생겨 서로 어긋날
  수 있다. 이 모듈은 오직 이미 내려진 판정을 «세는 일»만 한다.

★ float을 쓰지 않는다. 건수는 `int`, 비율은 `Decimal` 나눗셈 결과를
  `format(x, "f")`로 고정소수점 문자열로 바꿔 돌려준다(과학적 표기 방지).
  분모가 0이면 비율은 `None`이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from src.shared.report_quality.generation import GenerationQualityObservation


#: (report_id, company_id, generated_at, release_mode, observation).
#: 네 문자열 필드는 저장 스키마의 원시 값을 그대로 옮긴 것이다 — 현재
#: 운영 SHADOW 보고서는 `company_id`·`release_mode`가 저장 시점에 빈
#: 문자열로 남는다(별도 결함, 이 집계기가 만든 값이 아니다). 이 모듈은
#: 그 빈 문자열도 하나의 유효한 그룹 키로 그대로 센다.
ObservationRow = tuple[str, str, str, str, GenerationQualityObservation]


def _require_str(value: object, *, label: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{label}은 문자열이어야 합니다: {value!r}")
    return value


def _ratio(numerator: int, denominator: int) -> str | None:
    """`numerator / denominator`를 canonical 고정소수점 문자열로. 분모 0이면 None."""

    if denominator == 0:
        return None
    if denominator < 0 or numerator < 0:
        raise ValueError("건수는 음수일 수 없습니다")
    return format(Decimal(numerator) / Decimal(denominator), "f")


@dataclass(frozen=True)
class ReleaseModeBreakdown:
    """`release_mode` 한 값에 대한 부분 집계."""

    release_mode: str
    total_count: int
    release_blocked_count: int
    release_blocked_ratio: str | None


@dataclass(frozen=True)
class CompanyObservationCount:
    """회사(`company_id`) 한 곳에 대한 부분 집계."""

    company_id: str
    total_count: int
    release_blocked_count: int


@dataclass(frozen=True)
class QualityObservationSummary:
    """관측값 모음 전체를 한 번 센 결과. 입력 순서와 무관하게 결정론적이다."""

    total_count: int
    release_blocked_count: int
    release_blocked_ratio: str | None
    #: `safety_decision` 값(예: "공개 가능"/"공개 차단")별 건수. 값 오름차순 정렬.
    safety_decision_counts: tuple[tuple[str, int], ...]
    #: `quality_grade` 값(예: "완성"/"부분 완성"/"미완성")별 건수. 값 오름차순 정렬.
    quality_grade_counts: tuple[tuple[str, int], ...]
    #: `quality_problem_codes`는 한 관측값에 여러 코드가 동시에 있을 수 있어
    #: 코드 하나당 그 코드를 가진 관측값 수를 센다(총합이 total_count를
    #: 넘을 수 있다). 코드 오름차순 정렬.
    quality_problem_code_counts: tuple[tuple[str, int], ...]
    #: `release_mode` 값별 분해. 값 오름차순 정렬.
    release_mode_breakdown: tuple[ReleaseModeBreakdown, ...]
    #: 거절 후보(`release_blocked_count`) 많은 회사 순 → 총 건수 순 →
    #: `company_id` 오름차순으로 상위 `top_companies_limit`건만 남긴다.
    top_companies: tuple[CompanyObservationCount, ...]


def summarize_quality_observations(
    rows: Iterable[ObservationRow],
    *,
    top_companies_limit: int = 20,
) -> QualityObservationSummary:
    """관측값 모음을 한 번 순회해 `QualityObservationSummary`로 센다.

    Args:
        rows: `(report_id, company_id, generated_at, release_mode, observation)`
            튜플의 모음. 순서는 결과에 영향을 주지 않는다.
        top_companies_limit: `top_companies`에 남길 회사 수 상한(0 이상).

    Raises:
        TypeError: 문자열 필드가 문자열이 아니거나 `observation`이 정확히
            `GenerationQualityObservation`이 아닐 때.
        ValueError: `top_companies_limit`이 음수일 때.
    """

    if top_companies_limit < 0:
        raise ValueError("top_companies_limit은 0 이상이어야 합니다")

    total_count = 0
    release_blocked_count = 0
    safety_decision_counter: dict[str, int] = {}
    quality_grade_counter: dict[str, int] = {}
    problem_code_counter: dict[str, int] = {}
    release_mode_totals: dict[str, int] = {}
    release_mode_blocked: dict[str, int] = {}
    company_totals: dict[str, int] = {}
    company_blocked: dict[str, int] = {}

    for row in rows:
        if len(row) != 5:
            raise TypeError(f"관측 행은 5-튜플이어야 합니다: {row!r}")
        report_id, company_id, generated_at, release_mode, observation = row
        _require_str(report_id, label="report_id")
        _require_str(company_id, label="company_id")
        _require_str(generated_at, label="generated_at")
        _require_str(release_mode, label="release_mode")
        if type(observation) is not GenerationQualityObservation:
            raise TypeError(
                f"observation은 정확히 GenerationQualityObservation이어야 합니다: "
                f"{observation!r}"
            )

        total_count += 1
        blocked = not observation.release_allowed
        if blocked:
            release_blocked_count += 1

        safety_decision_counter[observation.safety_decision] = (
            safety_decision_counter.get(observation.safety_decision, 0) + 1
        )
        quality_grade_counter[observation.quality_grade] = (
            quality_grade_counter.get(observation.quality_grade, 0) + 1
        )
        for code in observation.quality_problem_codes:
            problem_code_counter[code] = problem_code_counter.get(code, 0) + 1

        release_mode_totals[release_mode] = release_mode_totals.get(release_mode, 0) + 1
        if blocked:
            release_mode_blocked[release_mode] = (
                release_mode_blocked.get(release_mode, 0) + 1
            )

        company_totals[company_id] = company_totals.get(company_id, 0) + 1
        if blocked:
            company_blocked[company_id] = company_blocked.get(company_id, 0) + 1

    release_mode_breakdown = tuple(
        ReleaseModeBreakdown(
            release_mode=mode,
            total_count=release_mode_totals[mode],
            release_blocked_count=release_mode_blocked.get(mode, 0),
            release_blocked_ratio=_ratio(
                release_mode_blocked.get(mode, 0), release_mode_totals[mode]
            ),
        )
        for mode in sorted(release_mode_totals)
    )

    companies_sorted = sorted(
        company_totals,
        key=lambda company_id: (
            -company_blocked.get(company_id, 0),
            -company_totals[company_id],
            company_id,
        ),
    )
    top_companies = tuple(
        CompanyObservationCount(
            company_id=company_id,
            total_count=company_totals[company_id],
            release_blocked_count=company_blocked.get(company_id, 0),
        )
        for company_id in companies_sorted[:top_companies_limit]
    )

    return QualityObservationSummary(
        total_count=total_count,
        release_blocked_count=release_blocked_count,
        release_blocked_ratio=_ratio(release_blocked_count, total_count),
        safety_decision_counts=tuple(
            (key, safety_decision_counter[key]) for key in sorted(safety_decision_counter)
        ),
        quality_grade_counts=tuple(
            (key, quality_grade_counter[key]) for key in sorted(quality_grade_counter)
        ),
        quality_problem_code_counts=tuple(
            (key, problem_code_counter[key]) for key in sorted(problem_code_counter)
        ),
        release_mode_breakdown=release_mode_breakdown,
        top_companies=top_companies,
    )

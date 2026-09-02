from __future__ import annotations

import random

import pytest

from src.shared.report_quality.generation import GenerationQualityObservation
from src.shared.report_quality.observation_summary import (
    CompanyObservationCount,
    ReleaseModeBreakdown,
    summarize_quality_observations,
)


def _observation(
    *,
    release_allowed: bool = True,
    safety_decision: str = "공개 가능",
    quality_grade: str = "완성",
    quality_problem_codes: tuple[str, ...] = (),
) -> GenerationQualityObservation:
    """시험에서만 쓰는 최소 관측값 공장. 실제 판정 로직은 흉내 내지 않는다."""

    return GenerationQualityObservation(
        mode="generation-shadow",
        contract_version="",
        quality_grade=quality_grade,
        safety_decision=safety_decision,
        publication_grade=quality_grade,
        release_allowed=release_allowed,
        quality_shortfalls=(),
        safety_problems=(),
        substantive_claims=10,
        verified_claims=5,
        verified_ratio="0.5",
        document_sources=3,
        quality_problem_codes=quality_problem_codes,
    )


def _row(
    report_id: str,
    company_id: str,
    generated_at: str,
    release_mode: str,
    observation: GenerationQualityObservation,
) -> tuple[str, str, str, str, GenerationQualityObservation]:
    return (report_id, company_id, generated_at, release_mode, observation)


def test_거절후보_비율은_release_allowed_False_건수를_총건수로_나눈다() -> None:
    rows = [
        _row("r1", "c1", "2026-01-01T00:00:00", "", _observation(release_allowed=False)),
        _row("r2", "c1", "2026-01-01T00:00:00", "", _observation(release_allowed=False)),
        _row("r3", "c2", "2026-01-01T00:00:00", "", _observation(release_allowed=False)),
        _row("r4", "c2", "2026-01-01T00:00:00", "", _observation(release_allowed=True)),
    ]

    summary = summarize_quality_observations(rows)

    assert summary.total_count == 4
    assert summary.release_blocked_count == 3
    assert summary.release_blocked_ratio == "0.75"


def test_분모_0이면_비율은_None() -> None:
    summary = summarize_quality_observations([])

    assert summary.total_count == 0
    assert summary.release_blocked_count == 0
    assert summary.release_blocked_ratio is None
    assert summary.release_mode_breakdown == ()
    assert summary.top_companies == ()


def test_release_mode별_분해에서도_분모_0인_release_mode는_없으므로_비율이_항상_계산된다() -> None:
    rows = [
        _row("r1", "c1", "", "SHADOW", _observation(release_allowed=False)),
        _row("r2", "c1", "", "SHADOW", _observation(release_allowed=True)),
        _row("r3", "c1", "", "FULL", _observation(release_allowed=True)),
    ]

    summary = summarize_quality_observations(rows)

    assert summary.release_mode_breakdown == (
        ReleaseModeBreakdown(
            release_mode="FULL",
            total_count=1,
            release_blocked_count=0,
            release_blocked_ratio="0",
        ),
        ReleaseModeBreakdown(
            release_mode="SHADOW",
            total_count=2,
            release_blocked_count=1,
            release_blocked_ratio="0.5",
        ),
    )


def test_부족사유_코드는_전부_세어지고_누락_없다() -> None:
    rows = [
        _row(
            "r1",
            "c1",
            "",
            "",
            _observation(
                release_allowed=False,
                quality_problem_codes=("too_few_document_sources", "low_verified_ratio"),
            ),
        ),
        _row(
            "r2",
            "c1",
            "",
            "",
            _observation(
                release_allowed=False,
                quality_problem_codes=("low_verified_ratio",),
            ),
        ),
        _row("r3", "c1", "", "", _observation(release_allowed=True, quality_problem_codes=())),
    ]

    summary = summarize_quality_observations(rows)

    assert dict(summary.quality_problem_code_counts) == {
        "too_few_document_sources": 1,
        "low_verified_ratio": 2,
    }
    # 코드 하나 안 빠짐 — 세 관측값이 낸 코드 총 발생 수(3)와 정확히 일치한다.
    assert sum(count for _, count in summary.quality_problem_code_counts) == 3


def test_요약은_결정론적이다_입력_순서_무관() -> None:
    rows = [
        _row("r1", "c1", "2026-01-01", "SHADOW", _observation(release_allowed=False)),
        _row("r2", "c2", "2026-01-02", "FULL", _observation(release_allowed=True)),
        _row(
            "r3",
            "c1",
            "2026-01-03",
            "SHADOW",
            _observation(
                release_allowed=False,
                quality_problem_codes=("too_few_document_sources",),
            ),
        ),
        _row("r4", "c3", "2026-01-04", "", _observation(release_allowed=True)),
    ]

    forward = summarize_quality_observations(rows)
    shuffled = list(rows)
    random.Random(42).shuffle(shuffled)
    reversed_order = summarize_quality_observations(list(reversed(rows)))
    shuffled_summary = summarize_quality_observations(shuffled)

    assert forward == reversed_order == shuffled_summary


def test_회사별_상위목록은_거절건수_내림차순_그다음_총건수_그다음_회사id_오름차순이다() -> None:
    rows = [
        _row("r1", "많은회사", "", "", _observation(release_allowed=False)),
        _row("r2", "많은회사", "", "", _observation(release_allowed=False)),
        _row("r3", "적은회사", "", "", _observation(release_allowed=False)),
        _row("r4", "거절없는회사", "", "", _observation(release_allowed=True)),
        _row("r5", "거절없는회사", "", "", _observation(release_allowed=True)),
    ]

    summary = summarize_quality_observations(rows, top_companies_limit=2)

    assert summary.top_companies == (
        CompanyObservationCount(
            company_id="많은회사", total_count=2, release_blocked_count=2
        ),
        CompanyObservationCount(
            company_id="적은회사", total_count=1, release_blocked_count=1
        ),
    )


def test_top_companies_limit_0이면_빈_튜플이다() -> None:
    rows = [_row("r1", "c1", "", "", _observation(release_allowed=False))]

    summary = summarize_quality_observations(rows, top_companies_limit=0)

    assert summary.top_companies == ()


def test_safety_decision과_quality_grade_분포는_값_오름차순으로_정렬된다() -> None:
    rows = [
        _row("r1", "c1", "", "", _observation(safety_decision="공개 차단", quality_grade="미완성")),
        _row("r2", "c1", "", "", _observation(safety_decision="공개 가능", quality_grade="완성")),
        _row("r3", "c1", "", "", _observation(safety_decision="공개 가능", quality_grade="부분 완성")),
    ]

    summary = summarize_quality_observations(rows)

    assert summary.safety_decision_counts == (("공개 가능", 2), ("공개 차단", 1))
    assert summary.quality_grade_counts == (("미완성", 1), ("부분 완성", 1), ("완성", 1))


def test_top_companies_limit이_음수면_ValueError() -> None:
    with pytest.raises(ValueError):
        summarize_quality_observations([], top_companies_limit=-1)


def test_행이_5튜플이_아니면_TypeError() -> None:
    with pytest.raises(TypeError):
        summarize_quality_observations([("r1", "c1", "", _observation())])  # type: ignore[list-item]


def test_observation이_정확한_타입이_아니면_TypeError() -> None:
    with pytest.raises(TypeError):
        summarize_quality_observations([("r1", "c1", "", "", "관측값_아님")])  # type: ignore[list-item]


def test_문자열_필드가_문자열이_아니면_TypeError() -> None:
    with pytest.raises(TypeError):
        summarize_quality_observations([(1, "c1", "", "", _observation())])  # type: ignore[list-item]

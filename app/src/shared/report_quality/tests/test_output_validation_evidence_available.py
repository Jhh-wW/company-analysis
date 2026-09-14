"""확보 근거 보고서 정책일 때만 v2 출고 검증의 요약 하한이 풀리는지 못 박는다."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.shared.report_quality.models import PublicationPolicy
from src.shared.report_quality.output_constants import (
    SUMMARY_MAX_SENTENCES,
    SUMMARY_MIN_SENTENCES,
)
from src.shared.report_quality.output_validation import (
    _summary_problems,
    summary_floor_relaxed,
)


@dataclass
class _Item:
    text: str


@dataclass
class _Report:
    summary_items: list[_Item] = field(default_factory=list)
    publication_policy: str = ""


def test_기본_정책은_요약_3문장_미만이면_문제를_남긴다() -> None:
    report = _Report(summary_items=[_Item("하나")])

    assert not summary_floor_relaxed(report)
    problems = _summary_problems(report)
    assert len(problems) == 1
    assert f"{SUMMARY_MIN_SENTENCES}~{SUMMARY_MAX_SENTENCES}" in problems[0]


def test_확보_근거_정책은_요약이_없어도_통과한다() -> None:
    report = _Report(
        summary_items=[],
        publication_policy=PublicationPolicy.EVIDENCE_AVAILABLE.value,
    )

    assert summary_floor_relaxed(report)
    assert _summary_problems(report) == []


def test_확보_근거_정책도_요약_상한은_그대로_막는다() -> None:
    report = _Report(
        summary_items=[_Item(str(index)) for index in range(SUMMARY_MAX_SENTENCES + 1)],
        publication_policy=PublicationPolicy.EVIDENCE_AVAILABLE.value,
    )

    problems = _summary_problems(report)
    assert len(problems) == 1
    assert f"최대 {SUMMARY_MAX_SENTENCES}문장" in problems[0]


def test_다른_공개_정책은_완화를_받지_않는다() -> None:
    for policy in (
        PublicationPolicy.STRUCTURED_SAFETY.value,
        PublicationPolicy.LEGACY_SHADOW_EXCEPTION.value,
        "evidence-available-v2",
    ):
        report = _Report(summary_items=[], publication_policy=policy)
        assert not summary_floor_relaxed(report), policy
        assert _summary_problems(report), policy

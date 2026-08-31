from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.shared.report_quality.summary_binding import (
    summary_evidence_text,
    summary_verification_binding,
)


@dataclass(frozen=True)
class _Fact:
    claim: str


def test_근거묶음은_본문사실_ID와_주장_순서를_그대로_보존한다() -> None:
    facts = {
        "fact-b": _Fact("둘째 검증 주장"),
        "fact-a": _Fact("첫째 검증 주장"),
    }

    assert summary_evidence_text(("fact-a", "fact-b"), facts) == (
        "fact-a: 첫째 검증 주장\nfact-b: 둘째 검증 주장"
    )


def test_없는_사실을_조용히_빼고_봉인하지_않는다() -> None:
    with pytest.raises(KeyError):
        summary_evidence_text(("missing",), {})


def test_요약_근거_상태_중_한글자라도_바뀌면_지문이_달라진다() -> None:
    original = summary_verification_binding(
        "검증 본문을 그대로 쓴 요약",
        "business_model",
        ("fact-a",),
        "fact-a: 검증 본문을 그대로 쓴 요약",
        "verified",
        ("검증", "본문"),
    )
    changed_values = (
        summary_verification_binding(
            "바뀐 요약",
            "business_model",
            ("fact-a",),
            "fact-a: 검증 본문을 그대로 쓴 요약",
            "verified",
            ("검증", "본문"),
        ),
        summary_verification_binding(
            "검증 본문을 그대로 쓴 요약",
            "portfolio",
            ("fact-a",),
            "fact-a: 검증 본문을 그대로 쓴 요약",
            "verified",
            ("검증", "본문"),
        ),
        summary_verification_binding(
            "검증 본문을 그대로 쓴 요약",
            "business_model",
            ("fact-b",),
            "fact-a: 검증 본문을 그대로 쓴 요약",
            "verified",
            ("검증", "본문"),
        ),
        summary_verification_binding(
            "검증 본문을 그대로 쓴 요약",
            "business_model",
            ("fact-a",),
            "바뀐 근거",
            "verified",
            ("검증", "본문"),
        ),
        summary_verification_binding(
            "검증 본문을 그대로 쓴 요약",
            "business_model",
            ("fact-a",),
            "fact-a: 검증 본문을 그대로 쓴 요약",
            "unverified",
            ("검증", "본문"),
        ),
        summary_verification_binding(
            "검증 본문을 그대로 쓴 요약",
            "business_model",
            ("fact-a",),
            "fact-a: 검증 본문을 그대로 쓴 요약",
            "verified",
            ("본문", "검증"),
        ),
    )

    assert len(original) == 64
    assert all(value != original for value in changed_values)
